"""SVG の <text> だけを読む。svg1 の <line> と svg2 の <path> は見ない。

両方の変換器が同じ位置に <text> を書くので、図形を捨てれば同じ表になる。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from html.parser import HTMLParser

ROW_TOL = 2.0
COL_TOL = 3.0

# 内側の | が範囲や △ の代替を割らないよう、全体を非捕捉にする。
_NUM = r"(?:\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+)"
# 数値の後ろの空白は食わない。食うと match の終端が次の <text> に入り、右の列へずれる。
_AMOUNT = re.compile(
    rf"[△▲]\s*{_NUM}"
    rf"|\(\s*{_NUM}\s*\)"
    rf"|{_NUM}\s*[~～〜]\s*{_NUM}"
    rf"|{_NUM}[%％]?"
    rf"|[―－−—-]"
)
_RANGE = re.compile(rf"^({_NUM})\s*[~～〜]\s*({_NUM})$")
_NIL = {"―", "－", "-", "−", "—"}
_UNIT_NOTE = re.compile(r"単位|百万円|千円|円銭|百万円未満")
# 銘柄コード (7453) を負の金額と取り違えない。カンマ付き (1,234) は金額のまま。
_TICKER_PAREN = re.compile(r"^\(\s*\d{4}\s*\)$")


class PdfLayoutError(ValueError):
    pass


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    x1: float
    base: float
    size: float
    bold: bool


@dataclass(frozen=True)
class Line:
    base: float
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return "".join(word.text for word in self.words)


@dataclass(frozen=True)
class Page:
    index: int
    lines: tuple[Line, ...]


@dataclass(frozen=True)
class Amount:
    """印字の 1 数値。value が None はダッシュ。upper は 100～120 の上側。"""

    value: Decimal | None
    upper: Decimal | None
    x1: float


@dataclass(frozen=True)
class Column:
    x0: float
    x1: float
    headers: tuple[str, ...]


@dataclass(frozen=True)
class GridRow:
    label: str
    cells: tuple[Amount | None, ...]


@dataclass(frozen=True)
class Grid:
    unit_note: str | None
    columns: tuple[Column, ...]
    rows: tuple[GridRow, ...]


class _SvgTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pages: list[list[Word]] = []
        self._words: list[Word] | None = None
        self._attrs: dict[str, str] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "svg":
            self._words = []
            self.pages.append(self._words)
            return
        if tag == "text" and self._words is not None:
            self._attrs = {key.lower(): value or "" for key, value in attrs}
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "text" and self._attrs is not None and self._words is not None:
            self._emit()
        if tag == "svg":
            self._words = None

    def handle_data(self, data: str) -> None:
        if self._attrs is not None:
            self._buf.append(data)

    def _emit(self) -> None:
        attrs = self._attrs or {}
        self._attrs = None
        text = "".join(self._buf)
        self._buf = []
        if text == "":
            return
        try:
            x0 = float(attrs.get("x", "0"))
            base = float(attrs.get("y", "0"))
            size = float(attrs.get("font-size", "10") or "10")
        except ValueError:
            return
        length = attrs.get("textlength")
        try:
            width = float(length) if length else max(len(text) * size * 0.5, 0.1)
        except ValueError:
            width = max(len(text) * size * 0.5, 0.1)
        weight = (attrs.get("font-weight") or "").lower()
        bold = weight == "bold" or (weight.isdigit() and int(weight) >= 600)
        assert self._words is not None
        self._words.append(Word(text, x0, x0 + width, base, size, bold))


def read_pages(html: str) -> list[Page]:
    parser = _SvgTextParser()
    parser.feed(html)
    parser.close()
    pages: list[Page] = []
    for index, words in enumerate(parser.pages):
        pages.append(Page(index, _group_lines(words)))
    return pages


def _group_lines(words: list[Word]) -> tuple[Line, ...]:
    ordered = sorted(words, key=lambda word: (word.base, word.x0))
    groups: list[list[Word]] = []
    for word in ordered:
        if groups and abs(groups[-1][0].base - word.base) <= ROW_TOL:
            groups[-1].append(word)
        else:
            groups.append([word])
    lines: list[Line] = []
    for group in groups:
        group.sort(key=lambda word: word.x0)
        base = sum(word.base for word in group) / len(group)
        lines.append(Line(base, tuple(group)))
    return tuple(lines)


def _line_string(line: Line) -> tuple[str, list[tuple[int, int, Word]]]:
    """語の間に空白を置く。隣の <text> を連結すると 39.1 と 1,234.50 が 39.11 になる。"""
    parts: list[str] = []
    spans: list[tuple[int, int, Word]] = []
    pos = 0
    for index, word in enumerate(line.words):
        if index:
            parts.append(" ")
            pos += 1
        text = unicodedata.normalize("NFKC", word.text)
        parts.append(text)
        spans.append((pos, pos + len(text), word))
        pos += len(text)
    return "".join(parts), spans


def _x1_at(spans: list[tuple[int, int, Word]], index: int) -> float:
    for start, end, word in spans:
        if start < index <= end or (index == start == end):
            return word.x1
        if start <= index < end:
            return word.x1
    return spans[-1][2].x1


def _decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace(" ", ""))


def _parse_amount(raw: str, x1: float) -> Amount:
    text = re.sub(r"\s+", "", raw).replace("％", "%").replace("%", "")
    if text in _NIL:
        return Amount(None, None, x1)
    found = _RANGE.match(text)
    if found:
        return Amount(_decimal(found.group(1)), _decimal(found.group(2)), x1)
    negative = False
    body = text
    if body[:1] in "△▲":
        negative = True
        body = body[1:]
    elif body.startswith("(") and body.endswith(")"):
        negative = True
        body = body[1:-1]
    value = _decimal(body)
    if negative:
        value = -value
    return Amount(value, None, x1)


def split_amounts(line: Line) -> tuple[str, list[Amount]]:
    """行の右端の数値。行頭の「1.」のような番号はラベルのまま。"""
    text, spans = _line_string(line)
    matches = [
        match for match in _AMOUNT.finditer(text)
        if not _TICKER_PAREN.fullmatch(re.sub(r"\s+", "", match.group(0)))
    ]
    if not matches or not spans:
        return text.strip(), []
    chosen: list[re.Match[str]] | None = None
    for index, match in enumerate(matches):
        cursor = match.start()
        ok = True
        for later in matches[index:]:
            if text[cursor:later.start()].strip():
                ok = False
                break
            cursor = later.end()
        if ok and not text[cursor:].strip():
            chosen = matches[index:]
            break
    if not chosen:
        return text.strip(), []
    amounts = [
        _parse_amount(match.group(0), _x1_at(spans, match.end()))
        for match in chosen
    ]
    return text[:chosen[0].start()].strip(), amounts


def _cluster(values: list[float]) -> list[float]:
    ordered = sorted(values)
    groups: list[list[float]] = [[ordered[0]]]
    for value in ordered[1:]:
        if value - groups[-1][-1] <= COL_TOL:
            groups[-1].append(value)
        else:
            groups.append([value])
    return [sum(group) / len(group) for group in groups]


def _unit_note(lines: list[Line]) -> str | None:
    for line in lines:
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", line.text))
        if _UNIT_NOTE.search(compact):
            return compact
    return None


def grid(lines: list[Line]) -> Grid:
    parsed = [(line, *split_amounts(line)) for line in lines]
    numeric = [index for index, (_, _, amounts) in enumerate(parsed) if amounts]
    if not numeric:
        raise PdfLayoutError("no anchors")
    anchors = _cluster([
        amount.x1
        for index in numeric
        for amount in parsed[index][2]
    ])
    header_lines = [parsed[index][0] for index in range(numeric[0]) if not parsed[index][2]]
    columns: list[Column] = []
    for anchor in anchors:
        headers: list[str] = []
        for line in header_lines:
            for word in line.words:
                if word.x0 - COL_TOL <= anchor <= word.x1 + COL_TOL:
                    headers.append(word.text)
        columns.append(Column(anchor, anchor, tuple(headers)))
    rows: list[GridRow] = []
    pending = ""
    for index, (line, label, amounts) in enumerate(parsed):
        if index < numeric[0]:
            continue
        if not amounts:
            pending = f"{pending}{line.text}"
            continue
        compact_label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", label))
        complete = bool(re.search(
            r"親会社株主に帰属する(当期|四半期|中間)純(利益|損失)$"
            r"|親会社の所有者に帰属する(当期|四半期|中間)利益$",
            compact_label,
        ))
        full = f"{pending}{label}" if pending and not complete else (label or pending)
        pending = ""
        cells: list[Amount | None] = []
        for column in columns:
            cell = next((amount for amount in amounts if abs(amount.x1 - column.x1) <= COL_TOL), None)
            cells.append(cell)
        rows.append(GridRow(full, tuple(cells)))
    return Grid(_unit_note(lines), tuple(columns), tuple(rows))
