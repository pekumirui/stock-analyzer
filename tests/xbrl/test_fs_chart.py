from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.xbrl.fs_chart import chart_series

M = 1_000_000


def col(end: str, pt: str, values: dict[str, float], consolidated: bool = True) -> SimpleNamespace:
    day = date.fromisoformat(end)
    return SimpleNamespace(primary=SimpleNamespace(period_type=pt, period_end=day), period_end=day,
                           values={k: v * M for k, v in values.items()}, consolidated=consolidated)


def test_pl_quarter_is_cumulative_minus_previous_quarter():
    run = [
        col("2025-06-30", "Q1", {"NetSales": 100, "OperatingIncome": 10}),
        col("2025-09-30", "Q2", {"NetSales": 250, "OperatingIncome": 30}),
        col("2025-12-31", "Q3", {"NetSales": 360, "OperatingIncome": 35}),
        col("2026-03-31", "FY", {"NetSales": 500, "OperatingIncome": 50}),
    ]
    out = chart_series(run, run[1:], "PL", {})
    assert out["series"]["net_sales"] == [250 * M, 360 * M, 500 * M]
    assert out["quarter"]["net_sales"] == [150 * M, 110 * M, 140 * M]
    assert out["quarter"]["operating_income"] == [20 * M, 5 * M, 15 * M]


def test_pl_quarter_is_blank_without_the_previous_quarter():
    run = [
        col("2025-09-30", "Q2", {"NetSales": 250}),
        col("2026-03-31", "FY", {"NetSales": 500}),
        col("2026-06-30", "Q1", {"NetSales": 130}),
    ]
    out = chart_series(run, run, "PL", {})
    assert out["quarter"]["net_sales"] == [None, None, 130 * M]


def test_pl_quarter_is_blank_across_consolidation_change():
    run = [col("2025-06-30", "Q1", {"NetSales": 100}, consolidated=False), col("2025-09-30", "Q2", {"NetSales": 250})]
    assert chart_series(run, run, "PL", {})["quarter"]["net_sales"] == [100 * M, None]


def test_net_income_prefers_owners_of_parent_and_falls_back_to_profit_loss():
    run = [
        col("2025-03-31", "FY", {"ProfitLoss": 90}),
        col("2026-03-31", "FY", {"ProfitLoss": 120, "ProfitLossAttributableToOwnersOfParent": 100}),
    ]
    assert chart_series(run, run, "PL", {})["series"]["net_income"] == [90 * M, 100 * M]


def test_net_sales_falls_back_to_company_total_revenue_label():
    run = [col("2026-03-31", "FY", {"ext:SalesOfProductsIFRS": 45, "ext:TotalNetRevenuesIFRS": 50})]
    labels = {"ext:SalesOfProductsIFRS": "商品・製品売上収益", "ext:TotalNetRevenuesIFRS": "営業収益合計"}
    assert chart_series(run, run, "PL", labels)["series"]["net_sales"] == [50 * M]


def test_ifrs_bs_and_cf_items():
    bs = [col("2026-03-31", "FY", {"CurrentAssetsIFRS": 3, "NonCurrentAssetsIFRS": 5, "AssetsIFRS": 8,
                                    "TotalCurrentLiabilitiesIFRS": 2, "NonCurrentLabilitiesIFRS": 2, "EquityIFRS": 4})]
    s = chart_series(bs, bs, "BS", {})["series"]
    assert [s[k][0] / M for k in ("current_assets", "noncurrent_assets", "current_liabilities",
                                  "noncurrent_liabilities", "net_assets")] == [3, 5, 2, 2, 4]
    cf = [col("2026-03-31", "FY", {"NetCashProvidedByUsedInOperatingActivitiesIFRS": 10,
                                    "NetCashProvidedByUsedInInvestingActivitiesIFRS": -4,
                                    "NetCashProvidedByUsedInFinancingActivitiesIFRS": -3,
                                    "CashAndCashEquivalentsIFRS": 20})]
    out = chart_series(cf, cf, "CF", {})
    assert "quarter" not in out
    assert [out["series"][k][0] / M for k in ("operating_cf", "investing_cf", "financing_cf", "cash_end")] == [10, -4, -3, 20]
