"""jobs/_tdnet.py（TDnet の開示一覧）。ネットワークには出ない。"""
from __future__ import annotations

import io
import re
import sys
from contextlib import redirect_stdout
from datetime import date
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jobs import _tdnet as tdnet_list  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _row(hhmm: str, code: str, name: str, title: str, pdf: str, zip_name: str | None,
         tr: str = "<tr>") -> str:
    xbrl = (f'<div class="xbrl-mask"><div class="xbrl-button"><a class="style002" '
            f'href="{zip_name}">XBRL</a></div></div> ') if zip_name else " "
    return (f'{tr}\n<td class="oddnew-L kjTime" noWrap>{hhmm}</td>\n'
            f'<td class="oddnew-M kjCode" noWrap>{code}</td>\n'
            f'<td class="oddnew-M kjName" noWrap>{name}          </td>\n'
            f'<td class="oddnew-M kjTitle" align="left"><a href="{pdf}" target="_blank">{title}</a></td>\n'
            f'<td class="oddnew-M kjXbrl" noWrap align="center">{xbrl}</td>\n'
            f'<td class="oddnew-M kjPlace" noWrap align="left">東       </td>\n</tr>\n')


def _page(day: date, n_pages: int, rows: list[str]) -> bytes:
    pager = "".join(
        f"<div onClick=\"pagerLink('I_list_{p:03d}_{day:%Y%m%d}.html')\">{p}</div>"
        for p in range(1, n_pages + 1)
    )
    return (f'<html><body>{pager}<table id="main-list-table">\n'
            + "".join(rows) + "</table></body></html>").encode("utf-8")


def _no_sleep(*_a: object) -> None:
    return None


def test_parse_list_page() -> None:
    day = date(2026, 10, 30)
    text = _page(day, 3, [
        _row("15:30", "19440", "きんでん", "2027年３月期 決算短信", "140120261028557709.pdf",
             "081220261028557709.zip"),
        _row("15:30", "19440", "きんでん", "決算補足資料", "140120261028557710.pdf", None),
        # 表題変更の行: <tr class="hyodaiUpd"> で、旧表題を <s> で並べる
        _row("16:00", "42160", "旭有機材", "新表題 &amp; X</a><BR><s>旧表題</s>",
             "140120261027556253.pdf", "081220261027556253.zip", tr='<tr class="hyodaiUpd">'),
    ]).decode("utf-8")
    rows, pages = tdnet_list.parse_list_page(text, day)
    if pages != {1, 2, 3}:
        _fail(f"pages {pages}")
    if len(rows) != 3:
        _fail(f"every row incl. hyodaiUpd: {rows}")
    a, b, c = rows
    want_a = {
        "company_code": "19440", "company_name": "きんでん", "title": "2027年３月期 決算短信",
        "pubdate": "2026-10-30 15:30:00",
        "document_url": "https://www.release.tdnet.info/inbs/140120261028557709.pdf",
        "url_xbrl": "https://www.release.tdnet.info/inbs/081220261028557709.zip",
    }
    if a != want_a:
        _fail(f"row a {a}")
    if b["url_xbrl"] != "" or not b["document_url"].endswith("140120261028557710.pdf"):
        _fail(f"row without XBRL {b}")
    if c["title"] != "新表題 & X":
        _fail(f"title must drop struck old title: {c['title']!r}")
    empty, pages0 = tdnet_list.parse_list_page("<html>no table</html>", day)
    if empty or pages0 != {1}:
        _fail(f"empty day {empty} {pages0}")


def test_fetch_days_reads_all_pages_and_skips_gone_days() -> None:
    d1, d2 = date(2026, 9, 1), date(2026, 9, 2)
    urls: list[str] = []

    def fake_get(url: str) -> bytes:
        urls.append(url)
        if f"_{d1:%Y%m%d}.html" in url:
            raise HTTPError(url, 404, "Not Found", Message(), None)
        page = int(re.search(r"I_list_(\d{3})_", url).group(1))
        return _page(d2, 2, [_row("15:30", f"1000{page}", "x", "t", f"14010000000{page}.pdf", None)])

    buf = io.StringIO()
    with redirect_stdout(buf):
        rows = tdnet_list.fetch_days(d1, d2, fake_get, sleep_sec=0)
    want = [tdnet_list.list_url(d1), tdnet_list.list_url(d2), tdnet_list.list_url(d2, 2)]
    if urls != want:
        _fail(f"urls {urls}")
    if [r["company_code"] for r in rows] != ["10001", "10002"]:
        _fail(f"rows {rows}")
    if [r["pubdate"] for r in rows] != ["2026-09-02 15:30:00"] * 2:
        _fail(f"pubdate {rows}")
    if "warn list gone 2026-09-01" not in buf.getvalue():
        _fail(f"missing gone warn: {buf.getvalue()!r}")

    def boom(url: str) -> bytes:
        raise HTTPError(url, 500, "err", Message(), None)

    try:
        tdnet_list.fetch_days(d2, d2, boom, sleep_sec=0)
    except HTTPError:
        pass
    else:
        _fail("non-404 must raise")


def main() -> None:
    test_parse_list_page()
    test_fetch_days_reads_all_pages_and_skips_gone_days()
    print("ok: tdnet list")


if __name__ == "__main__":
    main()
