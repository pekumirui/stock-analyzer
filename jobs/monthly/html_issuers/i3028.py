"""アルペン。月次の売上表に、合計の出退店・店舗数だけ足す。"""
from __future__ import annotations

from jobs.monthly.html_issuers._page import Page
from jobs.monthly.html_monthly import _is_count_header

PAGE = Page(
    code='3028',
    table=None,
    unit=None,
    rows=('売上高', '全店', '既存店'),
)

_TOTAL_STORE = {
    "開店": "出店",
    "閉店": "退店",
    "店数": "店舗数",
    "合計 開店": "出店",
    "合計 閉店": "退店",
    "合計 店数": "店舗数",
}


def _keep_sales_and_total_stores(rows: list[dict]) -> list[dict]:
    """業態別の開店/閉店/店数は落とし、合計だけ 出店/退店/店舗数 にする。"""
    out: list[dict] = []
    seen: set[tuple[str, object]] = set()
    for row in rows:
        el = str(row.get("element") or "")
        renamed = _TOTAL_STORE.get(el)
        if renamed is not None:
            copied = dict(
                row,
                element=renamed,
                unit="number",
                values=dict(row.get("values") or {}),
            )
            key = (copied["element"], copied.get("unit"))
            if key in seen:
                continue
            seen.add(key)
            out.append(copied)
            continue
        if _is_count_header(el):
            continue
        key = (el, row.get("unit"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def parse(html: str, *, as_of: str | None = None) -> dict:
    payload = PAGE.parse(html, as_of=as_of)
    payload["rows"] = _keep_sales_and_total_stores(payload["rows"])
    return payload
