"""素材の月次量: 対応表、e-Stat / 経産省 Excel の解析、revision の上書き規則、API の形。HTTP は使わない。"""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-material-stats-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db  # noqa: E402
from app.supply.estat import (  # noqa: E402
    match_codes, month_of, parse_production_label, production_dims, published_through,
    seidou_records, sokuho_month, trade_country_rows, trade_rows,
)
from app.supply.material_stats import (  # noqa: E402
    REVISION_KAKUHO, REVISION_NENPO, REVISION_SOKUHO, CountryRow, MappingError, Row, SeidouRecord,
    load_mapping, prune_unmapped, sekiyu_rows, seidou_rows, validate_mapping, write_country_rows, write_rows,
)
from app.supply.meti import (  # noqa: E402
    MetiChallenged, MetiClient, MetiError, MetiFile, sekiyu_files, sekiyu_kakuho_cells, sekiyu_nenpo_cells,
    sekiyu_nenpo_inventory_cells, sekiyu_sokuho_cells, seidou_december_kakuho, seidou_files,
)
from app.supply.meti import seidou_records as meti_records  # noqa: E402
from app.models import MaterialMonthly, MaterialTradeCountry  # noqa: E402
from app.supply.chain import apparent_consumption, load_supply_chain, monthly_payload  # noqa: E402

from jobs._r2common import MountDisconnected  # noqa: E402
from jobs.supply.material_stats import (  # noqa: E402
    MetiArchive, MetiLedger, ingest_meti_seidou, ingest_meti_sekiyu, meti_targets, sekiyu_done,
)
import jobs.supply.material_stats as material_stats_job  # noqa: E402


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
        {"sekiyu": {"items": [{"material": "lpg", "metric": "import", "kakuho": {"blocks": ["LPG"], "column": "輸入"}}]},
         "trade": {"items": [{"material": "lpg", "import": ["271112"]}]}},
        {"trade": {"items": [{"material": "pe", "import": ["390110"]}],
                   "country_items": [{"material": "pe", "import": ["390120"]}]}},
        {"trade": {"country_items": [{"material": "crude"}]}},
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
    rows, _ = seidou_rows([SeidouRecord("5040", "産業用品_飲料用缶_缶体", "生産", "kg", "2025-01", "2500")],
                          items=[{"material": "can", "report": "5040", "products": ["産業用品_飲料用缶_缶体"]}],
                          metrics=METRICS, revision=REVISION_KAKUHO, origin="t")
    _eq([(r.value, r.unit) for r in rows], [(2.5, "t")], "kg to t")
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
    dec = seidou_december_kakuho(2024, "https://www.meti.go.jp/statistics/tyo/seidou/result/ichiran/08_seidou.html")
    _eq((dec.url, dec.revision, dec.final),
        ("https://www.meti.go.jp/statistics/tyo/seidou/result/ichiran/resourceData/08_seidou/kakuho/202412/h2daa202412_jikei.xlsx",
         2, True), "december kakuho")
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

    inv = _book({"２（５）石油製品月別業態別在庫": [
        [None, "（５）石油製品月別業態別在庫", None, None, "(単位：kl、アスファルト以降:t)"],
        [None, None, None, "ナフサ", "アスファルト"],
        [None, "区分", None, None, None],
        [None, None, None, "Naphtha", "Asphalt"],
        [None, "令和7年", None, None, None],
        [None, "計", None, 100, 7],
        [None, "  1月", "製油所", 60, 4],
        [None, None, "製造・輸入業者", 40, 3],
        [None, "計", None, 110, 9],
        [None, "  2月", "製油所", 70, 5],
        [None, None, "製造・輸入業者", 40, 4],
    ]})
    _eq(sorted((c.block, c.column, c.month, c.raw, c.unit) for c in sekiyu_nenpo_inventory_cells(inv)),
        [("アスファルト", "石油製品月別業態別在庫", "2025-01", 7, "t"),
         ("アスファルト", "石油製品月別業態別在庫", "2025-02", 9, "t"),
         ("ナフサ", "石油製品月別業態別在庫", "2025-01", 100, "kl"),
         ("ナフサ", "石油製品月別業態別在庫", "2025-02", 110, "kl")], "nenpo inventory: 計 rows")
    items = [{"material": "heavy", "metric": "domestic_sales",
              "nenpo": {"blocks": ["B・C重油"], "column": ["石油製品月別国内向販売", "石油製品国内向月別販売"]}}]
    rows, _ = sekiyu_rows(sekiyu_nenpo_cells(nenpo), items=items, kind="nenpo", revision=REVISION_NENPO, origin="n")
    _eq([(r.month, r.value) for r in rows], [("2025-01", 50.0), ("2025-02", 0.0)], "― is zero")

    html_k = ('<a href="xls/h2dhhpe2021k.xlsx"><a href="xls/se202606kakji.xlsx"><a href="xls/se202607kakji.xlsx">'
              '<a href="xls/se202607kakse.xlsx">')
    html_s = '<a href="result/xls/h2j581011j.xlsx"><a href="result/xls/h2j581011e.xlsx">'
    files = sekiyu_files(html_k, html_s, "https://www.meti.go.jp/statistics/tyo/sekiyuka/index.html",
                         "https://www.meti.go.jp/statistics/tyo/sekiyuso/result.html")
    _eq([(f.name, f.revision, f.final) for f in files],
        [("h2dhhpe2021k.xlsx", 3, True), ("se202112kakji.xlsx", 2, True), ("se202607kakji.xlsx", 2, False),
         ("h2j581011j.xlsx", 1, False)], "sekiyu files: December kakuho of each nenpo year")
    _eq(files[1].url, "https://www.meti.go.jp/statistics/tyo/sekiyuka/xls/se202112kakji.xlsx", "december url")
    done = {"h2dhhpe2021k.xlsx", "se202112kakji.xlsx", "se202607kakji.xlsx"}
    _eq([f.name for f in meti_targets(files, done, False)], ["se202607kakji.xlsx", "h2j581011j.xlsx"],
        "final files are skipped once ingested")

    old = _book({"２（４）①石油製品月別輸入": [
        [None, "   ①月別輸入 / Import by Month"],
        [None, "年・期・月", None, None, None, "燃料油計", "ナフサ"],
        [None, None, None, None, None, "Total", "Naphtha"],
        [None, None, None, None, None, 1100596, None],
        [None, "平成", "29年", None, None, 33510425, 28277414],
        [None, "令和", " 3年", " 1月", None, 3, 2],
        [None, None, None, None, None, 9, None],
        [None, None, None, "2", None, 5, 4],
        [],
    ]})
    _eq(sorted((c.block, c.column, c.month, c.raw) for c in sekiyu_nenpo_cells(old)),
        [("ナフサ", "月別輸入", "2021-01", 2), ("ナフサ", "月別輸入", "2021-02", 4),
         ("燃料油計", "月別輸入", "2021-01", 3), ("燃料油計", "月別輸入", "2021-02", 5)], "heisei rows")


class _FakeResp:
    def __init__(self, status: int, content: bytes = b"", headers: dict | None = None) -> None:
        self.status_code, self.content, self.headers = status, content, headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _FakeHttp:
    def __init__(self, replies: list[_FakeResp]) -> None:
        self.replies, self.calls = list(replies), 0

    def get(self, url: str) -> _FakeResp:
        self.calls += 1
        return self.replies.pop(0)


def test_meti_waf_challenge() -> None:
    challenge = _FakeResp(202, b"", {"x-amzn-waf-action": "challenge"})
    ok = _FakeResp(200, b"PK\x03\x04data")
    http = _FakeHttp([challenge, ok])
    _eq(MetiClient(http=http, pause=0, challenge_wait=0).get("https://x/a.xlsx"), b"PK\x03\x04data", "retry once")
    http = _FakeHttp([challenge, challenge, ok])
    try:
        MetiClient(http=http, pause=0, challenge_wait=0).get("https://x/a.xlsx")
    except MetiChallenged:
        _eq(http.calls, 2, "stop after the second challenge")
    else:
        _fail("second challenge must raise MetiChallenged")


def test_trade() -> None:
    cat02 = {"100": "単位1", "110": "単位2", "150": "1月_数量1", "160": "1月_数量2",
             "180": "2月_数量1", "190": "2月_数量2", "270": "9月_数量1", "280": "9月_数量2"}
    times = {"2025000000": "2025年", "2026000000": "2026年"}
    v: list[dict] = []

    def cell(code: str, area: str, kind: str, val: str, t: str = "2026000000") -> None:
        v.append({"@cat01": code, "@cat02": kind, "@area": area, "@time": t, "$": val})

    cell("270900900", "a", "100", "ＫＬ"); cell("270900900", "b", "100", "ＫＬ")
    cell("270900900", "a", "150", "10"); cell("270900900", "b", "150", "5")
    cell("270900900", "a", "270", "0")
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
    crows = trade_country_rows(
        flow="import", cat02=cat02, times=times, values=v, last_month=8,
        areas={"a": "137_サウジアラビア", "b": "147_アラブ首長国連邦"},
        items=[{"material": "crude", "codes": ["270900900"]}], stats_data_id="t", sokuho=1,
    )
    _eq([(r.material_id, r.month, r.country, r.country_name, r.value, r.unit, r.revision) for r in crows],
        [("crude", "2026-01", "137", "サウジアラビア", 10.0, "kl", 1),
         ("crude", "2026-01", "147", "アラブ首長国連邦", 5.0, "kl", 1)], "trade country rows")


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


def test_prune_and_done() -> None:
    init_db()
    db = SessionLocal()
    try:
        db.query(MaterialMonthly).delete()
        write_rows(db, [
            Row("lpg", "2025-01", "import", "trade", 1.0, "t", 2, "t"),
            Row("lpg", "2025-01", "import", "sekiyu", 2.0, "t", 3, "n"),
            Row("lpg", "2025-01", "production", "seidou:8061", 3.0, "kl", 2, "k"),
            Row("ethylene", "2025-01", "production", "seidou:6100", 4.0, "t", 2, "k"),
        ])
        mapping = {
            "seidou": {"metrics": {"生産": "production"},
                       "items": [{"material": "ethylene", "report": "6100", "products": ["エチレン"]}]},
            "sekiyu": {"items": [
                {"material": "lpg", "metric": "import", "nenpo": {"blocks": ["液化石油ガス"], "column": "月別輸入"}},
                {"material": "lpg", "metric": "export", "nenpo": {"blocks": ["液化石油ガス"], "column": "月別輸出"},
                 "kakuho": {"blocks": ["液化石油ガス"], "column": "輸出"}},
            ]},
        }
        db.query(MaterialTradeCountry).delete()
        write_country_rows(db, [CountryRow("crude", "2025-01", "import", "137", "サウジアラビア", 1.0, "kl", 2, "t"),
                                CountryRow("pe", "2025-01", "import", "105", "中華人民共和国", 1.0, "t", 2, "t")])
        mapping["trade"] = {"country_items": [{"material": "crude", "import": ["2709"]}]}
        _eq(prune_unmapped(db, mapping), 3, "pruned rows (2 monthly + 1 country)")
        _eq([r.material_id for r in db.query(MaterialTradeCountry)], ["crude"], "kept country rows")
        _eq(sorted((r.material_id, r.metric, r.source) for r in db.query(MaterialMonthly)),
            [("ethylene", "production", "seidou:6100"), ("lpg", "import", "sekiyu")], "kept rows")
        items = mapping["sekiyu"]["items"] + [
            {"material": "lpg", "metric": "production", "kakuho": {"blocks": ["液化石油ガス"], "column": "生産"}}]
        files = [MetiFile("https://x/n2024.xlsx", REVISION_NENPO, True), MetiFile("https://x/n2025.xlsx", REVISION_NENPO, True),
                 MetiFile("https://x/k2024.xlsx", REVISION_KAKUHO, True), MetiFile("https://x/latest.xlsx", REVISION_KAKUHO)]
        write_rows(db, [
            Row("lpg", "2024-01", "import", "sekiyu", 1.0, "t", 3, "n2024.xlsx"),
            Row("lpg", "2024-01", "export", "sekiyu", 1.0, "t", 3, "n2024.xlsx"),
            Row("lpg", "2025-01", "import", "sekiyu", 1.0, "t", 3, "n2025.xlsx"),
            Row("lpg", "2024-01", "production", "sekiyu", 1.0, "t", 2, "k2024.xlsx"),
        ])
        _eq(sekiyu_done(db, items, files), {"n2024.xlsx", "k2024.xlsx"},
            "a file missing a mapped item is refetched even if another year has it; kakuho checks production only")
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

    rows = [SimpleNamespace(metric="export", unit="t", month="2023-10", value=1.0, source="trade", revision=2),
            SimpleNamespace(metric="import", unit="t", month="2025-01", value=9.0, source="trade", revision=2),
            SimpleNamespace(metric="import", unit="kl", month="2025-01", value=5.0, source="sekiyu", revision=2)]
    trade = {"export": {"2023-10": 2, "2025-01": 2, "2025-02": 1}, "import": {"2025-01": 2, "2025-02": 1}}
    p = monthly_payload(rows, "x", trade)
    got = {(s["metric"], s["unit"]): s["points"] for s in p["series"]}
    _eq(got[("export", "t")], [["2023-10", 1.0, 2], ["2025-01", 0.0, 2], ["2025-02", 0.0, 1]], "trade zero fill")
    _eq(got[("import", "t")], [["2025-01", 9.0, 2], ["2025-02", 0.0, 1]], "trade import zero fill")
    _eq(got[("import", "kl")], [["2025-01", 5.0, 2]], "non-trade series is not filled")

    rows = [SimpleNamespace(metric="production", unit="t", month="2025-01", value=8.0, source="seidou:2560", revision=2),
            SimpleNamespace(metric="production", unit="t", month="2025-01", value=3.0, source="seidou:2550", revision=1),
            SimpleNamespace(metric="production", unit="t", month="2025-02", value=9.0, source="seidou:2560", revision=1)]
    p = monthly_payload(rows, "x")
    _eq(p["series"][0]["points"], [["2025-01", 11.0, 1]], "reports summed, month without every report dropped")


def test_meti_archive() -> None:

    class Client:
        def __init__(self, body: bytes) -> None:
            self.body, self.calls = body, 0

        def get(self, url: str) -> bytes:
            self.calls += 1
            return self.body

    with tempfile.TemporaryDirectory() as tmp:
        arc = MetiArchive(Path(tmp))
        final = MetiFile("https://x/kakuho/2025/h2daa2025_hosei_jikei.xlsx", 3, True)
        live = MetiFile("https://x/sokuho/h2daa202608s.xlsx", 1)
        _eq(arc.missing("seidou", {final.name}), {final.name}, "archive missing")
        c = Client(b"v1")
        _eq(arc.get(c, final, "seidou", False), (b"v1", "meti"), "archive first download")
        _eq(arc.get(c, final, "seidou", False), (b"v1", "r2"), "archive read final")
        _eq((c.calls, arc.missing("seidou", {final.name})), (1, set()), "archive no second download")
        _eq(arc.get(Client(b"v2"), final, "seidou", True), (b"v2", "meti"), "archive refetch")
        names = sorted(p.name for p in (Path(tmp) / "seidou").iterdir())
        _eq((len(names), names[0]), (2, "h2daa2025_hosei_jikei.xlsx"), "archive keeps old version")
        _eq(arc.get(Client(b"v9"), final, "seidou", False), (b"v2", "r2"), "archive reads latest version")
        c = Client(b"s")
        arc.get(c, live, "seidou", False)
        arc.get(c, live, "seidou", False)
        _eq((c.calls, len(list((Path(tmp) / "seidou").glob("h2daa202608s*")))), (2, 1), "archive same body once")
    off = MetiArchive(None)
    _eq((off.get(Client(b"z"), final, "seidou", False), off.missing("seidou", {"a"})), ((b"z", "meti"), set()),
        "archive off")


def test_meti_archive_open_expected_errors() -> None:
    root = Path("/tmp/stock-analyzer-r2-share")

    def _open(exc: BaseException) -> tuple[MetiArchive, str]:
        buf = io.StringIO()
        with patch("jobs.supply.material_stats.check_r2_mount", side_effect=exc), \
             patch("jobs.supply.material_stats.r2_share_root", return_value=root), \
             redirect_stdout(buf):
            return MetiArchive.open(), buf.getvalue()

    for exc in (MountDisconnected("down"), PermissionError("denied"), OSError("io")):
        arc, out = _open(exc)
        _eq(arc.root, None, f"archive off on {type(exc).__name__}")
        if "warn meti archive off:" not in out:
            _fail(f"expected warn for {type(exc).__name__}: {out!r}")

    try:
        _open(RuntimeError("bug"))
    except RuntimeError as e:
        _eq(str(e), "bug", "unexpected error propagates from open")
    else:
        _fail("RuntimeError must propagate from MetiArchive.open")


class _ErrArchive:
    def __init__(self, errors: list[BaseException | None]) -> None:
        self.errors = list(errors)
        self.calls: list[str] = []

    def missing(self, kind: str, names: set[str]) -> set[str]:
        return set()

    def get(self, client, f, kind: str, refetch: bool) -> tuple[bytes, str]:
        self.calls.append(f.name)
        err = self.errors.pop(0)
        if err is not None:
            raise err
        return b"data", "meti"


def _ingest_mapping() -> dict:
    return {
        "seidou": {"items": ITEMS, "metrics": METRICS},
        "sekiyu": {"items": [{"material": "lpg", "metric": "import",
                              "kakuho": {"blocks": ["液化石油ガス"], "column": "輸入"}}]},
    }


def test_ingest_meti_skips_io_only() -> None:
    mapping = _ingest_mapping()
    f1 = MetiFile("https://x/a.xlsx", REVISION_SOKUHO)
    f2 = MetiFile("https://x/b.xlsx", REVISION_SOKUHO)
    client = SimpleNamespace(get=lambda url: b"<html>")
    init_db()
    db = SessionLocal()
    try:
        arc = _ErrArchive([OSError("disk"), None])
        with patch("jobs.supply.material_stats.MetiClient", return_value=client), \
             patch("jobs.supply.material_stats.seidou_files", return_value=[f1, f2]), \
             patch("jobs.supply.material_stats.meti_seidou_records", return_value=[]), \
             redirect_stdout(io.StringIO()):
            fails: list[str] = []
            ingest_meti_seidou(db, mapping, archive=arc, fails=fails)
        _eq(fails, [f1.name], "seidou skips IO on one file")
        _eq(arc.calls, [f1.name, f2.name], "seidou continues after IO")

        arc = _ErrArchive([RuntimeError("parse bug")])
        with patch("jobs.supply.material_stats.MetiClient", return_value=client), \
             patch("jobs.supply.material_stats.seidou_files", return_value=[f1]), \
             redirect_stdout(io.StringIO()):
            try:
                ingest_meti_seidou(db, mapping, archive=arc, fails=[])
            except RuntimeError as e:
                _eq(str(e), "parse bug", "seidou unexpected error propagates")
            else:
                _fail("seidou RuntimeError must propagate")

        arc = _ErrArchive([OSError("disk"), None])
        with patch("jobs.supply.material_stats.MetiClient", return_value=client), \
             patch("jobs.supply.material_stats.sekiyu_files", return_value=[f1, f2]), \
             patch("jobs.supply.material_stats.sekiyu_sokuho_cells", return_value=[]), \
             redirect_stdout(io.StringIO()):
            fails = []
            ingest_meti_sekiyu(db, mapping, archive=arc, fails=fails)
        _eq(fails, [f1.name], "sekiyu skips IO on one file")
        _eq(arc.calls, [f1.name, f2.name], "sekiyu continues after IO")

        arc = _ErrArchive([RuntimeError("parse bug")])
        with patch("jobs.supply.material_stats.MetiClient", return_value=client), \
             patch("jobs.supply.material_stats.sekiyu_files", return_value=[f1]), \
             redirect_stdout(io.StringIO()):
            try:
                ingest_meti_sekiyu(db, mapping, archive=arc, fails=[])
            except RuntimeError as e:
                _eq(str(e), "parse bug", "sekiyu unexpected error propagates")
            else:
                _fail("sekiyu RuntimeError must propagate")
    finally:
        db.close()


def test_ingest_meti_skips_broken_excel() -> None:
    mapping = _ingest_mapping()
    f1 = MetiFile("https://x/a.xlsx", REVISION_SOKUHO)
    f2 = MetiFile("https://x/b.xlsx", REVISION_SOKUHO)
    client = SimpleNamespace(get=lambda url: b"<html>")
    init_db()
    db = SessionLocal()
    try:
        arc = _ErrArchive([MetiError("no sheet 実数表"), None])
        buf = io.StringIO()
        with patch("jobs.supply.material_stats.MetiClient", return_value=client), \
             patch("jobs.supply.material_stats.seidou_files", return_value=[f1, f2]), \
             patch("jobs.supply.material_stats.meti_seidou_records", return_value=[]), \
             redirect_stdout(buf):
            fails: list[str] = []
            ingest_meti_seidou(db, mapping, archive=arc, fails=fails)
        _eq(fails, [f1.name], "seidou skips MetiError on one file")
        _eq(arc.calls, [f1.name, f2.name], "seidou continues after MetiError")
        if f"fail meti seidou {f1.name}:" not in buf.getvalue():
            _fail(f"seidou must log the skipped file: {buf.getvalue()!r}")

        arc = _ErrArchive([MetiError("empty or not an xlsx"), None])
        buf = io.StringIO()
        with patch("jobs.supply.material_stats.MetiClient", return_value=client), \
             patch("jobs.supply.material_stats.sekiyu_files", return_value=[f1, f2]), \
             patch("jobs.supply.material_stats.sekiyu_sokuho_cells", return_value=[]), \
             redirect_stdout(buf):
            fails = []
            ingest_meti_sekiyu(db, mapping, archive=arc, fails=fails)
        _eq(fails, [f1.name], "sekiyu skips MetiError on one file")
        _eq(arc.calls, [f1.name, f2.name], "sekiyu continues after MetiError")
        if f"fail meti sekiyu {f1.name}:" not in buf.getvalue():
            _fail(f"sekiyu must log the skipped file: {buf.getvalue()!r}")
    finally:
        db.close()


def test_meti_ledger() -> None:
    from datetime import date
    sys.path.insert(0, str(ROOT / "deploy"))
    from alert_patterns import ALERT_PATTERNS

    pages = [re.compile(a.regex) for a in ALERT_PATTERNS if a.source == "batch"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "ledger.json"
        led = MetiLedger(path)
        _eq(led.record("sekiyu", ["se.xlsx"], date(2026, 10, 1), 3), None, "ledger first fail")
        _eq(led.record("sekiyu", ["se.xlsx"], date(2026, 10, 3), 3), None, "ledger fail 2 days")
        line = led.record("sekiyu", ["list"], date(2026, 10, 4), 3)
        _eq(bool(line and "not fetched for 3 days" in line and "n=1" in line), True, "ledger reports after 3 days")
        _eq(any(r.search(line) for r in pages), False, "ledger line must not extra-page")
        _eq(led.record("seidou", [], date(2026, 10, 4), 3), None, "ledger ok")
        led.save()
        led = MetiLedger(path)
        _eq(led.data["seidou"], {"ok": "2026-10-04"}, "ledger saved")
        _eq(led.record("seidou", ["h.xlsx"], date(2026, 10, 6), 3), None, "ledger counts from last ok")
        _eq(led.record("seidou", ["h.xlsx"], date(2026, 10, 7), 3) is not None, True, "ledger reports 3 days after ok")
        _eq(led.record("seidou", [], date(2026, 10, 8), 3), None, "ledger recovers")
        _eq("fail_since" in led.data["seidou"], False, "ledger clears fail_since")
    _eq(MetiLedger(None).record("seidou", ["x"], date(2026, 10, 1), 0) is not None, True, "ledger without file")


class _WafClient:
    """Every METI GET is a WAF challenge (list pages included)."""

    def __init__(self, *a, **k) -> None:
        pass

    def get(self, url: str) -> bytes:
        raise MetiChallenged(f"WAF challenge: {url}")


class _ListThenWafClient:
    """List HTML succeeds; the first Excel GET is a WAF challenge (ingest stops)."""

    def __init__(self, *a, **k) -> None:
        pass

    def get(self, url: str) -> bytes:
        if url.endswith(".xlsx"):
            raise MetiChallenged(f"WAF challenge: {url}")
        return (
            '<a href="resourceData/08_seidou/sokuho/h2daa202608s.xlsx">'
            '<a href="resourceData/08_seidou/kakuho/2026/h2daa202607_jikei.xlsx">'
        ).encode()


def _batch_pages(out: str) -> list[str]:
    sys.path.insert(0, str(ROOT / "deploy"))
    from alert_patterns import should_page

    return [ln for ln in out.splitlines() if should_page("batch", ln)]


def _run_material_stats(argv: list[str], client=None) -> tuple[int, str]:
    from contextlib import redirect_stdout
    from unittest.mock import patch

    buf = io.StringIO()
    with patch.object(material_stats_job, "ESTAT_APP_ID", ""), \
            patch.object(material_stats_job.MetiArchive, "open", return_value=MetiArchive(None)), \
            patch.object(sys, "argv", ["material_stats.py", *argv]), \
            redirect_stdout(buf):
        if client is not None:
            with patch.object(material_stats_job, "MetiClient", client):
                rc = material_stats_job.main()
        else:
            rc = material_stats_job.main()
    return rc, buf.getvalue()


def test_material_stats_waf_summary_pages_once() -> None:
    """WAF で seidou/sekiyu が止まっても、page するのは summary の failed=K 1 行だけ。"""
    init_db()
    with tempfile.TemporaryDirectory() as tmp:
        ledger = Path(tmp) / "ledger.json"
        ledger.write_text(
            '{"seidou": {"fail_since": "2026-01-01"}, "sekiyu": {"fail_since": "2026-01-01"}}',
            encoding="utf-8",
        )
        rc, out = _run_material_stats(
            ["--source", "meti", "--ledger", str(ledger)], _WafClient,
        )
    if "fail meti seidou list:" not in out or "fail meti sekiyu list:" not in out:
        _fail(f"expected two fail meti list lines: {out}")
    if "material_stats done written=0 failed=2" not in out:
        _fail(f"summary: {out}")
    if "failed=" in out.split("material_stats done", 1)[0]:
        _fail(f"ledger must not emit failed=: {out}")
    _eq(rc, 1, "waf exit")
    paged = _batch_pages(out)
    _eq(len(paged), 1, f"one page, got {paged!r}")
    if "material_stats done written=0 failed=2" not in paged[0]:
        _fail(f"page must be the summary: {paged}")


def test_material_stats_waf_file_stop() -> None:
    """一覧は取れて Excel で WAF なら、止まった 1 本を failed=1 にして非ゼロ終了。"""
    init_db()
    rc, out = _run_material_stats(["--source", "meti", "--only", "seidou"], _ListThenWafClient)
    if "fail meti seidou h2daa202608s.xlsx:" not in out:
        _fail(f"expected file stop: {out}")
    if "material_stats done written=0 failed=1" not in out:
        _fail(f"summary: {out}")
    _eq(rc, 1, "file waf exit")
    paged = _batch_pages(out)
    _eq(len(paged), 1, f"one page, got {paged!r}")
    if "failed=1" not in paged[0]:
        _fail(f"page must be the summary: {paged}")


def test_material_stats_ok_failed_zero() -> None:
    init_db()
    rc, out = _run_material_stats(["--source", "estat"])
    if "material_stats done written=0 failed=0" not in out:
        _fail(f"ok summary: {out}")
    _eq(rc, 0, "ok exit")
    _eq(_batch_pages(out), [], f"failed=0 must not page: {out}")


def main() -> None:
    test_config_mapping()
    test_labels()
    test_estat_old_layout()
    test_alternatives_and_units()
    test_meti()
    test_sekiyu_kakuho()
    test_sekiyu_sokuho_and_nenpo()
    test_meti_waf_challenge()
    test_meti_archive()
    test_meti_archive_open_expected_errors()
    test_ingest_meti_skips_io_only()
    test_ingest_meti_skips_broken_excel()
    test_meti_ledger()
    test_material_stats_waf_summary_pages_once()
    test_material_stats_waf_file_stop()
    test_material_stats_ok_failed_zero()
    test_trade()
    test_revision_guard()
    test_prune_and_done()
    test_apparent_and_payload()
    print("ok: material_stats")


if __name__ == "__main__":
    main()
