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
# 同じ列でも △ の数値は右端が数 pt ずれることがある。同じ行に並ばない近い列は 1 列にまとめる。
COL_MERGE = 8.0

# 内側の | が範囲や △ の代替を割らないよう、全体を非捕捉にする。
_NUM = r"(?:\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+\.\d+)"
# 「20円00銭」（語が分かれて "20 円 00 銭" でも）。_NUM より前に置く。
_YEN_SEN = rf"{_NUM}\s*円\s*\d{{2}}\s*銭"
_YEN_SEN_VALUE = re.compile(rf"^({_NUM})円(\d{{2}})銭$")
# 数値の後ろの空白は食わない。食うと match の終端が次の <text> に入り、右の列へずれる。
_AMOUNT = re.compile(
    rf"[△▲]\s*{_NUM}(?:\s*[~～〜](?!\s*[△▲\d]))?"
    rf"|\(\s*{_NUM}\s*\)"
    # 1 語の中のレンジ（「100～120」）。語の間の空白をまたがない（「5,100～ 30.9～」は 2 つの下限）。
    rf"|{_NUM}[~～〜]{_NUM}"
    # レンジの下限だけ（「5,100～」）。上限は下の行にある。
    rf"|{_NUM}[%％]?\s*[~～〜]"
    # レンジの上限だけ（「～5,257」「～△18.4」）。下限は上の行にある。
    rf"|[~～〜]\s*[△▲]?\s*{_NUM}[%％]?"
    rf"|{_YEN_SEN}"
    rf"|{_NUM}[%％]?"
    rf"|[―－−—-]"
    rf"|未定"
    # レンジの「～」だけ（下限の行と上限の行の間）。
    rf"|[~～〜]"
)
_TILDE = "~～〜"
_TILDE_TOL = 30.0
_RANGE = re.compile(rf"^({_NUM})\s*[~～〜]\s*({_NUM})$")
_NIL = {"―", "－", "-", "−", "—", "未定"}
_UNIT_NOTE = re.compile(r"単位|百万円|千円|億円|兆円|円銭|百万円未満")
# 銘柄コード (7453) を負の金額と取り違えない。カンマ付き (1,234) は金額のまま。
_TICKER_PAREN = re.compile(r"^\(\s*\d{4}\s*\)$")
# ページ番号（- 6 - / ―6― / 6）。ダッシュが金額の「－」と読まれて幻の列になる。
_PAGE_NUMBER = re.compile(r"^[-－―‐−—]?\d{1,3}[-－―‐−—]?$")
# 柱（銘柄名・コード・題名）は 3 ページ以上で同じ高さに同じ文字で出る。ページの上下の帯だけを見る
# （表の「(単位：千円)」や列見出しもページをまたいで同じ高さに出る）。
_FURNITURE_PAGES = 3
_FURNITURE_TOL = 3.0
_FURNITURE_TOP = 60.0
_FURNITURE_BOTTOM = 780.0
# 表の右上の注記。列見出しではない（「対前期増減率」で列を捨てない）。
# ＝ で始まる式（KAB-216 9983「＝売上収益－売上原価－…」）も見出しにしない。
_HEADER_NOTE = re.compile(r"表示は|^\(注|[＝=]")
# 注記の行（「(注)包括利益 … △52百万円」）。列見出しにしない。
_NOTE_LINE = re.compile(r"^(\(注|※|\(参考\)|[＝=])")
# 数値の無い式の行（KAB-216 9983「事業利益 ＝ 売上収益 － 売上原価 －…」と折り返しの「－ 販売費…」）。
# NFKC 後の行に当てる。- は文字クラスの先頭でエスケープする（[＝=-−] は = から − までの範囲になり、
# EPS・ROE のような英字で始まる行まで注記になる）。ダッシュの後ろが数字・ダッシュなら金額の印なので外す。
_FORMULA_LINE = re.compile(r"=|^[\-‐−—―](?=[^\d\-‐−—―])")
# ※1 / ※ の注記番号（KAB-214）。空白の後ろだけ消す（※2171,958 の番号と金額をつながない）。
_NOTE_MARK = re.compile(r"※\s*\d{0,2}(?=\s|$)")
# 配当の「40 円 40 円」（KAB-213）。金額の間と末尾の飾り。
_AMOUNT_GAP = re.compile(r"^[\s円銭]+$")
# 語の途中から始まる数字は金額にしない（KAB-216）。直前が行頭・空白・△▲・～ のときだけ。
_AMOUNT_PREV = set("△▲~～〜")
# 列見出しの打ち切り（KAB-216）。空白を除いてこの字数を超える「語」は注記（行全体ではない。KAB-224）。
_HEADER_PROSE_LEN = 40
# 本表ページの文字化け（KAB-218）。想定する文字種以外の割合。
_GARBLED_RATIO = 0.5
_GARBLED_MIN_CHARS = 30
# 見出しの語が数値の列に重ならないとき、この距離までなら中心がいちばん近い列の見出し。
_HEADER_REACH = 80.0
# ％ 列の単位語（NFKC 後）。またがる見出しの分け先から外す（KAB-238。1377 の純利益は金額列に残す）。
_PERCENT_UNIT = frozenset({"%", "(%)"})
# 分けたかけらがこれより短い列は、分割せず語全体を載せる（「金」だけ隣へ、を防ぐ）。
_SPAN_MIN_CHARS = 3
# 行名がここで終われば、次の数値の無い行は同じ行名の続き。
_CONTINUES = re.compile(r"(及び|並びに|又は|に|の|・|、|帰属する|係る|対する)$")
# 数値の無い行が前の行名の折り返しの尾（「…中間純」＋「損失（△）」、「額（△は減少）」、「(予想)」）。
_TAIL = re.compile(r"^純?損失|^額|^[（(]予想")
# 値だけの行のあとでも、これは新しい行名（KAB-213 の今回予想を前回に付けない）。
_ROW_ROLE = re.compile(r"^(前回|今回|前期|通期|第[123]四半期|(当|前|次)期)")
# 数値の下にずれた行名（松井の配当通知。KAB-237）。上の次の行の見出しにはしない。
_UNDER_ROW = re.compile(r"^(前回|今回|実績)")


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
    x0: float = 0.0
    # レンジの印。value があれば「下限～」、無ければ「～」だけ。上限は下の行。
    tilde: bool = False
    # 「～上限」。下限は上の行。
    upper_mark: bool = False


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
    return _strip_furniture(pages)


def _furniture_key(line: Line) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", line.text))


def _strip_furniture(pages: list[Page]) -> list[Page]:
    """ページ番号と柱を落とす。柱は 2 行に分かれることがあり、行ごとに数える。"""
    seen: dict[str, list[tuple[int, float]]] = {}
    for page in pages:
        for line in page.lines:
            seen.setdefault(_furniture_key(line), []).append((page.index, line.base))

    def repeated(line: Line) -> bool:
        hits = seen.get(_furniture_key(line), [])
        if _FURNITURE_TOP < line.base < _FURNITURE_BOTTOM:
            return False
        near = {index for index, base in hits if abs(base - line.base) <= _FURNITURE_TOL}
        return len(near) >= _FURNITURE_PAGES

    def page_number(line: Line) -> bool:
        # 表の途中の「- 104」（前期ダッシュ・当期 104）をページ番号と取り違えない（KAB-220）。
        if not _PAGE_NUMBER.fullmatch(_furniture_key(line)):
            return False
        return not (_FURNITURE_TOP < line.base < _FURNITURE_BOTTOM)

    out: list[Page] = []
    for page in pages:
        kept = tuple(
            line for line in page.lines
            if not page_number(line) and not repeated(line)
        )
        out.append(Page(page.index, kept))
    return out


def _is_expected_char(ch: str) -> bool:
    """ASCII・かな・漢字・全角・CJK 記号と、短信でよく出る印（※△①）。"""
    if ch.isascii():
        return True
    code = ord(ch)
    if 0x3000 <= code <= 0x30FF:
        return True
    if 0x4E00 <= code <= 0x9FFF:
        return True
    if 0xFF00 <= code <= 0xFFEF:
        return True
    if 0x2000 <= code <= 0x206F:
        return True
    if 0x2190 <= code <= 0x21FF:
        return True
    if 0x2460 <= code <= 0x24FF:
        return True
    if 0x2500 <= code <= 0x25FF:
        return True
    if 0x3200 <= code <= 0x33FF:
        return True
    if 0x2E80 <= code <= 0x2EFF:
        return True
    if 0x31C0 <= code <= 0x31FF:
        return True
    if 0x00A0 <= code <= 0x024F:
        return True
    return False


def garbled_ratio(page: Page) -> float:
    chars = [ch for line in page.lines for ch in line.text if not ch.isspace()]
    if len(chars) < _GARBLED_MIN_CHARS:
        return 0.0
    bad = sum(1 for ch in chars if not _is_expected_char(ch))
    return bad / len(chars)


def garbled_page_indexes(pages: list[Page]) -> list[int]:
    """0 始まりのページ番号。想定する文字種以外が半分以上なら文字化け（KAB-218）。"""
    return [page.index for page in pages if garbled_ratio(page) >= _GARBLED_RATIO]


def format_garbled_pages(indexes: list[int]) -> str:
    if not indexes:
        return ""
    pages = [index + 1 for index in indexes]
    ranges: list[str] = []
    start = prev = pages[0]
    for number in pages[1:]:
        if number == prev + 1:
            prev = number
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = number
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return "garbled pages=" + ",".join(ranges)


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


def _x0_at(spans: list[tuple[int, int, Word]], index: int) -> float:
    for start, end, word in spans:
        if start <= index < end:
            return word.x0
    return spans[-1][2].x0


def _x1_at(spans: list[tuple[int, int, Word]], index: int) -> float:
    for start, end, word in spans:
        if start < index <= end or (index == start == end):
            return word.x1
        if start <= index < end:
            return word.x1
    return spans[-1][2].x1


def _decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace(" ", ""))


def _parse_amount(raw: str, x1: float, x0: float = 0.0) -> Amount:
    text = re.sub(r"\s+", "", raw).replace("％", "%").replace("%", "")
    if text in _NIL:
        return Amount(None, None, x1, x0)
    if text in _TILDE:
        return Amount(None, None, x1, x0, tilde=True)
    if text[0] in _TILDE:
        upper = _parse_amount(text[1:], x1, x0)
        return Amount(upper.value, None, x1, x0, upper_mark=True)
    if text[-1] in _TILDE:
        lower = _parse_amount(text[:-1], x1, x0)
        return Amount(lower.value, None, x1, x0, tilde=True)
    found = _RANGE.match(text)
    if found:
        return Amount(_decimal(found.group(1)), _decimal(found.group(2)), x1, x0)
    yen_sen = _YEN_SEN_VALUE.fullmatch(text)
    if yen_sen:
        value = _decimal(yen_sen.group(1)) + _decimal(yen_sen.group(2)) / Decimal(100)
        return Amount(value, None, x1, x0)
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
    return Amount(value, None, x1, x0)


def _mask_note_marks(text: str) -> str:
    return _NOTE_MARK.sub(lambda match: " " * len(match.group(0)), text)


def _amount_start_ok(text: str, start: int) -> bool:
    if start == 0:
        return True
    prev = text[start - 1]
    return prev.isspace() or prev in _AMOUNT_PREV


def _skippable_gap(text: str) -> bool:
    return not text.strip() or bool(_AMOUNT_GAP.fullmatch(text))


def split_amounts(line: Line) -> tuple[str, list[Amount]]:
    """行の右端の数値。行頭の「1.」のような番号はラベルのまま。"""
    text, spans = _line_string(line)
    text = _mask_note_marks(text)
    matches = [
        match for match in _AMOUNT.finditer(text)
        if not _TICKER_PAREN.fullmatch(re.sub(r"\s+", "", match.group(0)))
        and _amount_start_ok(text, match.start())
    ]
    if not matches or not spans:
        return text.strip(), []
    chosen: list[re.Match[str]] | None = None
    for index, match in enumerate(matches):
        cursor = match.start()
        ok = True
        for later in matches[index:]:
            if not _skippable_gap(text[cursor:later.start()]):
                ok = False
                break
            cursor = later.end()
        if ok and _skippable_gap(text[cursor:]):
            chosen = matches[index:]
            break
    if not chosen:
        return text.strip(), []
    amounts = [
        _parse_amount(
            match.group(0),
            _x1_at(spans, match.end()),
            # 語の途中から始まる数値（「△ 1,234」の後半など）は語の左端で代える。
            _x0_at(spans, match.start() + len(match.group(0)) - len(match.group(0).lstrip())),
        )
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
        if not (amount.tilde and amount.value is None)
    ] or [0.0])
    anchors = _merge_anchors(anchors, [
        [amount.x1 for amount in parsed[index][2] if not (amount.tilde and amount.value is None)]
        for index in numeric
    ])
    # 列の左端は、その列に入る数値の左端の最小。見出しは中央に寄るので右端だけでは当たらない。
    anchors, tolerance = anchors
    lefts = {anchor: anchor for anchor in anchors}
    has_value = {anchor: False for anchor in anchors}
    for index in numeric:
        for amount in parsed[index][2]:
            anchor = min(anchors, key=lambda x: abs(x - amount.x1))
            if abs(anchor - amount.x1) <= tolerance[anchor]:
                if amount.x0:
                    lefts[anchor] = min(lefts[anchor], amount.x0)
                if amount.value is not None:
                    has_value[anchor] = True
    # ダッシュだけの列も残す（配当の「第１四半期末 －」）。消すと見出しが隣の列へ寄る。
    if not any(has_value.values()):
        raise PdfLayoutError("no anchors")
    min_left = min(lefts.values()) if lefts else 0.0

    def is_note(index: int) -> bool:
        compact = _compact_text(parsed[index][0].text)
        if _NOTE_LINE.match(compact):
            return True
        return not parsed[index][2] and bool(_FORMULA_LINE.search(compact))

    def is_prose(index: int) -> bool:
        line = parsed[index][0]
        compact = _compact_text(line.text)
        if "。" in compact or is_note(index):
            return True
        # 行全体ではなく語ごと（KAB-224。武田の EPS 見出しは短い語が 1 行に並び 40 字を超える）。
        words = line.words
        if not words:
            return len(compact) > _HEADER_PROSE_LEN
        return any(len(_compact_text(word.text)) > _HEADER_PROSE_LEN for word in words)

    def is_hanging_label(index: int) -> bool:
        """数値より上にずれた行名（列より左だけ。列に乗る語があれば見出し）。KAB-213。"""
        _, _, amounts = parsed[index]
        if amounts:
            return False
        compact = _compact_text(parsed[index][0].text)
        if _UNIT_NOTE.search(compact) or compact in {"円", "銭"}:
            return False
        words = parsed[index][0].words
        if not words:
            return False
        edge = min_left - COL_TOL
        return max(word.x1 for word in words) < edge

    hanging: set[int] = set()
    cursor = numeric[0] - 1
    while cursor >= 0:
        if parsed[cursor][2] or is_prose(cursor) or not is_hanging_label(cursor):
            break
        hanging.add(cursor)
        cursor -= 1
    header_from = 0
    for index in range(numeric[0] - 1, -1, -1):
        if index in hanging:
            continue
        # 式の注記は見出しの途中に挟まる。ここで切ると上の科目名が落ちる（KAB-216 9983）。
        if is_note(index):
            continue
        if parsed[index][2] or is_prose(index):
            header_from = index + 1
            break
    header_lines = [
        parsed[index][0]
        for index in range(header_from, numeric[0])
        if index not in hanging
        and not parsed[index][2]
        and not is_note(index)
    ]
    headers_of: dict[float, list[str]] = {anchor: [] for anchor in anchors}
    percent = _percent_unit_columns(header_lines, anchors, lefts)
    for line in header_lines:
        for word in line.words:
            compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", word.text))
            if _HEADER_NOTE.search(compact):
                continue
            for anchor, fragment in _assign_header_word(word, anchors, lefts, percent):
                headers_of[anchor].append(fragment)
    columns = [Column(lefts[anchor], anchor, tuple(headers_of[anchor])) for anchor in anchors]
    column_tol = {anchor: tolerance[anchor] for anchor in anchors}
    rows: list[GridRow] = []
    pending = ""
    last_numeric_y: float | None = None
    # 直前の行が「数値だけの行」に行名を前の行から借りたか（3 行に折り返した行名の真ん中に数値がある）
    value_only = False
    for index, (line, label, amounts) in enumerate(parsed):
        if index < numeric[0] and index not in hanging:
            continue
        if not amounts:
            compact_line = _compact_text(line.text)
            if (
                rows
                and last_numeric_y is not None
                and is_hanging_label(index)
                and _UNDER_ROW.match(compact_line)
            ):
                next_num = next((j for j in range(index + 1, len(parsed)) if parsed[j][2]), None)
                if next_num is None or abs(line.base - last_numeric_y) <= abs(
                    parsed[next_num][0].base - line.base
                ):
                    rows[-1] = GridRow(f"{rows[-1].label}{line.text}", rows[-1].cells)
                    value_only = False
                    continue
            if rows and not pending and not _ROW_ROLE.match(compact_line) and (
                value_only
                or _CONTINUES.search(_compact_text(rows[-1].label))
                or _TAIL.search(compact_line)
            ):
                rows[-1] = GridRow(f"{rows[-1].label}{line.text}", rows[-1].cells)
                value_only = False
                continue
            pending = f"{pending}{line.text}"
            continue
        compact_label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", label))
        complete = bool(re.search(
            r"親会社株主に帰属する(当期|四半期|中間)純(利益|損失)$"
            r"|親会社の所有者に帰属する(当期|四半期|中間)利益$",
            compact_label,
        ))
        full = f"{pending}{label}" if pending and not complete else (label or pending)
        # 行名を借りた数値行のあと、「(予想)」「額（△は減少）」は続き。今回予想は新しい行。
        value_only = bool(pending) and not label.strip()
        pending = ""
        cells: list[Amount | None] = []
        for column in columns:
            cell = next((amount for amount in amounts if abs(amount.x1 - column.x1) <= column_tol[column.x1]), None)
            cells.append(cell)
        # 「～」だけの印は数値の列に揃わない。いちばん近い空の列へ。
        for amount in amounts:
            if not (amount.tilde and amount.value is None) or not columns:
                continue
            nearest = min(range(len(columns)), key=lambda n: abs(columns[n].x1 - amount.x1))
            if cells[nearest] is None and abs(columns[nearest].x1 - amount.x1) <= _TILDE_TOL:
                cells[nearest] = amount
        if not any(cells):
            continue
        rows.append(GridRow(full, tuple(cells)))
        last_numeric_y = line.base
    return Grid(_unit_note(lines), tuple(columns), tuple(_join_ranges(rows)))


def _range_label(*labels: str) -> str:
    for label in labels:
        text = label.strip(_TILDE + " ")
        if text:
            return text
    return ""


def _join_ranges(rows: list[GridRow]) -> list[GridRow]:
    """3 行に分かれたレンジ（下限の行・「～」の行・上限の行）を 1 行の下限～上限にする。

    「5,100～」のように下限の後ろに ～ がある書き方は、下限の行・ダッシュの行・上限の行のこともある。
    行名は 3 行のうち空でない最初のもの（たいてい真ん中の「通期」）。
    """
    out: list[GridRow] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        bare = any(cell is not None and cell.tilde and cell.value is None for cell in row.cells)
        lower_open = any(cell is not None and cell.tilde and cell.value is not None for cell in row.cells)
        if bare and out and index + 1 < len(rows):
            low, high = out.pop(), rows[index + 1]
            out.append(_merge_range(low, high, _range_label(row.label, low.label, high.label)))
            index += 2
            continue
        if any(cell is not None and cell.upper_mark for cell in row.cells) and out:
            # 下限の行と「～上限」の行の間に、行名だけ（とダッシュ）の行が挟まることがある。
            between: list[GridRow] = []
            while out and not any(
                cell is not None and cell.value is not None for cell in out[-1].cells
            ):
                between.insert(0, out.pop())
            if out:
                low = out.pop()
                label = _range_label(*(other.label for other in between), low.label, row.label)
                out.append(_merge_range(low, row, label))
                index += 1
                continue
            out.extend(between)
        if lower_open:
            ahead = rows[index + 1:index + 3]
            high_at = next(
                (n for n, other in enumerate(ahead) if any(
                    cell is not None and cell.value is not None and not cell.tilde for cell in other.cells
                )),
                None,
            )
            if high_at is not None:
                between = [other.label for other in ahead[:high_at]]
                high = ahead[high_at]
                out.append(_merge_range(row, high, _range_label(row.label, *between, high.label)))
                index += high_at + 2
                continue
        out.append(row)
        index += 1
    return out


def _merge_range(low: GridRow, high: GridRow, label: str) -> GridRow:
    cells: list[Amount | None] = []
    for left, right in zip(low.cells, high.cells):
        if left is not None and right is not None and left.value is not None and right.value is not None:
            cells.append(Amount(left.value, right.value, right.x1, left.x0))
        else:
            cells.append(left if left is not None and left.value is not None else right)
    return GridRow(label, tuple(cells))


def _compact_text(raw: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", raw or ""))


def _header_overlap(word: Word, anchor: float, left: float) -> float:
    """見出しの語と列（左端〜右端）が重なる幅。端が触れるだけの列は 0。"""
    return max(0.0, min(word.x1, anchor) - max(word.x0, left))


def _columns_under(word: Word, anchors: list[float], lefts: dict[float, float]) -> list[float]:
    """見出しの語が重なる列。隣の列と端だけ接する語は入れない（KAB-213 の経常利益）。

    重なりが正の列はすべて返す。2 列以上の金額列にまたがる語の分割は _assign_header_word。
    どれにも重ならなければ、中心がいちばん近い列（_HEADER_REACH 以内）。
    """
    overlaps = [(anchor, _header_overlap(word, anchor, lefts[anchor])) for anchor in anchors]
    positive = [anchor for anchor, width in overlaps if width > 0]
    if positive:
        return positive
    center = (word.x0 + word.x1) / 2
    best = min(anchors, key=lambda anchor: abs((lefts[anchor] + anchor) / 2 - center))
    if abs((lefts[best] + best) / 2 - center) <= _HEADER_REACH:
        return [best]
    return []


def _percent_unit_columns(
    header_lines: list[Line],
    anchors: list[float],
    lefts: dict[float, float],
) -> set[float]:
    """単位が ％ だけの列。またがる見出しをここに分けない。"""
    found: set[float] = set()
    for line in header_lines:
        for word in line.words:
            if _compact_text(word.text) not in _PERCENT_UNIT:
                continue
            found.update(_columns_under(word, anchors, lefts))
    return found


def _assign_header_word(
    word: Word,
    anchors: list[float],
    lefts: dict[float, float],
    percent: set[float],
) -> list[tuple[float, str]]:
    """見出しの語を列へ。％ 列を挟んで金額列をまたぐ 1 語は文字ごとに分ける（KAB-238）。

    隣り合う金額列にまたがる親見出し（「年間配当金」）は語全体を載せる。
    """
    overlapping = _columns_under(word, anchors, lefts)
    if not overlapping:
        return []
    targets = [anchor for anchor in overlapping if anchor not in percent]
    assign_to = targets or overlapping
    if len(targets) >= 2 and not _amount_span_contiguous(overlapping, targets):
        pieces = _split_spanning_header(word, targets, lefts)
        if sum(1 for _anchor, fragment in pieces if len(fragment) >= _SPAN_MIN_CHARS) >= 2:
            return pieces
    return [(anchor, word.text) for anchor in assign_to]


def _amount_span_contiguous(overlapping: list[float], targets: list[float]) -> bool:
    """targets が overlapping の中で途切れない（間に ％ 列が無い）。"""
    start = overlapping.index(targets[0])
    end = overlapping.index(targets[-1])
    return overlapping[start:end + 1] == targets


def _split_spanning_header(
    word: Word,
    anchors: list[float],
    lefts: dict[float, float],
) -> list[tuple[float, str]]:
    """textLength / 字数で 1 文字ごとの x を推定し、最寄りの列へ割り当ててから列ごとに連結する。"""
    text = word.text
    width = word.x1 - word.x0
    if not text or width <= 0:
        return [(anchors[0], text)]
    char_w = width / len(text)
    buckets: dict[float, list[str]] = {anchor: [] for anchor in anchors}
    for index, char in enumerate(text):
        center = word.x0 + (index + 0.5) * char_w
        best = min(anchors, key=lambda anchor: abs((lefts[anchor] + anchor) / 2 - center))
        buckets[best].append(char)
    return [(anchor, "".join(chars)) for anchor, chars in buckets.items() if chars]


def _merge_anchors(anchors: list[float], rows: list[list[float]]) -> tuple[list[float], dict[float, float]]:
    """COL_MERGE 以内で、同じ行に両方の値が無い隣の列をまとめる。戻り値は列と、その列に入る右端の幅。"""
    groups: list[list[float]] = [[anchor] for anchor in sorted(anchors)]

    def together(left: list[float], right: list[float]) -> bool:
        for row in rows:
            in_left = any(any(abs(x - a) <= COL_TOL for a in left) for x in row)
            in_right = any(any(abs(x - a) <= COL_TOL for a in right) for x in row)
            if in_left and in_right:
                return True
        return False

    merged: list[list[float]] = []
    for group in groups:
        if merged and group[0] - merged[-1][-1] <= COL_MERGE and not together(merged[-1], group):
            merged[-1].extend(group)
        else:
            merged.append(group)
    out = [max(group) for group in merged]
    tolerance = {max(group): COL_TOL + (max(group) - min(group)) for group in merged}
    return out, tolerance
