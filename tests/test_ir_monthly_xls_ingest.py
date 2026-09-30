"""ir_monthly_xls_ingest の純関数。ネットワークには出ない。"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))
sys.path.insert(0, str(ROOT))

from app.monthly_ingest import (  # noqa: E402
    canonicalize_element_label,
    format_monthly_cell,
    monthly_display_label,
    plan_column_years,
)
from ir_monthly_xls_ingest import (  # noqa: E402
    as_of_from_url,
    ingest_from_grid,
    ingest_from_xls,
    latest_xls,
    scale_occupancy,
)
from tools.read_xls import read_sheet_builtin  # noqa: E402

FIXTURE = ROOT / "tests" / "testdata" / "1878_history_1.xls"
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
    # 第50期 FY start 2023: need Jan-Mar 2024
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

    from ir_monthly_xls_ingest import run

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
        if set(body) != {"months", "rows", "as_of"} or body["as_of"] != "2026-09-02" or not body["rows"]:
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
    from ir_monthly_xls_ingest import main

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


def main() -> None:
    import tempfile

    test_scale_occupancy()
    test_tiny_grid_years_and_trailing()
    test_as_of_from_url()
    test_canonicalize_labels()
    test_fixture_spots()
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        test_latest_xls(tmp / "a")
        test_cli_dry_run(tmp / "b")
        test_post_sends_ingest_body(tmp / "c")
    print("ok")


if __name__ == "__main__":
    main()
