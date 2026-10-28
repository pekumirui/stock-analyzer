"""ZIP と PDF の突き合わせヘルパ（KAB-173）。IR BANK も本番 stock.db も呼ばない。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from tools.tdnet_pdf.verify_financials import (
    Slot,
    align_statement_from_zip,
    close_enough,
    compare_maps,
    disclosure_no_of,
    fy_start,
    has_complete_dates,
    submitted_at_of,
)
from pathlib import Path


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def test_align_statement_keeps_twentieth_fy() -> None:
    st = SimpleNamespace(
        type_of_period="2Q",
        period_start=date(2026, 3, 31),
        period_end=date(2026, 9, 30),
        fiscal_year_start=date(2026, 3, 31),
        fiscal_year_end=date(2027, 3, 31),
    )
    filing = SimpleNamespace(fiscal_year_end=date(2026, 9, 20))
    align_statement_from_zip(st, filing)
    if st.period_end != date(2026, 9, 20):
        _fail(f"period_end {st.period_end}")
    if st.fiscal_year_end != date(2027, 3, 20):
        _fail(f"fy_end {st.fiscal_year_end}")
    if st.fiscal_year_start != date(2026, 3, 21):
        _fail(f"fy_start {st.fiscal_year_start}")
    if st.period_start != st.fiscal_year_start:
        _fail(f"period_start {st.period_start}")


def test_align_statement_month_end_q1() -> None:
    st = SimpleNamespace(type_of_period="1Q")
    filing = SimpleNamespace(fiscal_year_end=date(2026, 6, 30))
    align_statement_from_zip(st, filing)
    if (st.period_start, st.period_end, st.fiscal_year_start, st.fiscal_year_end) != (
        date(2026, 4, 1), date(2026, 6, 30), date(2026, 4, 1), date(2027, 3, 31),
    ):
        _fail(repr((st.period_start, st.period_end, st.fiscal_year_start, st.fiscal_year_end)))


def test_compare_maps_counts_q1_cf_as_expected() -> None:
    key = Slot("operating_cf", 1, "2026-06-30", "actual", "point", "Q1")
    sales = Slot("net_sales", 1, "2026-06-30", "actual", "point", "Q1")
    counts, details = compare_maps(
        {key: Decimal("1"), sales: Decimal("10")},
        {sales: Decimal("10")},
        period="Q1",
    )
    if counts["expected_zip_only"] != 1 or counts["matched"] != 1 or counts["zip_only"] != 0:
        _fail(repr(counts))
    if details[0]["status"] not in {"expected_zip_only", "matched"}:
        _fail(details)


def test_disclosure_no_from_zip_name(tmp_path: Path) -> None:
    path = tmp_path / "20261020_5933_title_081220261020553439.zip"
    if disclosure_no_of(path) != "20261020553439":
        _fail(disclosure_no_of(path))


def test_close_enough_dps_and_ratio() -> None:
    if not close_enough("dps", Decimal("40"), Decimal("40.02")):
        _fail("dps tol")
    if close_enough("dps", Decimal("40"), Decimal("40000000")):
        _fail("dps millions")
    if not close_enough("equity_ratio", Decimal("0.2210"), Decimal("0.2214")):
        _fail("ratio tol")


def test_fy_start_from_twentieth() -> None:
    if fy_start(date(2027, 3, 20)) != date(2026, 3, 21):
        _fail(str(fy_start(date(2027, 3, 20))))


def test_submitted_at_prefers_statement_not_disclosure_no(tmp_path: Path) -> None:
    """KAB-219: 135A の ZIP 名は 20261014、開示番号の先頭は 20260831。"""
    zip_path = tmp_path / "20261014_135A_第2四半期決算短信_081220260831528808.zip"
    zip_path.write_bytes(b"x")
    st = SimpleNamespace(disclosed_date=date(2026, 10, 14), disclosed_time="16:15:00")
    got = submitted_at_of(statement=st, zip_path=zip_path)
    if got != "2026-10-14 16:15:00":
        _fail(got)
    from_name = submitted_at_of(statement=None, zip_path=zip_path)
    if from_name != "2026-10-14 15:00:00":
        _fail(from_name)
    if "2026-08-31" in from_name:
        _fail("disclosure_no date leaked")


def test_has_complete_dates_skips_zip_overwrite() -> None:
    """KAB-219: J-Quants の日付がある statements は ZIP の誤った期末で上書きしない。"""
    complete = SimpleNamespace(
        period_start=date(2026, 3, 1),
        period_end=date(2026, 8, 31),
        fiscal_year_start=date(2026, 3, 1),
        fiscal_year_end=date(2027, 2, 28),
    )
    if not has_complete_dates(complete):
        _fail("complete")
    if has_complete_dates(SimpleNamespace(period_start=None, period_end=None,
                                          fiscal_year_start=None, fiscal_year_end=None)):
        _fail("empty")
    if has_complete_dates(None):
        _fail("none")
