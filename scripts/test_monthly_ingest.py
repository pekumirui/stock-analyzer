"""月次 ingest: PDF列順→暦年の純関数と API。手元の stock.db は触らない。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-ingest-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.monthly_ingest import (  # noqa: E402
    MonthlyIngestError,
    format_monthly_cell,
    map_pdf_columns,
    parse_monthly_unit,
    plan_column_years,
    resolve_fy_end_month,
)

GIFT_MONTHS = [11, 12, 1, 2, 3, 4, 5, 6, 7]
MARCH_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
CAL_MONTHS = list(range(1, 13))


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _err(resp) -> dict:
    body = resp.json()
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict) or "code" not in err or "message" not in err:
        _fail(f"error shape: {resp.status_code} {body!r}")
    return err


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        _fail(f"{label}: {resp.status_code} {resp.text[:400]}")


def _expect_ingest_error(fn, code: str, needle: str | None = None) -> MonthlyIngestError:
    try:
        fn()
    except MonthlyIngestError as exc:
        if exc.code != code:
            _fail(f"expected {code}, got {exc.code}: {exc.message}")
        if needle is not None and needle not in exc.message:
            _fail(f"{code} message {exc.message!r} missing {needle!r}")
        return exc
    _fail(f"expected {code}")
    raise AssertionError  # unreachable


def test_mapping_examples() -> None:
    layout, mapping = plan_column_years(GIFT_MONTHS, 10, date(2026, 8, 5))
    if layout != "fiscal":
        _fail(f"gift layout: {layout}")
    expect = {11: 2025, 12: 2025, 1: 2026, 2: 2026, 3: 2026, 4: 2026, 5: 2026, 6: 2026, 7: 2026}
    if mapping != expect:
        _fail(f"gift mapping: {mapping}")

    fy, layout, mapping = map_pdf_columns(
        GIFT_MONTHS,
        [
            (date(2026, 5, 8), date(2025, 10, 31)),
            (date(2025, 5, 9), date(2024, 10, 31)),
        ],
        date(2026, 8, 5),
    )
    if fy != 10 or layout != "fiscal" or mapping != expect:
        _fail(f"gift map_pdf_columns: {fy} {layout} {mapping}")

    layout, mapping = plan_column_years(MARCH_MONTHS, 3, date(2026, 4, 10))
    if layout != "fiscal":
        _fail(f"march layout: {layout}")
    expect = {m: 2025 for m in range(4, 13)}
    expect.update({1: 2026, 2: 2026, 3: 2026})
    if mapping != expect:
        _fail(f"march mapping: {mapping}")

    layout, mapping = plan_column_years(CAL_MONTHS, 12, date(2027, 1, 10))
    if layout != "calendar":
        _fail(f"dec layout: {layout}")
    if mapping != {m: 2026 for m in CAL_MONTHS}:
        _fail(f"dec mapping: {mapping}")


def test_fy_end_and_layout_errors() -> None:
    as_of = date(2026, 8, 5)
    _expect_ingest_error(lambda: resolve_fy_end_month([], as_of), "NEED_FY_END")
    _expect_ingest_error(lambda: resolve_fy_end_month([(None, None)], as_of), "NEED_FY_END")
    _expect_ingest_error(
        lambda: resolve_fy_end_month([(None, date(2025, 10, 31))], as_of),
        "NEED_FY_END",
    )

    oct_rows = [
        (date(2026, 5, 8), date(2025, 10, 31)),
        (date(2025, 5, 9), date(2024, 10, 31)),
    ]
    if resolve_fy_end_month(oct_rows, as_of) != 10:
        _fail("same fy_end month should be accepted")

    # 2種の期末月があっても as_of 時点の最新開示の1か月。FY_END_CHANGED にはしない。
    changed = [
        (date(2026, 6, 1), date(2026, 3, 31)),
        (date(2026, 5, 1), date(2025, 10, 31)),
    ]
    if resolve_fy_end_month(changed, as_of) != 3:
        _fail("as-of after both disclosures should use latest month 3")
    if resolve_fy_end_month(changed, date(2026, 5, 15)) != 10:
        _fail("as-of between disclosures should use Oct")
    _expect_ingest_error(
        lambda: resolve_fy_end_month(changed, date(2026, 4, 30)),
        "NEED_FY_END",
    )

    same_day = [
        (date(2026, 6, 1), date(2026, 2, 28)),
        (date(2026, 6, 1), date(2026, 11, 30)),
    ]
    if resolve_fy_end_month(same_day, as_of) != 11:
        _fail("same disclosed_date should pick later fiscal_year_end")

    # 実在の期変更銘柄: 後の開示の月末を使う（live DB は見ない）
    for older_m, newer_m in ((2, 11), (1, 6), (2, 3), (11, 3), (8, 11), (1, 2)):
        rows = [
            (date(2020, 6, 1), date(2020, older_m, 1)),
            (date(2021, 6, 1), date(2021, newer_m, 1)),
        ]
        got = resolve_fy_end_month(rows, date(2021, 7, 1))
        if got != newer_m:
            _fail(f"{older_m}->{newer_m}: expected {newer_m}, got {got}")

    fy, layout, mapping = map_pdf_columns(
        MARCH_MONTHS, changed, date(2026, 8, 5),
    )
    if fy != 3 or layout != "fiscal":
        _fail(f"changed-fy map_pdf_columns: {fy} {layout} {mapping}")

    exc = _expect_ingest_error(
        lambda: plan_column_years([7, 8, 9, 10, 11, 12, 1], 10, date(2026, 8, 5)),
        "AMBIGUOUS_LAYOUT",
        needle="7",
    )
    if "10" not in exc.message:
        _fail(f"AMBIGUOUS_LAYOUT should include fy_end_month: {exc.message}")


def _add_fy_end(code: str, disclosed: date, fy_end: date, disclosure_no: str) -> None:
    from app.db import SessionLocal
    from app.models import Statement

    db = SessionLocal()
    try:
        db.add(Statement(
            disclosure_no=disclosure_no,
            code=code,
            disclosed_date=disclosed,
            fiscal_year_end=fy_end,
        ))
        db.commit()
    finally:
        db.close()


def test_api_gift_ingest() -> None:
    c = TestClient(app)
    code = "92790"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 10, 31), "gift-fy-1")
    _add_fy_end(code, date(2025, 5, 9), date(2024, 10, 31), "gift-fy-2")

    months = GIFT_MONTHS
    values = {str(m): 100.0 + m for m in months}
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": months,
        "rows": [{"element": "全店 売上高", "values": values}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "gift ingest")
    body = r.json()
    if body.get("code") != code or body.get("fy_end_month") != 10 or body.get("layout") != "fiscal":
        _fail(f"gift ingest header: {body!r}")
    if body.get("as_of") != "2026-08-05":
        _fail(f"as_of: {body!r}")
    mapping = body.get("mapping")
    expect_map = {str(m): (2025 if m >= 11 else 2026) for m in months}
    if mapping != expect_map:
        _fail(f"mapping: {mapping!r}")

    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET 2025")
    els = r.json()["elements"]
    if [e["element"] for e in els] != ["全店 売上高"]:
        _fail(f"2025 elements: {els!r}")
    if els[0].get("unit") != "number":
        _fail(f"omitted unit defaults to number: {els!r}")
    v2025 = {str(k): float(val) for k, val in els[0]["values"].items()}
    if set(v2025) != {"11", "12"}:
        _fail(f"GET 2025 months: {v2025!r}")

    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET 2026")
    v2026 = {str(k): float(val) for k, val in r.json()["elements"][0]["values"].items()}
    if set(v2026) != {"1", "2", "3", "4", "5", "6", "7"}:
        _fail(f"GET 2026 months: {v2026!r}")


def test_api_errors() -> None:
    c = TestClient(app)

    r = c.post("/api/v1/tables/99990/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "売上高", "values": {"11": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "NEED_FY_END")
    if _err(r)["code"] != "NEED_FY_END":
        _fail(f"NEED_FY_END: {r.json()!r}")

    _add_fy_end("88880", date(2026, 6, 1), date(2026, 3, 31), "chg-1")
    _add_fy_end("88880", date(2026, 5, 1), date(2025, 10, 31), "chg-2")
    r = c.post("/api/v1/tables/88880/ingest", json={
        "months": MARCH_MONTHS,
        "rows": [{"element": "売上高", "values": {"4": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "fy change as-of")
    body = r.json()
    if body.get("fy_end_month") != 3 or body.get("layout") != "fiscal":
        _fail(f"as-of after fy change: {body!r}")

    r = c.post("/api/v1/tables/88880/ingest", json={
        "months": MARCH_MONTHS,
        "rows": [{"element": "売上高", "values": {"4": 1}}],
        "as_of": "2026-04-30",
    })
    _expect(r, 400, "NEED_FY_END before statements")
    if _err(r)["code"] != "NEED_FY_END":
        _fail(f"as_of before fy_end rows: {r.json()!r}")

    # 2月と11月: 後の開示が11月。FY_END_CHANGED にはしない。
    _add_fy_end("26980", date(2020, 4, 10), date(2020, 2, 29), "candu-feb")
    _add_fy_end("26980", date(2020, 12, 15), date(2020, 11, 30), "candu-nov")
    r = c.post("/api/v1/tables/26980/ingest", json={
        "months": CAL_MONTHS,
        "rows": [{"element": "売上高", "values": {"1": 1}}],
        "as_of": "2021-06-01",
    })
    _expect(r, 200, "2 and 11 as-of")
    if r.json().get("fy_end_month") != 11 or r.json().get("layout") != "calendar":
        _fail(f"2/11 as-of: {r.json()!r}")

    # 同日開示は fiscal_year_end の新しい方
    _add_fy_end("36120", date(2021, 6, 1), date(2021, 2, 28), "world-feb")
    _add_fy_end("36120", date(2021, 6, 1), date(2021, 3, 31), "world-mar")
    r = c.post("/api/v1/tables/36120/ingest", json={
        "months": MARCH_MONTHS,
        "rows": [{"element": "売上高", "values": {"4": 1}}],
        "as_of": "2021-06-01",
    })
    _expect(r, 200, "same-day later fy_end")
    if r.json().get("fy_end_month") != 3:
        _fail(f"same-day later fy_end: {r.json()!r}")

    _add_fy_end("77770", date(2026, 5, 1), date(2025, 10, 31), "amb-1")
    r = c.post("/api/v1/tables/77770/ingest", json={
        "months": [7, 8, 9, 10, 11, 12, 1],
        "rows": [{"element": "売上高", "values": {"7": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "AMBIGUOUS_LAYOUT")
    err = _err(r)
    if err["code"] != "AMBIGUOUS_LAYOUT" or "7" not in err["message"] or "10" not in err["message"]:
        _fail(f"AMBIGUOUS_LAYOUT: {r.json()!r}")

    r = c.post("/api/v1/tables/77770/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "売上高", "values": {"8": 1.0}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "month not in months")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"extra month: {r.json()!r}")


def test_all_or_nothing_similar() -> None:
    c = TestClient(app)
    code = "66660"
    _add_fy_end(code, date(2026, 5, 1), date(2025, 10, 31), "sim-1")
    r = c.post(f"/api/v1/tables/{code}/2025/elements", json={"element": "全店売上高"})
    _expect(r, 201, "seed similar")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "全店 売上高", "values": {"11": 1, "1": 2}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 409, "similar ingest")
    if _err(r)["code"] != "ELEMENT_SIMILAR_EXISTS":
        _fail(f"similar ingest: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 404, "2026 rolled back")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"2026 should not exist: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "全店 売上高", "values": {"11": 1, "1": 2}, "force": True}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "force ingest")
    r = c.get(f"/api/v1/tables/{code}/2025")
    names = [e["element"] for e in r.json()["elements"]]
    if "全店 売上高" not in names or "全店売上高" not in names:
        _fail(f"force elements: {names!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET 2026 after force")
    if [e["element"] for e in r.json()["elements"]] != ["全店 売上高"]:
        _fail(f"2026 after force: {r.json()!r}")


def test_api_alnum_code() -> None:
    c = TestClient(app)
    _add_fy_end("262A0", date(2026, 5, 8), date(2025, 10, 31), "alnum-fy-1")
    months = GIFT_MONTHS
    values = {str(m): 100.0 + m for m in months}
    r = c.post("/api/v1/tables/262A/ingest", json={
        "months": months,
        "rows": [{"element": "全店 売上高", "values": values}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "alnum ingest")
    if r.json().get("code") != "262A0":
        _fail(f"alnum ingest code: {r.json()!r}")
    r = c.get("/api/v1/tables/262A0/2025")
    _expect(r, 200, "alnum GET 2025")
    if r.json()["code"] != "262A0":
        _fail(f"alnum GET code: {r.json()!r}")
    r = c.post("/api/v1/tables/abc/ingest", json={
        "months": months,
        "rows": [{"element": "売上高", "values": {"11": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "abc ingest")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"abc ingest: {r.json()!r}")


def test_unit_display_and_ingest() -> None:
    if format_monthly_cell(125.8, "yoy_index") != "125.8":
        _fail(f"yoy_index display: {format_monthly_cell(125.8, 'yoy_index')!r}")
    if format_monthly_cell(105.2, "yoy_index") != "105.2":
        _fail(f"yoy_index 105.2: {format_monthly_cell(105.2, 'yoy_index')!r}")
    if format_monthly_cell(-7.1, "yoy_pct") != "92.9":
        _fail(f"yoy_pct display: {format_monthly_cell(-7.1, 'yoy_pct')!r}")
    if format_monthly_cell(303, "number") != "303":
        _fail(f"number display: {format_monthly_cell(303, 'number')!r}")
    if format_monthly_cell(None, "number") != "":
        _fail("empty cell")
    try:
        parse_monthly_unit("percent")
    except MonthlyIngestError as exc:
        if exc.status != 400 or exc.code != "INVALID_PARAM":
            _fail(f"invalid unit: {exc.status} {exc.code}")
    else:
        _fail("invalid unit should 400")

    c = TestClient(app)
    code = "55550"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 10, 31), "unit-fy-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11, 12, 1],
        "rows": [
            {"element": "全店売上高", "values": {"11": 125.8}, "unit": "yoy_index"},
            {"element": "売上高前年比", "values": {"11": -7.1}, "unit": "yoy_pct"},
            {"element": "店舗数", "values": {"11": 303}, "unit": "number"},
        ],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest with units")
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET ingest units")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店売上高"]["unit"] != "yoy_index" or float(by["全店売上高"]["values"]["11"]) != 125.8:
        _fail(f"ingest yoy_index stored: {by['全店売上高']!r}")
    if format_monthly_cell(float(by["全店売上高"]["values"]["11"]), "yoy_index") != "125.8":
        _fail("ingest yoy_index display")
    if by["売上高前年比"]["unit"] != "yoy_pct" or float(by["売上高前年比"]["values"]["11"]) != -7.1:
        _fail(f"ingest yoy_pct stored: {by['売上高前年比']!r}")
    if format_monthly_cell(float(by["売上高前年比"]["values"]["11"]), "yoy_pct") != "92.9":
        _fail("ingest yoy_pct display")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"ingest number stored: {by['店舗数']!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11, 12, 1],
        "rows": [{"element": "x", "values": {"11": 1}, "unit": "percent"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "ingest invalid unit")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"ingest invalid unit: {r.json()!r}")

    # magnitude must not decide unit: 125.8 with yoy_pct stays 125.8
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11, 12, 1],
        "rows": [{"element": "大きい指数", "values": {"11": 125.8}, "unit": "yoy_pct"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 125.8 as yoy_pct")
    r = c.get(f"/api/v1/tables/{code}/2025")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["大きい指数"]["unit"] != "yoy_pct" or float(by["大きい指数"]["values"]["11"]) != 125.8:
        _fail(f"must not convert 125.8: {by['大きい指数']!r}")


def main() -> None:
    test_mapping_examples()
    test_fy_end_and_layout_errors()
    test_api_gift_ingest()
    test_api_errors()
    test_all_or_nothing_similar()
    test_api_alnum_code()
    test_unit_display_and_ingest()
    print("ok: monthly ingest")


if __name__ == "__main__":
    main()
