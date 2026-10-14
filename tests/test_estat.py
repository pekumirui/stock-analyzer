"""e-Stat の表 → material_monthly の解析と対応表の検査。HTTP は使わない。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-estat-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from app.estat import (  # noqa: E402
    EstatMappingError, load_mapping, match_codes, month_of, parse_production_label,
    production_dims, production_rows, published_through, trade_rows, validate_mapping,
)
from app.supply_chain import apparent_consumption, load_supply_chain, monthly_payload  # noqa: E402


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _eq(got: object, want: object, label: str) -> None:
    if got != want:
        _fail(f"{label}: got {got!r}, want {want!r}")


def test_config_mapping() -> None:
    ids = [m["id"] for m in load_supply_chain()["materials"]]
    load_mapping(material_ids=ids)
    try:
        validate_mapping({"production": {"metrics": {}, "items": [
            {"material": "nope", "report": "6100", "products": ["エチレン"]}]}}, ids)
    except EstatMappingError:
        pass
    else:
        _fail("unknown material accepted")


def test_labels() -> None:
    _eq(parse_production_label("製品_0106_ＡＳ樹脂・ＡＢＳ樹脂_A_生産_t"), ("AS樹脂・ABS樹脂", "生産", "t"), "label")
    _eq(parse_production_label("部門_0301_合成ゴム部門_B_月末従事者数_人"), None, "non product")
    _eq(month_of("202501"), "2025-01", "2025 style")
    _eq(month_of("2022000101_2022年1月(Jan.)"), "2022-01", "2022 style")
    _eq(month_of("2022000103_2022年1～3月期(Q1)"), None, "quarter")
    _eq(month_of("2022000000_2022年(CY)"), None, "year")
    _eq(published_through("確速 品別国別表 輸入(1-7月：確報、8月：輸入9桁速報) 2026年"), 8, "through")
    _eq(match_codes(["2709", "271012181"], ["270900100", "271012181", "271012139"]),
        ["270900100", "271012181"], "prefix")


ITEMS = [
    {"material": "abs", "report": "6100", "products": ["AS樹脂・ABS樹脂", "AS樹脂", "ABS樹脂"]},
    {"material": "tire", "report": "6201", "products": ["乗用車用タイヤ", "二輪自動車用タイヤ"]},
]
METRICS = {"生産": "production", "月末在庫": "inventory"}


def test_production_old_layout() -> None:
    # 2021〜2023 年: AS と ABS が別品目。品目が cat02、月が cat01
    meta = {
        "cat01": {"m1": "2022000101_2022年1月(Jan.)", "m2": "2022000202_2022年2月(Feb.)", "y": "2022000000_2022年(CY)"},
        "cat02": {"a": "製品_0106_ＡＳ樹脂_A_生産_t", "b": "製品_0107_ＡＢＳ樹脂_A_生産_t",
                  "c": "製品_0107_ＡＢＳ樹脂_E_販売金額_千円"},
    }
    item_dim, month_dim = production_dims(meta)
    _eq((item_dim, month_dim), ("cat02", "cat01"), "dims")
    values = [
        {"@cat02": "a", "@cat01": "m1", "$": "100"}, {"@cat02": "b", "@cat01": "m1", "$": "1,000"},
        {"@cat02": "a", "@cat01": "m2", "$": "X"}, {"@cat02": "b", "@cat01": "m2", "$": "900"},
        {"@cat02": "a", "@cat01": "y", "$": "5000"}, {"@cat02": "c", "@cat01": "m1", "$": "7"},
    ]
    rows, warnings = production_rows(
        report="6100", items=ITEMS, metrics=METRICS, labels=meta["cat02"], months=meta["cat01"],
        item_dim=item_dim, month_dim=month_dim, values=values, stats_code="00550200", stats_data_id="t",
    )
    _eq([(r.material_id, r.month, r.metric, r.value, r.unit) for r in rows],
        [("abs", "2022-01", "production", 1100.0, "t")], "old rows")
    if not any("2022-02" in w and "AS樹脂" in w for w in warnings):
        _fail(f"secret month must be skipped with a warning: {warnings}")


def test_production_new_layout_units() -> None:
    meta = {
        "cat01": {"p": "製品_0102_乗用車用タイヤ_A_生産数量_千本", "q": "製品_0104_二輪自動車用タイヤ_A_生産数量_本",
                  "r": "製品_0104_二輪自動車用タイヤ_F_月末在庫_本", "s": "製品_0102_乗用車用タイヤ_F_月末在庫_千本"},
        "time": {"2025000101": "202501"},
    }
    item_dim, month_dim = production_dims(meta)
    values = [
        {"@cat01": "p", "@time": "2025000101", "$": "10"}, {"@cat01": "q", "@time": "2025000101", "$": "500"},
        {"@cat01": "r", "@time": "2025000101", "$": "-"}, {"@cat01": "s", "@time": "2025000101", "$": "2"},
    ]
    rows, _ = production_rows(
        report="6201", items=ITEMS, metrics={"生産数量": "production", "月末在庫": "inventory"},
        labels=meta["cat01"], months=meta["time"], item_dim=item_dim, month_dim=month_dim,
        values=values, stats_code="00550200", stats_data_id="t",
    )
    got = {(r.metric, r.value, r.unit) for r in rows}
    _eq(got, {("production", 10500.0, "本"), ("inventory", 2000.0, "本")}, "千本 and '-' as zero")


def test_trade() -> None:
    cat02 = {"100": "単位1", "110": "単位2", "150": "1月_数量1", "160": "1月_数量2",
             "180": "2月_数量1", "190": "2月_数量2", "270": "9月_数量1", "280": "9月_数量2"}
    times = {"2025000000": "2025年", "2026000000": "2026年"}
    v = []

    def cell(code: str, area: str, kind: str, val: str, t: str = "2026000000") -> None:
        v.append({"@cat01": code, "@cat02": kind, "@area": area, "@time": t, "$": val})

    # 原油: 単位1 KL、国を足す
    cell("270900900", "a", "100", "ＫＬ"); cell("270900900", "b", "100", "ＫＬ")
    cell("270900900", "a", "150", "10"); cell("270900900", "b", "150", "5")
    cell("270900900", "a", "270", "0")  # 未公表月の 0
    # エチレン: 単位1 が無く 数量2 の KG。国ごとに MT のこともある
    cell("290121000", "a", "110", "ＫＧ"); cell("290121000", "a", "160", "2000")
    cell("290121000", "b", "100", "ＭＴ"); cell("290121000", "b", "150", "3")
    cell("290121000", "a", "110", "ＫＧ", "2025000000"); cell("290121000", "a", "190", "4000", "2025000000")
    rows = trade_rows(
        flow="import", cat02=cat02, times=times, values=v, last_month=8,
        items=[{"material": "crude", "codes": ["270900900"]}, {"material": "ethylene", "codes": ["290121000"]}],
        stats_code="00350300", stats_data_id="t",
    )
    _eq([(r.material_id, r.month, r.value, r.unit) for r in rows],
        [("crude", "2026-01", 15.0, "kl"), ("ethylene", "2025-02", 4.0, "t"), ("ethylene", "2026-01", 5.0, "t")],
        "trade rows")


def test_apparent_and_payload() -> None:
    series = {
        "production": {"2025-01": 100.0, "2025-02": 110.0},
        "import": {"2025-01": 20.0, "2025-02": 30.0},
        "export": {"2025-01": 5.0, "2025-02": 10.0},
        "inventory": {"2024-12": 50.0, "2025-01": 60.0, "2025-02": 55.0},
    }
    _eq(apparent_consumption(series), {"2025-01": 105.0, "2025-02": 135.0}, "apparent")
    rows = [SimpleNamespace(metric=m, unit="t", month=mo, value=v, source="s")
            for m, pts in series.items() for mo, v in pts.items()]
    rows.append(SimpleNamespace(metric="production", unit="kl", month="2025-01", value=1.0, source="s2"))
    p = monthly_payload(rows, "x")
    _eq([(s["metric"], s["unit"]) for s in p["series"]],
        [("production", "kl"), ("production", "t"), ("inventory", "t"), ("import", "t"),
         ("export", "t"), ("apparent", "t")], "series order")


def main() -> None:
    test_config_mapping()
    test_labels()
    test_production_old_layout()
    test_production_new_layout_units()
    test_trade()
    test_apparent_and_payload()
    print("ok: estat")


if __name__ == "__main__":
    main()
