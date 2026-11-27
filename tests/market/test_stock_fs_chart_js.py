from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
M = 1_000_000

_NODE = r"""
const c = require(process.argv[1]);
const d = JSON.parse(process.argv[2]);
const model = (st, opt) => c.fsChartModel({...d[st], statement: st}, opt);
const pick = m => m && {periods: m.periods.map(p => `${p.label} ${p.sub}`), stacks: m.stacks,
  series: Object.fromEntries(m.series.map(s => [s.key, {values: s.values, axis: s.axis, unit: s.unit, kind: s.kind}]))};
const pl = model("PL", {quarter: true});
console.log(JSON.stringify({
  plq: pick(pl),
  plcr: pick(model("PL", {quarter: false, rate: true})),
  bs: pick(model("BS", {})),
  bsr: pick(model("BS", {rate: true})),
  bank: pick(c.fsChartModel({...d.BANK, statement: "BS"}, {})),
  cf: pick(model("CF", {})),
  none: model("PL", {}) === null ? null : "x",
  empty: c.fsChartModel({statement: "PL", periods: [], chart: {series: {}}}),
  axes: c.fsChartAxes([[-30, 100]]),
  axes2: c.fsChartAxes([[0, 480], [-20, 90]]),
  svg: c.fsChartSvg(pl, 600).length > 0,
  tip: c.fsChartTip(pl, 1),
  fmt: [c.fsChartFmt(1234567890, "money"), c.fsChartFmt(12.345, "pct"), c.fsChartFmt(null, "money")],
}));
"""


def _p(label: str, pt: str) -> dict:
    return {"label": label, "period_type": pt}


DATA = {
    "PL": {
        "periods": [_p("25.06", "Q1"), _p("25.09", "Q2"), _p("25.12", "Q3")],
        "chart": {
            "series": {"net_sales": [100 * M, 250 * M, 400 * M], "gross_profit": [None, None, None],
                       "operating_income": [10 * M, 30 * M, 20 * M], "ordinary_income": [None, None, None],
                       "net_income": [5 * M, 15 * M, 10 * M]},
            "quarter": {"net_sales": [100 * M, 150 * M, None], "gross_profit": [None, None, None],
                        "operating_income": [10 * M, 20 * M, -10 * M], "ordinary_income": [None, None, None],
                        "net_income": [5 * M, 10 * M, None]},
        },
    },
    "BS": {
        "periods": [_p("25.03", "FY"), _p("25.06", "Q1")],
        "chart": {"series": {"current_assets": [60 * M, 70 * M], "noncurrent_assets": [40 * M, 30 * M],
                             "total_assets": [100 * M, 100 * M], "current_liabilities": [30 * M, 20 * M],
                             "noncurrent_liabilities": [20 * M, 30 * M], "total_liabilities": [50 * M, 50 * M],
                             "net_assets": [50 * M, 50 * M]}},
    },
    "BANK": {
        "periods": [_p("25.03", "FY")],
        "chart": {"series": {"current_assets": [None], "noncurrent_assets": [None], "total_assets": [100 * M],
                             "current_liabilities": [None], "noncurrent_liabilities": [None],
                             "total_liabilities": [90 * M], "net_assets": [10 * M]}},
    },
    "CF": {
        "periods": [_p("25.03", "FY"), _p("25.06", "Q1"), _p("25.09", "Q2")],
        "chart": {"series": {"operating_cf": [80 * M, None, 30 * M], "investing_cf": [-50 * M, None, None],
                             "financing_cf": [-10 * M, None, -5 * M], "cash_end": [100 * M, None, 120 * M]}},
    },
}


def _run() -> dict:
    out = subprocess.run(
        ["node", "-e", _NODE, str(ROOT / "static/js/stock-fs-chart.js"), json.dumps(DATA)],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def test_pl_quarter_bars_sales_left_and_profit_lines_right_without_empty_items():
    r = _run()["plq"]
    assert r["periods"] == ["25.06 1Q", "25.09 2Q", "25.12 3Q"]
    assert r["stacks"] == [["net_sales"]]
    assert list(r["series"]) == ["net_sales", "operating_income", "net_income"]
    assert r["series"]["net_sales"] == {"values": [100 * M, 150 * M, None], "axis": "left", "unit": "money", "kind": "bar"}
    assert r["series"]["operating_income"]["axis"] == "right"
    assert r["series"]["operating_income"]["values"] == [10 * M, 20 * M, -10 * M]


def test_pl_cumulative_rate_divides_each_profit_by_sales():
    s = _run()["plcr"]["series"]
    assert s["net_sales"]["values"] == [100 * M, 250 * M, 400 * M]
    assert s["operating_income"]["unit"] == "pct"
    assert s["operating_income"]["values"] == [10, 12, 5]
    assert s["net_income"]["values"] == [5, 6, 2.5]


def test_bs_stacks_assets_against_liabilities_and_equity():
    r = _run()
    assert r["bs"]["stacks"] == [["noncurrent_assets", "current_assets"],
                                 ["net_assets", "noncurrent_liabilities", "current_liabilities"]]
    assert list(r["bs"]["series"]) == ["current_assets", "noncurrent_assets", "current_liabilities",
                                       "noncurrent_liabilities", "net_assets"]
    assert r["bsr"]["series"]["current_assets"]["values"] == [60, 70]
    assert r["bsr"]["series"]["net_assets"]["unit"] == "pct"


def test_bs_without_current_split_uses_totals():
    assert _run()["bank"]["stacks"] == [["total_assets"], ["net_assets", "total_liabilities"]]


def test_cf_keeps_only_periods_with_cash_flows_and_adds_free_cash_flow():
    r = _run()["cf"]
    assert r["periods"] == ["25.03 通期", "25.09 2Q"]
    assert r["series"]["free_cf"] == {"values": [30 * M, None], "axis": "left", "unit": "money", "kind": "line"}
    assert r["stacks"] == [["operating_cf"], ["investing_cf"], ["financing_cf"], ["cash_end"]]


def test_axes_share_the_zero_line_and_cover_the_values():
    r = _run()
    assert r["empty"] is None
    for axes, extents in ((r["axes"], [[-30, 100]]), (r["axes2"], [[0, 480], [-20, 90]])):
        assert len({a["n"] for a in axes}) == 1
        assert len({-a["min"] / a["step"] for a in axes}) == 1
        for a, (lo, hi) in zip(axes, extents):
            assert a["min"] <= lo and a["max"] >= hi


def test_tip_and_format():
    r = _run()
    assert r["svg"] is True
    assert "25.09 2Q" in r["tip"] and "150 百万円" in r["tip"]
    assert r["fmt"] == ["1,235 百万円", "12.3%", "–"]
