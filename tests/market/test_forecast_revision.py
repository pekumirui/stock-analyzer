"""決算速報一覧の通期営利予想の上方/下方と、既出期間の訂正。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='stock-analyzer-frev-')}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.config import EARNINGS_PARAMS as EP  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.db import upsert  # noqa: E402
from app.main import app  # noqa: E402
from app.market.analytics import forecast_revision, quarter_summary  # noqa: E402
from app.models import Statement, Stock  # noqa: E402

CODE = "72030"
FY = date(2026, 3, 31)
DOC = "2QFinancialStatements_Consolidated_JP"
REV_DOC = "EarnForecastRevision"


def _stmt(
    no: str,
    period: str,
    disclosed: date,
    *,
    sales: float = 100,
    op: float = 10,
    fy_sales: float | None = 400,
    fy_op: float | None = 40,
    fy_odp: float | None = 40,
    fy_np: float | None = 20,
    is_revision: bool = False,
    doc: str = DOC,
    fy_end: date = FY,
) -> Statement:
    return Statement(
        disclosure_no=no,
        code=CODE,
        disclosed_date=disclosed,
        disclosed_time="15:30:00",
        type_of_period=period,
        type_of_document=doc,
        fiscal_year_end=fy_end,
        net_sales=None if is_revision else sales * 1_000_000,
        operating_profit=None if is_revision else op * 1_000_000,
        ordinary_profit=None if is_revision else op * 1_000_000,
        profit=None if is_revision else op * 500_000,
        fy_net_sales=None if fy_sales is None else fy_sales * 1_000_000,
        fy_operating_profit=None if fy_op is None else fy_op * 1_000_000,
        fy_ordinary_profit=None if fy_odp is None else fy_odp * 1_000_000,
        fy_profit=None if fy_np is None else fy_np * 1_000_000,
        is_revision=is_revision,
    )


def test_up_and_down_from_operating_profit() -> None:
    q1 = _stmt("q1", "1Q", date(2025, 8, 1), fy_op=40)
    up = _stmt("q2u", "2Q", date(2025, 11, 1), fy_op=48)
    down = _stmt("q2d", "2Q", date(2025, 11, 1), fy_op=32)
    assert forecast_revision([q1], up) == {"direction": "up", "pct": 20.0}
    assert forecast_revision([q1], down) == {"direction": "down", "pct": -20.0}


def test_mixed_items_follow_operating_profit() -> None:
    q1 = _stmt("q1", "1Q", date(2025, 8, 1), fy_sales=400, fy_op=47, fy_np=29)
    q2 = _stmt("q2", "2Q", date(2025, 11, 1), fy_sales=470, fy_op=46, fy_np=29)
    # 売上は上方、営利は下方 → 営利に従う
    assert forecast_revision([q1], q2) == {"direction": "down", "pct": -2.1}


def test_undeterminable_is_none() -> None:
    q1 = _stmt("q1", "1Q", date(2025, 8, 1), fy_op=40)
    same = _stmt("q2", "2Q", date(2025, 11, 1), fy_op=40)
    tiny = _stmt("q2t", "2Q", date(2025, 11, 1), fy_op=40 + EP["revision_noise_th"] * 40 * 0.5)
    missing_cur = _stmt("q2n", "2Q", date(2025, 11, 1), fy_op=None)
    missing_prev = _stmt("q1n", "1Q", date(2025, 8, 1), fy_op=None)
    later = _stmt("q2", "2Q", date(2025, 11, 1), fy_op=48)
    zero_prev = _stmt("q1z", "1Q", date(2025, 8, 1), fy_op=0)
    assert forecast_revision([q1], same) is None
    assert forecast_revision([q1], tiny) is None
    assert forecast_revision([q1], missing_cur) is None
    assert forecast_revision([missing_prev], later) is None
    assert forecast_revision([], later) is None
    assert forecast_revision([zero_prev], later) is None


def test_loss_forecast_shrink_is_up() -> None:
    q1 = _stmt("q1", "1Q", date(2025, 8, 1), fy_op=-100)
    q2 = _stmt("q2", "2Q", date(2025, 11, 1), fy_op=-50)
    worse = _stmt("q2w", "2Q", date(2025, 11, 1), fy_op=-150)
    assert forecast_revision([q1], q2) == {"direction": "up", "pct": 50.0}
    assert forecast_revision([q1], worse) == {"direction": "down", "pct": -50.0}


def test_same_day_revision_doc_is_not_previous() -> None:
    q1 = _stmt("q1", "1Q", date(2025, 10, 14), fy_op=93)
    fs = _stmt("q2", "2Q", date(2026, 1, 13), fy_op=47)
    rev = _stmt("rev", "FY", date(2026, 1, 13), fy_op=47, is_revision=True, doc=REV_DOC)
    # 同日の EarnForecastRevision を直前にすると 47→47 で消える
    assert forecast_revision([q1, rev], fs) == {"direction": "down", "pct": -49.5}


def test_kept_forecast_after_prior_revision_is_not_marked() -> None:
    q1 = _stmt("q1", "1Q", date(2025, 10, 14), fy_op=93)
    q2 = _stmt("q2", "2Q", date(2026, 1, 13), fy_op=47)
    q3 = _stmt("q3", "3Q", date(2026, 4, 10), fy_op=47)
    assert forecast_revision([q1, q2], q3) is None


def test_other_fiscal_year_is_ignored() -> None:
    prev_year = _stmt("p1", "1Q", date(2024, 8, 1), fy_op=20, fy_end=date(2025, 3, 31))
    q1 = _stmt("q1", "1Q", date(2025, 8, 1), fy_op=40)
    assert forecast_revision([prev_year], q1) is None


def test_quarter_summary_exposes_forecast_revision() -> None:
    q1 = _stmt("q1", "1Q", date(2025, 8, 1), fy_op=40)
    q2 = _stmt("q2", "2Q", date(2025, 11, 1), fy_op=48)
    got = quarter_summary(None, q2, hist=[q1, q2])
    assert got["forecast_revision"] == {"direction": "up", "pct": 20.0}
    assert got["revision_pct"] == 20.0
    assert got["revised_up"] is True


def test_list_api_forecast_revision_and_restated() -> None:
    init_db()
    db = SessionLocal()
    try:
        upsert(db, Stock, [{"code": CODE, "name": "トヨタ自動車", "product_category": "011"}])
        rows = []
        for s in (
            _stmt("q1", "1Q", date(2025, 8, 1), fy_op=40),
            _stmt("q2", "2Q", date(2025, 11, 1), fy_op=48, sales=250, op=25),
            _stmt("q2b", "2Q", date(2025, 11, 10), fy_op=48, sales=260, op=26),
        ):
            rows.append({c.key: getattr(s, c.key) for c in Statement.__table__.columns
                         if getattr(s, c.key) is not None or c.key == "is_revision"})
        upsert(db, Statement, rows)
    finally:
        db.close()

    c = TestClient(app)
    up = c.get("/api/earnings", params={"date": "2025-11-01", "today": "2026-05-02"}).json()
    assert len(up["actuals"]) == 1
    assert up["actuals"][0]["disclosure_no"] == "q2"
    assert up["actuals"][0]["forecast_revision"] == {"direction": "up", "pct": 20.0}
    assert up["actuals"][0]["restated"] is False

    rest = c.get("/api/earnings", params={"date": "2025-11-10", "today": "2026-05-02"}).json()
    assert len(rest["actuals"]) == 1
    assert rest["actuals"][0]["disclosure_no"] == "q2b"
    assert rest["actuals"][0]["restated"] is True
    assert rest["actuals"][0]["forecast_revision"] is None

    first = c.get("/api/earnings", params={"date": "2025-08-01", "today": "2026-05-02"}).json()
    assert first["actuals"][0]["forecast_revision"] is None
    assert first["actuals"][0]["restated"] is False


def test_list_markup_has_direction_chips() -> None:
    js = (ROOT / "static" / "js" / "earnings.js").read_text(encoding="utf-8")
    assert "上方" in js and "下方" in js
    assert "qb-up" in js and "qb-dn" in js
    css = (ROOT / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".qb-up" in css and ".qb-dn" in css
