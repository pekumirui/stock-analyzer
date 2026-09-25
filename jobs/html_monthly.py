"""HTML 月次表のセル文法。表、月、数値、単位、期見出し、as_of。"""
from __future__ import annotations

import calendar
import json
import re
import unicodedata
from datetime import date
from html.parser import HTMLParser


_MONTH_RE = re.compile(
    r"(?:(?P<y>\d{4})\s*[/-年]\s*)?(?P<m>1[0-2]|0?[1-9])\s*月"
    r"|(?P<y2>\d{4})\s*[./-]\s*(?P<m2>1[0-2]|0?[1-9])(?!\d)(?!\s*[./-]\s*\d)"
)
# CanDo `26.03`. Fullmatch + zero-padded MM so `97.6` (yoy) and `2026.03.01` are not months.
_YY_MM_CELL_RE = re.compile(r"^(?P<y>\d{2})\s*[./-]\s*(?P<m>0[1-9]|1[0-2])$")
_FY_CAPTION_RE = re.compile(r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月期")
_MIN_AS_OF_YEAR = 2015
# `2026年度` と `2026年（令和8年）度`（大黒天物産の年度帯）
_FY_YEAR_RE = re.compile(r"(\d{4})\s*年(?:\s*[（(][^）)]*[）)])?\s*度")
_FY_RANGE_RE = re.compile(
    r"(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})\s*[～〜~\-－]\s*"
    r"(\d{4})\s*[./-]\s*(\d{1,2})\s*[./-]\s*(\d{1,2})"
)
_FY_MONTH_RANGE_RE = re.compile(
    r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月\s*[～〜~\-－]\s*"
    r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月(?!期)"
)
_YEAR_TIT_RE = re.compile(r"(20\d{2})\s*年\s*[（(]\s*令和")
_TABLE_START_RE = re.compile(r"<table\b", re.I)
_SECTION_LOOKBACK = 900
# 注記の「YYYY年M月度より」は開示月ではない
_YM_DO_RE = re.compile(r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月度(?!より|から|以降)")
_YM_HEAD_RE = re.compile(
    r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月(?!\s*期)(?!\s*度)(?!\s*\d)"
)
# <p> は「YYYY年M月」が月次見出しに直結したものだけ（子会社化月などは除外）
_YM_P_TITLE_RE = re.compile(
    r"(?:(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月(?:度)?(?:の)?\s*月次"
    r"|月次[^\s]{0,8}[（(](\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月[)）])"
)
# カレンダー日付・季節予定の「2027年」は期末に使わない（改装表 lookback 対策）。
_CAL_DATE_YEAR_RE = re.compile(r"20\d{2}\s*年\s*(?:1[0-2]|0?[1-9])\s*月")
_SEASON_YEAR_RE = re.compile(r"20\d{2}\s*年\s*[春夏秋冬]")
_YEAR_ONLY_HEAD_RE = re.compile(r"(20\d{2})\s*年(?!\s*\d)")
_YEAR_TAB_RE = re.compile(
    r'class=["\'][^"\']*\b(?:here|current|active|selected|is-active|is-current)\b'
    r'[^"\']*["\'][^>]*>[\s\S]{0,160}?(20\d{2})',
    re.I,
)
_SKIP_AXIS_RE = re.compile(
    r"^(?:累計|通期計|通期|通期累計|年度累計|通年|合計|計|年月|"
    r"上期合計|下期合計|通期合計|上期計|下期計|上期累計|下期累計|上半期|下半期|上期|下期|半期|"
    r"[1-4]Q|[１-４]Q|[1-4]四半期|四半期|\d+\s*ヶ月|月計)$"
)
_SKIP_SPAN_RE = re.compile(r"\d+\s*月?\s*[～〜~\-－]\s*\d+\s*月")
_NAMED_METRIC_RE = re.compile(
    r"来客数|客単価|客数|店舗数|店数|拠点数|営業所数|開店|閉店|出店|退店|"
    r"千食|お届け|開設|転換|撤退|テナント数|稼働店舗数|取引店舗数|"
    r"増加\(店\)|減少\(店\)"
)
_COUNT_METRIC_MARKS = (
    "店舗数",
    "店数",
    "開店",
    "閉店",
    "出店",
    "退店",
    "千食",
    "お届け",
    "営業所",
    "拠点",
    "開設",
    "転換",
    "撤退",
    "テナント数",
    "稼働店舗数",
    "取引店舗数",
)
_STORE_FLOW_RE = re.compile(r"開店|閉店|出店|退店|開設|撤退")
_COUNT_HEAD_RE = re.compile(
    r"店舗数|拠点数|営業所数|千食|お届け|テナント数|稼働店舗数|取引店舗数"
)
# 舖 (U+8216) vs 舗 (U+8217): NFKC does not unify; some IR HTML uses 舖.
_COUNT_DELTA_RE = re.compile(r"(?:増加|減少)\(店\)")


def _normalize_count_text(header: str) -> str:
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", header or ""))
    return compact.replace("舖", "舗")


_UPDATE_RE = re.compile(
    r"(\d{4})\s*[/-年]\s*(\d{1,2})\s*[/-月]\s*(\d{1,2})\s*日?\s*"
    r"(?:更新|公表|発表|開示|掲載|確定|現在|時点)"
)
_UNIT_TAIL_RE = re.compile(r"(?:百万円|千円|億円|百万|円|店|度)+$")
_EMPTY_VAL_RE = re.compile(r"^(?:[-−－―–—ー…‥.．]|n/?a|なし)?$", re.I)
_PCT_MARK_RE = re.compile(r"[%％]")
_UNIT_MARK_RE = re.compile(r"[(（]?\s*単位\s*[:：]?\s*[%％]\s*[)）]?")
_VALID_UNITS = frozenset({"number", "yoy_index", "yoy_pct"})
_TRI_RE = re.compile(r"[△▲]")
_CHARSET_RE = re.compile(br"charset\s*=\s*[\"']?\s*([a-zA-Z0-9_-]+)", re.I)
_HEADING_RE = re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>", re.I | re.S)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.I | re.S)
_CAPTION_RE = re.compile(r"<caption[^>]*>(.*?)</caption>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_CHARSET_ALIAS = {
    "utf8": "utf-8",
    "shift_jis": "cp932",
    "shift-jis": "cp932",
    "sjis": "cp932",
    "x-sjis": "cp932",
    "windows-31j": "cp932",
    "ms932": "cp932",
}


def decode_html_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    enc = None
    m = _CHARSET_RE.search(data[:4096])
    if m:
        enc = _CHARSET_ALIAS.get(m.group(1).decode("ascii", "ignore").lower(), m.group(1).decode("ascii", "ignore").lower())
    candidates = [enc, "utf-8", "cp932"] if enc else ["utf-8", "cp932"]
    seen: set[str] = set()
    for name in candidates:
        if not name or name in seen:
            continue
        seen.add(name)
        try:
            return data.decode(name)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def _plain(html_frag: str) -> str:
    t = unicodedata.normalize("NFKC", html_frag or "")
    t = _TAG_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def page_context(html: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for rx in (_HEADING_RE, _CAPTION_RE):
        for m in rx.finditer(html or ""):
            t = _plain(m.group(1))
            if t and t not in seen:
                seen.add(t)
                parts.append(t)
    for m in _P_RE.finditer(html or ""):
        t = _plain(m.group(1))
        if not t or t in seen:
            continue
        if any(k in t for k in ("月期", "年度", "月度", "前年", "対前年")):
            seen.add(t)
            parts.append(t)
        if len(parts) >= 24:
            break
    return " ".join(parts)


def unit_mark_context(html: str) -> str:
    plain = _plain(html)
    seen: set[str] = set()
    parts: list[str] = []
    for m in _UNIT_MARK_RE.finditer(plain):
        t = m.group(0).strip()
        if t and t not in seen:
            seen.add(t)
            parts.append(t)
    return " ".join(parts)


def parse_as_of_text(html: str) -> str | None:
    s = unicodedata.normalize("NFKC", html or "")
    m = _UPDATE_RE.search(s)
    if m:
        y = int(m.group(1))
        if y >= _MIN_AS_OF_YEAR:
            try:
                return date(y, int(m.group(2)), int(m.group(3))).isoformat()
            except ValueError:
                pass
    found: list[tuple[int, int]] = []
    for y, mo in _YM_DO_RE.findall(s):
        yi = int(y)
        if yi >= _MIN_AS_OF_YEAR:
            found.append((yi, int(mo)))
    if found:
        y, mo = max(found)
        try:
            return month_end_iso(y, mo)
        except ValueError:
            return None
    heads = " ".join(_plain(m.group(1)) for m in _HEADING_RE.finditer(html or ""))
    headed = [
        (int(y), int(mo))
        for y, mo in _YM_HEAD_RE.findall(unicodedata.normalize("NFKC", heads))
        if int(y) >= _MIN_AS_OF_YEAR
    ]
    if headed:
        y, mo = max(headed)
        try:
            return month_end_iso(y, mo)
        except ValueError:
            return None
    # Vega: `2026年8月の月次業績` is a <p>, not h1-h4
    for m in _P_RE.finditer(html or ""):
        t = _plain(m.group(1))
        if "月次" not in t:
            continue
        for mm in _YM_P_TITLE_RE.finditer(t):
            y_raw = mm.group(1) or mm.group(3)
            mo_raw = mm.group(2) or mm.group(4)
            yi = int(y_raw)
            if yi >= _MIN_AS_OF_YEAR:
                headed.append((yi, int(mo_raw)))
    if headed:
        y, mo = max(headed)
        try:
            return month_end_iso(y, mo)
        except ValueError:
            return None
    return None


def heading_year_month(html: str) -> tuple[int, int] | None:
    heads = " ".join(_plain(m.group(1)) for m in _HEADING_RE.finditer(html or ""))
    found = [
        (int(y), int(mo))
        for y, mo in _YM_HEAD_RE.findall(unicodedata.normalize("NFKC", heads))
        if int(y) >= _MIN_AS_OF_YEAR
    ]
    return max(found) if found else None


def page_year(html: str) -> int | None:
    m = _YEAR_TAB_RE.search(html or "")
    if m:
        y = int(m.group(1))
        if y >= _MIN_AS_OF_YEAR:
            return y
    ctx = page_context(html)
    years = [
        int(y)
        for y in _YEAR_ONLY_HEAD_RE.findall(unicodedata.normalize("NFKC", ctx))
        if int(y) >= _MIN_AS_OF_YEAR
    ]
    return max(years) if years else None


def parse_month_year(text: str) -> tuple[int | None, int | None]:
    """セルから (year or None, month or None)。月期キャプションは月軸にしない。"""
    s = unicodedata.normalize("NFKC", text or "").strip()
    s_bare = re.sub(r"^※\s*", "", s)
    if (
        not s
        or _SKIP_AXIS_RE.fullmatch(s)
        or _FY_CAPTION_RE.search(s)
        or _FY_YEAR_RE.search(s_bare)
    ):
        return None, None
    # `2026年度 3月期` は `_FY_CAPTION_RE` に当たらず、`3月` だけ月軸になる
    if "月期" in s:
        return None, None
    yy = _YY_MM_CELL_RE.fullmatch(s)
    if yy:
        year = 2000 + int(yy.group("y"))
        month = int(yy.group("m"))
        if month < 1 or month > 12 or year < _MIN_AS_OF_YEAR or year > 2040:
            return None, None
        return year, month
    m = _MONTH_RE.search(s)
    if not m:
        return None, None
    n = m.group("m") or m.group("m2")
    month = int(n)
    if month < 1 or month > 12:
        return None, None
    y_raw = m.group("y") or m.group("y2")
    year = int(y_raw) if y_raw else None
    if year is not None and year < 1990:
        year = None
    return year, month


def parse_month_cell(text: str) -> int | None:
    _year, month = parse_month_year(text)
    return month


def month_end_iso(year: int, month: int) -> str:
    return date(year, month, calendar.monthrange(year, month)[1]).isoformat()


def fy_end_from_text(text: str) -> tuple[int, int] | None:
    """`2027年3月期` の最新（年, 期末月）。無ければ期間末、さらに `YYYY年度` は4月始まり（翌年3月期末）。"""
    s = unicodedata.normalize("NFKC", text or "")
    found = [
        (int(y), int(m))
        for y, m in _FY_CAPTION_RE.findall(s)
        if int(y) >= _MIN_AS_OF_YEAR
    ]
    if found:
        return max(found)
    ranges = _FY_RANGE_RE.findall(s)
    if ranges:
        y2, m2 = int(ranges[-1][3]), int(ranges[-1][4])
        if 1 <= m2 <= 12:
            return y2, m2
    month_ranges = _FY_MONTH_RANGE_RE.findall(s)
    if month_ranges:
        y2, m2 = int(month_ranges[-1][2]), int(month_ranges[-1][3])
        if 1 <= m2 <= 12:
            return y2, m2
    years = [int(y) for y in _FY_YEAR_RE.findall(s) if int(y) >= _MIN_AS_OF_YEAR]
    if years:
        y = max(years)
        return y + 1, 3
    return None


def _named_year_fy_end(year: int, months: list[int] | None) -> tuple[int, int]:
    """`2026年(令和…)` のような年ラベル。3月始まりの月軸なら期末は翌年2月。"""
    if months and (any(m >= 3 for m in months) or (1 in months or 2 in months)):
        if any(m >= 3 for m in months):
            return year + 1, 2
    return year, 12


def section_fy_end_from_text(
    text: str,
    months: list[int] | None = None,
) -> tuple[int, int] | None:
    """表直前の期見出しから期末を取る。ページ全体の古い年は見ない。"""
    found = fy_end_from_text(text)
    if found:
        return found
    s = unicodedata.normalize("NFKC", text or "")
    m = _YEAR_TIT_RE.search(s)
    if m and int(m.group(1)) >= _MIN_AS_OF_YEAR:
        return _named_year_fy_end(int(m.group(1)), months)
    s_for_year = _CAL_DATE_YEAR_RE.sub(" ", s)
    s_for_year = _SEASON_YEAR_RE.sub(" ", s_for_year)
    years = [
        int(y)
        for y in _YEAR_ONLY_HEAD_RE.findall(s_for_year)
        if int(y) >= _MIN_AS_OF_YEAR
    ]
    if years:
        return _named_year_fy_end(years[-1], months)
    return None


def _is_count_header(header: str) -> bool:
    h = _normalize_count_text(header)
    return (
        _COUNT_HEAD_RE.search(h) is not None
        or _STORE_FLOW_RE.search(h) is not None
        or _COUNT_DELTA_RE.search(h) is not None
    )


_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)


def _is_prior_year_absolute(text: str) -> bool:
    """前年/前期の実数列。前年比は除外。"""
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))
    if not compact or "比" in compact:
        return False
    return compact in ("前年", "前期", "前年同月", "前年同期")


def _rewrite_period_header(text: str) -> str:
    """当年/当期/前年比 → 売上高。unit 判定は書き換え前の見出しで行う。"""
    s = (text or "").strip()
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))
    if compact in ("当年", "当期", "前年比"):
        return "売上高"
    return s


def _fy_captions(text: str) -> list[tuple[int, int]]:
    s = unicodedata.normalize("NFKC", text or "")
    return [
        (int(y), int(m))
        for y, m in _FY_CAPTION_RE.findall(s)
        if int(y) >= _MIN_AS_OF_YEAR
    ]


def table_section_texts(html: str) -> list[str]:
    """各 table 開始直前のテキスト（新しい期見出しが表の直上にある前提）。"""
    raw = _HTML_COMMENT_RE.sub(" ", html or "")
    out: list[str] = []
    for m in _TABLE_START_RE.finditer(raw):
        start = max(0, m.start() - _SECTION_LOOKBACK)
        out.append(_plain(raw[start:m.start()]))
    return out


def pick_fy_end(
    grid: list[list[str]],
    html: str,
    months: list[int] | None = None,
    section_fy: tuple[int, int] | None = None,
) -> tuple[int, int] | None:
    if section_fy is not None:
        return section_fy
    blob = " ".join(c for row in grid for c in row)
    found = fy_end_from_text(blob)
    if found:
        return found
    years = [
        int(y)
        for y in _YEAR_ONLY_HEAD_RE.findall(unicodedata.normalize("NFKC", blob))
        if int(y) >= _MIN_AS_OF_YEAR
    ]
    if years:
        return _named_year_fy_end(max(years), months)
    found = fy_end_from_text(page_context(html)) or fy_end_from_text(html)
    if found:
        return found
    y = page_year(html)
    if y is None:
        return None
    if months:
        return y, months[-1]
    return y + 1, 3


def last_filled_month(rows: list[dict], months: list[int] | None = None) -> int | None:
    filled: set[int] = set()
    for row in rows:
        values = row.get("values") or {}
        if not isinstance(values, dict):
            continue
        for k, v in values.items():
            if v is None:
                continue
            try:
                filled.add(int(k))
            except (TypeError, ValueError):
                continue
    if not filled:
        return None
    if months:
        for m in reversed(months):
            if m in filled:
                return m
    return max(filled)


def drop_trailing_empty_months(months: list[int], rows: list[dict]) -> list[int]:
    last = last_filled_month(rows, months)
    if last is None or not months:
        return months
    for i in range(len(months) - 1, -1, -1):
        if months[i] == last:
            return months[: i + 1]
    return months


def year_for_month(month: int, fy_end: tuple[int, int] | None, cell_year: int | None) -> int | None:
    if cell_year is not None:
        return cell_year
    if fy_end is None:
        return None
    fy_y, fy_m = fy_end
    return fy_y if month <= fy_m else fy_y - 1


def as_of_from_table(
    grid: list[list[str]],
    html: str,
    rows: list[dict],
    months: list[int] | None = None,
    section_fy: tuple[int, int] | None = None,
) -> str | None:
    """値がある最後の月の年月。セルに年が無ければ期見出し / `YYYY年M月期` / `YYYY年度` から補う。"""
    last_m = last_filled_month(rows, months)
    if last_m is None:
        return None
    cell_year: int | None = None
    for row in grid:
        for cell in row:
            year, month = parse_month_year(cell)
            if month == last_m and year is not None and year >= _MIN_AS_OF_YEAR:
                cell_year = year if cell_year is None else max(cell_year, year)
    year = year_for_month(last_m, pick_fy_end(grid, html, months, section_fy), cell_year)
    if year is None:
        head = heading_year_month(html)
        if head is not None:
            year = year_for_month(last_m, (head[0], head[1]), None)
    if year is None:
        return None
    return month_end_iso(year, last_m)


def resolve_as_of(
    html: str,
    grid: list[list[str]] | None,
    rows: list[dict],
    hint: str | None,
    months: list[int] | None = None,
    section_fy: tuple[int, int] | None = None,
) -> str:
    """開示日 → 表の最終値の年月 → 呼び出し側 hint（E-IR 日付）。取得日は使わない。"""
    explicit = parse_as_of_text(html)
    if explicit:
        return explicit
    derived = as_of_from_table(grid or [], html, rows, months, section_fy)
    if derived:
        return derived
    if hint:
        return hint
    raise ValueError("no as_of")


def parse_number(text: str) -> float | None:
    s = unicodedata.normalize("NFKC", text or "")
    s = s.replace(",", "").replace("，", "")
    s = re.sub(r"\s+", "", s)
    s = _PCT_MARK_RE.sub("", s)
    s = _TRI_RE.sub("-", s)
    s = s.replace("−", "-").replace("－", "-")
    s = _UNIT_TAIL_RE.sub("", s)
    s = s.strip()
    if not s or _EMPTY_VAL_RE.fullmatch(s):
        return None
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)", s):
        return None
    return float(s)


def json_number(v: float) -> int | float:
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError("non-finite")
    iv = int(v)
    if float(iv) == v:
        return iv
    return v


class _TableParser(HTMLParser):
    """rowspan/colspan を展開した矩形グリッド。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._raw: list[list[tuple[str, int, int]]] | None = None
        self._row: list[tuple[str, int, int]] | None = None
        self._cell: tuple[int, int] | None = None
        self._text: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip += 1
            return
        if self._skip:
            return
        ad = {k.lower(): (v or "") for k, v in attrs}
        if tag == "table":
            self._raw = []
            self._row = None
        elif tag == "tr" and self._raw is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            rs = _span(ad.get("rowspan"), 1)
            cs = _span(ad.get("colspan"), 1)
            self._cell = (rs, cs)
            self._text = []
        elif tag == "br" and self._cell is not None:
            self._text.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = re.sub(r"\s+", " ", "".join(self._text)).strip()
            self._row.append((text, self._cell[0], self._cell[1]))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._raw is not None:
            self._raw.append(self._row)
            self._row = None
        elif tag == "table" and self._raw is not None:
            self.tables.append(_expand_grid(self._raw))
            self._raw = None

    def handle_data(self, data: str) -> None:
        if self._skip or self._cell is None:
            return
        self._text.append(data)


def _span(raw: str | None, default: int) -> int:
    try:
        n = int(raw or default)
    except ValueError:
        return default
    return n if n > 0 else default


def _expand_grid(rows: list[list[tuple[str, int, int]]]) -> list[list[str]]:
    occupy: dict[tuple[int, int], str] = {}
    max_c = 0
    for r_i, row in enumerate(rows):
        c = 0
        for text, rs, cs in row:
            while (r_i, c) in occupy:
                c += 1
            for dr in range(rs):
                for dc in range(cs):
                    occupy[(r_i + dr, c + dc)] = text
            c += cs
            max_c = max(max_c, c)
    n_r = max((r for r, _c in occupy), default=-1) + 1
    grid: list[list[str]] = []
    for r in range(n_r):
        grid.append([occupy.get((r, c), "") for c in range(max_c)])
    return grid


def parse_tables(html: str) -> list[list[list[str]]]:
    p = _TableParser()
    p.feed(html or "")
    p.close()
    return p.tables


def _is_skip_axis(text: str) -> bool:
    s = unicodedata.normalize("NFKC", text or "").strip()
    s = re.sub(r"\s+", "", s)
    # 末尾の注記記号（※3）は軸名ではない
    s = re.sub(r"(?:※\d*)+$", "", s)
    if not s:
        return False
    if s.startswith("累計"):
        return True
    if "四半期" in s:
        return True
    if _SKIP_AXIS_RE.fullmatch(s) or _SKIP_SPAN_RE.search(s):
        return True
    return False


def _strip_fy_caption(text: str) -> str:
    """`2027年3月期 取扱高` → `取扱高`。キャプションだけのセルは空。"""
    s = unicodedata.normalize("NFKC", text or "").strip()
    s = re.sub(r"^※\s*", "", s)
    s = _FY_CAPTION_RE.sub(" ", s)
    s = _FY_YEAR_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _is_caption_cell(text: str) -> bool:
    s = unicodedata.normalize("NFKC", text or "").strip()
    s = re.sub(r"^※\s*", "", s)
    if not (_FY_CAPTION_RE.search(s) or _FY_YEAR_RE.search(s) or "月期" in s):
        return False
    return _strip_fy_caption(s) == ""


def _label_from_cell(text: str) -> str:
    """FY キャプションを剥がしたラベル。キャプション以外は原文のまま（NFKC しない）。"""
    raw = (text or "").strip()
    if not raw or _is_caption_cell(raw):
        return ""
    nfkc = unicodedata.normalize("NFKC", raw)
    nfkc_bare = re.sub(r"^※\s*", "", nfkc).strip()
    if _FY_CAPTION_RE.search(nfkc_bare) or _FY_YEAR_RE.search(nfkc_bare):
        return _strip_fy_caption(raw)
    return raw


def _cells_fy(cells: list[str]) -> tuple[int, int] | None:
    """ラベルセルだけの期末。値セルの年月は見ない。"""
    blob = " ".join(str(c) for c in cells)
    found = _fy_captions(blob)
    if found:
        return max(found)
    return fy_end_from_text(blob)


def _header_label(header_rows: list[list[str]], col: int) -> str:
    parts: list[str] = []
    for row in header_rows:
        t = _label_from_cell(row[col] if col < len(row) else "")
        if not t or t in parts:
            continue
        if parse_month_cell(t) is not None or _is_skip_axis(t):
            continue
        parts.append(t)
    return " ".join(parts)


def _is_fiscal_seq(months: list[int]) -> bool:
    if len(months) <= 1:
        return True
    return all(b == a % 12 + 1 for a, b in zip(months, months[1:]))


def _is_non_increasing(months: list[int]) -> bool:
    """新しい月が上に来る表（ハローズ / オークワ）。1→12 の年跨ぎだけ折り返し。"""
    if len(months) < 2:
        return False
    for a, b in zip(months, months[1:]):
        if b < a:
            continue
        if a == 1 and b == 12:
            continue
        return False
    return True


def normalize_month_order(months: list[int]) -> list[int]:
    if not months:
        return []
    if _is_fiscal_seq(months):
        return months
    rev = list(reversed(months))
    if _is_fiscal_seq(rev):
        return rev
    if _is_non_increasing(months):
        return rev
    return months


_CJK_CHAR = r"[\u3040-\u30FF\u4E00-\u9FFF]"
# `ト ラ イ ア ル` → `トライアル`. Lookahead stops at `店 売上高` so 売上高 is not eaten.
_CJK_LETTERSPACING_RE = re.compile(rf"(?:{_CJK_CHAR} ){{2,}}{_CJK_CHAR}(?=\s|$)")


def _squash_label_metric_spaces(label: str) -> str:
    s = label.replace("客 数", "客数").replace("売 上", "売上")
    s = re.sub(r"[［\[]([^］\]]*)[］\]]", r"\1 ", s)
    s = _CJK_LETTERSPACING_RE.sub(lambda m: m.group(0).replace(" ", ""), s)
    return re.sub(r"\s+", " ", s).strip()


def _compact_store_status_label(label: str) -> str:
    s = _squash_label_metric_spaces(label.strip())
    if s.startswith("店舗状況 "):
        s = s[len("店舗状況 ") :].strip()
    if s.startswith("拠点数 "):
        s = s[len("拠点数 ") :].strip()
    if re.search(r"（内\s*開店", s) or s.startswith("（内 出店"):
        return "出店"
    if re.search(r"（内\s*閉店", s) or s.startswith("（内 退店"):
        return "退店"
    if s == "店舗数":
        return "店舗数"
    return s


def _monthly_pair_key(element: str) -> str:
    s = element
    for frag in ("単月 Monthly", "単月Monthly", "単月", "Monthly"):
        s = s.replace(frag, " ")
    return re.sub(r"\s+", " ", s).strip()


def _drop_ytd_when_monthly_pair(rows: list[dict]) -> list[dict]:
    monthly_keys = {
        _monthly_pair_key(r["element"])
        for r in rows
        if "単月" in r["element"] or "Monthly" in r["element"]
    }
    if not monthly_keys:
        return rows
    out: list[dict] = []
    for r in rows:
        el = r["element"]
        if "単月" not in el and "Monthly" not in el and _monthly_pair_key(el) in monthly_keys:
            continue
        out.append(r)
    return out


def _is_count_metric(header: str) -> bool:
    """店舗数・出退店・千食/営業所など。ページ見出しの前年で yoy にしない。"""
    compact = _normalize_count_text(header)
    if any(mark in compact for mark in _COUNT_METRIC_MARKS):
        return True
    return _COUNT_DELTA_RE.search(compact) is not None


def infer_unit(header: str, samples: list[str], context: str = "") -> str:
    h = header or ""
    compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", h))
    # 当年/当期の実数は、他表の「前年」が context に混ざっても number に固定
    if compact in ("当年", "当期"):
        return "number"
    blob = f"{h} {context}"
    if _is_count_metric(h) or _is_count_header(h):
        return "number"
    if any(k in h for k in ("百万円", "千円", "億円")) and "前年" not in h:
        return "number"
    has_tri = any(_TRI_RE.search(v or "") for v in samples)
    has_pct = any(_PCT_MARK_RE.search(v or "") for v in samples) or bool(_PCT_MARK_RE.search(h))
    signed_pct = any(
        re.match(r"^[+＋]", unicodedata.normalize("NFKC", v or "").strip()) and _PCT_MARK_RE.search(v or "")
        for v in samples
    )
    yoy = any(k in blob for k in ("前年", "同月比", "100基準"))
    nums = [n for v in samples if (n := parse_number(v)) is not None]
    index_like = bool(nums) and all(50 <= n <= 200 for n in nums) and not _is_count_header(h)
    # 増減比/伸び率は変化率。単位:% より優先（ツルハ・サガミ）
    if any(k in blob for k in ("増減比", "増減率", "変化率", "伸び率", "伸長率")):
        return "yoy_pct"
    if "100基準" in blob:
        return "yoy_index"
    if has_tri or signed_pct:
        return "yoy_pct"
    # 100基準のセルは index。成長率%（マクドナルド 13.6%）は yoy_pct → ingest で +100
    if index_like and (yoy or has_pct or "売上" in blob or "同月" in blob):
        return "yoy_index"
    if yoy:
        if "前年" in compact or "同月比" in compact or has_pct:
            return "yoy_pct"
        return "number"
    return "number"


def _row_has_number(row: list[str], start: int = 0) -> bool:
    return any(parse_number(c) is not None for c in row[start:])


def _detect_col_header(grid: list[list[str]]) -> int | None:
    for i, row in enumerate(grid[:4]):
        n_month = 0
        n_skip = 0
        for c in row:
            if _is_skip_axis(c):
                n_skip += 1
                continue
            if parse_month_cell(c) is not None:
                n_month += 1
        if n_month >= 3 or (n_month >= 1 and n_skip >= 1):
            return i
    return None


def _first_data_row(grid: list[list[str]]) -> int | None:
    for i, row in enumerate(grid):
        if not row:
            continue
        if parse_month_cell(row[0]) is not None:
            return i
    return None


def parse_grid(grid: list[list[str]], context: str = "", html: str = "") -> dict | None:
    if not grid:
        return None
    col_hi = _detect_col_header(grid)
    if col_hi is not None:
        return _parse_col_months(grid, col_hi, context)
    data_i = _first_data_row(grid)
    if data_i is not None:
        return _parse_row_months(grid, data_i, context)
    snap = heading_year_month(html)
    if snap:
        return _parse_snapshot_month(grid, snap[0], snap[1], context)
    return None


def _axis_compact(text: str) -> str:
    s = unicodedata.normalize("NFKC", text or "").strip()
    return re.sub(r"\s+", "", s)


def _is_tangetsu_cum_row(row: list[str]) -> bool:
    cells = [_axis_compact(c) for c in row if str(c).strip()]
    if len(cells) < 2:
        return False
    return all(c == "単月" or c.startswith("単月") or c == "累計" or c.startswith("累計") for c in cells)


def _parse_snapshot_month(grid: list[list[str]], year: int, month: int, context: str) -> dict | None:
    """見出し `YYYY年M月` + 単月/累計列（住友林業の当月速報）。"""
    header_i = None
    for i, row in enumerate(grid[:4]):
        if _is_tangetsu_cum_row(row):
            header_i = i
            break
    if header_i is None:
        return None
    header = grid[header_i]
    cats = grid[header_i - 1] if header_i > 0 else [""] * len(header)
    month_cols: list[int] = []
    for c, cell in enumerate(header):
        ax = _axis_compact(cell)
        if ax == "単月" or ax.startswith("単月"):
            month_cols.append(c)
    if not month_cols:
        return None
    rows_out: list[dict] = []
    for row in grid[header_i + 1 :]:
        if not _row_has_number(row):
            continue
        for c in month_cols:
            cat = cats[c].strip() if c < len(cats) else ""
            if not cat or _is_skip_axis(cat):
                continue
            if c >= len(row):
                continue
            n = parse_number(row[c])
            if n is None:
                continue
            samples = [row[c]]
            unit = infer_unit(cat, samples, context)
            rows_out.append(
                {"element": cat, "unit": unit, "values": {str(month): json_number(n)}}
            )
        break
    if not rows_out:
        return None
    return {"months": [month], "rows": rows_out}


def _repeated_scope_row(row: list[str]) -> str | None:
    """全セルが同じ 既存店/全店 の帯。数値行のスコープにする。"""
    filled = [_plain(c).strip() for c in row if str(c).strip()]
    if len(filled) < 2 or len(set(filled)) != 1:
        return None
    if any(parse_number(c) is not None for c in filled):
        return None
    name = filled[0]
    if parse_month_cell(name) is not None or _is_skip_axis(name):
        return None
    return name


def _label_band_end(header: list[str], month_start: int) -> int:
    """同じ文言が横に続くスキップ軸は colspan の帯。1列だけの軸（累計）で切る。"""
    c = 0
    while c < month_start:
        cell = header[c] if c < len(header) else ""
        if not _is_skip_axis(cell):
            c += 1
            continue
        end = c + 1
        while end < month_start and end < len(header) and header[end] == cell:
            end += 1
        if end == c + 1:
            return c
        c = end
    return month_start


def _month_header_cols(row: list[str]) -> list[tuple[int, int]] | None:
    """月見出し行。上期/下期は無視し、ラベルや数値が混ざったら見出しではない。"""
    month_cols: list[tuple[int, int]] = []
    for c, cell in enumerate(row):
        text = str(cell).strip()
        if not text or _is_skip_axis(text):
            continue
        month = parse_month_cell(text)
        if month is None:
            return None
        month_cols.append((c, month))
    if len(month_cols) < 3:
        return None
    return month_cols


def _parse_col_months(grid: list[list[str]], header_i: int, context: str) -> dict | None:
    header = grid[header_i]
    month_cols = []
    for c, cell in enumerate(header):
        if _is_skip_axis(cell):
            continue
        m = parse_month_cell(cell)
        if m is not None:
            month_cols.append((c, m))
    if not month_cols:
        return None
    months: list[int] = []
    seen_months: set[int] = set()
    for _c, month in month_cols:
        if month not in seen_months:
            seen_months.add(month)
            months.append(month)
    month_start = min(c for c, _m in month_cols)
    label_end = _label_band_end(header, month_start)
    current_fy: tuple[int, int] | None = None
    for row in grid[header_i + 1 :]:
        if _month_header_cols(row) is not None or _repeated_scope_row(row) is not None:
            continue
        fy = _cells_fy([row[c] if c < len(row) else "" for c in range(label_end)])
        if fy is not None and (current_fy is None or fy > current_fy):
            current_fy = fy
    series: dict[tuple[str, str], dict[str, int | float]] = {}
    scope = ""
    for row in grid[header_i + 1 :]:
        nxt = _month_header_cols(row)
        if nxt is not None:
            month_cols = nxt
            for _c, month in month_cols:
                if month not in seen_months:
                    seen_months.add(month)
                    months.append(month)
            continue
        band = _repeated_scope_row(row)
        if band is not None:
            scope = band
            continue
        row_fy = _cells_fy([row[c] if c < len(row) else "" for c in range(label_end)])
        # 古い期の行は同名指標の欠測埋めに使わない
        if current_fy is not None and row_fy is not None and row_fy < current_fy:
            continue
        parts: list[str] = []
        for c in range(label_end):
            t = _label_from_cell(row[c] if c < len(row) else "")
            if not t or t in parts:
                continue
            if parse_month_cell(t) is not None or _is_skip_axis(t):
                continue
            parts.append(t)
        raw_label = _compact_store_status_label(" ".join(parts).strip())
        if scope and scope not in raw_label:
            raw_label = _compact_store_status_label(f"{scope} {raw_label}".strip())
        if not raw_label or parse_month_cell(raw_label) is not None:
            continue
        if _is_prior_year_absolute(raw_label):
            continue
        if not _row_has_number(row, label_end):
            continue
        samples = [row[c] if c < len(row) else "" for c, _m in month_cols]
        unit = infer_unit(raw_label, samples, context)
        label = _rewrite_period_header(raw_label)
        values: dict[str, int | float] = {}
        for c, month in month_cols:
            if c >= len(row):
                continue
            n = parse_number(row[c])
            if n is None:
                continue
            values[str(month)] = json_number(n)
        if not values:
            continue
        element = label
        # Page yoy context is for unlabeled 全店/既存 rows, not 客数/客単価/出退店.
        if (
            context
            and "前年" in context
            and "前年" not in label
            and "売上" in context
            and "売上" not in label
            and _NAMED_METRIC_RE.search(_normalize_count_text(label)) is None
            and not _is_count_metric(label)
        ):
            element = f"{label} 売上高前年同月比".strip()
        vals = series.setdefault((element, unit), {})
        for mk, mv in values.items():
            if mk not in vals:
                vals[mk] = mv
    rows_out = [{"element": el, "unit": unit, "values": vals} for (el, unit), vals in series.items() if vals]
    if not rows_out:
        return None
    return {"months": months, "rows": rows_out}


def _label_col_count(grid: list[list[str]], data_i: int) -> int:
    """month 列の右、数値前のラベル列（ハローズの 全店/既存店）。"""
    n = 0
    width = max((len(r) for r in grid), default=0)
    for c in range(1, min(3, width)):
        cells = []
        for row in grid[data_i:]:
            if c >= len(row):
                continue
            t = row[c].strip()
            if t:
                cells.append(t)
        if not cells:
            break
        numeric = sum(1 for t in cells if parse_number(t) is not None)
        monthish = sum(1 for t in cells if parse_month_cell(t) is not None)
        if numeric > len(cells) / 2 or monthish > len(cells) / 2:
            break
        n += 1
    return n


def _parse_row_months(grid: list[list[str]], data_i: int, context: str) -> dict | None:
    header_rows = grid[:data_i] or [[]]
    width = max(len(r) for r in grid)
    n_label = _label_col_count(grid, data_i)
    metric_start = 1 + n_label
    if metric_start >= width:
        return None
    col_fy_of = {
        c: _cells_fy([row[c] if c < len(row) else "" for row in header_rows])
        for c in range(metric_start, width)
    }
    captioned = [fy for fy in col_fy_of.values() if fy is not None]
    current_fy = max(captioned) if captioned else None
    col_meta: list[tuple[int, str, str]] = []
    for c in range(metric_start, width):
        fy = col_fy_of[c]
        if current_fy is not None and fy is not None and fy < current_fy:
            continue
        raw_header = _header_label(header_rows, c)
        if _is_skip_axis(raw_header) or _is_prior_year_absolute(raw_header):
            continue
        samples = [row[c] for row in grid[data_i:] if c < len(row)]
        if not any(parse_number(v) is not None for v in samples):
            continue
        unit = infer_unit(raw_header, samples, context)
        header = _rewrite_period_header(raw_header)
        col_meta.append((c, header, unit))
    if not col_meta:
        return None
    month_order: list[int] = []
    series: dict[tuple[str, str], dict[str, int | float]] = {}
    carry_month: int | None = None
    for row in grid[data_i:]:
        if not row:
            continue
        head = (row[0] or "").strip()
        if _is_skip_axis(head):
            continue
        month = parse_month_cell(head)
        if month is None:
            if head:
                continue
            month = carry_month
        else:
            carry_month = month
        if month is None:
            continue
        if month not in month_order:
            month_order.append(month)
        labels = [
            t
            for c in range(1, metric_start)
            if c < len(row) and (t := _label_from_cell(row[c]))
        ]
        for c, header, unit in col_meta:
            if c >= len(row):
                continue
            n = parse_number(row[c])
            if n is None:
                continue
            parts = [p for p in [*labels, header] if p]
            element = _squash_label_metric_spaces(" ".join(parts).strip()) or header or "売上高"
            key = (element, unit)
            # 複数年アーカイブ表は同月が繰り返される。先に出た方（新しい方）を残す。
            vals = series.setdefault(key, {})
            mk = str(month)
            if mk not in vals:
                vals[mk] = json_number(n)
    if not series:
        return None
    months = normalize_month_order(month_order)
    rows_out = [{"element": el, "unit": unit, "values": vals} for (el, unit), vals in series.items() if vals]
    if not rows_out:
        return None
    return {"months": months, "rows": rows_out}


def _apply_force_unit(rows: list[dict], force_unit: str | None) -> None:
    if not force_unit:
        return
    if force_unit not in _VALID_UNITS:
        raise ValueError(f"invalid force_unit: {force_unit!r}")
    for row in rows:
        if row.get("unit") in ("yoy_pct", "yoy_index"):
            row["unit"] = force_unit


def dumps_ingest(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


_PRE_MERGER_REF_RE = re.compile(r"経営統合前|統合前")
_NUMBERED_NOTE_RE = re.compile(r"\d+\.[^。]*。")
_PAREN_NOTE_RE = re.compile(r"（注）[^。]*。?")


def _section_heading_tail(section_text: str, n: int = 160) -> str:
    s = unicodedata.normalize("NFKC", section_text or "")
    s = re.sub(r"\s+", "", s)
    s = _NUMBERED_NOTE_RE.sub("", s)
    s = _PAREN_NOTE_RE.sub("", s)
    return s[-n:] if len(s) > n else s


def _is_pre_merger_reference_section(section_text: str) -> bool:
    return _PRE_MERGER_REF_RE.search(_section_heading_tail(section_text)) is not None


def _section_pick_rank(section_text: str) -> int:
    tail = _section_heading_tail(section_text)
    if "（累計）" in tail or "(累計)" in tail:
        return -1
    if _is_pre_merger_reference_section(section_text):
        return -1
    if "拠点数" in tail or "店舗数" in tail:
        return 0
    return 1


def _sticky_section_fy(section_texts: list[str]) -> list[tuple[int, int] | None]:
    """単月表は直前の `YYYY年M月期` を引き継ぐ。年セレクトに複数期があるときは新しい方。"""
    out: list[tuple[int, int] | None] = []
    prev: tuple[int, int] | None = None
    for text in section_texts:
        found = _fy_captions(text)
        if not found:
            out.append(prev)
            continue
        years = {y for y, _m in found}
        cur = max(found) if len(years) > 1 else found[-1]
        prev = cur
        out.append(cur)
    return out


def _parsed_score(
    html: str,
    grid: list[list[str]],
    parsed: dict,
    *,
    table_index: int = 0,
    section_fy: tuple[int, int] | None = None,
) -> tuple[int, int, int, int, int, int]:
    """期が新しい表を優先。同点なら最終値の新しい表（期末まで埋まった表は除く）。"""
    months = parsed.get("months")
    last_m = last_filled_month(parsed["rows"], months)
    derived = as_of_from_table(grid, html, parsed["rows"], months, section_fy)
    # 見出しの無い表はページ全体の年度ナビを見ない（ワタミ後続表が翌年度になる）
    if section_fy is not None:
        fy = section_fy
    elif derived:
        d = date.fromisoformat(derived)
        fy = (d.year, d.month)
    else:
        fy = pick_fy_end(grid, html, months)
    fy_y, fy_m = fy if fy is not None else (0, 0)
    filled_n = max(
        (
            sum(1 for v in (r.get("values") or {}).values() if v is not None)
            for r in parsed["rows"]
        ),
        default=0,
    )
    complete = last_m is not None and fy_m != 0 and last_m == fy_m and filled_n >= 12
    as_key = 0
    if derived and not complete:
        as_key = date.fromisoformat(derived).toordinal()
    n_yoy = sum(1 for r in parsed["rows"] if r.get("unit") != "number")
    n_val = sum(len(r.get("values") or {}) for r in parsed["rows"])
    return (fy_y, fy_m, as_key, -table_index, n_yoy, n_val)
