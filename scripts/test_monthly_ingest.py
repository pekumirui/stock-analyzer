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
    apply_issuer_aliases,
    canonicalize_element_label,
    format_monthly_cell,
    map_pdf_columns,
    parse_monthly_unit,
    plan_column_years,
    plan_issuer_alias_cases,
    plan_yoy_duplicate_merges,
    reject_prior_period_element,
    resolve_fy_end_month,
    same_yoy_element,
)

GIFT_MONTHS = [11, 12, 1, 2, 3, 4, 5, 6, 7]
MARCH_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
CAL_MONTHS = list(range(1, 13))
# Genky 20250630: Jun2024 … Jun2025 (13 cols, month 6 twice)
GENKY_MONTHS = [6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]


def _map_dict(mapping: list[tuple[int, int]]) -> dict[int, int]:
    """一意月の比較用。重複がある mapping では使わない。"""
    return {month: year for month, year in mapping}


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


def test_canonicalize_element_label() -> None:
    if canonicalize_element_label("売上高前年同月比", "yoy_index") != "売上高(前年比)":
        _fail("strip 前年同月比 + suffix")
    if canonicalize_element_label("売上高前年比", "yoy_pct") != "売上高(前年比)":
        _fail("strip 前年比 + suffix")
    if canonicalize_element_label("既存売上高(前年比)", "yoy_index") != "既存売上高(前年比)":
        _fail("no double suffix")
    if canonicalize_element_label("既存売上高（前年比）", "yoy_index") != "既存売上高(前年比)":
        _fail("fullwidth parens to ascii suffix")
    if canonicalize_element_label("全店 売上高", "yoy_index") != "全店 売上高(前年比)":
        _fail("yoy append even without 前年比")
    if canonicalize_element_label(" 売上高 前年同月比 ", "yoy_index") != "売上高(前年比)":
        _fail("trim leftover spaces after strip")
    if canonicalize_element_label("売上高", "number") != "売上高":
        _fail("number unchanged")
    if canonicalize_element_label("売上高前年同月比", "number") != "売上高前年同月比":
        _fail("number must not strip")
    if canonicalize_element_label("店舗数", "number") != "店舗数":
        _fail("number 店舗数")
    if canonicalize_element_label("売上高前年同月比", None) != "売上高前年同月比":
        _fail("omitted unit is number: no strip/suffix")
    if not same_yoy_element("売上高", "yoy_index", "売上高(前年比)", "yoy_index"):
        _fail("short yoy name matches canonical")
    if not same_yoy_element("カラオケ全店 売上高", "yoy_index", "カラオケ全店 売上高(前年比)", "yoy_index"):
        _fail("karaoke short matches canonical")
    if same_yoy_element("売上高", "number", "売上高(前年比)", "number"):
        _fail("number 売上高 is not the yoy row")
    if same_yoy_element("売上高", "number", "売上高(前年比)", "yoy_index"):
        _fail("number 売上高 stays separate from yoy")
    planned = plan_yoy_duplicate_merges([
        ("60590", 2026, "カラオケ全店 売上高", "yoy_index"),
        ("60590", 2026, "カラオケ全店 売上高(前年比)", "yoy_index"),
        ("72030", 2024, "売上高", "number"),
        ("72030", 2024, "売上高(前年比)", "number"),
        ("44440", 2025, "売上高(前年比)", "yoy_index"),
        ("44440", 2025, "売上高", "number"),
        ("44440", 2025, "売上高前年同月比", "number"),
        ("29260", 2026, "全店 売上高前期比", "yoy_index"),
        ("29260", 2026, "店舗数", "number"),
        ("29260", 2026, "売上高", "number"),
    ])
    if planned != [
        ("29260", 2026, "全店 売上高前期比(前年比)", ["全店 売上高前期比"]),
        ("60590", 2026, "カラオケ全店 売上高(前年比)", ["カラオケ全店 売上高"]),
    ]:
        _fail(f"plan_yoy_duplicate_merges: {planned!r}")


def test_issuer_alias_cases() -> None:
    cases = plan_issuer_alias_cases()
    if not cases:
        _fail("issuer_aliases.json の rename が空")
    from app.monthly_ingest import ISSUER_ELEMENT_ALIASES
    codes = {code for code, _, _, _ in cases}
    missing = sorted(set(ISSUER_ELEMENT_ALIASES) - codes)
    if missing:
        _fail(f"rename が無い銘柄: {missing!r}")
    for code, incoming, stored, ctx in cases:
        got = apply_issuer_aliases(
            code, incoming, year=ctx.get("year"), as_of=ctx.get("as_of"),  # type: ignore[arg-type]
        )
        if got != stored:
            _fail(f"{code} {incoming!r} {ctx} → {got!r}, want {stored!r}")
        if apply_issuer_aliases("72030", incoming, year=ctx.get("year"), as_of=ctx.get("as_of")) != incoming:  # type: ignore[arg-type]
            _fail(f"other issuer must not alias {incoming!r}")

    sample_34150 = apply_issuer_aliases(
        "34150", "全社 国内 合計 売上高(前年比)", year=2099,
    )
    if sample_34150 != "日本 合計 売上高(前年比)":
        _fail(f"34150 sample: {sample_34150!r}")
    sample_26980 = apply_issuer_aliases("26980", "全社(前年比)", year=2099)
    if sample_26980 != "全社 売上高(前年比)":
        _fail(f"26980 sample: {sample_26980!r}")


def test_mapping_examples() -> None:
    layout, mapping = plan_column_years(GIFT_MONTHS, 10, date(2026, 8, 5))
    if layout != "fiscal":
        _fail(f"gift layout: {layout}")
    expect = {11: 2025, 12: 2025, 1: 2026, 2: 2026, 3: 2026, 4: 2026, 5: 2026, 6: 2026, 7: 2026}
    if _map_dict(mapping) != expect:
        _fail(f"gift mapping: {mapping}")

    fy, layout, mapping = map_pdf_columns(
        GIFT_MONTHS,
        [
            (date(2026, 5, 8), date(2025, 10, 31)),
            (date(2025, 5, 9), date(2024, 10, 31)),
        ],
        date(2026, 8, 5),
    )
    if fy != 10 or layout != "fiscal" or _map_dict(mapping) != expect:
        _fail(f"gift map_pdf_columns: {fy} {layout} {mapping}")

    layout, mapping = plan_column_years(MARCH_MONTHS, 3, date(2026, 4, 10))
    if layout != "fiscal":
        _fail(f"march layout: {layout}")
    expect = {m: 2025 for m in range(4, 13)}
    expect.update({1: 2026, 2: 2026, 3: 2026})
    if _map_dict(mapping) != expect:
        _fail(f"march mapping: {mapping}")

    layout, mapping = plan_column_years(CAL_MONTHS, 12, date(2027, 1, 10))
    if layout != "calendar":
        _fail(f"dec layout: {layout}")
    if _map_dict(mapping) != {m: 2026 for m in CAL_MONTHS}:
        _fail(f"dec mapping: {mapping}")

    # 先頭が1でも会計年度開始でもない列。暦年は最終列 + as_of。拒否しない。
    as_of_aug = date(2026, 8, 5)
    layout, mapping = plan_column_years([7], 10, as_of_aug)
    if layout != "partial":
        _fail(f"single July layout: {layout}")
    if _map_dict(mapping) != {7: 2026}:
        _fail(f"single July mapping: {mapping}")

    layout, mapping = plan_column_years([7, 8], 3, as_of_aug)
    if layout != "partial":
        _fail(f"mid-start layout: {layout}")
    if _map_dict(mapping) != {7: 2026, 8: 2026}:
        _fail(f"mid-start mapping: {mapping}")

    fy, layout, mapping = map_pdf_columns(
        [7],
        [(date(2026, 5, 8), date(2025, 10, 31))],
        as_of_aug,
    )
    if fy != 10 or layout != "partial" or _map_dict(mapping) != {7: 2026}:
        _fail(f"single July map_pdf_columns: {fy} {layout} {mapping}")

    # Genky 13列: 月6が2回。右端 as_of=2025-06-30 → 先頭6は2024、末尾6は2025。
    layout, mapping = plan_column_years(GENKY_MONTHS, 6, date(2025, 6, 30))
    if layout != "partial":
        _fail(f"genky layout: {layout}")
    expect_years = [2024] * 7 + [2025] * 6
    got_years = [y for _, y in mapping]
    if [m for m, _ in mapping] != GENKY_MONTHS or got_years != expect_years:
        _fail(f"genky mapping: {mapping}")
    if mapping[0] != (6, 2024) or mapping[-1] != (6, 2025):
        _fail(f"genky June split: {mapping[0]} {mapping[-1]}")


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

    layout, mapping = plan_column_years([7, 8, 9, 10, 11, 12, 1], 10, date(2026, 8, 5))
    if layout != "partial":
        _fail(f"wrap mid-start layout: {layout}")
    expect_wrap = {m: 2025 for m in (7, 8, 9, 10, 11, 12)}
    expect_wrap[1] = 2026
    if _map_dict(mapping) != expect_wrap:
        _fail(f"wrap mid-start mapping: {mapping}")


def _ensure_stock(code: str, name: str | None = None) -> None:
    from app.db import SessionLocal
    from app.models import Stock

    db = SessionLocal()
    try:
        if db.get(Stock, code) is None:
            db.add(Stock(code=code, name=name))
            db.commit()
    finally:
        db.close()


def _add_fy_end(code: str, disclosed: date, fy_end: date, disclosure_no: str) -> None:
    from app.db import SessionLocal
    from app.models import Statement

    _ensure_stock(code)
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

    r = c.post("/api/v1/tables/30320/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "売上高", "values": {"11": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 404, "ingest without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"NOT_IN_MASTER: {r.json()!r}")

    _ensure_stock("99990")
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
        "months": [7, 8],
        "rows": [{"element": "売上高", "values": {"7": 1, "8": 2}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "mid-start ingest")
    body = r.json()
    if body.get("layout") != "partial" or body.get("fy_end_month") != 10:
        _fail(f"mid-start ingest header: {body!r}")
    if body.get("mapping") != {"7": 2026, "8": 2026}:
        _fail(f"mid-start ingest mapping: {body.get('mapping')!r}")
    r = c.get("/api/v1/tables/77770/2026")
    _expect(r, 200, "GET 2026 mid-start")
    v2026 = {str(k): float(val) for k, val in r.json()["elements"][0]["values"].items()}
    if set(v2026) != {"7", "8"}:
        _fail(f"GET 2026 mid-start months: {v2026!r}")
    r = c.get("/api/v1/tables/77770/2025")
    _expect(r, 404, "GET 2025 mid-start")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"2025 should not exist: {r.json()!r}")

    _add_fy_end("33330", date(2026, 5, 1), date(2026, 3, 31), "single-jul-1")
    r = c.post("/api/v1/tables/33330/ingest", json={
        "months": [7],
        "rows": [{"element": "売上高", "values": {"7": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "single July ingest")
    body = r.json()
    if body.get("layout") != "partial" or body.get("mapping") != {"7": 2026}:
        _fail(f"single July ingest: {body!r}")

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
    if names != ["全店 売上高"]:
        _fail(f"force elements omit empty seed: {names!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET 2026 after force")
    if [e["element"] for e in r.json()["elements"]] != ["全店 売上高"]:
        _fail(f"2026 after force: {r.json()!r}")


def test_tdnet_4digit_fy_end_lookup() -> None:
    """TDnet 4桁パスは J-Quants 5桁の fiscal_year_end を使い、保存キーも 5桁。"""
    c = TestClient(app)
    _add_fy_end("92790", date(2026, 5, 8), date(2025, 10, 31), "tdnet-pad-fy-1")
    months = GIFT_MONTHS
    values = {str(m): 100.0 + m for m in months}
    r = c.post("/api/v1/tables/9279/ingest", json={
        "months": months,
        "rows": [{"element": "全店 売上高", "values": values}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "9279 ingest with 92790 fy_end")
    if r.json().get("code") != "92790":
        _fail(f"9279 ingest code: {r.json()!r}")
    r = c.get("/api/v1/tables/92790/2025")
    _expect(r, 200, "GET 92790 after 9279 ingest")
    if r.json()["code"] != "92790":
        _fail(f"9279 save key: {r.json()!r}")


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
    if format_monthly_cell(None, "number") != "-":
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
    if "全店売上高(前年比)" not in by:
        _fail(f"ingest yoy_index name: {list(by)!r}")
    if by["全店売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"ingest yoy_index stored: {by['全店売上高(前年比)']!r}")
    if format_monthly_cell(float(by["全店売上高(前年比)"]["values"]["11"]), "yoy_index") != "125.8":
        _fail("ingest yoy_index display")
    if "売上高(前年比)" not in by:
        _fail(f"ingest yoy_pct name: {list(by)!r}")
    if by["売上高(前年比)"]["unit"] != "yoy_pct" or float(by["売上高(前年比)"]["values"]["11"]) != -7.1:
        _fail(f"ingest yoy_pct stored: {by['売上高(前年比)']!r}")
    if format_monthly_cell(float(by["売上高(前年比)"]["values"]["11"]), "yoy_pct") != "92.9":
        _fail("ingest yoy_pct display")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"ingest number stored: {by['店舗数']!r}")
    if "売上高前年比" in by:
        _fail("yoy_pct must not keep PDF 前年比 wording")

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
    if "大きい指数(前年比)" not in by:
        _fail(f"yoy_pct name missing suffix: {list(by)!r}")
    if by["大きい指数(前年比)"]["unit"] != "yoy_pct" or float(by["大きい指数(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"must not convert 125.8: {by['大きい指数(前年比)']!r}")


def test_ingest_yoy_label_and_number_separate() -> None:
    c = TestClient(app)
    code = "44440"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 10, 31), "label-fy-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11],
        "rows": [{"element": "売上高前年同月比", "values": {"11": 125.8}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 前年同月比")
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET after 前年同月比")
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["売上高(前年比)"]:
        _fail(f"save 売上高前年同月比 as 売上高(前年比): {names!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11],
        "rows": [{"element": "売上高前年比", "values": {"11": 126.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest canonical same row")
    r = c.get(f"/api/v1/tables/{code}/2025")
    els = r.json()["elements"]
    if [e["element"] for e in els] != ["売上高(前年比)"]:
        _fail(f"canonical similar must be same row: {[e['element'] for e in els]!r}")
    if float(els[0]["values"]["11"]) != 126.0:
        _fail(f"canonical ingest overwrite: {els[0]!r}")

    r = c.post(f"/api/v1/tables/{code}/{2025}/elements", json={"element": "売上高", "unit": "number"})
    _expect(r, 201, "number 売上高 separate")
    r = c.put(f"/api/v1/tables/{code}/2025/values",
              json={"element": "売上高", "month": 11, "value": 1.0})
    _expect(r, 200, "number 売上高 value")
    r = c.get(f"/api/v1/tables/{code}/2025")
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["売上高(前年比)", "売上高"]:
        _fail(f"number and yoy stay separate: {names!r}")

    r = c.post(f"/api/v1/tables/{code}/{2025}/elements",
               json={"element": "売上高前年同月比", "unit": "number"})
    _expect(r, 201, "number keeps 前年同月比")
    if r.json()["element"] != "売上高前年同月比":
        _fail(f"number must not strip: {r.json()!r}")


def test_ingest_yoy_short_and_canonical_same_row() -> None:
    c = TestClient(app)
    code = "88880"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 12, 31), "yoy-same-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "売上高", "values": {"1": 101.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 売上高 yoy_index")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "売上高(前年比)", "values": {"1": 102.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 売上高(前年比) yoy_index")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET after yoy short+canonical")
    els = r.json()["elements"]
    names = [e["element"] for e in els]
    if names != ["売上高(前年比)"]:
        _fail(f"ingest 売上高 then 売上高(前年比) must be one row: {names!r}")
    if els[0]["unit"] != "yoy_index" or float(els[0]["values"]["1"]) != 102.0:
        _fail(f"second ingest upsert: {els[0]!r}")


def test_existing_short_yoy_renamed_on_canonical() -> None:
    c = TestClient(app)
    code = "88890"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 12, 31), "yoy-rename-1")
    r = c.post(f"/api/v1/tables/{code}/2026/elements", json={"element": "カラオケ全店 売上高"})
    _expect(r, 201, "seed short name")
    if r.json()["element"] != "カラオケ全店 売上高":
        _fail(f"number short stored: {r.json()!r}")
    r = c.put(f"/api/v1/tables/{code}/2026/elements",
              json={"element": "カラオケ全店 売上高", "unit": "yoy_index"})
    _expect(r, 200, "PUT unit yoy on short")
    r = c.put(f"/api/v1/tables/{code}/2026/values",
              json={"element": "カラオケ全店 売上高", "month": 1, "value": 110.0})
    _expect(r, 200, "PUT value on short")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "カラオケ全店 売上高(前年比)", "values": {"1": 111.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest canonical onto short")
    r = c.get(f"/api/v1/tables/{code}/2026")
    els = r.json()["elements"]
    names = [e["element"] for e in els]
    if names != ["カラオケ全店 売上高(前年比)"]:
        _fail(f"short must rename to canonical: {names!r}")
    if els[0]["unit"] != "yoy_index" or float(els[0]["values"]["1"]) != 111.0:
        _fail(f"rename+upsert: {els[0]!r}")

    r = c.post(f"/api/v1/tables/{code}/2026/elements",
               json={"element": "カラオケ全店 売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "POST canonical after rename")
    if r.json()["created"] is not False or r.json()["element"] != "カラオケ全店 売上高(前年比)":
        _fail(f"POST after rename: {r.json()!r}")


def test_number_stores_not_yoy_suffix() -> None:
    c = TestClient(app)
    code = "88900"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 12, 31), "stores-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "店舗数", "values": {"1": 42}, "unit": "number"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 店舗数")
    r = c.get(f"/api/v1/tables/{code}/2026")
    els = r.json()["elements"]
    if [e["element"] for e in els] != ["店舗数"]:
        _fail(f"number 店舗数 must not get (前年比): {[e['element'] for e in els]!r}")
    if els[0]["unit"] != "number" or float(els[0]["values"]["1"]) != 42:
        _fail(f"店舗数 stored: {els[0]!r}")


def test_merge_uchiyama_like_yoy_duplicates() -> None:
    from app.db import SessionLocal, merge_yoy_duplicate_elements
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    code, year = "60590", 2026
    short = "カラオケ全店 売上高"
    canon = "カラオケ全店 売上高(前年比)"
    _ensure_stock(code, "ウチヤマ")
    db = SessionLocal()
    try:
        db.add(MonthlyTable(code=code, year=year))
        db.add(MonthlyElement(code=code, year=year, element=short, sort_order=1, unit="yoy_index"))
        db.add(MonthlyElement(code=code, year=year, element=canon, sort_order=2, unit="yoy_index"))
        db.add(MonthlyValue(code=code, year=year, element=short, month=1, value=101.0))
        db.add(MonthlyValue(code=code, year=year, element=canon, month=1, value=101.0))
        db.add(MonthlyValue(code=code, year=year, element=short, month=2, value=102.0))
        db.add(MonthlyValue(code=code, year=year, element=canon, month=3, value=103.0))
        db.add(MonthlyValue(code=code, year=year, element=short, month=4, value=104.0))
        db.add(MonthlyValue(code=code, year=year, element=canon, month=4, value=105.0))
        db.commit()
    finally:
        db.close()

    reports = merge_yoy_duplicate_elements()
    print(f"merge 60590 reports: {reports!r}")
    if any(r.get("code") == "44440" for r in reports):
        _fail(f"must not merge number 売上高 into yoy: {reports!r}")
    hit = [r for r in reports if r.get("code") == code and r.get("year") == year]
    if not hit:
        _fail(f"60590 2026 merge missing: {reports!r}")
    one = hit[0]
    if one.get("canonical") != canon or short not in (one.get("deleted") or []):
        _fail(f"60590 merge target: {one!r}")
    conflicts = one.get("conflicts") or []
    if not any(c.get("month") == 4 and c.get("kept") == 105.0 and c.get("dropped") == 104.0
               for c in conflicts):
        _fail(f"60590 month 4 conflict not reported: {conflicts!r}")

    db = SessionLocal()
    try:
        els = (db.query(MonthlyElement)
               .filter(MonthlyElement.code == code, MonthlyElement.year == year)
               .all())
        names = sorted(e.element for e in els)
        if names != [canon]:
            _fail(f"60590 after merge names: {names!r}")
        if els[0].unit != "yoy_index":
            _fail(f"60590 unit: {els[0].unit!r}")
        vals = {
            v.month: float(v.value)
            for v in db.query(MonthlyValue).filter(
                MonthlyValue.code == code, MonthlyValue.year == year,
            )
        }
        if vals != {1: 101.0, 2: 102.0, 3: 103.0, 4: 105.0}:
            _fail(f"60590 after merge values: {vals!r}")
        if db.get(MonthlyElement, (code, year, short)) is not None:
            _fail("short row must be deleted")
    finally:
        db.close()

    again = merge_yoy_duplicate_elements()
    if any(r.get("code") == code and r.get("year") == year for r in again):
        _fail(f"merge must be idempotent: {again!r}")


def test_rename_29260_style_cross_year_yoy() -> None:
    """年Aは短い yoy 名、年Bは正規名。migrate で短い名を正規名へ。number は触らない。"""
    from app.db import SessionLocal, merge_yoy_duplicate_elements
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue
    from app.monthly_ingest import union_monthly_elements

    c = TestClient(app)
    code = "29260"
    short = "全店 売上高前期比"
    canon = "全店 売上高前期比(前年比)"
    _ensure_stock(code, "Action")
    db = SessionLocal()
    try:
        db.add(MonthlyTable(code=code, year=2026))
        db.add(MonthlyTable(code=code, year=2025))
        db.add(MonthlyElement(code=code, year=2026, element=short, sort_order=1, unit="yoy_index"))
        db.add(MonthlyElement(code=code, year=2026, element="店舗数", sort_order=2, unit="number"))
        db.add(MonthlyElement(code=code, year=2026, element="売上高", sort_order=3, unit="number"))
        db.add(MonthlyElement(code=code, year=2025, element=canon, sort_order=1, unit="yoy_index"))
        db.add(MonthlyElement(code=code, year=2025, element="店舗数", sort_order=2, unit="number"))
        db.add(MonthlyElement(code=code, year=2025, element="売上高", sort_order=3, unit="number"))
        db.add(MonthlyValue(code=code, year=2026, element=short, month=1, value=110.0))
        db.add(MonthlyValue(code=code, year=2026, element="店舗数", month=1, value=50.0))
        db.add(MonthlyValue(code=code, year=2026, element="売上高", month=1, value=1000.0))
        db.add(MonthlyValue(code=code, year=2025, element=canon, month=1, value=105.0))
        db.add(MonthlyValue(code=code, year=2025, element="店舗数", month=1, value=48.0))
        db.add(MonthlyValue(code=code, year=2025, element="売上高", month=1, value=900.0))
        db.commit()
    finally:
        db.close()

    reports = merge_yoy_duplicate_elements()
    print(f"rename 29260 reports: {reports!r}")
    hit = [r for r in reports if r.get("code") == code and r.get("year") == 2026]
    if not hit:
        _fail(f"29260 2026 rename missing: {reports!r}")
    one = hit[0]
    if one.get("canonical") != canon:
        _fail(f"29260 rename target: {one!r}")
    moved = (one.get("renamed") or []) + (one.get("deleted") or [])
    if short not in moved:
        _fail(f"29260 short not moved: {one!r}")
    if any(r.get("canonical") in ("店舗数", "売上高") for r in reports if r.get("code") == code):
        _fail(f"must not rename number rows: {reports!r}")

    db = SessionLocal()
    try:
        els = (db.query(MonthlyElement)
               .filter(MonthlyElement.code == code, MonthlyElement.year == 2026)
               .all())
        names = sorted(e.element for e in els)
        if short in names:
            _fail(f"29260 2026 short still present: {names!r}")
        if canon not in names:
            _fail(f"29260 2026 missing canonical: {names!r}")
        if "店舗数" not in names or "売上高" not in names:
            _fail(f"number rows must stay: {names!r}")
        if "売上高(前年比)" in names:
            _fail(f"number 売上高 must not gain (前年比): {names!r}")
        v = db.get(MonthlyValue, (code, 2026, canon, 1))
        if v is None or float(v.value) != 110.0:
            _fail(f"2026 values must come from the renamed short row: {v}")
        if db.get(MonthlyValue, (code, 2026, short, 1)) is not None:
            _fail("old short values must move with the rename")
        stores = db.get(MonthlyValue, (code, 2026, "店舗数", 1))
        if stores is None or float(stores.value) != 50.0:
            _fail(f"number 店舗数 values: {stores}")
        sales = db.get(MonthlyValue, (code, 2026, "売上高", 1))
        if sales is None or float(sales.value) != 1000.0:
            _fail(f"number 売上高 values: {sales}")
        if db.get(MonthlyElement, (code, 2025, canon)) is None:
            _fail("2025 canonical row must stay")
    finally:
        db.close()

    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET 29260 2026")
    y2026 = r.json()
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET 29260 2025")
    y2025 = r.json()
    order = [row["element"] for row in union_monthly_elements([y2026, y2025])]
    if order != [canon, "店舗数", "売上高"]:
        _fail(f"29260 union must be one yoy row: {order!r}")
    by = {e["element"]: e for e in y2026["elements"]}
    if canon not in by or float(by[canon]["values"]["1"]) != 110.0:
        _fail(f"GET 2026 values from renamed short: {by!r}")
    if by.get("店舗数", {}).get("unit") != "number" or float(by["店舗数"]["values"]["1"]) != 50.0:
        _fail(f"GET 2026 店舗数: {by.get('店舗数')!r}")
    if by.get("売上高", {}).get("unit") != "number" or float(by["売上高"]["values"]["1"]) != 1000.0:
        _fail(f"GET 2026 number 売上高: {by.get('売上高')!r}")

    leftover = [
        {"year": 2026, "elements": [
            {"element": short, "unit": "yoy_index", "values": {"1": 110.0}},
            {"element": "店舗数", "unit": "number"},
            {"element": "売上高", "unit": "number"},
        ]},
        {"year": 2025, "elements": [
            {"element": canon, "unit": "yoy_index", "values": {"1": 105.0}},
            {"element": "店舗数", "unit": "number"},
            {"element": "売上高", "unit": "number"},
        ]},
    ]
    leftover_order = [row["element"] for row in union_monthly_elements(leftover)]
    if leftover_order != [canon, "店舗数", "売上高"]:
        _fail(f"leftover short names must collapse by display: {leftover_order!r}")

    again = merge_yoy_duplicate_elements()
    if any(r.get("code") == code for r in again):
        _fail(f"29260 rename must be idempotent: {again!r}")


def test_issuer_alias_ingest_and_migrate() -> None:
    from app.db import SessionLocal, apply_issuer_element_aliases
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    cases = plan_issuer_alias_cases()
    by_code: dict[str, list[tuple[str, str, date]]] = {}
    after_until: dict[str, date] = {}
    for code, incoming, stored, ctx in cases:
        as_of = ctx.get("as_of")
        if not isinstance(as_of, date):
            continue
        if incoming != stored:
            by_code.setdefault(code, []).append((incoming, stored, as_of))
        else:
            after_until[code] = as_of

    c = TestClient(app)
    for i, (code, pairs) in enumerate(sorted(by_code.items())):
        as_of = pairs[0][2]
        year = as_of.year if as_of.month >= 6 else as_of.year - 1
        _add_fy_end(code, date(year, 6, 1), date(year, 6, 30), f"alias-fy-{code}-{year}")
        # many-to-one rename（複数 before → 同一 after）は同一 ingest で衝突するため stored で畳む
        rows = []
        seen_stored: set[str] = set()
        for j, (incoming, stored, _) in enumerate(pairs):
            if stored in seen_stored:
                continue
            seen_stored.add(stored)
            unit = "yoy_index" if stored.endswith("(前年比)") else "number"
            rows.append({
                "element": incoming,
                "values": {"6": 100.0 + j},
                "unit": unit,
            })
        r = c.post(f"/api/v1/tables/{code}/ingest", json={
            "months": [6],
            "rows": rows,
            "as_of": as_of.isoformat(),
        })
        _expect(r, 200, f"{code} ingest old as_of")
        mapped_year = r.json()["updated"][0]["year"]
        r = c.get(f"/api/v1/tables/{code}/{mapped_year}")
        _expect(r, 200, f"GET {code} {mapped_year}")
        names = [e["element"] for e in r.json()["elements"]]
        for incoming, stored, _ in pairs:
            if stored not in names:
                _fail(f"{code} stored missing {stored!r}: {names!r}")
            if incoming != stored and incoming in names:
                _fail(f"{code} incoming leftover {incoming!r}: {names!r}")

        incoming, stored, _ = pairs[0]
        unit = "yoy_index" if stored.endswith("(前年比)") else "number"
        new_as_of = after_until.get(code)
        if new_as_of is not None:
            r = c.post(f"/api/v1/tables/{code}/ingest", json={
                "months": [6],
                "rows": [{"element": incoming, "values": {"6": 1.0}, "unit": unit}],
                "as_of": new_as_of.isoformat(),
            })
            _expect(r, 200, f"{code} ingest new as_of")
            new_name = r.json()["updated"][0]["element"]
            if new_name != incoming and incoming.endswith("(前年比)"):
                _fail(f"{code} as_of>={new_as_of} must keep {incoming!r}, got {new_name!r}")
            if incoming.endswith("(前年比)") is False:
                canon = canonicalize_element_label(incoming, unit)
                kept = r.json()["updated"][0]["element"]
                if kept != canon:
                    _fail(f"{code} new as_of must keep {canon!r}, got {kept!r}")

        old_year = 2008 + i
        merge_year = 2007 + i
        db = SessionLocal()
        try:
            db.add(MonthlyTable(code=code, year=old_year))
            db.add(MonthlyElement(
                code=code, year=old_year, element=incoming,
                sort_order=1, unit=unit,
            ))
            db.add(MonthlyValue(
                code=code, year=old_year, element=incoming, month=5, value=105.2,
            ))
            db.add(MonthlyTable(code=code, year=merge_year))
            db.add(MonthlyElement(
                code=code, year=merge_year, element=stored,
                sort_order=1, unit=unit,
            ))
            db.add(MonthlyElement(
                code=code, year=merge_year, element=incoming,
                sort_order=2, unit=unit,
            ))
            db.add(MonthlyValue(
                code=code, year=merge_year, element=stored, month=8, value=98.3,
            ))
            db.add(MonthlyValue(
                code=code, year=merge_year, element=incoming, month=7, value=101.4,
            ))
            db.commit()
        finally:
            db.close()

        reports = apply_issuer_element_aliases()
        if not any(
            r.get("code") == code and r.get("year") == old_year and r.get("canonical") == stored
            for r in reports
        ):
            _fail(f"{code} {old_year} rename missing: {reports!r}")
        db = SessionLocal()
        try:
            names_old = sorted(
                e.element for e in db.query(MonthlyElement).filter_by(code=code, year=old_year)
            )
            if names_old != [stored]:
                _fail(f"{code} {old_year} after migrate: {names_old!r}")
            v = db.get(MonthlyValue, (code, old_year, stored, 5))
            if v is None or float(v.value) != 105.2:
                _fail(f"{code} {old_year} value not moved: {v}")
            names_merge = sorted(
                e.element for e in db.query(MonthlyElement).filter_by(code=code, year=merge_year)
            )
            if names_merge != [stored]:
                _fail(f"{code} {merge_year} merge leftover: {names_merge!r}")
            vals_merge = {
                v.month: float(v.value)
                for v in db.query(MonthlyValue).filter_by(code=code, year=merge_year)
            }
            if vals_merge != {7: 101.4, 8: 98.3}:
                _fail(f"{code} {merge_year} merged values: {vals_merge!r}")
        finally:
            db.close()

        later = (after_until[code].year + 1) if code in after_until else 2026 + i
        r = c.post(f"/api/v1/tables/{code}/{later}/elements", json={
            "element": incoming, "unit": unit,
        })
        _expect(r, 201, f"{code} POST after until.year")
        created = r.json()["element"]
        expect_kept = canonicalize_element_label(incoming, unit)
        if created != expect_kept:
            _fail(f"{code} year {later} must keep {expect_kept!r}, got {created!r}")

        again = apply_issuer_element_aliases()
        if any(r.get("code") == code and r.get("year") in {old_year, merge_year} for r in again):
            _fail(f"{code} alias migrate must be idempotent: {again!r}")


def test_reject_prior_period_element() -> None:
    blocked = (
        "前期計店舗数",
        "店舗数（前期）",
        "前期店舗数",
        "店舗数 前期",
        "国内リテール 前年同月店舗数",
        "ディスカウント 前年同月店舗数",
        "UNY 前年同月店舗数",
        "前年同期 店舗数",
    )
    allowed = (
        "当期計店舗数",
        "全店 売上高(前年比)",
        "売上高前年同月比",
        "前期比",
        "既存店 店舗数",
    )
    for name in blocked:
        _expect_ingest_error(
            lambda n=name: reject_prior_period_element(n),
            "PRIOR_PERIOD_ELEMENT",
            "前期の実数",
        )
    for name in allowed:
        reject_prior_period_element(name)

    c = TestClient(app)
    code = "88880"
    _add_fy_end(code, date(2026, 5, 1), date(2026, 3, 31), "prior-fy-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [7],
        "rows": [
            {"element": "当期計店舗数", "values": {"7": 68}, "unit": "number"},
            {"element": "前期計店舗数", "values": {"7": 66}, "unit": "number"},
        ],
        "as_of": "2026-08-14",
    })
    _expect(r, 400, "ingest prior period")
    if _err(r)["code"] != "PRIOR_PERIOD_ELEMENT":
        _fail(f"ingest prior: {r.json()!r}")
    if "前期計店舗数" not in _err(r)["message"]:
        _fail(f"message missing name: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    if r.status_code == 200:
        names = [e["element"] for e in r.json()["elements"]]
        if "前期計店舗数" in names or "当期計店舗数" in names:
            _fail(f"prior ingest leftover: {names!r}")
    elif r.status_code != 404:
        _fail(f"prior ingest GET: {r.status_code} {r.text[:200]}")

    r = c.post(f"/api/v1/tables/{code}/2026/elements", json={
        "element": "店舗数（前期）", "unit": "number",
    })
    _expect(r, 400, "POST prior period")
    if _err(r)["code"] != "PRIOR_PERIOD_ELEMENT":
        _fail(f"POST prior: {r.json()!r}")


def test_api_genky_year_wrap_ingest() -> None:
    """Genky 20250630 型: 13列で月6が2回。配列 values で暦年分割して両年に残る。"""
    c = TestClient(app)
    code = "92670"
    _add_fy_end(code, date(2025, 5, 1), date(2025, 6, 30), "genky-fy-1")
    _add_fy_end(code, date(2024, 5, 1), date(2024, 6, 30), "genky-fy-2")

    # fixture 売上高前年比（全店）の数値
    values = [
        104.6, 107.5, 106.7, 106.2, 110.4, 104.5, 109.4,
        111.1, 109.1, 106.5, 113.0, 108.8, 110.1,
    ]
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GENKY_MONTHS,
        "rows": [{"element": "売上高前年比（全店）", "unit": "yoy_index", "values": values}],
        "as_of": "2025-06-30",
    })
    _expect(r, 200, "genky ingest")
    body = r.json()
    if body.get("fy_end_month") != 6 or body.get("layout") != "partial":
        _fail(f"genky header: {body!r}")
    mapping = body.get("mapping")
    if not isinstance(mapping, list) or len(mapping) != 13:
        _fail(f"genky mapping shape: {mapping!r}")
    if mapping[0] != {"month": 6, "year": 2024} or mapping[-1] != {"month": 6, "year": 2025}:
        _fail(f"genky mapping ends: {mapping[0]!r} {mapping[-1]!r}")
    expect_years = [2024] * 7 + [2025] * 6
    if [item["year"] for item in mapping] != expect_years:
        _fail(f"genky mapping years: {mapping!r}")

    r = c.get(f"/api/v1/tables/{code}/2024")
    _expect(r, 200, "GET genky 2024")
    els = r.json()["elements"]
    if len(els) != 1:
        _fail(f"2024 elements: {els!r}")
    v2024 = {str(k): float(val) for k, val in els[0]["values"].items()}
    if set(v2024) != {"6", "7", "8", "9", "10", "11", "12"}:
        _fail(f"GET 2024 months: {v2024!r}")
    if abs(v2024["6"] - 104.6) > 1e-6:
        _fail(f"2024 June value: {v2024!r}")

    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET genky 2025")
    v2025 = {str(k): float(val) for k, val in r.json()["elements"][0]["values"].items()}
    if set(v2025) != {"1", "2", "3", "4", "5", "6"}:
        _fail(f"GET 2025 months: {v2025!r}")
    if abs(v2025["6"] - 110.1) > 1e-6:
        _fail(f"2025 June value: {v2025!r}")

    # 重複月なのに月キー object は拒否
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GENKY_MONTHS,
        "rows": [{"element": "客数", "unit": "yoy_index", "values": {"6": 1.0}}],
        "as_of": "2025-06-30",
    })
    _expect(r, 400, "genky ambiguous values")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"ambiguous: {r.json()!r}")

    # 列番号キーは許可
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GENKY_MONTHS,
        "rows": [{"element": "客数前年比（既存店）", "unit": "yoy_index",
                  "values": {"0": 102.5, "12": 102.2}}],
        "as_of": "2025-06-30",
    })
    _expect(r, 200, "genky col-index values")
    r = c.get(f"/api/v1/tables/{code}/2024")
    names = [e["element"] for e in r.json()["elements"]]
    if "客数前年比（既存店）(前年比)" not in names and "客数前年比（既存店）" not in names:
        # canonicalize may add (前年比)
        if not any("客数" in n for n in names):
            _fail(f"col-index 2024 missing: {names!r}")


def main() -> None:
    test_canonicalize_element_label()
    test_issuer_alias_cases()
    test_mapping_examples()
    test_fy_end_and_layout_errors()
    test_api_gift_ingest()
    test_api_genky_year_wrap_ingest()
    test_tdnet_4digit_fy_end_lookup()
    test_api_errors()
    test_all_or_nothing_similar()
    test_api_alnum_code()
    test_unit_display_and_ingest()
    test_ingest_yoy_label_and_number_separate()
    test_ingest_yoy_short_and_canonical_same_row()
    test_existing_short_yoy_renamed_on_canonical()
    test_number_stores_not_yoy_suffix()
    test_merge_uchiyama_like_yoy_duplicates()
    test_rename_29260_style_cross_year_yoy()
    test_issuer_alias_ingest_and_migrate()
    test_reject_prior_period_element()
    print("ok: monthly ingest")


if __name__ == "__main__":
    main()
