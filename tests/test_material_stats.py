"""素材の月次量: 対応表、e-Stat / 経産省 Excel の解析、revision の上書き規則、API の形。HTTP は使わない。"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-material-stats-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from app.db import SessionLocal, init_db  # noqa: E402
from app.estat import (  # noqa: E402
    match_codes, month_of, parse_production_label, production_dims, published_through,
    seidou_records, sokuho_month, trade_rows,
)
from app.material_stats import (  # noqa: E402
    REVISION_KAKUHO, REVISION_NENPO, REVISION_SOKUHO, MappingError, Row, SeidouRecord,
    load_mapping, sekiyu_rows, seidou_rows, validate_mapping, write_rows,
)
from app.meti import (  # noqa: E402
    sekiyu_files, sekiyu_kakuho_cells, sekiyu_nenpo_cells, sekiyu_sokuho_cells, seidou_files,
)
from app.meti import seidou_records as meti_records  # noqa: E402
from app.models import MaterialMonthly  # noqa: E402
from app.supply_chain import apparent_consumption, load_supply_chain, monthly_payload  # noqa: E402


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _eq(got: object, want: object, label: str) -> None:
    if got != want:
        _fail(f"{label}: got {got!r}, want {want!r}")


def test_config_mapping() -> None:
    ids = [m["id"] for m in load_supply_chain()["materials"]]
    load_mapping(material_ids=ids)
    for bad in (
        {"seidou": {"items": [{"material": "nope", "report": "6100", "products": ["エチレン"]}]}},
        {"seidou": {"items": [{"material": "abs", "report": "6100", "products": [["AS樹脂"], "ABS樹脂"]}]}},
        {"trade": {"items": [{"material": "crude", "import": ["27x9"]}]}},
    ):
        try:
            validate_mapping(bad, ids)
        except MappingError:
            continue
        _fail(f"invalid mapping accepted: {bad}")


def test_labels() -> None:
    _eq(parse_production_label("製品_0106_ＡＳ樹脂・ＡＢＳ樹脂_A_生産_t"), ("AS樹脂・ABS樹脂", "生産", "t"), "label")
    _eq(parse_production_label("部門_0301_合成ゴム部門_B_月末従事者数_人"), None, "non product")
    _eq(month_of("202501"), "2025-01", "2025 style")
    _eq(month_of("2022000101_2022年1月(Jan.)"), "2022-01", "2022 style")
    _eq(month_of("2022000103_2022年1～3月期(Q1)"), None, "quarter")
    _eq(month_of("2022000000_2022年(CY)"), None, "year")
    _eq(published_through("確速 品別国別表 輸入(1-7月：確報、8月：輸入9桁速報) 2026年"), 8, "through")
    _eq(sokuho_month("確速 品別国別表 輸入(1-7月：確報、8月：輸入9桁速報) 2026年"), 8, "sokuho month")
    _eq(sokuho_month("確報 品別国別表 輸出(1-8月：確報) 2026年"), None, "no sokuho")
    _eq(match_codes(["2709", "271012181"], ["270900100", "271012181", "271012139"]),
        ["270900100", "271012181"], "prefix")


ITEMS = [
    {"material": "abs", "report": "6100", "products": [["AS樹脂・ABS樹脂"], ["AS樹脂", "ABS樹脂"]]},
    {"material": "tire", "report": "6201", "products": ["乗用車用タイヤ", "二輪自動車用タイヤ"]},
]
METRICS = {"生産": "production", "生産数量": "production", "月末在庫": "inventory"}


def test_estat_old_layout() -> None:
    # 2021〜2023 年: AS と ABS が別品目。品目が cat02、月が cat01
    meta = {
        "cat01": {"m1": "2022000101_2022年1月(Jan.)", "m2": "2022000202_2022年2月(Feb.)", "y": "2022000000_2022年(CY)"},
        "cat02": {"a": "製品_0106_ＡＳ樹脂_A_生産_t", "b": "製品_0107_ＡＢＳ樹脂_A_生産_t",
                  "c": "製品_0107_ＡＢＳ樹脂_E_販売金額_千円"},
    }
    _eq(production_dims(meta), ("cat02", "cat01"), "dims")
    values = [
        {"@cat02": "a", "@cat01": "m1", "$": "100"}, {"@cat02": "b", "@cat01": "m1", "$": "1,000"},
        {"@cat02": "a", "@cat01": "m2", "$": "X"}, {"@cat02": "b", "@cat01": "m2", "$": "900"},
        {"@cat02": "a", "@cat01": "y", "$": "5000"}, {"@cat02": "c", "@cat01": "m1", "$": "7"},
    ]
    rows, warnings = seidou_rows(seidou_records("6100", meta, values), items=ITEMS, metrics=METRICS,
                                 revision=REVISION_NENPO, origin="t")
    _eq([(r.material_id, r.month, r.metric, r.value, r.unit, r.source) for r in rows],
        [("abs", "2022-01", "production", 1100.0, "t", "seidou:6100")], "old rows")
    if not any("2022-02" in w and "AS樹脂" in w for w in warnings):
        _fail(f"secret month must be skipped with a warning: {warnings}")


def test_alternatives_and_units() -> None:
    rec = [
        SeidouRecord("6100", "AS樹脂・ABS樹脂", "生産", "t", "2025-01", 300),
        SeidouRecord("6201", "乗用車用タイヤ", "生産数量", "千本", "2025-01", "10"),
        SeidouRecord("6201", "二輪自動車用タイヤ", "生産数量", "本", "2025-01", "500"),
        SeidouRecord("6201", "二輪自動車用タイヤ", "月末在庫", "本", "2025-01", "-"),
        SeidouRecord("6201", "乗用車用タイヤ", "月末在庫", "千本", "2025-01", 2),
    ]
    rows, _ = seidou_rows(rec, items=ITEMS, metrics=METRICS, revision=REVISION_KAKUHO, origin="t")
    _eq({(r.material_id, r.metric, r.value, r.unit) for r in rows},
        {("abs", "production", 300.0, "t"), ("tire", "production", 10500.0, "本"),
         ("tire", "inventory", 2000.0, "本")}, "combined ABS, 千本 and '-' as zero")
    # 速報は二輪が無い: 候補が揃わないので書かない（乗用車だけの過小な合計にしない）
    rows, warnings = seidou_rows(rec[1:2], items=ITEMS, metrics=METRICS, revision=REVISION_SOKUHO, origin="s")
    _eq(rows, [], "incomplete group")
    if not any(w.startswith("tire -") for w in warnings):
        _fail(f"missing product must warn: {warnings}")


def _xlsx(title: str, rows: list[list]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active.title = "目次 "
    ws = wb.create_sheet("実数表")
    ws.append([title, None, None, None, None, None, None, "時間軸コード", "2026000101", "2026000202", "2026000303"])
    ws.append(["調査票番号", "品目番号", "アイテム記号", "調査票名", "項目名", "品目名", "アイテム名", "単位名",
               "202601", "202602", "202603", "Questionnaires"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_meti() -> None:
    html = ('<a href="resourceData/08_seidou/sokuho/h2daa202608s.xlsx">'
            '<a href="resourceData/08_seidou/kakuho/2026/h2daa202607_jikei.xlsx">'
            '<a href="resourceData/08_seidou/kakuho/2026/h2daa202607_sanko_kikai_kmp.xlsx">'
            '<a href="resourceData/08_seidou/kakuho/2025/h2daa2025_hosei_jikei.xlsx">'
            '<a href="resourceData/08_seidou/kakuho/2025/h2daa2025_hosei_jikei.xlsx">')
    files = seidou_files(html, "https://www.meti.go.jp/statistics/tyo/seidou/result/ichiran/08_seidou.html")
    _eq([(f.name, f.revision) for f in files],
        [("h2daa202608s.xlsx", 1), ("h2daa202607_jikei.xlsx", 2), ("h2daa2025_hosei_jikei.xlsx", 3)], "links")
    _eq(files[0].url, "https://www.meti.go.jp/statistics/tyo/seidou/result/ichiran/resourceData/08_seidou/sokuho/h2daa202608s.xlsx", "url")
    data = _xlsx("速報_実数表", [
        ["6100", "0106", "A", "石油化学製品月報", "製品", "ＡＳ樹脂・ＡＢＳ樹脂", "生産", "t", 300, "X", None, "x"],
        ["6100", "0106", "D", "石油化学製品月報", "製品", "ＡＳ樹脂・ＡＢＳ樹脂", "販売数量", "t", 200, 210, "", "x"],
        ["6100", "0301", "B", "石油化学製品月報", "部門", "合成ゴム部門", "月末従事者数", "人", 9, 9, 9, "x"],
        ["1010", "0108", "A", "鉄鋼月報", "製品", "鋼塊普通鋼", "生産", "t", 1, 1, 1, "x"],
    ])
    recs = meti_records(data, {"6100"})
    _eq([(r.product, r.metric, r.month, r.raw) for r in recs],
        [("AS樹脂・ABS樹脂", "生産", "2026-01", 300), ("AS樹脂・ABS樹脂", "生産", "2026-02", "X"),
         ("AS樹脂・ABS樹脂", "販売数量", "2026-01", 200), ("AS樹脂・ABS樹脂", "販売数量", "2026-02", 210)],
        "excel records")


def _book(sheets: dict[str, list[list]]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        for r in rows:
            ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_sekiyu_kakuho() -> None:
    # 確報の時系列表: 左右 2 ブロック。下の左ブロックは見出しが無く上（原油）の続き
    n = None
    data = _book({"(1)～(3)": [
        [None, "　（１）原油　Crude Oil", None, None, None, "　（単位：kl）", None, "　（２）天然ガス　Natural Gas", None, "（単位：千m3）"],
        [None, None, None, None, "消費"],
        [None, "年・期・月", "生産", "輸入", "Consumption", n, n, "生産", "出荷"],
        [None, "令和７年", 1, 2, 3, n, n, 4, 5],
        [None, "令和７年　４～６　月", 9, 9, 9, n, n, 9, 9],
        [None, "令和７年　１２月", 10, 20, 30, n, n, 40, 50],
        [None, "令和８年　１　", 11, 21, "X", n, n, 41, 51],
        [None, "　　２　", 12, 22, 32, n, n, 42, 52],
        [None, "前年同月比（％）", 1, 1, 1, n, n, 1, 1],
        [],
        [None, None, None, None, None, "　（単位：kl）", None, "　（３）ガソリン　Gasoline", None, "　（単位：kl）"],
        [None, "年・期・月", "出荷", "在庫", n, n, n, "生産", "国内向販売"],
        [None, "令和８年　２　", 1, 700, n, n, n, 2, 900],
        [None, "前年同月比（％）"],
    ]})
    cells = sekiyu_kakuho_cells(data)
    got = {(c.block, c.column, c.month): (c.raw, c.unit) for c in cells}
    _eq(got[("原油", "消費", "2025-12")], (30, "kl"), "crude consumption")
    _eq(got[("原油", "消費", "2026-02")], (32, "kl"), "bare month after 令和8年1")
    _eq(got[("天然ガス", "生産", "2026-01")], (41, "千m3"), "right block unit")
    _eq(got[("原油", "在庫", "2026-02")], (700, "kl"), "continuation block inherits 原油")
    _eq(got[("ガソリン", "国内向販売", "2026-02")], (900, "kl"), "gasoline sales")
    if any(c.month.startswith("2025-0") for c in cells):
        _fail("year and quarter rows must not become months")
    items = [{"material": "crude", "metric": "processing", "kakuho": {"blocks": ["原油"], "column": "消費"}}]
    rows, warnings = sekiyu_rows(cells, items=items, kind="kakuho", revision=REVISION_KAKUHO, origin="k")
    _eq([(r.month, r.value, r.source) for r in rows], [("2025-12", 30.0, "sekiyu"), ("2026-02", 32.0, "sekiyu")],
        "secret month skipped")
    if not any("2026-01" in w for w in warnings):
        _fail(f"secret month must warn: {warnings}")


def test_sekiyu_sokuho_and_nenpo() -> None:
    data = _book({
        "原油受払～石油製品生産在庫（3頁目）": [
            ["2．原油受払"],
            ["区　分", None, None, None, "単 位", "8年8月", None, None, "8年7月"],
            ["処理", None, None, None, "kl", 11573650, 97.7, 98.2, 11843023],
            ["月末在庫", None, None, None, "kl", 12374741, 107.7, 119.6, 11491917],
            ["3．原油出荷内訳(非精製用)"],
            ["計", None, None, None, "kl", 1164],
        ],
        "原油受払～石油製品生産在庫（4頁目）": [["4．石油製品"], ["潤滑油", None, None, None, "kl", 5]],
        "需給概要（5頁目）": [
            [None, "5． 需　給　概　要", "(8年8月)"],
            ["区 　　　分", None, "燃料油計", "ジェット", "灯　　油", "Ａ 重 油", "Ｌ Ｐ Ｇ"],
            [None, None, None, "燃 料 油", None, None, None],
            ["単 　　　位", None, "kl", "kl", "kl", "kl", "ｔ"],
            [" 国 内 向 販 売", None, 100, 10, 20, 30, 40],
            [None, "前　月　比（%）", 1, 1, 1, 1, 1],
        ],
    })
    got = {(c.block, c.column, c.month): (c.raw, c.unit) for c in sekiyu_sokuho_cells(data)}
    _eq(got[("原油", "処理", "2026-08")], (11573650, "kl"), "sokuho crude")
    _eq(got[("ジェット燃料油", "国内向販売", "2026-08")], (10, "kl"), "two-row product header")
    _eq(got[("LPG", "国内向販売", "2026-08")], (40, "t"), "LPG unit")
    if ("原油", "潤滑油", "2026-08") in got or ("原油", "計", "2026-08") in got:
        _fail("rows outside 原油受払 must be ignored")

    nenpo = _book({"２（３）石油製品月別国内向販売": [
        [None, "（３）石油製品月別国内向販売", None, None, None, None, None, "(単位：kl、アスファルト以降:t)"],
        [None, "年・期・月", None, None, "ガソリン", "Ｂ・Ｃ重油\nFuel Oil B・C", "アスファルト", "液化石油ガス"],
        [None, None, None, None, "Gasoline", None, "Asphalt", "LPG"],
        [None, "令和", "7 年", None, 1, 1, 1, 1],
        [None, "令和", "7 年", "  1 月", 100, 50, 7, 8],
        [None, None, None, "  2", 110, "―", 9, 10],
        [],
    ]})
    got = {(c.block, c.month): (c.raw, c.unit) for c in sekiyu_nenpo_cells(nenpo)}
    _eq(got[("ガソリン", "2025-02")], (110, "kl"), "nenpo month")
    _eq(got[("B・C重油", "2025-01")], (50, "kl"), "Japanese line only")
    _eq(got[("アスファルト", "2025-01")], (7, "t"), "unit switches at アスファルト")
    _eq(got[("液化石油ガス", "2025-02")], (10, "t"), "unit after switch")
    items = [{"material": "heavy", "metric": "domestic_sales",
              "nenpo": {"blocks": ["B・C重油"], "column": ["石油製品月別国内向販売", "石油製品国内向月別販売"]}}]
    rows, _ = sekiyu_rows(sekiyu_nenpo_cells(nenpo), items=items, kind="nenpo", revision=REVISION_NENPO, origin="n")
    _eq([(r.month, r.value) for r in rows], [("2025-01", 50.0), ("2025-02", 0.0)], "― is zero")

    html_k = ('<a href="xls/h2dhhpe2021k.xlsx"><a href="xls/se202606kakji.xlsx"><a href="xls/se202607kakji.xlsx">'
              '<a href="xls/se202607kakse.xlsx">')
    html_s = '<a href="result/xls/h2j581011j.xlsx"><a href="result/xls/h2j581011e.xlsx">'
    files = sekiyu_files(html_k, html_s, "https://www.meti.go.jp/statistics/tyo/sekiyuka/index.html",
                         "https://www.meti.go.jp/statistics/tyo/sekiyuso/result.html")
    _eq([(f.name, f.revision) for f in files],
        [("h2dhhpe2021k.xlsx", 3), ("se202607kakji.xlsx", 2), ("h2j581011j.xlsx", 1)], "sekiyu files")


def test_trade() -> None:
    cat02 = {"100": "単位1", "110": "単位2", "150": "1月_数量1", "160": "1月_数量2",
             "180": "2月_数量1", "190": "2月_数量2", "270": "9月_数量1", "280": "9月_数量2"}
    times = {"2025000000": "2025年", "2026000000": "2026年"}
    v: list[dict] = []

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
        stats_data_id="t", sokuho=1,
    )
    _eq([(r.material_id, r.month, r.value, r.unit, r.source, r.revision) for r in rows],
        [("crude", "2026-01", 15.0, "kl", "trade", 1), ("ethylene", "2025-02", 4.0, "t", "trade", 2),
         ("ethylene", "2026-01", 5.0, "t", "trade", 1)], "trade rows")


def test_revision_guard() -> None:
    init_db()
    db = SessionLocal()
    try:
        def row(value: float, revision: int) -> Row:
            return Row("ethylene", "2026-07", "production", "seidou:6100", value, "t", revision, f"r{revision}")

        _eq(write_rows(db, [row(100, REVISION_KAKUHO)]), 1, "first write")
        _eq(write_rows(db, [row(90, REVISION_SOKUHO)]), 0, "sokuho must not overwrite kakuho")
        _eq(write_rows(db, [row(101, REVISION_KAKUHO)]), 1, "same revision overwrites")
        _eq(write_rows(db, [row(102, REVISION_NENPO)]), 1, "nenpo overwrites")
        got = db.query(MaterialMonthly).one()
        _eq((got.value, got.revision, got.origin), (102.0, 3, "r3"), "stored")
    finally:
        db.close()


def test_apparent_and_payload() -> None:
    series = {
        "production": {"2025-01": 100.0, "2025-02": 110.0},
        "import": {"2025-01": 20.0, "2025-02": 30.0},
        "export": {"2025-01": 5.0, "2025-02": 10.0},
        "inventory": {"2024-12": 50.0, "2025-01": 60.0, "2025-02": 55.0},
    }
    _eq(apparent_consumption(series), {"2025-01": 105.0, "2025-02": 135.0}, "apparent")
    rows = [SimpleNamespace(metric=m, unit="t", month=mo, value=v, source="s",
                            revision=1 if (m, mo) == ("production", "2025-02") else 2)
            for m, pts in series.items() for mo, v in pts.items()]
    rows.append(SimpleNamespace(metric="production", unit="kl", month="2025-01", value=1.0, source="s2", revision=3))
    p = monthly_payload(rows, "x")
    _eq([(s["metric"], s["unit"]) for s in p["series"]],
        [("production", "kl"), ("production", "t"), ("inventory", "t"), ("import", "t"),
         ("export", "t"), ("apparent", "t")], "series order")
    _eq(p["series"][-1]["points"], [["2025-01", 105.0, 2], ["2025-02", 135.0, 1]], "apparent revision is the minimum")


def main() -> None:
    test_config_mapping()
    test_labels()
    test_estat_old_layout()
    test_alternatives_and_units()
    test_meti()
    test_sekiyu_kakuho()
    test_sekiyu_sokuho_and_nenpo()
    test_trade()
    test_revision_guard()
    test_apparent_and_payload()
    print("ok: material_stats")


if __name__ == "__main__":
    main()
