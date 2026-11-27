from __future__ import annotations

import calendar
import json
import re
import shutil
import subprocess
import unicodedata
from datetime import date
from pathlib import Path

from app.monthly.publications import Pub, dated, unknown
from jobs.monthly.html_monthly import calendar_page_date

_COLUMN_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_YEAR_MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月度")
_FY_MONTH_RE = re.compile(r"(\d{1,2})\s*月期\s*(\d{1,2})\s*月度")
_BARE_MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*月度")
_CAL_MONTH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月(?!度|期|\s*\d)")
_MONTH_WORD_RE = re.compile(r"月\s*度")
_PDF_SIDECAR = ".pdf-ingest.json"
CRAWLER_START = date(2026, 9, 1)
ESTIMATE_DAYS = 45


def expected_month(as_of: date) -> tuple[int, int]:
    if as_of.month == 1:
        return as_of.year - 1, 12
    return as_of.year, as_of.month - 1


def is_month_end(day: date) -> bool:
    return day.day == calendar.monthrange(day.year, day.month)[1]


def split_pubdate(pubdate: str) -> tuple[date | None, str | None]:
    s = (pubdate or "").strip()
    if len(s) < 10 or s[4] != "-" or s[7] != "-":
        return None, None
    try:
        day = date.fromisoformat(s[:10])
    except ValueError:
        return None, None
    clock = None
    if len(s) >= 19 and s[10] in " T":
        raw = s[11:19]
        if len(raw) == 8 and raw[2] == ":" and raw[5] == ":":
            clock = raw
    return day, clock


def normalize_title(title: str) -> str:
    return unicodedata.normalize("NFKC", title or "")


def _on_or_before(year: int, month: int, disclosed: date) -> tuple[int, int] | None:
    if not 1 <= month <= 12:
        return None
    if date(year, month, 1) <= disclosed:
        return year, month
    return None


def _inferred_year(month: int, disclosed: date) -> int:
    return disclosed.year if month <= disclosed.month else disclosed.year - 1


def report_month(title: str, disclosed: date) -> tuple[int, int] | None:
    s = normalize_title(title)
    found: list[tuple[int, int]] = []
    for year_s, month_s in _YEAR_MONTH_RE.findall(s):
        item = _on_or_before(int(year_s), int(month_s), disclosed)
        if item is not None:
            found.append(item)
    for _fy, month_s in _FY_MONTH_RE.findall(s):
        month = int(month_s)
        item = _on_or_before(_inferred_year(month, disclosed), month, disclosed)
        if item is not None:
            found.append(item)
    if found:
        return max(found)
    for match in _BARE_MONTH_RE.finditer(s):
        if re.search(r"年\s*$", s[: match.start(1)]):
            continue
        month = int(match.group(1))
        item = _on_or_before(_inferred_year(month, disclosed), month, disclosed)
        if item is not None:
            found.append(item)
    if found:
        return max(found)
    return None


def titled_calendar_month(title: str, disclosed: date) -> tuple[int, int] | None:
    s = normalize_title(title)
    found: list[tuple[int, int]] = []
    for year_s, month_s in _CAL_MONTH_RE.findall(s):
        item = _on_or_before(int(year_s), int(month_s), disclosed)
        if item is not None:
            found.append(item)
    if not found:
        return None
    return max(found)


def month_from_title(title: str, disclosed: date) -> tuple[int, int] | None:
    return report_month(title, disclosed) or titled_calendar_month(title, disclosed)


def title_mentions_month(title: str) -> bool:
    return _MONTH_WORD_RE.search(normalize_title(title)) is not None


def estimate_month(disclosed: date) -> tuple[int, int] | None:
    year, month = disclosed.year, disclosed.month
    found: list[tuple[int, int]] = []
    for _ in range(3):
        if month == 1:
            year, month = year - 1, 12
        else:
            month -= 1
        end = date(year, month, calendar.monthrange(year, month)[1])
        delta = (disclosed - end).days
        if 0 < delta <= ESTIMATE_DAYS:
            found.append((year, month))
    if not found:
        return None
    return max(found)


def latest_column(columns: object) -> tuple[int, int] | None:
    if not isinstance(columns, (list, tuple)):
        return None
    found: list[tuple[int, int]] = []
    for item in columns:
        if not isinstance(item, str):
            return None
        m = _COLUMN_RE.fullmatch(item)
        if m is None:
            return None
        found.append((int(m.group(1)), int(m.group(2))))
    if not found:
        return None
    return max(found)


def tdnet_release(
    disclosed: date,
    columns: object = None,
    clock: str | None = None,
    ref: str | None = None,
) -> tuple[tuple[int, int], Pub]:
    latest = latest_column(columns)
    ym = latest if latest is not None else expected_month(disclosed)
    return ym, dated("tdnet", disclosed, clock, ref)


def ir_release(
    as_of: date,
    seen: date | None,
    html: str | None,
    ref: str | None = None,
) -> tuple[tuple[int, int], Pub]:
    page = calendar_page_date(html) if html else None
    if page is not None and page.year == as_of.year:
        return expected_month(page), dated("ir_page", page, ref=ref)
    if not is_month_end(as_of):
        return expected_month(as_of), dated("ir_page", as_of, ref=ref)
    ym = (as_of.year, as_of.month)
    if seen is not None and seen != date.min:
        return ym, dated("first_seen", seen, ref=ref)
    return ym, unknown("no_page_date", ref)


def pdf_page_text(path: Path) -> str:
    exe = shutil.which("pdftotext")
    if exe is None or not path.is_file():
        return ""
    try:
        proc = subprocess.run(
            [exe, "-f", "1", "-l", "2", "-enc", "UTF-8", "-q", str(path), "-"],
            check=False,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", "replace")


def pdf_backfill_claim(
    name: str,
    disclosed: date,
    clock: str | None,
    ref: str | None,
    body: str = "",
) -> tuple[tuple[int, int], Pub] | None:
    parsed = report_month(body, disclosed) if body else None
    if parsed is None:
        parsed = month_from_title(name, disclosed)
    if parsed is not None:
        return parsed, dated("tdnet", disclosed, clock, ref, confidence="exact")
    if title_mentions_month(body) or title_mentions_month(name):
        return None
    estimated = estimate_month(disclosed)
    if estimated is None:
        return None
    return estimated, dated("tdnet", disclosed, clock, ref, confidence="estimated")


def html_backfill_claim(
    as_of: date,
    seen: date | None,
    html: str | None,
    ref: str | None,
) -> tuple[tuple[int, int], Pub] | None:
    page = calendar_page_date(html) if html else None
    if page is not None and page.year == as_of.year:
        return expected_month(page), dated("ir_page", page, ref=ref)
    if not is_month_end(as_of):
        return expected_month(as_of), dated("ir_page", as_of, ref=ref)
    ym = (as_of.year, as_of.month)
    if (
        seen is not None and seen != date.min and seen >= CRAWLER_START
        and ym >= (CRAWLER_START.year, CRAWLER_START.month)
    ):
        return ym, dated("first_seen", seen, ref=ref)
    return None


def origin_pub(origin: str, day: date, seen: date | None, ref: str | None) -> Pub:
    if origin == "ir_page":
        return dated("ir_page", day, ref=ref)
    if seen is not None and seen != date.min:
        return dated("first_seen", seen, ref=ref)
    return unknown("no_page_date", ref)


def html_sibling(path: Path) -> Path | None:
    name = path.name
    if not name.endswith(".ingest.json") or name.endswith(_PDF_SIDECAR):
        return None
    html = path.with_name(name[: -len(".ingest.json")])
    if html.is_file():
        return html
    return None


def read_html(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def pdf_for_sidecar(path: Path) -> Path | None:
    if not path.name.endswith(_PDF_SIDECAR):
        return None
    pdf = path.with_name(path.name[: -len(_PDF_SIDECAR)] + ".pdf")
    return pdf


def meta_clock(pdf: Path | None, disclosed: date) -> str | None:
    if pdf is None:
        return None
    meta = pdf.with_name(pdf.name + ".meta.json")
    if not meta.is_file():
        return None
    try:
        raw = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    day, clock = split_pubdate(str(raw.get("pubdate") or ""))
    if day != disclosed:
        return None
    return clock


def write_pdf_meta(pdf: Path, pubdate: str, pdf_id: str) -> None:
    path = pdf.with_name(pdf.name + ".meta.json")
    payload = (json.dumps(
        {"pubdate": pubdate, "pdf_id": pdf_id},
        ensure_ascii=False,
    ) + "\n").encode("utf-8")
    if path.is_file():
        try:
            if path.read_bytes() == payload:
                return
        except OSError:
            pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(payload)
    tmp.replace(path)
