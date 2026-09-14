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
from urllib.parse import urlparse

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
    r"|(?P<y2>\d{4})\s*[/-]\s*(?P<m2>1[0-2]|0?[1-9])\b"
)
_FY_CAPTION_RE = re.compile(r"(\d{4})\s*年\s*(1[0-2]|0?[1-9])\s*月期")
_FY_YEAR_RE = re.compile(r"(\d{4})\s*年度")
_SKIP_AXIS_RE = re.compile(r"^(?:累計|通期計|通期|合計|計|年月)$")
_UPDATE_RE = re.compile(
    r"(\d{4})\s*[/-年]\s*(\d{1,2})\s*[/-月]\s*(\d{1,2})\s*日?\s*"
    r"(?:更新|公表|発表|開示|掲載)"
)
_UNIT_TAIL_RE = re.compile(r"(?:百万円|千円|億円|百万|円|店|度)+$")
_EMPTY_VAL_RE = re.compile(r"^(?:[-−－―–—ー…‥.．]|n/?a|なし)?$", re.I)
_PCT_MARK_RE = re.compile(r"[%％]")
_TRI_RE = re.compile(r"[△▲]")
_CHARSET_RE = re.compile(br"charset\s*=\s*[\"']?\s*([a-zA-Z0-9_-]+)", re.I)
_HEADING_RE = re.compile(r"<h[1-4][^>]*>(.*?)</h[1-4]>", re.I | re.S)
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


def page_context(html: str) -> str:
    parts: list[str] = []
    for m in _HEADING_RE.finditer(html or ""):
        t = re.sub(r"\s+", " ", _TAG_RE.sub(" ", m.group(1))).strip()
        if t:
            parts.append(t)
    return " ".join(parts)


def parse_as_of_text(html: str) -> str | None:
    m = _UPDATE_RE.search(html or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


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
    """`2027年3月期` の最新（年, 期末月）。`YYYY年度` は開始/終了が曖昧なので使わない。"""
    found = [(int(y), int(m)) for y, m in _FY_CAPTION_RE.findall(text or "")]
    if not found:
        return None
    return max(found)


def pick_fy_end(grid: list[list[str]], html: str) -> tuple[int, int] | None:
    blob = " ".join(c for row in grid for c in row)
    return fy_end_from_text(blob) or fy_end_from_text(page_context(html)) or fy_end_from_text(html)


def last_filled_month(rows: list[dict]) -> int | None:
    months: list[int] = []
    for row in rows:
        values = row.get("values") or {}
        if not isinstance(values, dict):
            continue
        for k, v in values.items():
            if v is None:
                continue
            try:
                months.append(int(k))
            except (TypeError, ValueError):
                continue
    return max(months) if months else None


def year_for_month(month: int, fy_end: tuple[int, int] | None, cell_year: int | None) -> int | None:
    if cell_year is not None:
        return cell_year
    if fy_end is None:
        return None
    fy_y, fy_m = fy_end
    return fy_y if month <= fy_m else fy_y - 1


def as_of_from_table(grid: list[list[str]], html: str, rows: list[dict]) -> str | None:
    """値がある最後の月の年月。セルに年が無ければ `YYYY年M月期` から補う。"""
    last_m = last_filled_month(rows)
    if last_m is None:
        return None
    cell_year: int | None = None
    for row in grid:
        for cell in row:
            year, month = parse_month_year(cell)
            if month == last_m and year is not None:
                cell_year = year if cell_year is None else max(cell_year, year)
    year = year_for_month(last_m, pick_fy_end(grid, html), cell_year)
    if year is None:
        return None
    return month_end_iso(year, last_m)


def resolve_as_of(
    html: str,
    grid: list[list[str]] | None,
    rows: list[dict],
    hint: str | None,
) -> str:
    """開示日 → 表の最終値の年月 → 呼び出し側 hint（E-IR 日付）。取得日は使わない。"""
    explicit = parse_as_of_text(html)
    if explicit:
        return explicit
    derived = as_of_from_table(grid or [], html, rows)
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
    return bool(_SKIP_AXIS_RE.fullmatch(s))


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
    if "店舗数" in h:
        return "number"
    if any(k in h for k in ("百万円", "千円", "億円")) and "前年" not in h:
        return "number"
    has_tri = any(_TRI_RE.search(v or "") for v in samples)
    has_pct = any(_PCT_MARK_RE.search(v or "") for v in samples)
    yoy = any(k in blob for k in ("前年", "同月比", "100基準"))
    if "100基準" in blob:
        return "yoy_index"
    if has_tri:
        return "yoy_pct"
    if has_pct:
        return "yoy_index"
    if yoy:
        return "yoy_pct"
    return "number"


def _row_has_number(row: list[str], start: int = 0) -> bool:
    return any(parse_number(c) is not None for c in row[start:])


def _detect_col_header(grid: list[list[str]]) -> int | None:
    for i, row in enumerate(grid[:4]):
        months = [parse_month_cell(c) for c in row]
        if sum(1 for m in months if m is not None) >= 3:
            return i
    return None


def _first_data_row(grid: list[list[str]]) -> int | None:
    for i, row in enumerate(grid):
        if not row:
            continue
        if parse_month_cell(row[0]) is not None:
            return i
    return None


def parse_grid(grid: list[list[str]], context: str = "") -> dict | None:
    if not grid:
        return None
    col_hi = _detect_col_header(grid)
    if col_hi is not None:
        return _parse_col_months(grid, col_hi, context)
    data_i = _first_data_row(grid)
    if data_i is None:
        return None
    return _parse_row_months(grid, data_i, context)


def _parse_col_months(grid: list[list[str]], header_i: int, context: str) -> dict | None:
    header = grid[header_i]
    month_cols: list[tuple[int, int]] = []
    for c, cell in enumerate(header):
        if _is_skip_axis(cell):
            continue
        m = parse_month_cell(cell)
        if m is not None:
            month_cols.append((c, m))
    if len(month_cols) < 3:
        return None
    months = [m for _c, m in month_cols]
    samples = [
        cell
        for row in grid[header_i + 1 :]
        for _c, cell in enumerate(row)
        if any(c == _c for c, _m in month_cols)
    ]
    unit = infer_unit("", samples, context)
    rows_out: list[dict] = []
    for row in grid[header_i + 1 :]:
        label = (row[0] if row else "").strip()
        if not label or _is_skip_axis(label) or parse_month_cell(label) is not None:
            continue
        if not _row_has_number(row, 1):
            continue
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
        if context and "前年" in context and "前年" not in label and "売上" not in label:
            metric = "売上高前年同月比" if "売上" in context else context.split()[-1]
            if metric and metric not in element:
                element = f"{label} {metric}".strip()
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


def html_to_ingest(
    html: str,
    code: str,
    *,
    as_of: str | None = None,
    table: int | None = 0,
    context: str | None = None,
) -> dict:
    tables = parse_tables(html)
    if not tables:
        raise ValueError("no html table")
    ctx = context if context is not None else page_context(html)
    indices: list[int]
    if table is None:
        indices = list(range(len(tables)))
    else:
        if table < 0 or table >= len(tables):
            raise ValueError(f"table index {table} out of range ({len(tables)})")
        indices = [table]
    parsed = None
    grid_used: list[list[str]] | None = None
    last_err: str | None = None
    for i in indices:
        try:
            parsed = parse_grid(tables[i], ctx)
        except Exception as e:
            last_err = str(e)
            parsed = None
        if parsed and parsed.get("rows"):
            grid_used = tables[i]
            break
        parsed = None
    if not parsed:
        raise ValueError(last_err or "no monthly rows")
    as_of_s = resolve_as_of(html, grid_used, parsed["rows"], as_of)
    return {
        "code": display_code(code),
        "months": parsed["months"],
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
    latest = max(matched, key=lambda it: str(it.get("date") or ""))
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


def fetch_html_bytes(iss: HtmlIssuer) -> tuple[bytes, str | None, str]:
    """returns (html_bytes, as_of, source_url)."""
    if iss.source == "eir":
        url = eir_url(iss.eir_code, iss.eir_n)
        body = http_get(url)
        time.sleep(SLEEP_SEC)
        payload = parse_eir_jsonp(decode_html_bytes(body))
        html, as_of = pick_eir_monthly(payload, iss.eir_title_re)
        return html.encode("utf-8"), as_of, url
    data = http_get(iss.index_url)
    time.sleep(SLEEP_SEC)
    return data, None, iss.index_url


def write_ingest(html_path: Path, html: str, iss: HtmlIssuer, as_of: str | None = None) -> None:
    payload = html_to_ingest(html, iss.code, as_of=as_of, table=iss.table)
    atomic_write(ingest_path(html_path), dumps_ingest(payload).encode("utf-8"))


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
        if missing:
            try:
                write_ingest(prev, html, iss, as_of)
            except Exception as e:
                print(f"fail ingest {prev.relative_to(dest)}: {e}", flush=True)
                return 1, 0, 0, 1
            print(f"saved {sidecar.relative_to(dest)}", flush=True)
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
    try:
        write_ingest(path, html, iss, as_of)
    except Exception as e:
        print(f"fail ingest {rel}: {e}", flush=True)
        return 1, 1, 0, 1
    print(f"saved {ingest_path(path).relative_to(dest)}", flush=True)
    return 1, 1, 0, 0


def reparse_issuer(iss: HtmlIssuer, dest: Path) -> tuple[int, int, int]:
    """returns parsed, skipped, failed."""
    code_dir = dest / display_code(iss.code)
    if not code_dir.is_dir():
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
