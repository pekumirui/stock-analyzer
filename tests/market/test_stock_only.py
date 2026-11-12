"""銘柄検索の候補と大量保有タブは株（011 / 021）だけ（KAB-447）。"""
from __future__ import annotations

from datetime import date, datetime

from app.db import SessionLocal
from app.models import LargeHolding, LargeHoldingReport, Stock

FETCHED = datetime(2026, 10, 28, 12, 0)  # noqa: DTZ001
DAY = date(2026, 10, 28)
CATS = {"13010": "011", "13020": "021", "13030": "012", "13040": "013", "13050": "014", "13060": "023"}


def _seed() -> None:
    db = SessionLocal()
    try:
        names = {"13010": "テスト内国株", "13020": "テスト外国株", "13030": "テスト出資証券",
                 "13040": "テストリート投資法人", "13050": "テストＥＴＦ", "13060": "テスト外国ＥＴＦ"}
        db.add_all(Stock(code=code, name=name, market="プライム") for code, name in names.items())
        docs = [*CATS, None]
        for i, code in enumerate(docs):
            doc_id = f"S100T{i:03d}"
            db.add(LargeHoldingReport(
                doc_id=doc_id, original_doc_id=doc_id, file_date=DAY, doc_type_code="350",
                form_code="010000", is_latest=True, withdrawn=False, parse_status="parsed",
                fetched_at=FETCHED, is_amendment=False, code=code,
                issuer_name="未上場テスト" if code is None else f"発行{code}",
                obligation_date=DAY, holding_ratio=0.06, shares_held=1000,
            ))
            db.add(LargeHolding(doc_id=doc_id, holder_no=1, edinet_code="E77777", name="テスト保有者",
                                holding_ratio=0.06, shares_held=1000))
        db.add(LargeHoldingReport(
            doc_id="S100T900", original_doc_id="S100T900", file_date=DAY, doc_type_code="350",
            form_code="010000", is_latest=True, withdrawn=False, parse_status="parsed",
            fetched_at=FETCHED, is_amendment=False, code="13040", issuer_name="発行13040",
            obligation_date=DAY, holding_ratio=0.07, shares_held=1000,
        ))
        db.add(LargeHolding(doc_id="S100T900", holder_no=1, edinet_code="E88888", name="リートだけ保有者",
                            holding_ratio=0.07, shares_held=1000))
        db.commit()
    finally:
        db.close()


def _set_categories(cats: dict[str, str | None]) -> None:
    db = SessionLocal()
    try:
        for code, cat in cats.items():
            db.get(Stock, code).product_category = cat
        db.commit()
    finally:
        db.close()


def _codes(items: list[dict]) -> set[str | None]:
    return {x["code"] for x in items}


def test_search_and_holders_show_stocks_only(c) -> None:
    _seed()
    every = {*CATS}

    assert {h["code"] for h in c.get("/api/stocks/search", params={"q": "テスト"}).json()} == every
    assert _codes(c.get("/api/holders/activity", params={"file_date": DAY.isoformat()}).json()["items"]) \
        == every | {None, "13040"}
    assert [x["edinet_code"] for x in c.get("/api/holders/search", params={"q": "リートだけ"}).json()["results"]] \
        == ["E88888"]

    _set_categories(CATS)
    stocks = {"13010", "13020"}
    assert {h["code"] for h in c.get("/api/stocks/search", params={"q": "テスト"}).json()} == stocks
    assert c.get("/api/stocks/search", params={"q": "1304"}).json() == []
    assert [h["code"] for h in c.get("/api/stocks/search", params={"q": "1302"}).json()] == ["13020"]

    day = c.get("/api/holders/activity", params={"file_date": DAY.isoformat()}).json()["items"]
    assert _codes(day) == stocks | {None}
    found = c.get("/api/holders/activity", params={"q": "テスト保有者"}).json()["items"]
    assert _codes(found) == stocks | {None}
    assert c.get("/api/holders/activity", params={"q": "1304"}).json()["items"] == []
    assert c.get("/api/holders/activity", params={"q": "リート"}).json()["items"] == []

    page = c.get("/api/holders/E77777", params={"today": "2026-10-29"}).json()
    assert {x["code"] for x in page["holdings"]} == stocks | {None}
    assert _codes(page["activity"]) == stocks | {None}
    assert c.get("/api/holders/E88888", params={"today": "2026-10-29"}).status_code == 404
    assert c.get("/api/holders/search", params={"q": "リートだけ"}).json()["results"] == []
    assert [x["edinet_code"] for x in c.get("/api/holders/search", params={"q": "テスト保有"}).json()["results"]] \
        == ["E77777"]
