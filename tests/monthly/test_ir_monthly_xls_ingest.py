"""ir_monthly_xls_ingest の純関数。ネットワークには出ない。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.monthly.ingest import (
    canonicalize_element_label,
    format_monthly_cell,
    monthly_display_label,
    plan_column_years,
)
from jobs.monthly.ir_monthly_xls_ingest import (
    COLUMNS,
    as_of_from_url,
    ingest_from_grid,
    ingest_from_xls,
    latest_xls,
    pick_xlsx_sheet,
    round_digits,
    scale_occupancy,
    spec_for,
)
from app.monthly.read_xls import read_sheet_builtin

FIXTURE = ROOT / "tests" / "monthly" / "testdata" / "1878_history_1.xls"
FIXTURE_7606 = ROOT / "tests" / "monthly" / "testdata" / "7606_sokuho2027.xlsx"
SOURCE_URL = "https://www.kentaku.co.jp/ir/library/monthly-report/__icsFiles/afieldfile/2026/09/02/history_1.xls"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _blank() -> list[Any]:
    return [""] * 16


def _row(
    fy: Any,
    month: int,
    *,
    new: float | None = None,
    cancel: float | None = None,
    orders: float | None = None,
    staff: float | None = None,
    per: float | None = None,
    tenants: float | None = None,
    occ: float | None = None,
    occ_c: float | None = None,
    occ_r: float | None = None,
    vac_m: float | None = None,
    vac: float | None = None,
    vac_n: float | None = None,
    vac_e: float | None = None,
) -> list[Any]:
    row = _blank()
    row[0] = fy
    row[1] = float(month)
    row[2] = "" if new is None else new
    row[3] = "" if cancel is None else cancel
    row[4] = "" if orders is None else orders
    row[5] = "" if staff is None else staff
    row[6] = "" if per is None else per
    row[7] = 999.0
    row[8] = "" if tenants is None else tenants
    row[9] = "" if occ is None else occ
    row[10] = "" if occ_c is None else occ_c
    row[11] = "" if occ_r is None else occ_r
    row[12] = "" if vac_m is None else vac_m
    row[13] = "" if vac is None else vac
    row[14] = "" if vac_n is None else vac_n
    row[15] = "" if vac_e is None else vac_e
    return row


def _header() -> list[list[Any]]:
    note = _blank()
    note[0] = "※ note"
    month = _blank()
    month[1] = "月"
    month[2] = "新規受注高"
    month[4] = "受注高"
    month[7] = "受注トレンド"
    month[9] = "家賃ベース入居率"
    units = _blank()
    units[9] = "合計"
    units[10] = "事業用"
    units[11] = "居住用"
    units[13] = "合計"
    units[14] = "新築"
    units[15] = "既存"
    return [note, month, units]


def tiny_grid() -> list[list[Any]]:
    """2023-04..2024-03 then 2024-04..2024-08 filled, rest of FY empty."""
    rows = _header()
    for m in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3):
        fy: Any = ""
        if m == 4:
            fy = "第５０期"
        elif m == 5:
            fy = 2023.0
        orders = 1000.0 + m
        occ = 0.97
        vac_m = 1.26
        rows.append(_row(fy, m, orders=orders, occ=occ, vac_m=vac_m, vac_e=10.0))
    for m in (4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3):
        fy = "第５１期" if m == 4 else (2024.0 if m == 5 else "")
        if m <= 8 and m >= 4:
            rows.append(_row(
                fy, m,
                new=10.0 + m,
                cancel=1.0,
                orders=29296.646549 if m == 4 else 2000.0 + m,
                occ=0.9785 if m == 8 else 0.97,
                vac_m=1.26,
                vac_e=20.0,
            ))
        else:
            rows.append(_row(fy, m))
    return rows


def _orders_row(payload: dict[str, Any]) -> dict[str, Any]:
    for row in payload["rows"]:
        if row["element"] == "受注高":
            return row
    _fail("missing 受注高")
    raise AssertionError


def _occ_row(payload: dict[str, Any]) -> dict[str, Any]:
    for row in payload["rows"]:
        if row["element"] == "家賃ベース入居率":
            return row
    _fail("missing 家賃ベース入居率")
    raise AssertionError


def test_scale_occupancy() -> None:
    if scale_occupancy(0.9785) != 97.85:
        _fail(f"0.9785 -> {scale_occupancy(0.9785)}")
    if scale_occupancy(0.9748) != 97.48:
        _fail(f"0.9748 -> {scale_occupancy(0.9748)}")
    if scale_occupancy(97.85) != 97.85:
        _fail("already-percent must stay")
    if scale_occupancy(1.26) != 126.0:
        _fail("1.26 is still in the Excel-percent band")


def test_tiny_grid_years_and_trailing() -> None:
    payload = ingest_from_grid(tiny_grid(), as_of=date(2024, 9, 2), from_year=2024)
    if payload["as_of"] != "2024-09-02":
        _fail(f"as_of {payload['as_of']}")
    if "year" in payload:
        _fail("year must not be sent")
    months = payload["months"]
    if months[0] != 1:
        _fail(f"must start at January so layout is not fiscal: {months[:4]}")
    if months != [1, 2, 3, 4, 5, 6, 7, 8]:
        _fail(f"months {months}")
    orders = _orders_row(payload)
    if orders["values"][0] != 1001.0:
        _fail(f"2024-01 受注高 {orders['values'][0]}")
    if orders["values"][3] != 29296.646549:
        _fail(f"2024-04 受注高 {orders['values'][3]}")
    occ = _occ_row(payload)
    if occ["values"][7] != 97.85:
        _fail(f"2024-08 occupancy {occ['values'][7]}")
    vac = next(r for r in payload["rows"] if r["element"] == "空室在庫")
    if vac["values"][7] != 1.26:
        _fail(f"空室在庫 must not be *100: {vac['values'][7]}")
    names = [r["element"] for r in payload["rows"]]
    if "受注トレンド" in names:
        _fail("derived 受注トレンド must be omitted")
    layout, mapping = plan_column_years(months, 3, date(2024, 9, 2))
    if layout != "calendar":
        _fail(f"tiny 2024 Jan-Aug should be calendar, got {layout}")
    if mapping[-1] != (8, 2024) or mapping[0] != (1, 2024):
        _fail(f"mapping {mapping}")


def test_as_of_from_url() -> None:
    got = as_of_from_url(SOURCE_URL)
    if got != date(2026, 9, 2):
        _fail(f"url as_of {got}")
    payload = ingest_from_grid(tiny_grid(), source_url=SOURCE_URL, from_year=2024)
    if payload["as_of"] != "2026-09-02":
        _fail(f"source_url as_of {payload['as_of']}")
    fallback = ingest_from_grid(tiny_grid(), from_year=2024)
    if fallback["as_of"] != "2024-08-31":
        _fail(f"last filled month-end {fallback['as_of']}")


def test_canonicalize_labels() -> None:
    if canonicalize_element_label("既存 空室数", "number") != "空室数 既存":
        _fail("既存 is a scope token")
    if canonicalize_element_label("新築 空室数", "number") != "新築 空室数":
        _fail("新築 空室数")
    if canonicalize_element_label("家賃ベース入居率 合計", "number") != "家賃ベース入居率":
        _fail("合計 dropped")
    if canonicalize_element_label("新規受注高", "number") != "新規受注高":
        _fail("新規受注高 must not become 出店")


def test_latest_xls(tmp: Path) -> None:
    folder = tmp / "1878"
    folder.mkdir(parents=True)
    older = folder / "20260901_1878_大東建託_history_1.xls"
    newer = folder / "20260918_1878_大東建託_history_1.xls"
    bad_date = folder / "20269999_1878_大東建託_history_1.xls"
    older.write_bytes(b"old")
    newer.write_bytes(b"new")
    bad_date.write_bytes(b"bad")
    got = latest_xls(tmp)
    if got != newer:
        _fail(f"latest {got} (an invalid filename date must not win)")


def test_post_sends_ingest_body(tmp: Path) -> None:
    import io
    from contextlib import redirect_stdout

    from jobs.monthly.ir_monthly_xls_ingest import run

    class Client:
        def __init__(self, status: int) -> None:
            self.status = status
            self.posts: list[tuple[str, dict[str, Any]]] = []

        def post_json(self, url: str, body: dict[str, Any]) -> tuple[int, Any]:
            self.posts.append((url, body))
            return self.status, {"ok": self.status == 200}

    for status, want_rc in ((200, 0), (409, 1)):
        client = Client(status)
        with redirect_stdout(io.StringIO()):
            rc = run(
                xls=FIXTURE, dest=tmp, as_of=date(2026, 9, 2), source_url=None, from_year=2024,
                dry_run=False, post=True, base_url="http://127.0.0.1:8000/", client=client,
            )
        if rc != want_rc:
            _fail(f"POST status {status} rc {rc}")
        if len(client.posts) != 1:
            _fail(f"posts {client.posts}")
        url, body = client.posts[0]
        if url != "http://127.0.0.1:8000/api/v1/tables/1878/ingest":
            _fail(f"POST url {url}")
        if (set(body) != {"months", "rows", "as_of", "via"} or body["via"] != "ir_xls"
                or body["as_of"] != "2026-09-02" or not body["rows"]):
            _fail(f"POST body keys {sorted(body)} as_of={body.get('as_of')}")
    client = Client(200)
    with redirect_stdout(io.StringIO()):
        rc = run(
            xls=FIXTURE, dest=tmp, as_of=date(2026, 9, 2), source_url=None, from_year=2024,
            dry_run=False, post=True, base_url="https://example.test", client=client,
        )
    if rc != 1 or client.posts:
        _fail(f"remote base_url must not POST rc={rc}")


def test_fixture_spots() -> None:
    if not FIXTURE.is_file():
        _fail(f"missing fixture {FIXTURE}")
    grid = read_sheet_builtin(FIXTURE)
    if grid[2][1] != "月" or grid[2][4] != "受注高":
        _fail(f"header {grid[2][:6]}")
    payload = ingest_from_grid(
        grid, as_of=date(2026, 9, 2), source_url=SOURCE_URL, from_year=2024,
    )
    if payload["as_of"] != "2026-09-02":
        _fail(payload["as_of"])
    months = payload["months"]
    if months[0] != 1 or months[-1] != 8:
        _fail(f"months start/end {months[0]} {months[-1]} n={len(months)}")
    if len(months) != 32:
        _fail(f"2024-01..2026-08 should be 32 months, got {len(months)}")
    layout, mapping = plan_column_years(months, 3, date(2026, 9, 2))
    if layout != "partial":
        _fail(f"multi-year wrap should be partial, got {layout}")
    if mapping[0] != (1, 2024) or mapping[3] != (4, 2024):
        _fail(f"2024 map {mapping[:5]}")
    if mapping[19] != (8, 2025):
        _fail(f"2025-08 map {mapping[19]}")
    if mapping[-1] != (8, 2026):
        _fail(f"2026-08 map {mapping[-1]}")
    orders = _orders_row(payload)["values"]
    occ = _occ_row(payload)["values"]
    if orders[3] != 29296.646549:
        _fail(f"2024-04 受注高 {orders[3]}")
    if occ[19] != 97.48:
        _fail(f"2025-08 入居率 {occ[19]}")
    if orders[-1] != 35646.391:
        _fail(f"2026-08 受注高 {orders[-1]}")
    if occ[-1] != 97.85:
        _fail(f"2026-08 入居率 {occ[-1]}")
    if format_monthly_cell(orders[3], "number", "受注高") != "29297":
        _fail("display 2024-04 受注高")
    if format_monthly_cell(occ[19], "number", "家賃ベース入居率") != "97.48":
        _fail("display 2025-08 入居率")
    if format_monthly_cell(orders[-1], "number", "受注高") != "35646":
        _fail("display 2026-08 受注高")
    if monthly_display_label("受注高", "number") != "受注高（百万円）":
        _fail("label 受注高")
    if monthly_display_label("家賃ベース入居率", "number") != "家賃ベース入居率（%）":
        _fail("label 入居率")
    from_xls = ingest_from_xls(FIXTURE, as_of=date(2026, 9, 2), from_year=2024)
    if from_xls["rows"][2]["values"][3] != 29296.646549:
        _fail("ingest_from_xls must match grid path")
    for row in payload["rows"]:
        if len(row["values"]) != len(months):
            _fail(f"values length {row['element']}")
        if row["unit"] != "number" or row.get("force") is not True:
            _fail(f"row meta {row}")


def test_cli_dry_run(tmp: Path) -> None:
    from io import StringIO
    from jobs.monthly.ir_monthly_xls_ingest import main

    dest = tmp / "monthly"
    folder = dest / "1878"
    folder.mkdir(parents=True)
    target = folder / "20260918_1878_大東建託_history_1.xls"
    target.write_bytes(FIXTURE.read_bytes())
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = main([
            "--dest", str(dest),
            "--as-of", "2026-09-02",
            "--from-year", "2024",
            "--dry-run",
        ])
        rc2 = main(["--dry-run", "--post"])
    finally:
        sys.stdout = old
    if rc != 0:
        _fail(f"dry-run rc {rc}")
    if rc2 != 2:
        _fail(f"dry-run+post rc {rc2}")
    if '"as_of": "2026-09-02"' not in buf.getvalue():
        _fail("dry-run JSON missing as_of")


def _named(payload: dict[str, Any], name: str) -> dict[str, Any]:
    for row in payload["rows"]:
        if row["element"] == name:
            return row
    _fail(f"missing {name}")
    raise AssertionError


def test_1878_columns_from_json() -> None:
    spec = spec_for("1878")
    names = [c.element for c in spec.columns]
    if names != [c.element for c in COLUMNS]:
        _fail(f"COLUMNS drifted from json: {names}")
    if spec.layout != "fy_blocks" or spec.force is not True or spec.exts != (".xls",) or spec.digits is not None:
        _fail(f"1878 spec meta {spec}")
    if any(c.element in {"受注トレンド", "管理戸数"} for c in spec.columns):
        _fail("1878 json must omit derived columns")
    occ = [c.element for c in spec.columns if c.kind == "occupancy"]
    if occ != ["家賃ベース入居率", "家賃ベース入居率 事業用", "家賃ベース入居率 居住用"]:
        _fail(f"occupancy cols {occ}")


def test_pick_xlsx_sheet() -> None:
    if pick_xlsx_sheet(["2026.05", "2026.08", "2026.07"], "latest_ym") != "2026.08":
        _fail("latest_ym")
    if pick_xlsx_sheet(["wk", "70", "71"], "latest_int") != "71":
        _fail("latest_int")
    if pick_xlsx_sheet(["A", "B"], "first") != "A":
        _fail("first")


def test_round_yoy_digits() -> None:
    if round_digits(109.1885, 1) != 109.2:
        _fail(f"109.1885 -> {round_digits(109.1885, 1)}")
    if round_digits(116.351, 1) != 116.4:
        _fail(f"116.351 -> {round_digits(116.351, 1)}")
    if round_digits(111.08804814301179, 1) != 111.1:
        _fail("111.088…")
    spec = spec_for("7606")
    if spec.digits != 1:
        _fail(f"7606 digits {spec.digits}")
    yoy = [c for sec in spec.sections if sec.unit == "yoy_index" for c in sec.rows]
    stores = [c for sec in spec.sections if sec.unit == "number" for c in sec.rows]
    if not yoy or any(c.digits != 1 for c in yoy):
        _fail(f"7606 yoy digits {[c.digits for c in yoy]}")
    if not stores or any(c.digits is not None for c in stores):
        _fail("7606 store counts must not inherit yoy digits")
    if spec_for("1878").digits is not None:
        _fail("1878 must not round")


def test_7606_fixture() -> None:
    if not FIXTURE_7606.is_file():
        _fail(f"missing fixture {FIXTURE_7606}")
    payload = ingest_from_xls(FIXTURE_7606, code="7606", from_year=2024)
    if payload["as_of"] != "2026-09-02":
        _fail(f"7606 as_of {payload['as_of']}")
    if "year" in payload:
        _fail("year must not be sent")
    if payload["months"] != [4, 5, 6, 7, 8]:
        _fail(f"7606 months {payload['months']}")
    names = [r["element"] for r in payload["rows"]]
    if "トレンドマーケット" in names or any("まで" in n for n in names):
        _fail(f"7606 leaked derived rows {names}")
    all_store = _named(payload, "全社")
    if all_store["unit"] != "yoy_index" or all_store.get("force"):
        _fail(f"7606 全社 meta {all_store}")
    if all_store["values"][-1] != 111.1:
        _fail(f"7606 2026-08 全社 {all_store['values'][-1]}")
    if any(v != round(v, 1) for v in all_store["values"]):
        _fail(f"7606 全社 not 1 decimal {all_store['values']}")
    existing = _named(payload, "小売＋ネット通販 既存店")
    if existing["values"][-1] != 108.4:
        _fail(f"7606 2026-08 既存 {existing['values'][-1]}")
    stores = _named(payload, "店舗数")
    if stores["unit"] != "number" or stores["values"][-1] != 324:
        _fail(f"7606 店舗数 {stores}")
    counted = _named(payload, "既存店カウント店舗数")
    if counted["values"][-1] != 243:
        _fail(f"7606 既存店カウント {counted['values'][-1]}")
    if canonicalize_element_label("既存店カウント店舗数", "number") != "カウント 既存 店舗数":
        _fail("7606 既存店カウント canonicalize")
    if canonicalize_element_label("全社", "yoy_index") != "全社 売上高(前年比)":
        _fail("7606 全社 canonicalize")
    if canonicalize_element_label("小売＋ネット通販 既存店 客数", "yoy_index") != "小売＋ネット通販 既存 客数(前年比)":
        _fail("7606 既存 客数 canonicalize")
    layout, mapping = plan_column_years(payload["months"], 3, date(2026, 9, 2))
    if mapping[0] != (4, 2026) or mapping[-1] != (8, 2026):
        _fail(f"7606 mapping {layout} {mapping}")


def test_cli_code_7606(tmp: Path) -> None:
    from io import StringIO
    from jobs.monthly.ir_monthly_xls_ingest import main as cli

    dest = tmp / "monthly"
    folder = dest / "7606"
    folder.mkdir(parents=True)
    target = folder / "20260902_7606_ユナイテッドアローズ_sokuho2027.xlsx"
    target.write_bytes(FIXTURE_7606.read_bytes())
    buf = StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = cli(["--dest", str(dest), "--code", "7606", "--from-year", "2024", "--dry-run"])
        rc_unknown = cli(["--code", "9999", "--dry-run"])
    finally:
        sys.stdout = old
    if rc != 0:
        _fail(f"7606 dry-run rc {rc}")
    if rc_unknown != 2:
        _fail(f"unknown code rc {rc_unknown}")
    if '"as_of": "2026-09-02"' not in buf.getvalue() or "全社" not in buf.getvalue():
        _fail("7606 dry-run JSON")


def test_run_all(tmp: Path) -> None:
    """cron の --all: config の全銘柄の最新ファイルを送る。1 銘柄の失敗で残りを止めない（KAB-117）。"""
    import io
    from contextlib import redirect_stdout

    from jobs.monthly.ir_monthly_xls_ingest import main as cli
    from jobs.monthly.ir_monthly_xls_ingest import run_all

    dest = tmp / "monthly"
    (dest / "1878").mkdir(parents=True)
    (dest / "7606").mkdir(parents=True)
    (dest / "1878" / "20260901_1878_大東建託_history_1.xls").write_bytes(b"old, must not be read")
    (dest / "1878" / "20260918_1878_大東建託_history_1.xls").write_bytes(FIXTURE.read_bytes())
    (dest / "7606" / "20260902_7606_ユナイテッドアローズ_sokuho2027.xlsx").write_bytes(FIXTURE_7606.read_bytes())

    class Client:
        def __init__(self, fail: str = "") -> None:
            self.fail = fail
            self.urls: list[str] = []

        def post_json(self, url: str, body: dict[str, Any]) -> tuple[int, Any]:
            self.urls.append(url)
            if body["via"] != "ir_xls" or not body["rows"]:
                _fail(f"run_all body {sorted(body)}")
            return (409, {"error": {}}) if self.fail and self.fail in url else (200, {"updated": []})

    def go(client: Client, **kw: Any) -> tuple[int, str]:
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = run_all(dest=dest, from_year=2024, dry_run=False, post=True,
                         base_url="http://127.0.0.1:8000", client=client, **kw)
        return rc, buf.getvalue()

    client = Client()
    rc, out = go(client)
    if rc != 0 or client.urls != ["http://127.0.0.1:8000/api/v1/tables/1878/ingest",
                                  "http://127.0.0.1:8000/api/v1/tables/7606/ingest"]:
        _fail(f"run_all rc={rc} urls={client.urls} {out!r}")
    if "post 1878 20260918_1878_" not in out or not out.rstrip().endswith(
            "ir_monthly_xls_ingest done posted=2 skipped=0 failed=0"):
        _fail(f"run_all out {out!r}")

    client = Client(fail="/1878/")
    rc, out = go(client)
    if rc != 1 or len(client.urls) != 2 or not out.rstrip().endswith("posted=1 skipped=0 failed=1"):
        _fail(f"run_all with one failure rc={rc} {out!r}")

    (dest / "7606" / "20260902_7606_ユナイテッドアローズ_sokuho2027.xlsx").unlink()
    client = Client()
    rc, out = go(client)
    if rc != 1 or len(client.urls) != 1 or "fail 7606" not in out:
        _fail(f"run_all missing workbook rc={rc} {out!r}")

    client = Client()
    with redirect_stdout(io.StringIO()):
        rc = run_all(dest=dest, from_year=2024, dry_run=False, post=True,
                     base_url="https://example.test", client=client)
    if rc != 1 or client.urls:
        _fail(f"run_all remote base_url must not POST rc={rc}")

    (dest / "7606" / "20260902_7606_ユナイテッドアローズ_sokuho2027.xlsx").write_bytes(FIXTURE_7606.read_bytes())
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc_dry = cli(["--all", "--dest", str(dest), "--from-year", "2024"])
        rc_mix = cli(["--all", "--code", "7606"])
    if rc_dry != 0 or "skipped=2" not in buf.getvalue():
        _fail(f"--all without --post must not POST rc={rc_dry} {buf.getvalue()!r}")
    if rc_mix != 2:
        _fail(f"--all with --code rc {rc_mix}")


def main() -> None:
    import tempfile

    test_scale_occupancy()
    test_tiny_grid_years_and_trailing()
    test_as_of_from_url()
    test_canonicalize_labels()
    test_1878_columns_from_json()
    test_pick_xlsx_sheet()
    test_round_yoy_digits()
    test_fixture_spots()
    test_7606_fixture()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_latest_xls(tmp / "a")
        test_cli_dry_run(tmp / "b")
        test_post_sends_ingest_body(tmp / "c")
        test_cli_code_7606(tmp / "d")
        test_run_all(tmp / "e")
    print("ok")


if __name__ == "__main__":
    main()
