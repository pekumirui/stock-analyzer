"""IR サイトの月次 HTML を Cloudflare R2 mount へ保存し、ingest JSON を隣に書く。

使い方:
  python scripts/ir_monthly_html_r2.py
  python scripts/ir_monthly_html_r2.py --dest /tmp/monthly --only 2664
  python scripts/ir_monthly_html_r2.py --dry-run
  python scripts/ir_monthly_html_r2.py --reparse

対象は config/ir_monthly.json の html。セレクタ駆動で銘柄クラスは作らない。
原本は .html、派生が .ingest.json。自動 POST しない。パーサーを直したら --reparse。
ネットワークと app/ はテストから分離する。
"""
from __future__ import annotations

import argparse
import calendar
import json
import re
import sys
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ir_monthly_r2 import (  # noqa: E402
    DEFAULT_CONFIG,
    DEFAULT_DEST,
    SLEEP_SEC,
    atomic_write,
    dest_path,
    http_get,
    jst_today,
    sha256_bytes,
    _AParser,
    _SKIP_HREF_RE,
)
from tdnet_monthly_r2 import (  # noqa: E402
    display_code,
    download_skip_reason,
    load_stock_product_map,
)

EIR_URL = "https://ssl4.eir-parts.net/V4Public/eir/{code}/ja/announcement/announcement_{n}.js"
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
_MONTH_RE = re.compile(
    r"(?:(?P<y>\d{4})\s*[/-年]\s*)?(?P<m>1[0-2]|0?[1-9])\s*月"
    r"|(?P<y2>\d{4})\s*[./-]\s*(?P<m2>1[0-2]|0?[1-9])(?!\d)(?!\s*[./-]\s*\d)"
)
_FY_CAPTION_RE = re.compile(r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月期")
_FY_YEAR_RE = re.compile(r"(\d{4})\s*年度")
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
_YM_DO_RE = re.compile(r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月度")
_YM_HEAD_RE = re.compile(
    r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月(?!\s*期)(?!\s*度)(?!\s*\d)"
)
_YEAR_ONLY_HEAD_RE = re.compile(r"(20\d{2})\s*年(?!\s*\d)")
_YEAR_TAB_RE = re.compile(
    r'class=["\'][^"\']*\b(?:here|current|active|selected|is-active|is-current)\b'
    r'[^"\']*["\'][^>]*>[\s\S]{0,160}?(20\d{2})',
    re.I,
)
_SKIP_AXIS_RE = re.compile(
    r"^(?:累計|通期計|通期|通期累計|年度累計|通年|合計|計|年月|"
    r"上期計|下期計|上期累計|下期累計|上半期|下半期|上期|下期|半期|"
    r"[1-4]Q|[１-４]Q|[1-4]四半期|四半期|\d+\s*ヶ月|月計)$"
)
_SKIP_SPAN_RE = re.compile(r"\d+\s*[～〜~\-－]\s*\d+\s*月")
_NAMED_METRIC_RE = re.compile(r"来客数|客単価|客数|店舗数|店数|開店|閉店|出店|退店")
_STORE_FLOW_RE = re.compile(r"開店|閉店|出店|退店")
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


@dataclass(frozen=True)
class HtmlIssuer:
    code: str
    name: str
    index_url: str
    title: str
    source: str
    table: int | None
    eir_code: str
    eir_n: int
    eir_title_re: str
    follow_re: str = ""
    parse: bool = True
    force_unit: str | None = None


def ingest_path(html_path: Path) -> Path:
    return html_path.with_name(html_path.stem + ".ingest.json")


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
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    found: list[tuple[int, int]] = []
    for y, mo in _YM_DO_RE.findall(s):
        found.append((int(y), int(mo)))
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
    ]
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
    ]
    return max(found) if found else None


def page_year(html: str) -> int | None:
    m = _YEAR_TAB_RE.search(html or "")
    if m:
        return int(m.group(1))
    ctx = page_context(html)
    years = [int(y) for y in _YEAR_ONLY_HEAD_RE.findall(unicodedata.normalize("NFKC", ctx))]
    return max(years) if years else None


def parse_eir_date(raw: str) -> str | None:
    s = (raw or "").strip()
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def parse_month_year(text: str) -> tuple[int | None, int | None]:
    """セルから (year or None, month or None)。月期キャプションは月軸にしない。"""
    s = unicodedata.normalize("NFKC", text or "").strip()
    if not s or _SKIP_AXIS_RE.fullmatch(s) or _FY_CAPTION_RE.search(s) or _FY_YEAR_RE.fullmatch(s):
        return None, None
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
    found = [(int(y), int(m)) for y, m in _FY_CAPTION_RE.findall(s)]
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
    years = [int(y) for y in _FY_YEAR_RE.findall(s)]
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
    if m:
        return _named_year_fy_end(int(m.group(1)), months)
    years = [int(y) for y in _YEAR_ONLY_HEAD_RE.findall(s)]
    if years:
        return _named_year_fy_end(years[-1], months)
    return None


def table_section_texts(html: str) -> list[str]:
    """各 table 開始直前のテキスト（新しい期見出しが表の直上にある前提）。"""
    raw = html or ""
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
    found = fy_end_from_text(blob) or fy_end_from_text(page_context(html)) or fy_end_from_text(html)
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
            if month == last_m and year is not None:
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
    if not s:
        return False
    if s.startswith("累計"):
        return True
    if _SKIP_AXIS_RE.fullmatch(s) or _SKIP_SPAN_RE.search(s):
        return True
    return False


def _is_caption_cell(text: str) -> bool:
    s = unicodedata.normalize("NFKC", text or "").strip()
    return bool(_FY_CAPTION_RE.search(s) or _FY_YEAR_RE.fullmatch(s))


def _header_label(header_rows: list[list[str]], col: int) -> str:
    parts: list[str] = []
    for row in header_rows:
        t = row[col].strip() if col < len(row) else ""
        if not t or t in parts:
            continue
        if parse_month_cell(t) is not None or _is_skip_axis(t) or _is_caption_cell(t):
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


def infer_unit(header: str, samples: list[str], context: str = "") -> str:
    h = header or ""
    blob = f"{h} {context}"
    if "店舗数" in h or _STORE_FLOW_RE.search(h):
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
    if "100基準" in blob:
        return "yoy_index"
    if has_tri or signed_pct:
        return "yoy_pct"
    if has_pct or (_PCT_MARK_RE.search(blob) and yoy):
        return "yoy_index"
    if yoy:
        return "yoy_pct"
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


def _parse_col_months(grid: list[list[str]], header_i: int, context: str) -> dict | None:
    header = grid[header_i]
    month_cols: list[tuple[int, int]] = []
    for c, cell in enumerate(header):
        if _is_skip_axis(cell):
            continue
        m = parse_month_cell(cell)
        if m is not None:
            month_cols.append((c, m))
    if not month_cols:
        return None
    months = [m for _c, m in month_cols]
    cuts = [
        c
        for c, cell in enumerate(header)
        if _is_skip_axis(cell) or parse_month_cell(cell) is not None
    ]
    label_end = min(cuts) if cuts else min(c for c, _m in month_cols)
    rows_out: list[dict] = []
    scope = ""
    for row in grid[header_i + 1 :]:
        band = _repeated_scope_row(row)
        if band is not None:
            scope = band
            continue
        parts: list[str] = []
        for c in range(label_end):
            t = row[c].strip() if c < len(row) else ""
            if not t or t in parts:
                continue
            if parse_month_cell(t) is not None or _is_skip_axis(t) or _is_caption_cell(t):
                continue
            parts.append(t)
        label = " ".join(parts).strip()
        if scope and scope not in label:
            label = f"{scope} {label}".strip()
        if not label or parse_month_cell(label) is not None:
            continue
        if not _row_has_number(row, label_end):
            continue
        samples = [row[c] if c < len(row) else "" for c, _m in month_cols]
        unit = infer_unit(label, samples, context)
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
            and _NAMED_METRIC_RE.search(label) is None
        ):
            element = f"{label} 売上高前年同月比".strip()
        rows_out.append({"element": element, "unit": unit, "values": values})
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
    col_meta: list[tuple[int, str, str]] = []
    for c in range(metric_start, width):
        header = _header_label(header_rows, c)
        if _is_skip_axis(header):
            continue
        samples = [row[c] for row in grid[data_i:] if c < len(row)]
        if not any(parse_number(v) is not None for v in samples):
            continue
        unit = infer_unit(header, samples, context)
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
        labels = [row[c].strip() for c in range(1, metric_start) if c < len(row) and row[c].strip()]
        for c, header, unit in col_meta:
            if c >= len(row):
                continue
            n = parse_number(row[c])
            if n is None:
                continue
            parts = [p for p in [*labels, header] if p]
            element = " ".join(parts).strip() or header or "売上高"
            key = (element, unit)
            series.setdefault(key, {})[str(month)] = json_number(n)
    if not series:
        return None
    months = normalize_month_order(month_order)
    rows_out = [{"element": el, "unit": unit, "values": vals} for (el, unit), vals in series.items() if vals]
    if not rows_out:
        return None
    return {"months": months, "rows": rows_out}


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
    fy = section_fy or pick_fy_end(grid, html, months)
    fy_y, fy_m = fy if fy is not None else (0, 0)
    last_m = last_filled_month(parsed["rows"], months)
    derived = as_of_from_table(grid, html, parsed["rows"], months, section_fy)
    complete = (
        last_m is not None
        and fy_m != 0
        and last_m == fy_m
        and len({m for m in (months or []) if isinstance(m, int)}) >= 12
    )
    as_key = 0
    if derived and not complete:
        as_key = date.fromisoformat(derived).toordinal()
    n_yoy = sum(1 for r in parsed["rows"] if r.get("unit") != "number")
    n_val = sum(len(r.get("values") or {}) for r in parsed["rows"])
    return (fy_y, fy_m, as_key, -table_index, n_yoy, n_val)


def _apply_force_unit(rows: list[dict], force_unit: str | None) -> None:
    if not force_unit:
        return
    if force_unit not in _VALID_UNITS:
        raise ValueError(f"invalid force_unit: {force_unit!r}")
    for row in rows:
        if row.get("unit") in ("yoy_pct", "yoy_index"):
            row["unit"] = force_unit


def html_to_ingest(
    html: str,
    code: str,
    *,
    as_of: str | None = None,
    table: int | None = 0,
    context: str | None = None,
    force_unit: str | None = None,
) -> dict:
    tables = parse_tables(html)
    if not tables:
        raise ValueError("no html table")
    sections = table_section_texts(html)
    base_ctx = context if context is not None else page_context(html)
    unit_marks = unit_mark_context(html)
    indices: list[int]
    if table is None:
        indices = list(range(len(tables)))
    else:
        if table < 0 or table >= len(tables):
            raise ValueError(f"table index {table} out of range ({len(tables)})")
        indices = [table]
    last_err: str | None = None
    candidates: list[tuple[int, list[list[str]], dict, tuple[int, int] | None]] = []
    for i in indices:
        section_text = sections[i] if i < len(sections) else ""
        ctx = " ".join(p for p in (base_ctx, section_text, unit_marks) if p)
        try:
            parsed = parse_grid(tables[i], ctx, html)
        except Exception as e:
            last_err = str(e)
            parsed = None
        if parsed and parsed.get("rows"):
            section_fy = section_fy_end_from_text(section_text, parsed.get("months"))
            candidates.append((i, tables[i], parsed, section_fy))
            if table is not None:
                break
    if not candidates:
        raise ValueError(last_err or "no monthly rows")
    table_index, grid_used, parsed, section_fy = max(
        candidates,
        key=lambda it: _parsed_score(
            html, it[1], it[2], table_index=it[0], section_fy=it[3]
        ),
    )
    _apply_force_unit(parsed["rows"], force_unit)
    as_of_s = resolve_as_of(
        html,
        grid_used,
        parsed["rows"],
        as_of,
        parsed.get("months"),
        section_fy,
    )
    months = drop_trailing_empty_months(list(parsed.get("months") or []), parsed["rows"])
    return {
        "code": display_code(code),
        "months": months,
        "rows": parsed["rows"],
        "as_of": as_of_s,
    }


def dumps_ingest(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def parse_eir_jsonp(body: str) -> dict:
    s = (body or "").lstrip("\ufeff").strip()
    lparen = s.find("(")
    rparen = s.rfind(")")
    if lparen >= 0 and rparen > lparen:
        payload = s[lparen + 1 : rparen]
    else:
        payload = s
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("eir jsonp is not an object")
    return data


def pick_eir_monthly(payload: dict, title_re: str) -> tuple[str, str | None]:
    try:
        rx = re.compile(title_re)
    except re.error as e:
        raise ValueError(f"invalid eir_title_re: {title_re!r} ({e})") from e
    items = payload.get("item")
    if not isinstance(items, list):
        raise ValueError("eir jsonp missing item")
    matched: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "")
        if rx.search(title):
            matched.append(it)
    if not matched:
        raise ValueError("no eir monthly item")
    with_text = [it for it in matched if str(it.get("text") or "").strip()]
    pool = with_text or matched
    latest = max(
        pool,
        key=lambda it: (
            str(it.get("date") or ""),
            fy_end_from_text(str(it.get("title") or "")) or (0, 0),
        ),
    )
    html = str(latest.get("text") or "").strip()
    if not html:
        raise ValueError("eir monthly text empty")
    return html, parse_eir_date(str(latest.get("date") or ""))


def eir_url(code: str, n: int) -> str:
    return EIR_URL.format(code=display_code(code), n=int(n))


def existing_html_hashes(code_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not code_dir.is_dir():
        return out
    for p in code_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".html":
            continue
        try:
            if p.stat().st_size <= 0:
                continue
            out[sha256_bytes(p.read_bytes())] = p
        except OSError:
            continue
    return out


def _parse_html_issuer(raw: object) -> HtmlIssuer:
    if not isinstance(raw, dict):
        raise ValueError("html issuer must be an object")
    raw_code = str(raw.get("code") or "").strip().upper()
    if not _CODE_RE.fullmatch(raw_code):
        raise ValueError(f"invalid issuer code: {raw.get('code')!r}")
    code = display_code(raw_code)
    name = str(raw.get("name") or "").strip()
    index_url = str(raw.get("index_url") or "").strip()
    title = str(raw.get("title") or "").strip() or "月次"
    source = str(raw.get("source") or "html").strip().lower() or "html"
    if source not in ("html", "eir"):
        raise ValueError(f"issuer {code} invalid source: {source!r}")
    if not name:
        raise ValueError(f"issuer {code} missing name")
    parsed = urlparse(index_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid index_url for {code}: {index_url!r}")
    table_raw = raw.get("table", 0)
    table: int | None
    if table_raw is None:
        table = None
    elif isinstance(table_raw, bool) or not isinstance(table_raw, int):
        raise ValueError(f"issuer {code} invalid table")
    else:
        table = table_raw
    eir_code = display_code(str(raw.get("eir_code") or code).strip().upper())
    eir_n_raw = raw.get("eir_n", 0)
    if isinstance(eir_n_raw, bool) or not isinstance(eir_n_raw, int) or eir_n_raw < 0:
        raise ValueError(f"issuer {code} invalid eir_n")
    eir_title_re = str(raw.get("eir_title_re") or "月次").strip() or "月次"
    try:
        re.compile(eir_title_re)
    except re.error as e:
        raise ValueError(f"issuer {code} invalid eir_title_re: {e}") from e
    follow_re = str(raw.get("follow_re") or "").strip()
    if follow_re:
        try:
            re.compile(follow_re)
        except re.error as e:
            raise ValueError(f"issuer {code} invalid follow_re: {e}") from e
    parse_raw = raw.get("parse", True)
    if not isinstance(parse_raw, bool):
        raise ValueError(f"issuer {code} invalid parse")
    force_raw = raw.get("force_unit")
    force_unit: str | None
    if force_raw is None:
        force_unit = None
    elif isinstance(force_raw, str) and force_raw in _VALID_UNITS:
        force_unit = force_raw
    else:
        raise ValueError(f"issuer {code} invalid force_unit: {force_raw!r}")
    return HtmlIssuer(
        code=code,
        name=name,
        index_url=index_url,
        title=title,
        source=source,
        table=table,
        eir_code=eir_code,
        eir_n=eir_n_raw,
        eir_title_re=eir_title_re,
        follow_re=follow_re,
        parse=parse_raw,
        force_unit=force_unit,
    )


def load_html_registry(path: Path) -> list[HtmlIssuer]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("html")
    else:
        items = raw
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path} has no html issuers")
    out: list[HtmlIssuer] = []
    seen: set[str] = set()
    for item in items:
        iss = _parse_html_issuer(item)
        if iss.code in seen:
            raise ValueError(f"duplicate html issuer code: {iss.code}")
        seen.add(iss.code)
        out.append(iss)
    return out


_FOLLOW_YM_RE = re.compile(r"(20\d{2})_(1[0-2]|0?[1-9])(?:/|$)")


def _follow_key(url: str) -> tuple[int, int, int, str]:
    """年 → 月 → パス深さ。`orders_2026/2026_8` は年ページより後。`2026_10` は `2026_9` より後。"""
    path = urlparse(url).path.rstrip("/")
    ms = list(_FOLLOW_YM_RE.finditer(path))
    if ms:
        return (int(ms[-1].group(1)), int(ms[-1].group(2)), path.count("/"), url)
    years = [int(y) for y in re.findall(r"20\d{2}", path)]
    y = years[-1] if years else 0
    return (y, 0, path.count("/"), url)


def pick_follow_url(
    html: str,
    base_url: str,
    follow_re: str,
    *,
    under: str | None = None,
) -> str | None:
    """index から follow_re に合うリンクを集め、年・月が最大のものを返す。

    under を付けると、その URL の子パスだけ（年一覧 → 最新月）。無ければ None。
    """
    try:
        rx = re.compile(follow_re)
    except re.error as e:
        raise ValueError(f"invalid follow_re: {follow_re!r} ({e})") from e
    parser = _AParser()
    parser.feed(html or "")
    parser.close()
    found: list[str] = []
    seen: set[str] = set()
    for href, _text in parser.links:
        raw = (href or "").strip()
        if not raw or _SKIP_HREF_RE.match(raw):
            continue
        abs_url = urljoin(base_url, raw).split("#", 1)[0]
        if abs_url in seen:
            continue
        if not rx.search(abs_url) and not rx.search(raw):
            continue
        seen.add(abs_url)
        found.append(abs_url)
    if under:
        prefix = under.rstrip("/") + "/"
        under_n = under.rstrip("/")
        found = [u for u in found if u.rstrip("/") != under_n and u.startswith(prefix)]
    if not found:
        if under is not None:
            return None
        raise ValueError(f"no follow url matching {follow_re!r}")
    found.sort(key=_follow_key)
    return found[-1]


def fetch_html_bytes(iss: HtmlIssuer) -> tuple[bytes, str | None, str]:
    """returns (html_bytes, as_of, source_url)."""
    if iss.source == "eir":
        url = eir_url(iss.eir_code, iss.eir_n)
        body = http_get(url)
        time.sleep(SLEEP_SEC)
        payload = parse_eir_jsonp(decode_html_bytes(body))
        html, as_of = pick_eir_monthly(payload, iss.eir_title_re)
        return html.encode("utf-8"), as_of, url
    url = iss.index_url
    data = http_get(url)
    time.sleep(SLEEP_SEC)
    if iss.follow_re:
        followed = pick_follow_url(decode_html_bytes(data), url, iss.follow_re)
        assert followed is not None
        if followed.rstrip("/") != url.rstrip("/"):
            data = http_get(followed)
            time.sleep(SLEEP_SEC)
            url = followed
        if iss.parse:
            html = decode_html_bytes(data)
            try:
                html_to_ingest(html, iss.code, table=iss.table, force_unit=iss.force_unit)
            except ValueError:
                child = pick_follow_url(html, url, iss.follow_re, under=url)
                if child and child.rstrip("/") != url.rstrip("/"):
                    data = http_get(child)
                    time.sleep(SLEEP_SEC)
                    url = child
    return data, None, url


def write_ingest(html_path: Path, html: str, iss: HtmlIssuer, as_of: str | None = None) -> None:
    payload = html_to_ingest(
        html, iss.code, as_of=as_of, table=iss.table, force_unit=iss.force_unit
    )
    atomic_write(ingest_path(html_path), dumps_ingest(payload).encode("utf-8"))


def _try_write_ingest(
    html_path: Path,
    html: str,
    iss: HtmlIssuer,
    as_of: str | None,
    rel: object,
) -> int:
    """0 ok, 1 failed. parse=false は JSON を書かず成功。"""
    if not iss.parse:
        print(f"skip parse {rel}", flush=True)
        return 0
    try:
        write_ingest(html_path, html, iss, as_of)
    except Exception as e:
        print(f"fail ingest {rel}: {e}", flush=True)
        return 1
    print(f"saved {ingest_path(html_path).name}", flush=True)
    return 0


def fetch_issuer(
    iss: HtmlIssuer,
    dest: Path,
    stock_map: Mapping[str, tuple[str | None, str | None]],
    today: date,
    *,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """returns listed, saved, skipped, failed."""
    reason = download_skip_reason(iss.code, iss.name, stock_map)
    if reason:
        print(f"skip {reason}", flush=True)
        return 0, 0, 1, 0
    if dry_run:
        if iss.source == "eir":
            print(f"dry-run {iss.code} eir {eir_url(iss.eir_code, iss.eir_n)}", flush=True)
        elif iss.follow_re:
            print(f"dry-run {iss.code} html {iss.index_url} follow={iss.follow_re}", flush=True)
        else:
            print(f"dry-run {iss.code} html {iss.index_url}", flush=True)
        return 1, 0, 1, 0
    try:
        data, eir_as_of, url = fetch_html_bytes(iss)
    except Exception as e:
        print(f"fail issuer {iss.code} fetch: {e}", flush=True)
        return 1, 0, 0, 1
    digest = sha256_bytes(data)
    code_dir = dest / display_code(iss.code)
    hashes = existing_html_hashes(code_dir)
    pubdate = today.isoformat()
    path = dest_path(dest, pubdate, iss.code, iss.name, iss.title, "html")
    rel = path.relative_to(dest)
    prev = hashes.get(digest)
    html = decode_html_bytes(data)
    as_of = eir_as_of
    if prev is not None:
        sidecar = ingest_path(prev)
        try:
            missing = (not sidecar.is_file()) or sidecar.stat().st_size <= 0
        except OSError:
            missing = True
        if missing and iss.parse:
            if _try_write_ingest(prev, html, iss, as_of, sidecar.relative_to(dest)):
                return 1, 0, 0, 1
            return 1, 1, 0, 0
        print(f"skip hash {rel} == {prev.relative_to(dest)}", flush=True)
        return 1, 0, 1, 0
    try:
        if path.is_file() and path.stat().st_size > 0:
            hashes.pop(sha256_bytes(path.read_bytes()), None)
        atomic_write(path, data)
    except Exception as e:
        print(f"fail {rel} url={url}: {e}", flush=True)
        return 1, 0, 0, 1
    print(f"saved {rel} ({len(data)} bytes)", flush=True)
    if _try_write_ingest(path, html, iss, as_of, rel):
        return 1, 1, 0, 1
    return 1, 1, 0, 0


def reparse_issuer(iss: HtmlIssuer, dest: Path) -> tuple[int, int, int]:
    """returns parsed, skipped, failed."""
    code_dir = dest / display_code(iss.code)
    if not code_dir.is_dir():
        return 0, 0, 0
    if not iss.parse:
        print(f"skip parse {iss.code}", flush=True)
        return 0, 0, 0
    parsed = skipped = failed = 0
    files = sorted(p for p in code_dir.iterdir() if p.is_file() and p.suffix.lower() == ".html")
    for path in files:
        rel = path.relative_to(dest)
        try:
            data = path.read_bytes()
            if not data:
                skipped += 1
                continue
            html = decode_html_bytes(data)
            write_ingest(path, html, iss)
            parsed += 1
            print(f"reparse {rel} -> {ingest_path(path).name}", flush=True)
        except Exception as e:
            failed += 1
            print(f"fail reparse {rel}: {e}", flush=True)
    return parsed, skipped, failed


def run(
    dest: Path,
    config_path: Path,
    *,
    only: list[str] | None = None,
    dry_run: bool = False,
    reparse: bool = False,
    today: date | None = None,
) -> int:
    issuers = load_html_registry(config_path)
    if only:
        want = {display_code(c.strip().upper()) for c in only if c.strip()}
        issuers = [i for i in issuers if i.code in want]
        missing = want - {i.code for i in issuers}
        if missing:
            print(f"warn unknown --only {sorted(missing)}", flush=True)
    if not issuers:
        print("ir_monthly_html_r2 no issuers", flush=True)
        return 2
    day = today or jst_today()
    dest.mkdir(parents=True, exist_ok=True)
    print(
        f"ir_monthly_html_r2 start dest={dest} issuers={len(issuers)} "
        f"day={day.isoformat()} dry_run={int(dry_run)} reparse={int(reparse)}",
        flush=True,
    )
    if reparse:
        parsed = skipped = failed = 0
        for iss in issuers:
            print(f"issuer {iss.code} {iss.name}", flush=True)
            p, k, f = reparse_issuer(iss, dest)
            parsed += p
            skipped += k
            failed += f
        print(
            f"ir_monthly_html_r2 reparse parsed={parsed} skipped={skipped} failed={failed}",
            flush=True,
        )
        return 1 if failed else 0
    stock_map = load_stock_product_map()
    if not stock_map:
        print("warn stocks map empty; unknown-stock skip disabled", flush=True)
    listed = saved = skipped = failed = 0
    for iss in issuers:
        print(f"issuer {iss.code} {iss.name}", flush=True)
        try:
            n, s, k, f = fetch_issuer(iss, dest, stock_map, day, dry_run=dry_run)
        except Exception as e:
            failed += 1
            print(f"fail issuer {iss.code} {iss.index_url}: {e}", flush=True)
            continue
        listed += n
        saved += s
        skipped += k
        failed += f
    print(
        f"ir_monthly_html_r2 done listed={listed} saved={saved} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch IR monthly HTML into R2 mount and write ingest JSON")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="issuer registry JSON")
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="limit to these codes (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true", help="list URLs, do not download")
    ap.add_argument("--reparse", action="store_true", help="rebuild ingest JSON from saved HTML")
    args = ap.parse_args(argv)
    return run(
        Path(args.dest),
        Path(args.config),
        only=args.only or None,
        dry_run=args.dry_run,
        reparse=args.reparse,
    )


if __name__ == "__main__":
    raise SystemExit(main())
