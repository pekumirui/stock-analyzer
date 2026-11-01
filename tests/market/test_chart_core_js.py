"""株価チャートの足と指標（KAB-300。static/js/chart-core.js）。週足・月足・移動平均はブラウザで作るので node で関数を呼ぶ。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

_SCRIPT = r"""
const c = require(process.argv[1]);
const daily = {
  t:  ["2026-08-17", "2026-08-18", "2026-08-21", "2026-08-24", "2026-08-31", "2026-09-01"],
  o:  [2980, 2990, 3000, 3010, 3100, 3200],
  h:  [2990, 3010, 3006, 3050, 3150, 3250],
  l:  [2970, 2985, 2995, 3000, 3090, 3190],
  c:  [2985, 3000, 3005, 3040, 3120, 3210],
  v:  [800, 1000, 0, 1200, 500, 700],
  tv: [10, 20, 30, 40, 50, 60],
};
const topix = {t: ["2026-08-17", "2026-08-21", "2026-08-24"], c: [100, 110, 100]};
const many = Array.from({length: 25}, (_, i) => 100);
many[22] = 200; many[23] = 199;
const out = {
  week: c.chartBars(daily, "W"),
  month: c.chartBars(daily, "M"),
  // 年をまたぐ ISO 週（2026-12-31 木 と 2027-01-01 金 は同じ週）
  isoWeek: [c.chartWeekKey("2026-12-28"), c.chartWeekKey("2027-01-03"), c.chartWeekKey("2027-01-04")],
  sma: c.chartSma([1, 2, 3, 4], 3),
  maLen: [c.chartMaLen(50, "D"), c.chartMaLen(50, "W"), c.chartMaLen(50, "M"), c.chartMaLen(10, "M")],
  mas: c.chartParseMas(" 50, 150,x,0,50 ,200"),
  spikes: c.chartSpikes(many).map((x, i) => x ? i : -1).filter(i => i >= 0),
  rel: [...c.chartRelative(daily, topix)],
  zoom: [c.chartZoomTf("D", 1.5), c.chartZoomTf("D", 30), c.chartZoomTf("W", 10), c.chartZoomTf("W", 20),
         c.chartZoomTf("M", 1), c.chartZoomTf("M", 17.2), c.chartZoomTf("M", 10)],
  pick: [c.chartPickTf(tf => c.chartBars(daily, tf), "2026-08-17", "2026-09-01", 24),
         c.chartPickTf(tf => c.chartBars(daily, tf), "2026-08-17", "2026-09-01", 12),
         c.chartPickTf(tf => c.chartBars(daily, tf), "2026-08-17", "2026-09-01", 4)],
  before: [c.chartMonthsBefore("2026-03-31", 1), c.chartMonthsBefore("2026-10-16", 6),
           c.chartMonthsBefore("2026-10-16", 12), c.chartMonthsBefore("2026-10-16", -3)],
  qty: [c.chartQty(800, "株"), c.chartQty(25007500, "株"), c.chartQty(77110432900, "円"), c.chartQty(1.5e12, "円")],
  price: [c.chartPrice(2821.5), c.chartPrice(3094), c.chartPrice(null)],
  fund: c.chartFundDaily({
    t: ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06"], c: [1000, 1200, 1100, 900],
    mc: [100, 120, null, 90],
    fund: {t: ["2026-08-04", "2026-08-06"], eps: [100, -5], op_q: [5e6, 6e6], op_ttm: [1e7, 0]},
  }),
  quantile: [c.chartQuantile([1, 2, 3, 4], 0.5), c.chartQuantile([], 0.5)],
  band: [c.chartBandMultiples([null, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, -3]), c.chartBandMultiples([12, 12]),
         c.chartBandMultiples([null])],
};
console.log(JSON.stringify(out));
"""


def _run() -> dict:
    js = ROOT / "static" / "js" / "chart-core.js"
    r = subprocess.run(["node", "-e", _SCRIPT, str(js)], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def test_chart_core_bars_and_indicators() -> None:
    o = _run()
    # 週足は ISO 週。足の日付は期間の最終営業日、始値は最初・終値は最後・高安は最大最小・出来高は合計
    assert o["week"] == {
        "t": ["2026-08-21", "2026-08-24", "2026-09-01"],
        "o": [2980, 3010, 3100], "h": [3010, 3050, 3250], "l": [2970, 3000, 3090],
        "c": [3005, 3040, 3210], "v": [1800, 1200, 1200], "tv": [60, 40, 110],
    }
    assert o["month"]["t"] == ["2026-08-31", "2026-09-01"]
    assert o["month"]["v"] == [3500, 700] and o["month"]["o"] == [2980, 3200] and o["month"]["c"] == [3120, 3210]
    assert o["isoWeek"][0] == o["isoWeek"][1] != o["isoWeek"][2]
    # 移動平均は窓が埋まるまで null。日数は週=÷5、月=÷20 の本数
    assert o["sma"] == [None, None, 2, 3]
    assert o["maLen"] == [50, 10, 2, 1]
    assert o["mas"] == [50, 150, 200]
    # 直前 20 本の平均の 2 倍以上
    assert o["spikes"] == [22]
    # 対TOPIX（TOPIX がある最初の日=100、TOPIX の無い日は直前の値）
    assert o["rel"] == [["2026-08-17", 100], ["2026-08-18", 100.5], ["2026-08-21", 91.52],
                        ["2026-08-24", 101.84], ["2026-08-31", 104.52], ["2026-09-01", 107.54]]
    # 2px より細いと粗い足へ、1 つ細かい足が 4px 以上なら細かい足へ
    assert o["zoom"] == ["W", "D", "W", "D", "M", "W", "M"]
    # 期間を幅に出すとき 1 本が 4px 以上になる一番細かい足（日足 6 本・週足 3 本）
    assert o["pick"] == ["D", "W", "M"]
    assert o["before"] == ["2026-02-28", "2026-04-16", "2025-10-16", "2027-01-16"]
    assert o["qty"] == ["800株", "2,500.8万株", "771.1億円", "1.5兆円"]
    assert o["price"] == ["2,821.5", "3,094", "–"]
    # 業績の階段を日足にそろえる。使い始める日より前は null、予想EPS・営業利益が 0 以下なら倍率を出さない
    assert o["fund"] == {
        "eps": [None, 100, 100, -5], "opq": [None, 5e6, 5e6, 6e6], "opttm": [None, 1e7, 1e7, 0],
        "per": [None, 12, 11, None], "mcop": [None, 12, None, None],
    }
    assert o["quantile"] == [2.5, None]
    # PERバンドは 10%・50%・90% 点。10 倍未満は小数 1 桁、重なれば 1 本
    assert o["band"] == [[8.9, 13, 16], [12], []]
