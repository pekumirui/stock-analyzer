"""TDnet 月次 PDF → `.pdf-ingest.json`（前回投入分と同じ書式のときだけ）。自動 POST しない。

銘柄ごとの書式は宣言しない。DB に入っている前回の PDF から学ぶ:

1. 前回の PDF（DB に開示月の値がある最新のもの）を `pdftotext -tsv` で語と座標にし、
   月見出しの行と、その列にそろう数値の行（グリッド）に分ける。月が行の縦組みも読む
2. DB の各行（element）の値を再現する行を探す。unit は DB から。
   DB が指数で PDF が増減率なら `yoy_pct`（+100 でそろう）
3. 新しい PDF の書式の指紋（数値・日付を伏せた見出しと行名）が前回と同じなら、
   同じ行を読んで sidecar を書く。重なる月は DB の値と突き合わせる

指紋が違う・DB の行を全部は再現できない・重なる月が食い違う PDF は書かない（人が入れる）。
理由は `manual` の行に出す。R2 の PDF は読むだけ。DB は sqlite の mode=ro。ネットワークには出ない。

  python jobs/tdnet_monthly_pdf_sidecar.py
  python jobs/tdnet_monthly_pdf_sidecar.py --only 428A --dry-run
  python jobs/tdnet_monthly_pdf_sidecar.py --dest /tmp/monthly --db /tmp/stock.db

VPS cron (JST, after tdnet_monthly_r2.py 18:00 / 21:00): 18:20 / 21:20
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import subprocess  # nosec B404
import sys
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _r2common import resolve_database_url, sqlite_path_from_url  # noqa: E402
from ir_monthly_r2 import atomic_write  # noqa: E402
from tdnet_monthly_r2 import DEFAULT_DEST, display_code, jq_lookup_code  # noqa: E402

SIDECAR_SUFFIX = ".pdf-ingest.json"
PDFTOTEXT_TIMEOUT = 60
# 前回の PDF を何本までさかのぼって学ぶか（DB に開示月の値があるものだけ数える）
MAX_SOURCES = 3
MIN_HEADER_MONTHS = 3
# 行の間がこれ（行の高さの倍数）以上空いたら表は終わり
GRID_GAP = 3.0
TOL = 0.051

_PDF_NAME_RE = re.compile(r"^(\d{8})_.*\.pdf$", re.I)
_CODE_DIR_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
# 9月 / 10月度 / 2025年7月 / 25年7月 / 2025/7 / 7月分
_MONTH_TOKEN_RE = re.compile(r"^(?:(?:\d{2}|\d{4})[年/.])?(\d{1,2})月(?:度|分)?$|^\d{4}[/.](\d{1,2})$")
_YEAR_PREFIX_RE = re.compile(r"^(\d{2}|\d{4})[年/.]")
_EN_MONTH_RE = re.compile(
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)"
    r"(uary|ruary|ch|il|e|y|ust|tember|ober|ember)?\.?$",
    re.I,
)
_EN_MONTHS = {
    m: i
    for i, m in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1
    )
}
_UNIT_SUFFIX = (
    r"(?:%|ポイント|pt|店舗|店|件|人|名|社|台|戸|室|棟|拠点|施設|ヶ所|か所|箇所|口座"
    r"|千円|百万円|億円|円|倍|日|回)"
)
_NUM_TOKEN_RE = re.compile(r"^([▲△\-−]?)([0-9][0-9,]*(?:\.[0-9]+)?)" + _UNIT_SUFFIX + "?$")
_UNIT_TOKEN_RE = re.compile("^" + _UNIT_SUFFIX + "$")
_BLANK_TOKENS = frozenset({"-", "−", "―", "—", "–", "ー", "‐", "*", "…", "n/a", "N/A"})
# tools/fmtdiff.py の norm と同じ伏せ方（pdfplumber は VPS に無いので import しない）
_RE_DATE = re.compile(r"\d+\s*年\s*\d+\s*月(\s*\d+\s*日)?|\d{4}[/-]\d{1,2}([/-]\d{1,2})?")
_RE_QUARTER = re.compile(r"第\s*\d+\s*(四半期|Q)")
_RE_NUM = re.compile(r"[△▲\-−+]?\d[\d,]*(\.\d+)?")


# ---------------------------------------------------------------- 語と行


@dataclass(frozen=True)
class Tok:
    text: str
    x0: float
    x1: float


@dataclass(frozen=True)
class Line:
    page: int
    top: float
    height: float
    toks: tuple[Tok, ...]


def mask(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = _RE_DATE.sub("<D>", s)
    s = _RE_QUARTER.sub("<Q>", s)
    s = _RE_NUM.sub("<N>", s)
    return re.sub(r"\s+", "", s)


def _month_of(token: str) -> int | None:
    m = _MONTH_TOKEN_RE.match(token)
    if m is None:
        if _EN_MONTH_RE.match(token):
            return _EN_MONTHS.get(token.rstrip(".").lower()[:3])
        return None
    month = int(m.group(1) or m.group(2))
    return month if 1 <= month <= 12 else None


def _year_of(token: str) -> int | None:
    m = _YEAR_PREFIX_RE.match(token)
    if m is None or _month_of(token) is None:
        return None
    year = int(m.group(1))
    return year + 2000 if year < 100 else year


def parse_number(token: str) -> tuple[bool, float | None]:
    """(数値セルか, 値)。- 類は (True, None)。▲△ は負。"""
    t = token.strip()
    if t in _BLANK_TOKENS:
        return True, None
    if len(t) > 2 and t.startswith("(") and t.endswith(")"):
        t = t[1:-1]  # 8 (0) の (0) は FC 店舗数などの別の値
    m = _NUM_TOKEN_RE.match(t)
    if m is None:
        return False, None
    value = float(m.group(2).replace(",", ""))
    return True, (-value if m.group(1) else value)


def _merge(toks: Sequence[Tok], near: float) -> tuple[Tok, ...]:
    """10 + 月 → 10月、月 + 度 → 月度、275 + 店 → 275店。near 以内の隣だけつなぐ。"""
    out: list[Tok] = []
    for t in toks:
        cur = Tok(unicodedata.normalize("NFKC", t.text), t.x0, t.x1)
        if out and cur.x0 - out[-1].x1 <= near:
            prev = out[-1]
            if (
                (re.fullmatch(r"\d{1,2}", prev.text) and re.fullmatch(r"月[度分]?", cur.text))
                or (prev.text.endswith("月") and cur.text == "度")
                or (_UNIT_TOKEN_RE.match(cur.text) and _NUM_TOKEN_RE.match(prev.text))
            ):
                out[-1] = Tok(prev.text + cur.text, prev.x0, cur.x1)
                continue
        out.append(cur)
    return tuple(out)


def lines_from_tsv(tsv: str) -> list[Line]:
    """`pdftotext -tsv` の語を、ページごとに縦位置で行にまとめる。"""
    words: dict[int, list[tuple[float, float, float, float, str]]] = {}
    for raw in tsv.splitlines()[1:]:
        cols = raw.split("\t")
        if len(cols) < 12 or cols[0] != "5":
            continue
        try:
            page = int(cols[1])
            left, top, width, height = (float(v) for v in cols[6:10])
        except ValueError:
            continue
        text = cols[11].strip()
        if not text or height <= 0:
            continue
        words.setdefault(page, []).append((top, height, left, left + width, text))
    lines: list[Line] = []
    for page in sorted(words):
        rows: list[list[tuple[float, float, float, float, str]]] = []
        for w in sorted(words[page], key=lambda w: (w[0] + w[1] / 2, w[2])):
            mid = w[0] + w[1] / 2
            if rows:
                last = rows[-1]
                ref = sum(x[0] + x[1] / 2 for x in last) / len(last)
                if abs(mid - ref) <= max(w[1], last[0][1]) * 0.45:
                    last.append(w)
                    continue
            rows.append([w])
        for row in rows:
            row.sort(key=lambda w: w[2])
            height = max(w[1] for w in row)
            toks = [Tok(w[4], w[2], w[3]) for w in row]
            lines.append(Line(page, min(w[0] for w in row), height, _merge(toks, height * 1.2)))
    return lines


def lines_from_layout(text: str) -> list[Line]:
    """`pdftotext -layout` の等幅テキストを行にする（テスト用。1文字 = 幅 1、行の高さ 1）。"""
    lines: list[Line] = []
    for page, chunk in enumerate(text.split("\f"), 1):
        for i, raw in enumerate(chunk.split("\n")):
            toks = [Tok(m.group(0), float(m.start()), float(m.end())) for m in re.finditer(r"\S+", raw)]
            if toks:
                lines.append(Line(page, float(i), 1.0, _merge(toks, 1.0)))
    return lines


def _gap(a: Line, b: Line) -> float:
    """a と b の間が行の高さ何行分空いているか。ページが変われば表は終わり。"""
    if a.page != b.page:
        return GRID_GAP
    return (b.top - a.top) / max(a.height, 1e-6) - 1


# ---------------------------------------------------------------- グリッド


@dataclass(frozen=True)
class Column:
    month: int | None  # None は 上期計 / 通期 / 累計 など
    x0: float
    x1: float
    label: str


@dataclass
class Series:
    """一つの指標。横組みなら行、縦組み（月が行）なら列。"""

    label: str
    key: tuple[str, int]  # (伏せた名前, 同じグリッド内で何回目か)
    cells: dict[int, float | None]  # 時間軸の位置 → 値（- は None）


@dataclass
class Grid:
    ordinal: int
    slots: list[int | None]  # 時間軸の位置ごとの月（None は小計列など）
    years: list[int | None]  # 見出しに年があればその年（25年4月 / 2025/7）
    skeleton: str
    series: list[Series] = field(default_factory=list)


def _header_columns(line: Line) -> list[Column] | None:
    cols: list[Column] = []
    months = 0
    for t in line.toks:
        month = _month_of(t.text)
        if month is not None:
            months += 1
            cols.append(Column(month, t.x0, t.x1, t.text))
        elif cols:
            cols.append(Column(None, t.x0, t.x1, t.text))
    if months < MIN_HEADER_MONTHS:
        return None
    return cols


def _align(columns: Sequence[tuple[float, float]], cells: Sequence[Tok]) -> dict[int, float | None] | None:
    """数値の語を列へ。左右の順を保ったまま、ずれの合計が一番小さくなるように置く。"""
    k, n = len(cells), len(columns)
    if k == 0 or k > n:
        return None
    values: list[float | None] = []
    for t in cells:
        ok, value = parse_number(t.text)
        if not ok:
            return None
        values.append(value)

    def cost(i: int, j: int) -> float:
        t = cells[i]
        c0, c1 = columns[j]
        return min(abs(t.x0 - c0), abs(t.x1 - c1), abs((t.x0 + t.x1) / 2 - (c0 + c1) / 2))

    inf = float("inf")
    # dp[i][j]: 先頭 i 個を先頭 j 列に置いたときの最小コスト
    dp = [[inf] * (n + 1) for _ in range(k + 1)]
    for j in range(n + 1):
        dp[0][j] = 0.0
    for i in range(1, k + 1):
        for j in range(i, n + 1):
            dp[i][j] = min(dp[i][j - 1], dp[i - 1][j - 1] + cost(i - 1, j - 1))
    out: dict[int, float | None] = {}
    i, j = k, n
    while i > 0:
        if j > i and dp[i][j] == dp[i][j - 1]:
            j -= 1
            continue
        out[j - 1] = values[i - 1]
        i -= 1
        j -= 1
    return out


def _split_label(line: Line) -> tuple[str, list[Tok]] | None:
    """先頭の文字列を行名、残りを数値セルとする。数値でない語が混じれば None。"""
    toks = line.toks
    first = None
    for i, t in enumerate(toks):
        ok, _v = parse_number(t.text)
        if ok and t.text not in _BLANK_TOKENS:
            first = i
            break
    if first is None:
        return None
    # 数値の直前の - は空セル（店舗数(店) - 130 …）
    while first > 0 and toks[first - 1].text in _BLANK_TOKENS:
        first -= 1
    cells = list(toks[first:])
    if not all(parse_number(t.text)[0] for t in cells):
        return None
    return "".join(t.text for t in toks[:first]), cells


def _keyed(labels: Iterable[str]) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    out = []
    for label in labels:
        k = mask(label)
        n = counts.get(k, 0)
        counts[k] = n + 1
        out.append((k, n))
    return out


def _widen(header: list[Column], band: Sequence[Line]) -> list[Column]:
    """月見出しの上下の行にだけある列（第1四半期 / 上期累計 など）を足す。"""
    cols = list(header)
    for line in band:
        phrases: list[list[Tok]] = []
        for t in line.toks:
            if phrases and t.x0 - phrases[-1][-1].x1 <= line.height * 0.6:
                phrases[-1].append(t)
            else:
                phrases.append([t])
        for ph in phrases:
            x0, x1 = ph[0].x0, ph[-1].x1
            if x0 < cols[0].x0 or any(x0 < c.x1 and c.x0 < x1 for c in cols):
                continue
            cols.append(Column(None, x0, x1, "".join(t.text for t in ph)))
    return sorted(cols, key=lambda c: c.x0)


def _row_grid(ordinal: int, header: list[Column], band: Sequence[Line], body: Sequence[Line]) -> Grid | None:
    splits = [sp for sp in map(_split_label, body) if sp is not None]
    header = _widen(header, band)
    columns = [(c.x0, c.x1) for c in header]
    labels: list[str] = []
    rows: list[dict[int, float | None]] = []
    for label, cells in splits:
        assigned = _align(columns, cells)
        if assigned is None or all(v is None for v in assigned.values()):
            continue
        labels.append(label)
        rows.append(assigned)
    if not labels:
        return None
    return Grid(
        ordinal=ordinal,
        slots=[c.month for c in header],
        years=[_year_of(c.label) for c in header],
        skeleton="R|" + "|".join(mask(c.label) for c in header),
        series=[Series(label, key, cells) for label, key, cells in zip(labels, _keyed(labels), rows)],
    )


def _col_grid(ordinal: int, run: Sequence[tuple[str, list[Tok]]]) -> Grid | None:
    """月が行ラベルの表（縦組み）。一番セルの多い行の位置を列とする。"""
    widest = max(run, key=lambda item: len(item[1]))[1]
    columns = [(t.x0, t.x1) for t in widest]
    slots: list[int | None] = []
    years: list[int | None] = []
    labels: list[str] = []
    per_slot: list[dict[int, float | None]] = []
    for label, cells in run:
        assigned = _align(columns, cells)
        if assigned is None:
            return None
        slots.append(_month_of(label))
        years.append(_year_of(label))
        labels.append(mask(label))
        per_slot.append(assigned)
    if sum(1 for m in slots if m is not None) < MIN_HEADER_MONTHS:
        return None
    series = []
    for ci in range(len(columns)):
        cells = {si: row[ci] for si, row in enumerate(per_slot) if ci in row}
        if any(v is not None for v in cells.values()):
            series.append(Series(f"col{ci}", (f"col{ci}", 0), cells))
    return Grid(
        ordinal=ordinal,
        slots=slots,
        years=years,
        skeleton=f"C{len(columns)}|" + "|".join(labels),
        series=series,
    )


def parse_grids(lines: Sequence[Line]) -> list[Grid]:
    grids: list[Grid] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        header = _header_columns(line)
        if header is not None:
            j = i + 1
            while j < n and _header_columns(lines[j]) is None and _gap(lines[j - 1], lines[j]) < GRID_GAP:
                j += 1
            band = [
                lines[k]
                for k in (i - 2, i - 1, i + 1, i + 2)
                if 0 <= k < n
                and lines[k].page == line.page
                and _split_label(lines[k]) is None
                and _header_columns(lines[k]) is None
            ]
            grid = _row_grid(len(grids), header, band, lines[i + 1 : j])
            if grid is not None:
                grids.append(grid)
            i = j
            continue
        split = _split_label(line)
        if split is not None and _month_of(split[0]) is not None:
            run: list[tuple[str, list[Tok]]] = [split]
            j = i + 1
            while j < n and _gap(lines[j - 1], lines[j]) < GRID_GAP:
                nxt = _split_label(lines[j])
                if nxt is None or not nxt[0]:
                    break
                run.append(nxt)
                j += 1
            grid = _col_grid(len(grids), run)
            if grid is not None:
                grids.append(grid)
                i = j
                continue
        i += 1
    return grids


def _anchor_year(last_month: int, as_of: date) -> int:
    # app.monthly_ingest._anchor_year と同じ（jobs は app を import しない）
    if date(as_of.year, last_month, 1) <= as_of:
        return as_of.year
    return as_of.year - 1


def column_years(months: Sequence[int], as_of: date) -> list[tuple[int, int]]:
    """列の月 → (year, month)。右端から左へ。サーバの calendar / partial と同じ割り当て。"""
    if not months:
        return []
    year = _anchor_year(months[-1], as_of)
    out = [(year, months[-1])]
    for pos in range(len(months) - 2, -1, -1):
        if months[pos] > months[pos + 1]:
            year -= 1
        out.append((year, months[pos]))
    return out[::-1]


def slot_years(grid: Grid, series: Series, as_of: date) -> dict[int, tuple[int, int]]:
    """位置 → (year, month)。見出しに年が無ければ、その行の値のある右端の月から左へ。
    同じ表の前年の行（店舗数（前期）など）は右端が違うので行ごとに決める。"""
    slots = [i for i, m in enumerate(grid.slots) if m is not None]
    last = max((pos for pos, si in enumerate(slots) if series.cells.get(si) is not None), default=-1)
    slots = slots[: last + 1]
    if not slots:
        return {}
    if all(grid.years[si] is not None for si in slots):
        return {si: (grid.years[si], grid.slots[si]) for si in slots}  # type: ignore[misc]
    months = [grid.slots[si] for si in slots]
    return dict(zip(slots, column_years(months, as_of)))  # type: ignore[arg-type]


# ---------------------------------------------------------------- DB から学ぶ


def close(a: float, b: float) -> bool:
    return abs(a - b) <= TOL + 1e-9 * max(abs(a), abs(b))


def ym_index(ym: tuple[int, int]) -> int:
    return ym[0] * 12 + ym[1]


def pdf_date_from_name(name: str) -> date | None:
    m = _PDF_NAME_RE.match(name or "")
    if not m:
        return None
    raw = m.group(1)
    try:
        return date(int(raw[0:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def expected_month(as_of: date) -> tuple[int, int]:
    """開示日の前の暦月（tools/tdnet_monthly_pending.py と同じ見立て）。"""
    if as_of.month == 1:
        return as_of.year - 1, 12
    return as_of.year, as_of.month - 1


# element → {(year, month): value}
DbValues = dict[str, dict[tuple[int, int], float]]


@dataclass(frozen=True)
class DbIssuer:
    values: DbValues
    units: dict[str, str]  # element → 保存 unit（最新の年のもの）

    def months(self) -> set[tuple[int, int]]:
        return {ym for cells in self.values.values() for ym in cells}


def load_db_issuer(conn: sqlite3.Connection, code5: str) -> DbIssuer:
    values: DbValues = {}
    for element, year, month, value in conn.execute(
        "SELECT element, year, month, value FROM monthly_values WHERE code = ?", (code5,)
    ):
        if value is None:
            continue
        values.setdefault(str(element), {})[(int(year), int(month))] = float(value)
    units: dict[str, str] = {}
    for element, unit in conn.execute(
        "SELECT element, unit FROM monthly_elements WHERE code = ? ORDER BY year", (code5,)
    ):
        units[str(element)] = str(unit or "number")
    return DbIssuer(values=values, units=units)


Place = tuple[int, tuple[str, int], float]  # (grid, key, shift)


@dataclass(frozen=True)
class RowRule:
    element: str
    unit: str  # 入力 unit（number / yoy_index / yoy_pct）
    grid: int
    key: tuple[str, int]
    shift: float  # PDF 値 + shift = DB 値（yoy_pct は 100）
    # 同じだけ当たった別の行。新しい PDF でも全部同じ値のときだけ書く
    alts: tuple[Place, ...] = ()


@dataclass(frozen=True)
class Template:
    source: str  # 学んだ PDF のファイル名
    as_of: date
    rules: tuple[RowRule, ...]
    fingerprint: tuple[tuple[str, tuple[tuple[str, int], ...] | None], ...]
    # 前回の PDF にあった (表の見出しの骨格, 行名)。値のある新しい行・表が増えたら書かない
    known: frozenset[tuple[str, str]] = frozenset()


class NoTemplate(ValueError):
    """前回の PDF から DB の行を再現できない。"""


class FormatChanged(ValueError):
    """前回と書式が違う、または読んだ値を信用できない。"""


def _used(rules: Iterable[RowRule]) -> dict[int, list[tuple[str, int]]]:
    out: dict[int, list[tuple[str, int]]] = {}
    for r in rules:
        for grid, key, _shift in ((r.grid, r.key, r.shift), *r.alts):
            out.setdefault(grid, []).append(key)
    return out


def fingerprint(
    grids: Sequence[Grid],
    used: Mapping[int, Iterable[tuple[str, int]]],
) -> tuple[tuple[str, tuple[tuple[str, int], ...] | None], ...]:
    """書式の指紋。使う表までの見出しの骨格（数値と日付は伏せる）と、使う表の行のうち
    使う行と同じ名前の行の並び。関係ない行（出店した店名・注記）の増減は見ない。"""
    if not used:
        return ()
    labels = {o: {k[0] for k in keys} for o, keys in used.items()}
    out = []
    for g in grids[: max(used) + 1]:
        keys = None
        if g.ordinal in labels:
            keys = tuple(sr.key for sr in g.series if sr.key[0] in labels[g.ordinal])
        out.append((g.skeleton, keys))
    return tuple(out)


def _score(
    cells: Mapping[tuple[int, int], float],
    db: Mapping[tuple[int, int], float],
    shift: float,
    anchor: tuple[int, int],
) -> tuple[int, int, bool]:
    hit = miss = 0
    anchored = False
    for ym, v in cells.items():
        d = db.get(ym)
        if d is None:
            continue
        if close(v + shift, d):
            hit += 1
            anchored = anchored or ym == anchor
        else:
            miss += 1
    return hit, miss, anchored


def _row_names(grids: Sequence[Grid]) -> frozenset[tuple[str, str]]:
    return frozenset((g.skeleton, s.key[0]) for g in grids for s in g.series)


def _new_rows(template: Template, grids: Sequence[Grid], as_of: date) -> list[str]:
    """前回無かった表・行で、開示月（または2か月以上）に値があるもの。新しい業態・セグメント。"""
    anchor = expected_month(as_of)
    out = []
    for g in grids:
        for s in g.series:
            if (g.skeleton, s.key[0]) in template.known:
                continue
            cells = _series_cells(g, s, as_of)
            if anchor in cells or len(cells) >= 2:
                out.append(f"表{g.ordinal + 1} {s.label or '(無名)'}")
    return out


def _series_cells(grid: Grid, series: Series, as_of: date) -> dict[tuple[int, int], float]:
    years = slot_years(grid, series, as_of)
    return {years[si]: v for si, v in series.cells.items() if v is not None and si in years}


def learn(grids: Sequence[Grid], as_of: date, db: DbIssuer, *, source: str = "") -> Template:
    """前回の PDF のどの行が DB のどの element か。開示月に値のある element を全部再現できたときだけ返す。"""
    anchor = expected_month(as_of)
    targets = sorted(e for e, cells in db.values.items() if anchor in cells)
    if not targets:
        raise NoTemplate(f"DB に {anchor[0]}-{anchor[1]:02d} の値が無い")
    cells_of = {(g.ordinal, s.key): _series_cells(g, s, as_of) for g in grids for s in g.series}
    rules: list[RowRule] = []
    missing: list[str] = []
    for element in targets:
        unit = db.units.get(element, "number")
        shifts = (0.0, 100.0) if unit == "yoy_index" else (0.0,)
        best: tuple[int, int] | None = None
        places: list[Place] = []
        for (grid, key), cells in cells_of.items():
            for shift in shifts:
                hit, miss, anchored = _score(cells, db.values[element], shift, anchor)
                if not anchored or miss * 5 > hit:
                    continue
                rank = (hit, -miss)
                if best is None or rank > best:
                    best, places = rank, [(grid, key, shift)]
                elif rank == best:
                    places.append((grid, key, shift))
        if not places:
            missing.append(element)
            continue
        grid, key, shift = places[0]
        rules.append(RowRule(
            element=element,
            unit="yoy_pct" if shift else unit,
            grid=grid,
            key=key,
            shift=shift,
            alts=tuple(places[1:]),
        ))
    if missing:
        raise NoTemplate("再現できない行: " + ", ".join(missing))
    used = _used(rules)
    return Template(
        source=source,
        as_of=as_of,
        rules=tuple(rules),
        fingerprint=fingerprint(grids, used),
        known=_row_names(grids),
    )


@dataclass(frozen=True)
class Extracted:
    """新しい PDF から読んだ値。cells は (year, month) → PDF の値（shift 前）。"""

    rule: RowRule
    cells: dict[tuple[int, int], float]


def apply(template: Template, grids: Sequence[Grid], as_of: date) -> list[Extracted]:
    got = fingerprint(grids, _used(template.rules))
    if got != template.fingerprint:
        raise FormatChanged(_describe_change(template.fingerprint, got))
    added = _new_rows(template, grids, as_of)
    if added:
        # 黙って入れると、その行だけ空のまま未投入レポートでも気づかれない
        raise FormatChanged(f"新しい行 {added}")
    series = {(g.ordinal, s.key): (g, s) for g in grids for s in g.series}

    def read(grid: int, key: tuple[str, int]) -> dict[tuple[int, int], float]:
        g, s = series[(grid, key)]
        return _series_cells(g, s, as_of)

    out: list[Extracted] = []
    for rule in template.rules:
        cells = read(rule.grid, rule.key)
        for grid, key, shift in rule.alts:
            other = read(grid, key)
            if other.keys() != cells.keys() or any(
                not close(cells[ym] + rule.shift, other[ym] + shift) for ym in cells
            ):
                raise FormatChanged(f"{rule.element}: 前回同じ値だった行が今回は違う")
        out.append(Extracted(rule=rule, cells=cells))
    return out


def _describe_change(
    old: Sequence[tuple[str, tuple[tuple[str, int], ...] | None]],
    new: Sequence[tuple[str, tuple[tuple[str, int], ...] | None]],
) -> str:
    if len(old) != len(new):
        return f"表の数 {len(old)} → {len(new)}"
    for i, (a, b) in enumerate(zip(old, new), 1):
        if a[0] != b[0]:
            return f"表{i} の見出し {a[0]} → {b[0]}"
        if a[1] != b[1]:
            gone = sorted({k for k, _n in a[1] or ()} - {k for k, _n in b[1] or ()})
            added = sorted({k for k, _n in b[1] or ()} - {k for k, _n in a[1] or ()})
            if gone or added:
                return f"表{i} の行 -{gone} +{added}"
            return f"表{i} の行の並び"
    return "書式が違う"


def _implausible(value: float, history: Sequence[float]) -> bool:
    """件数・金額が直近の中央値から桁で外れる（累計列を読んだなど）。"""
    base = sorted(abs(v) for v in history)
    if not base:
        return False
    median = base[len(base) // 2]
    return abs(value - median) > 10 and (abs(value) > median * 5 or abs(value) * 5 < median)


def verify(extracted: Sequence[Extracted], db: DbIssuer, anchor: tuple[int, int]) -> None:
    """DB に既にある月が DB と合うか（速報の訂正は 5 か月に 1 つまで許す）。開示月の値が無い・
    桁が外れる値は書かない。"""
    if not any(anchor in x.cells for x in extracted):
        raise FormatChanged(f"{anchor[0]}-{anchor[1]:02d} の値が無い")
    for x in extracted:
        dv = db.values.get(x.rule.element, {})
        recent = [v for ym, v in dv.items() if 0 < ym_index(anchor) - ym_index(ym) <= 12]
        for ym, v in x.cells.items():
            if ym not in dv and _implausible(v + x.rule.shift, recent):
                raise FormatChanged(f"{x.rule.element}: {ym[0]}-{ym[1]:02d} の {_num(v)} が直近と桁違い")
        hit = miss = 0
        for ym, v in x.cells.items():
            d = dv.get(ym)
            if d is None:
                continue
            if close(v + x.rule.shift, d):
                hit += 1
            else:
                miss += 1
        if miss * 5 > hit:
            raise FormatChanged(f"{x.rule.element}: DB と {miss} か月食い違う（一致 {hit}）")


def _num(v: float) -> int | float:
    return int(v) if float(v).is_integer() else v


def build_payload(
    code: str,
    as_of: date,
    extracted: Sequence[Extracted],
    *,
    source: str,
    learned_from: str,
) -> dict[str, Any]:
    """`POST /api/v1/tables/{code}/ingest` の形。year は送らない。`columns` は各列の暦年月
    （ingest 側で dry_run の mapping と突き合わせる）。"""
    cols = sorted({ym for x in extracted for ym in x.cells}, key=ym_index)
    if not cols:
        raise FormatChanged("値が無い")
    for a, b in zip(cols, cols[1:]):
        if ym_index(b) - ym_index(a) >= 12:
            raise FormatChanged(f"列が1年以上飛ぶ {a} → {b}")
    months = [m for _y, m in cols]
    if column_years(months, as_of) != cols:
        raise FormatChanged("列の年がサーバの割り当てと合わない")
    index = {ym: i for i, ym in enumerate(cols)}
    dup = len(set(months)) != len(months)
    rows = []
    for x in extracted:
        # null はセルを消すので送らない。月が重複するときは列番号キー
        items = sorted(x.cells.items(), key=lambda kv: index[kv[0]])
        if dup:
            values = {str(index[ym]): _num(v) for ym, v in items}
        else:
            values = {str(ym[1]): _num(v) for ym, v in items}
        rows.append({"element": x.rule.element, "unit": x.rule.unit, "values": values})
    return {
        "code": code,
        "as_of": as_of.isoformat(),
        "months": months,
        "rows": rows,
        "columns": [f"{y:04d}-{m:02d}" for y, m in cols],
        "source": source,
        "learned_from": learned_from,
    }


# ---------------------------------------------------------------- R2 と DB


def pdftotext_tsv(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe is None:
        raise OSError("pdftotext not found (apt install poppler-utils)")
    proc = subprocess.run(  # nosec B603 - 固定の実行ファイルとローカルのパス
        [exe, "-tsv", str(path), "-"],
        capture_output=True,
        timeout=PDFTOTEXT_TIMEOUT,
        check=False,
    )
    if proc.returncode != 0:
        raise OSError(f"pdftotext exit={proc.returncode}: {proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout.decode("utf-8", "replace")


def dated_pdfs(code_dir: Path) -> list[tuple[date, Path]]:
    out = []
    for p in code_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".pdf":
            continue
        d = pdf_date_from_name(p.name)
        if d is not None:
            out.append((d, p))
    return sorted(out, key=lambda item: (item[0], item[1].name))


def sidecar_path(pdf: Path) -> Path:
    return pdf.with_name(pdf.stem + SIDECAR_SUFFIX)


@dataclass(frozen=True)
class Plan:
    code: str
    status: str  # ready / current / manual / skip
    detail: str
    pdf: Path | None = None
    payload: dict[str, Any] | None = None


def plan_code(
    code: str,
    code_dir: Path,
    db: DbIssuer,
    *,
    read_lines: Callable[[Path], list[Line]],
) -> Plan:
    pdfs = dated_pdfs(code_dir)
    if not pdfs:
        return Plan(code, "skip", "PDF が無い")
    latest_date, latest = pdfs[-1]
    anchor = expected_month(latest_date)
    have = db.months()
    if not have:
        return Plan(code, "skip", "DB に値が無い（初回は人が入れる）", latest)
    if max(ym_index(ym) for ym in have) >= ym_index(anchor):
        return Plan(code, "current", f"{anchor[0]}-{anchor[1]:02d} 投入済み", latest)
    new_grids = parse_grids(read_lines(latest))
    reasons: list[str] = []
    tried = 0
    for d, pdf in reversed(pdfs[:-1]):
        source_month = expected_month(d)
        if source_month == anchor or source_month not in have:
            continue
        tried += 1
        try:
            template = learn(parse_grids(read_lines(pdf)), d, db, source=pdf.name)
            extracted = apply(template, new_grids, latest_date)
            verify(extracted, db, anchor)
            payload = build_payload(code, latest_date, extracted, source=latest.name, learned_from=pdf.name)
        except (NoTemplate, FormatChanged) as e:
            reasons.append(f"{d.isoformat()}: {e}")
        else:
            cols = payload["columns"]
            detail = f"rows={len(payload['rows'])} cols={cols[0]}..{cols[-1]} learned={pdf.name}"
            return Plan(code, "ready", detail, latest, payload)
        if tried >= MAX_SOURCES:
            break
    if not tried:
        return Plan(code, "manual", "DB に入った前回の PDF が無い", latest)
    return Plan(code, "manual", " / ".join(reasons), latest)


def resolve_db_path(db_arg: str | None) -> Path:
    root = SCRIPT_DIR.parent
    raw = (db_arg or "").strip() or resolve_database_url(repo_root=root)
    if raw.startswith("sqlite:"):
        parsed = sqlite_path_from_url(raw, repo_root=root)
        if parsed is None:
            raise ValueError(f"not a sqlite file URL: {raw}")
        return parsed
    if "://" in raw:
        raise ValueError(f"DATABASE_URL is not sqlite: {raw}")
    return Path(raw)


def _code_dirs(dest: Path, only: frozenset[str] | None) -> list[tuple[str, Path]]:
    out = []
    for p in sorted(dest.iterdir()):
        if not p.is_dir() or not _CODE_DIR_RE.fullmatch(p.name):
            continue
        code = display_code(p.name)
        if only is not None and code not in only:
            continue
        out.append((code, p))
    return out


def run(
    dest: Path,
    db_path: Path,
    *,
    only: Sequence[str] | None = None,
    dry_run: bool = False,
    read_lines: Callable[[Path], list[Line]] | None = None,
) -> int:
    if not dest.is_dir():
        print(f"tdnet_monthly_pdf_sidecar dest missing {dest}", flush=True)
        return 2
    if not db_path.is_file():
        print(f"tdnet_monthly_pdf_sidecar db missing {db_path}", flush=True)
        return 2
    if read_lines is None:
        if shutil.which("pdftotext") is None:
            print("tdnet_monthly_pdf_sidecar pdftotext not found (apt install poppler-utils)", flush=True)
            return 2

        def read_lines(path: Path) -> list[Line]:
            return lines_from_tsv(pdftotext_tsv(path))

    want = frozenset(display_code(c.strip().upper()) for c in only if c.strip()) if only else None
    conn = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True, timeout=8)
    counts = {"written": 0, "same": 0, "current": 0, "manual": 0, "skip": 0, "failed": 0}
    try:
        for code, code_dir in _code_dirs(dest, want):
            try:
                plan = plan_code(code, code_dir, load_db_issuer(conn, jq_lookup_code(code)), read_lines=read_lines)
            except (OSError, subprocess.SubprocessError, sqlite3.Error) as e:
                counts["failed"] += 1
                print(f"fail {code}: {e}", flush=True)
                continue
            name = plan.pdf.name if plan.pdf else "-"
            if plan.status != "ready" or plan.payload is None or plan.pdf is None:
                counts[plan.status] += 1
                if plan.status == "manual" or want is not None:
                    print(f"{plan.status} {code} {name}: {plan.detail}", flush=True)
                continue
            path = sidecar_path(plan.pdf)
            data = (json.dumps(plan.payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            if path.is_file() and path.read_bytes() == data:
                counts["same"] += 1
                continue
            if dry_run:
                counts["written"] += 1
                print(f"plan {code} {path.name} {plan.detail}", flush=True)
                continue
            atomic_write(path, data)
            counts["written"] += 1
            print(f"ok {code} {path.name} {plan.detail}", flush=True)
    finally:
        conn.close()
    summary = " ".join(f"{k}={v}" for k, v in counts.items())
    print(f"tdnet_monthly_pdf_sidecar done dry_run={int(dry_run)} {summary}", flush=True)
    return 1 if counts["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Write .pdf-ingest.json for TDnet monthly PDFs in last month's format")
    ap.add_argument("--dest", default=DEFAULT_DEST, help=f"R2 monthly directory (default {DEFAULT_DEST})")
    ap.add_argument("--db", default=None, help="sqlite file (default DATABASE_URL / repo stock.db)")
    ap.add_argument("--only", action="append", default=[], help="issuer code (4 or 5 char; repeatable)")
    ap.add_argument("--dry-run", action="store_true", help="print plans, do not write sidecars")
    args = ap.parse_args(argv)
    try:
        db_path = resolve_db_path(args.db)
    except ValueError as e:
        print(f"tdnet_monthly_pdf_sidecar {e}", flush=True)
        return 2
    return run(Path(args.dest), db_path, only=args.only or None, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
