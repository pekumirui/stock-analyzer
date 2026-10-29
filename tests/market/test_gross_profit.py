"""決算速報の売上総利益（KAB-278）。statements の開示ごとに xbrl_financials の gross_profit を選ぶ。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='stock-analyzer-gp-')}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Statement, Stock, XbrlFiling, XbrlFinancial, XbrlStdItem  # noqa: E402
from app.xbrl.gross_profit import gross_profit_map  # noqa: E402

CODE = "41230"
FY25, FY26 = date(2025, 3, 31), date(2026, 3, 31)
M = 1_000_000


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _stmt(no: str, period: str, fy_end: date, start: date, end: date, disclosed: date, sales: float,
          doc: str = "FinancialStatements_Consolidated_JP") -> Statement:
    return Statement(disclosure_no=no, code=CODE, disclosed_date=disclosed, disclosed_time="15:00",
                     type_of_period=period, type_of_document=period + doc,
                     fiscal_year_start=date(fy_end.year - 1, 4, 1), fiscal_year_end=fy_end,
                     period_start=start, period_end=end,
                     net_sales=sales * M, operating_profit=sales * M / 10, ordinary_profit=sales * M / 10,
                     profit=sales * M / 20)


# 前期 1Q・2Q と当期 1Q・2Q。当期 2Q には訂正短信も出た
STATEMENTS = [
    _stmt("20240809000001", "1Q", FY25, date(2024, 4, 1), date(2024, 6, 30), date(2024, 8, 9), 100),
    _stmt("20241108000001", "2Q", FY25, date(2024, 4, 1), date(2024, 9, 30), date(2024, 11, 8), 210),
    _stmt("20250808000001", "1Q", FY26, date(2025, 4, 1), date(2025, 6, 30), date(2025, 8, 8), 120),
    _stmt("20251107000001", "2Q", FY26, date(2025, 4, 1), date(2025, 9, 30), date(2025, 11, 7), 250),
    _stmt("20251120000001", "2Q", FY26, date(2025, 4, 1), date(2025, 9, 30), date(2025, 11, 20), 250),
]


def _seed(db) -> None:
    db.merge(Stock(code=CODE, name="テスト"))
    for s in STATEMENTS:
        db.merge(s)
    gp = XbrlStdItem(code="gross_profit", statement="PL", label_ja="売上総利益")
    db.add(gp)
    db.flush()

    filings: dict[tuple[str, str], XbrlFiling] = {}

    def put(source, doc_id, at: date, start, end, value, *, consolidated=True):
        ts = datetime.combine(at, time(15))
        f = filings.get((source, doc_id))
        if f is None:
            f = XbrlFiling(code=CODE, source=source, doc_id=doc_id, doc_type="quarterly", period_type="Q2",
                           submitted_at=ts, raw_path=doc_id, parse_status="parsed", created_at=ts)
            db.add(f)
            db.flush()
            filings[(source, doc_id)] = f
        db.add(XbrlFinancial(code=CODE, filing_id=f.id, std_item_id=gp.id, is_consolidated=consolidated,
                             period_type="Q2", period_start=start, period_end=end, value=value * M,
                             value_kind="actual", generated_at=ts))

    # 前期 1Q: PDF の短信だけ
    put("tdnet_pdf", "20240809000001", date(2024, 8, 9), date(2024, 4, 1), date(2024, 6, 30), 40)
    # 前期 2Q: 短信が無く EDINET（半期報）だけ。訂正の半期報は後のほう。期首の違う値（別の書類）は使わない
    put("edinet", "S100AAAA", date(2024, 11, 14), date(2024, 4, 1), date(2024, 9, 30), 85)
    put("edinet", "S100ZZZZ", date(2024, 11, 1), date(2024, 7, 1), date(2024, 9, 30), 45)
    put("edinet", "S100BBBB", date(2025, 1, 20), date(2024, 4, 1), date(2024, 9, 30), 86)
    # 当期 1Q: ZIP。単体の値は使わない
    put("tdnet", "081220250808000001", date(2025, 8, 8), date(2025, 4, 1), date(2025, 6, 30), 50)
    put("tdnet", "081220250808000001", date(2025, 8, 8), date(2025, 4, 1), date(2025, 6, 30), 7,
        consolidated=False)
    # 当期 2Q: 元の短信は PDF、訂正短信には値が無い。半期報（EDINET）もある
    put("tdnet_pdf", "20251107000001", date(2025, 11, 7), date(2025, 4, 1), date(2025, 9, 30), 105)
    put("edinet", "S100CCCC", date(2025, 11, 13), date(2025, 4, 1), date(2025, 9, 30), 104)


def test_pick() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(XbrlStdItem).filter_by(code="gross_profit").count() == 0:
            _seed(db)
            db.commit()
        got = gross_profit_map(db, db.query(Statement).filter_by(code=CODE).all())
    finally:
        db.close()
    want = {
        "20240809000001": 40 * M,   # その開示の PDF
        "20241108000001": 85 * M,   # 短信が無い → EDINET の最初の書類
        "20250808000001": 50 * M,   # その開示の ZIP（連結・期首から）
        "20251107000001": 105 * M,  # その開示の PDF。EDINET の 104 より先
        "20251120000001": 105 * M,  # 訂正短信に値が無い → 同じ期の元の短信
    }
    if got != want:
        _fail(got)


def test_detail_api() -> None:
    test_pick()
    c = TestClient(app)
    d = c.get(f"/api/earnings/detail/{CODE}", params={"disclosure_no": "20251107000001"}).json()
    cur = next(r for r in d["cumulative"] if r["kind"] == "actual_current")
    if cur["values"]["gross_profit"] != 105 * M or cur["growth"]["gross_profit"]["pct"] != 23.5:
        _fail(cur)  # 105 / 85 - 1
    fc = [r for r in d["cumulative"] if r["kind"].startswith("forecast")]
    if any(r["values"].get("gross_profit") is not None for r in fc):
        _fail(fc)
    q2 = d["quarterly"][-1]
    if q2["values"]["gross_profit"] != 55 * M or q2["growth"]["gross_profit"]["pct"] != 22.2:
        _fail(q2)  # Q単独 105 - 50 = 55、前年 85 - 40 = 45
    if "gross_profit" in (d["progress"][-1].get("progress") or {}):
        _fail(d["progress"][-1])

    rows = c.get("/api/earnings", params={"date": "2025-11-07", "today": "2025-11-07"}).json()["actuals"]
    row = next(r for r in rows if r["code"] == CODE)
    if row["cumulative"]["gross_profit"] != 105 * M or row["single"]["gross_profit"] != 55 * M:
        _fail(row)
    if row["yoy"]["gross_profit"]["pct"] != 23.5 or row["qoq"]["gross_profit"]["pct"] != 10.0:
        _fail(row)  # QoQ: 55 / 50 - 1


def main() -> None:
    init_db()
    test_pick()
    test_detail_api()
    print("ok: gross_profit")


if __name__ == "__main__":
    main()
