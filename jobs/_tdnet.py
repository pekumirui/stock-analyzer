"""TDnet の開示一覧（I_list_NNN_YYYYMMDD.html）を読む。XBRL と月次 PDF の取得で共通（KAB-294）。

1 日分は 1 ページ 100 件で、ページ番号はページャーの I_list_NNN から取る。
TDnet は約 1 か月で一覧も PDF / ZIP も消す（404）。その日は飛ばす。
行のキーは company_code / company_name / title / pubdate（YYYY-MM-DD HH:MM:00）/
document_url（PDF）/ url_xbrl（ZIP。無ければ ""）。
"""
from __future__ import annotations

import html
import re
import time
from collections.abc import Callable
from datetime import date, timedelta
from urllib.error import HTTPError

TDNET_BASE = "https://www.release.tdnet.info/inbs"

_PAGE_RE = re.compile(r"I_list_(\d{3})_\d{8}\.html")
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r'<td class="[^"]*\b(kj[A-Za-z]+)"[^>]*>(.*?)</td>', re.S | re.I)
_HREF_RE = re.compile(r'href="([0-9A-Za-z]+\.(?:pdf|zip))"', re.I)
_STRIKE_RE = re.compile(r"<s>.*?</s>", re.S | re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def list_url(day: date, page: int = 1) -> str:
    return f"{TDNET_BASE}/I_list_{page:03d}_{day:%Y%m%d}.html"


def _text(cell: str) -> str:
    return html.unescape(_TAG_RE.sub("", cell)).strip()


def _href(cell: str) -> str:
    m = _HREF_RE.search(cell)
    return f"{TDNET_BASE}/{m.group(1)}" if m else ""


def parse_list_page(text: str, day: date) -> tuple[list[dict], set[int]]:
    """1 ページ → (行, ページ番号)。表が無い（開示ゼロの日）なら ([], {1})。"""
    pages = {int(n) for n in _PAGE_RE.findall(text)} or {1}
    rows: list[dict] = []
    i = text.find('id="main-list-table"')
    if i < 0:
        return rows, pages
    for tr in _TR_RE.findall(text[i:]):
        cells = dict(_TD_RE.findall(tr))
        if "kjCode" not in cells:
            continue
        hhmm = _text(cells.get("kjTime", "")) or "00:00"
        rows.append({
            "company_code": _text(cells["kjCode"]),
            "company_name": _text(cells.get("kjName", "")),
            # 表題変更の行は旧表題を <s> で並べる
            "title": _text(_STRIKE_RE.sub("", cells.get("kjTitle", ""))),
            "pubdate": f"{day.isoformat()} {hhmm}:00",
            "document_url": _href(cells.get("kjTitle", "")),
            "url_xbrl": _href(cells.get("kjXbrl", "")),
        })
    return rows, pages


def fetch_day(day: date, get: Callable[[str], bytes], sleep_sec: float = 0.4) -> list[dict]:
    """1 日分を全ページ読む。404 などは上げる。"""
    first = get(list_url(day)).decode("utf-8", errors="replace")
    time.sleep(sleep_sec)
    rows, pages = parse_list_page(first, day)
    for page in sorted(pages - {1}):
        text = get(list_url(day, page)).decode("utf-8", errors="replace")
        time.sleep(sleep_sec)
        rows.extend(parse_list_page(text, day)[0])
    return rows


def fetch_days(
    start: date,
    end: date,
    get: Callable[[str], bytes],
    sleep_sec: float = 0.4,
) -> list[dict]:
    """start〜end を 1 日ずつ読む。TDnet が消した日（404）は warn して飛ばす。"""
    out: list[dict] = []
    cur = start
    while cur <= end:
        try:
            out.extend(fetch_day(cur, get, sleep_sec))
        except HTTPError as e:
            if e.code != 404:
                raise
            print(f"warn list gone {cur.isoformat()}: TDnet keeps about a month", flush=True)
        cur += timedelta(days=1)
    return out
