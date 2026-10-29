"""大量保有の保有者画面と銘柄の株主タブ（KAB-267）。"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from app.db import SessionLocal
from app.holders import classify_move
from app.models import DailyPrice, EdinetBuyback, EdinetBuybackResolution, LargeHolding, LargeHoldingReport, Stock

ROOT = Path(__file__).resolve().parents[2]
FETCHED = datetime(2026, 10, 28, 12, 0)  # noqa: DTZ001
D = date.fromisoformat
_SEEDED = False


def _rep(doc_id: str, **kw) -> LargeHoldingReport:
    row = {
        "doc_id": doc_id, "file_date": D("2026-10-20"), "doc_type_code": "350", "form_code": "010002",
        "original_doc_id": doc_id, "is_latest": True, "withdrawn": False, "parse_status": "parsed",
        "fetched_at": FETCHED, "is_amendment": False,
    }
    row.update(kw)
    return LargeHoldingReport(**row)


def _h(doc_id: str, no: int = 1, **kw) -> LargeHolding:
    row = {"doc_id": doc_id, "holder_no": no, "edinet_code": "E09096", "name": "ブラックロック・ジャパン株式会社"}
    row.update(kw)
    return LargeHolding(**row)


def _seed() -> None:
    global _SEEDED
    if _SEEDED:
        return
    db = SessionLocal()
    try:
        db.add_all([
            Stock(code="18020", name="大林組", market="プライム", sector33="建設業"),
            Stock(code="22220", name="分割工業", market="プライム", sector33="金属製品"),
            Stock(code="33330", name="退出工業", market="スタンダード", sector33="化学"),
        ])
        db.add_all([
            DailyPrice(code="18020", date=D("2026-10-22"), close=1800.0, adj_close=1800.0, adj_factor=1.0),
            DailyPrice(code="18020", date=D("2026-10-28"), close=1850.0, adj_close=1850.0, adj_factor=1.0),
            DailyPrice(code="22220", date=D("2026-09-01"), close=1000.0, adj_close=500.0, adj_factor=0.5),
            DailyPrice(code="22220", date=D("2026-10-28"), close=510.0, adj_close=510.0, adj_factor=1.0),
            DailyPrice(code="33330", date=D("2026-10-28"), close=800.0, adj_close=800.0, adj_factor=1.0),
        ])
        db.add_all([
            _rep("S100AAAA", filer_edinet_code="E09096", filer_name="ブラックロック・ジャパン株式会社",
                 code="18020", issuer_name="株式会社大林組", form_code="010000",
                 obligation_date=D("2026-03-02"), file_date=D("2026-03-08"),
                 holding_ratio=0.018, holding_ratio_prev=None, shares_held=12_000_000),
            _rep("S100BBBB", filer_edinet_code="E09096", filer_name="ブラックロック・ジャパン株式会社",
                 code="18020", issuer_name="株式会社大林組",
                 obligation_date=D("2026-10-22"), file_date=D("2026-10-28"),
                 holding_ratio=0.0202, holding_ratio_prev=0.018, shares_held=14_008_800),
            _rep("S100CCCC", filer_edinet_code="E09096", filer_name="ブラックロック・ジャパン株式会社",
                 code="22220", issuer_name="分割工業",
                 obligation_date=D("2026-09-01"), file_date=D("2026-09-08"),
                 holding_ratio=0.06, holding_ratio_prev=0.055, shares_held=1_000),
            _rep("S100DDDD", filer_edinet_code="E11111", filer_name="退出ファンド",
                 code="33330", issuer_name="退出工業",
                 obligation_date=D("2026-10-10"), file_date=D("2026-10-15"),
                 holding_ratio=0.04, holding_ratio_prev=0.06, shares_held=100_000),
            _rep("S100EEEE", filer_edinet_code="E25156", filer_name="インテグラル・グループ株式会社",
                 code="18020", issuer_name="株式会社大林組",
                 obligation_date=D("2027-10-01"), file_date=D("2026-10-08"),
                 holding_ratio=0.1877, holding_ratio_prev=0.1877, shares_held=1_000_000),
            _rep("S100FFFF", filer_edinet_code="E09096", filer_name="旧名アセット",
                 code="18020", issuer_name="株式会社大林組", is_latest=False,
                 obligation_date=D("2026-01-01"), file_date=D("2026-01-05"),
                 holding_ratio=0.01, shares_held=1),
        ])
        db.add_all([
            _h("S100AAAA", shares_held=12_000_000, holding_ratio=0.018, holding_ratio_prev=None,
               purpose="純投資（顧客資産）", total_funds=20_000_000_000),
            _h("S100BBBB", shares_held=14_008_800, holding_ratio=0.0202, holding_ratio_prev=0.018,
               purpose="純投資（顧客資産）", total_funds=26_815_314_000),
            _h("S100CCCC", shares_held=1_000, holding_ratio=0.06, holding_ratio_prev=0.055,
               purpose="純投資", total_funds=500_000),
            _h("S100DDDD", edinet_code="E11111", name="退出ファンド",
               shares_held=100_000, holding_ratio=0.04, holding_ratio_prev=0.06,
               purpose="純投資", total_funds=80_000_000),
            _h("S100EEEE", edinet_code="E25156", name="インテグラル・グループ株式会社",
               shares_held=1_000_000, holding_ratio=0.1877, holding_ratio_prev=0.1877,
               purpose="支配", total_funds=0),
            _h("S100FFFF", name="旧名アセット", shares_held=1, holding_ratio=0.01, total_funds=1),
            _h("S100BBBB", no=2, edinet_code=None, name="コード無し共同保有",
               shares_held=100, holding_ratio=0.001),
        ])
        db.add(EdinetBuyback(
            doc_id="S100BUY1", file_date=D("2026-10-01"), doc_type_code="220", code="18020",
            edinet_code="E18020", filer_name="大林組", report_month="2026-09",
            issued_shares=20_000_000, treasury_shares=1_200_000,
            original_doc_id="S100BUY1", is_latest=True, withdrawn=False,
            parse_status="parsed", fetched_at=FETCHED, is_amendment=False,
        ))
        db.add(EdinetBuybackResolution(
            doc_id="S100BUY1", resolution_no=1, kind="board", resolution_date=D("2026-05-12"),
            acquire_from=D("2026-05-13"), acquire_to=D("2027-03-31"),
            limit_shares=500_000, cumulative_shares=264_500, progress_shares=0.529,
        ))
        db.commit()
        _SEEDED = True
    finally:
        db.close()


def test_classify_move() -> None:
    assert classify_move(0.06, None, "010000") == "new"
    assert classify_move(0.02, None, "010000") == "new"
    assert classify_move(0.064, 0.052, "010002") == "buy"
    assert classify_move(0.05, 0.06, "010002") == "sell"
    assert classify_move(0.04, 0.06, "010002") == "below"
    assert classify_move(0.1877, 0.1877, "010002") is None


def test_search_and_holder_page(c) -> None:
    _seed()
    empty = c.get("/api/holders/search?q=").json()
    assert empty["results"] == []
    one = c.get("/api/holders/search?q=ブ").json()
    assert one["results"] == []
    d = c.get("/api/holders/search?q=ブラック").json()
    assert [x["edinet_code"] for x in d["results"]] == ["E09096"]
    assert d["results"][0]["name"] == "ブラックロック・ジャパン株式会社"
    exact = c.get("/api/holders/search?q=E09096").json()
    assert exact["results"][0]["edinet_code"] == "E09096"
    assert all(x["edinet_code"] for x in d["results"])

    assert c.get("/api/holders/xxxx").status_code == 400
    assert c.get("/api/holders/E00000").status_code == 404

    p = c.get("/api/holders/E09096?today=2026-10-29").json()
    assert p["edinet_code"] == "E09096"
    codes = [x["code"] for x in p["holdings"]]
    assert codes[0] == "22220" and codes[-1] == "18020"
    oak = next(x for x in p["holdings"] if x["code"] == "18020")
    assert oak["holding_ratio"] == 0.0202 and oak["shares_held"] == 14_008_800
    assert oak["market_value"] == 1850.0 * 14_008_800
    assert abs(oak["avg_cost"] - 26_815_314_000 / 14_008_800) < 1e-6
    split = next(x for x in p["holdings"] if x["code"] == "22220")
    assert split["market_value"] == 510.0 * 1000
    assert split["avg_cost"] is None and split["unrealized"] is None
    assert any(a["kind"] == "buy" and a["code"] == "18020" for a in p["activity"])
    assert [h["date"] for h in oak["history"]] == ["2026-03-02", "2026-10-22"]


def test_activity_future_date_and_kinds(c) -> None:
    _seed()
    d = c.get("/api/holders/activity?today=2026-10-29&limit=80").json()
    kinds = {(x["edinet_code"], x["code"], x["kind"]) for x in d["items"]}
    assert ("E09096", "18020", "buy") in kinds or ("E09096", "18020", "new") in kinds
    assert ("E11111", "33330", "below") in kinds
    # 割合が変わらない未来日付の行はフィードに出さない。出る場合は提出日順で、日付は報告義務発生日のまま
    future = [x for x in d["items"] if x["obligation_date"] == "2027-10-01"]
    assert future == []
    same = c.get("/api/holders/E25156?today=2026-10-29").json()
    assert same["holdings"][0]["obligation_date"] == "2027-10-01"
    assert same["holdings"][0]["file_date"] == "2026-10-08"
    assert same["holdings"][0]["avg_cost"] is None  # total_funds 0


def test_stock_holders_and_treasury(c) -> None:
    _seed()
    assert c.get("/api/stock/0000/holders").status_code == 404
    d = c.get("/api/stock/1802/holders?today=2026-10-29").json()
    assert d["code"] == "18020"
    names = [x["filer_name"] for x in d["large_holders"]]
    assert names[0] == "インテグラル・グループ株式会社"
    br = next(x for x in d["large_holders"] if x["filer_edinet_code"] == "E09096")
    assert br["holding_ratio"] == 0.0202
    assert br["news_id"] == "lS100BBBB"
    assert br["purpose"] == "純投資（顧客資産）"
    t = d["treasury"]
    assert t["report_month"] == "2026-09"
    assert t["issued_shares"] == 20_000_000 and t["treasury_shares"] == 1_200_000
    assert abs(t["treasury_ratio"] - 0.06) < 1e-9
    assert t["active"][0]["acquire_to"] == "2027-03-31"
    expired = c.get("/api/stock/1802/holders?today=2027-04-01").json()
    assert expired["treasury"]["active"] == []


def test_spa_holders_tab() -> None:
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    tabs = (ROOT / "static/js/tabs.js").read_text(encoding="utf-8")
    js = (ROOT / "static/js/holders.js").read_text(encoding="utf-8")
    stock = (ROOT / "static/js/stock.js").read_text(encoding="utf-8")
    assert 'data-tab="holders"' in html and 'id="tab-holders"' in html
    assert 'id="hd-sym"' in html and 'id="hd-feed"' in html and 'id="hd-holdings"' in html
    assert ">← 保有者<" in html
    assert 'id="st-holders-large"' in html and 'id="st-holders-chart"' in html
    assert '"holders"' in tabs.split("const TABS")[1].split(";")[0]
    assert "EDINET_RE" in tabs
    assert "/api/holders/search" in js and "/api/stock/${code}/holders" in js
    assert "準備中" not in stock
    assert "renderStockHoldersTab" in stock
    # PTS はランキングの中（KAB-265）。保有者はナビ末尾
    assert 'data-tab="pts"' not in html and 'id="tab-pts"' not in html
    assert 'data-rksub="pts"' in html
    assert html.index('data-tab="ranking"') < html.index('data-tab="holders"')
