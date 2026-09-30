"""XBRL context / parser / job helpers. ネットワークなし。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "jobs"))
sys.path.insert(0, str(ROOT / "tools"))

# ci_smoke から呼ばれても自分の空 DB を使う（setdefault だと test_xbrl_parse の行が残った DB を共有する）
os.environ["DATABASE_URL"] = f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-units-')}/stock.db"

from app.db import SessionLocal, init_db  # noqa: E402
from app.models import XbrlFiling, XbrlJobRun  # noqa: E402
from app.xbrl.context import (  # noqa: E402
    ParsedContext,
    fiscal_year_end_from_dei,
    fiscal_year_end_from_duration_contexts,
    fiscal_year_end_from_pl_contexts,
    resolve_fiscal_year_end,
)
from app.xbrl.jobs import finish_run, report_unmapped, start_run  # noqa: E402
from app.xbrl.normalize import _dims, rebuild_all  # noqa: E402
from app.xbrl.parser import find_manifest, namespace_is_standard, package_meta  # noqa: E402
from app.xbrl.store import mark_failed  # noqa: E402
import fetch_xbrl_golden as fetch_mod  # noqa: E402
import xbrl_match_rate as mr  # noqa: E402


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _ctx(
    ref: str,
    *,
    period_type: str = "duration",
    end: date = date(2026, 3, 31),
    start: date | None = date(2025, 4, 1),
    dims: dict | None = None,
) -> ParsedContext:
    return ParsedContext(
        context_ref=ref,
        period_type=period_type,
        start_date=start,
        end_date=end,
        is_consolidated=1,
        relative_period=None,
        dimensions=dims or {},
    )


def test_parsed_context_dimensions() -> None:
    empty = _ctx("a")
    if empty.has_dimensions or empty.dimensions_json != "{}":
        _fail("empty dims")
    if empty.only_consol_dimensions() is not True:
        _fail("empty is consol-only")
    consol = _ctx("b", dims={"axis": "jppfs_cor:ConsolidatedMember"})
    if not consol.has_dimensions:
        _fail("has dims")
    if consol.dimensions_json != '{"axis": "jppfs_cor:ConsolidatedMember"}':
        _fail(consol.dimensions_json)
    if consol.only_consol_dimensions() is not True:
        _fail("consol member")
    other = _ctx("c", dims={"axis": "jpcrp_cor:BusinessSegmentMember"})
    if other.only_consol_dimensions() is not False:
        _fail("segment member")


def test_fy_end_fallback_order() -> None:
    def dei_fact(text: str | None) -> SimpleNamespace:
        return SimpleNamespace(local_name="CurrentPeriodEndDateDEI", value_text=text, value_num=None)

    dei = dei_fact("2026-06-30")
    if fiscal_year_end_from_dei([dei]) != date(2026, 6, 30):
        _fail("dei")
    for bad in ("2026年6月30日", "", None):
        if fiscal_year_end_from_dei([dei_fact(bad)]) is not None:
            _fail(f"DEI {bad!r} is not an ISO date")
    if fiscal_year_end_from_dei([]) is not None:
        _fail("dei empty")
    duration = _ctx("dur", end=date(2026, 9, 30))
    instant = _ctx("inst", period_type="instant", end=date(2026, 8, 10), start=None)
    if fiscal_year_end_from_duration_contexts([duration, instant]) != date(2026, 9, 30):
        _fail("duration max")
    pl_fact = SimpleNamespace(
        is_numeric=True, value_num=1, local_name="NetSales", context_ref="dur",
    )
    if fiscal_year_end_from_pl_contexts([duration], [pl_fact]) != date(2026, 9, 30):
        _fail("pl")
    if fiscal_year_end_from_pl_contexts([duration], []) is not None:
        _fail("pl empty")
    # DEI wins over title / PL / duration
    got = resolve_fiscal_year_end(
        [duration], [dei, pl_fact], title="2027年3月期 決算短信〔日本基準〕（連結）",
    )
    if got != date(2026, 6, 30):
        _fail(f"dei first {got}")
    # excluded DEI falls through to annual title
    got = resolve_fiscal_year_end(
        [duration], [dei], title="2027年3月期 決算短信〔日本基準〕（連結）",
        exclude_dates={date(2026, 6, 30)},
    )
    if got != date(2027, 3, 31):
        _fail(f"title after excluded dei {got}")
    # quarterly title skipped, PL used
    got = resolve_fiscal_year_end(
        [duration], [pl_fact], title="2027年3月期 第1四半期決算短信",
    )
    if got != date(2026, 9, 30):
        _fail(f"pl after quarterly title {got}")


def test_parser_helpers(tmp: Path) -> None:
    if not namespace_is_standard("jppfs_cor") or namespace_is_standard("custom"):
        _fail("namespace")
    if find_manifest(tmp) is not None:
        _fail("no manifest")
    man = tmp / "Meta" / "manifest.xml"
    man.parent.mkdir(parents=True)
    man.write_text("<xml/>", encoding="utf-8")
    if find_manifest(tmp) != man:
        _fail(find_manifest(tmp))
    meta = package_meta("2026年3月期 第1四半期決算短信〔日本基準〕（連結）")
    if not isinstance(meta, dict) or meta.get("doc_type") != "quarterly" or meta.get("period_type") != "Q1":
        _fail(meta)


def test_jobs_store_and_normalize() -> None:
    init_db()
    db = SessionLocal()
    try:
        run = start_run(db, "parse")
        finish_run(db, run, "success", 3, 0, "ok")
        row = db.get(XbrlJobRun, run.id)
        if row is None or row.status != "success" or row.processed_count != 3 or row.message != "ok":
            _fail(row)
        if row.finished_at is None:
            _fail("finished_at")
        ts = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc).replace(tzinfo=None)
        filing = XbrlFiling(
            code="72030", source="tdnet", doc_id="d1", doc_type="other",
            submitted_at=ts,
            raw_path="7203/a.zip", parse_status="pending",
            created_at=ts,
        )
        db.add(filing)
        db.commit()
        mark_failed(db, filing, ValueError("boom"))
        if filing.parse_status != "failed" or "boom" not in (filing.parse_error or ""):
            _fail(filing.parse_error)
        if report_unmapped(db) != []:
            _fail("empty unmapped")
        rebuilt = rebuild_all(db)
        if rebuilt["processed"] != 0 or rebuilt["deleted"] != 0:
            _fail(rebuilt)
        if _dims(None) != {} or _dims("{") != {} or _dims("[1]") != {}:
            _fail("bad dims")
        if _dims('{"a": "b"}') != {"a": "b"}:
            _fail(_dims('{"a": "b"}'))
    finally:
        db.close()


def test_match_rate_and_golden_helpers() -> None:
    st = mr.MetricStats(matched=1, compared=4)
    if st.rate is None or st.rate != 0.25:
        _fail(st.rate)
    if mr.MetricStats().rate is not None:
        _fail("empty rate")
    rep = mr.Report(matched=2, compared=5)
    if rep.rate is None or rep.rate != 0.4:
        _fail(rep.rate)
    if not mr.unit_is_yen("JPY") or not mr.unit_is_yen("iso4217:JPY") or mr.unit_is_yen("USD"):
        _fail("yen")
    if mr.unit_is_yen(None) is not True:
        _fail("empty unit is yen")
    if mr.norm_date("2026-03-31T15:00:00") != "2026-03-31":
        _fail(mr.norm_date("2026-03-31T15:00:00"))
    if mr.norm_date("2026-03-31 12:00") != "2026-03-31":
        _fail("space date")
    if mr.norm_date(None) != "":
        _fail("none date")
    if fetch_mod.detect_period_basis({"period_basis": "cumulative"}, "Q2") != "cumulative":
        _fail("cumulative basis")
    if fetch_mod.detect_period_basis({"period_basis": "single_quarter"}, "Q2") != "single_quarter":
        _fail("single basis")
    if fetch_mod.detect_period_basis({}, "Q2") != "single_quarter":
        _fail("q2 default single")
    if fetch_mod.detect_period_basis({}, "FY") != "cumulative":
        _fail("fy cumulative")
    if fetch_mod._cumulative_label("Q1") != "Q1":
        _fail("q1 label")
    if fetch_mod._cumulative_label("H1") != "Q2":
        _fail("h1 label")
    if fetch_mod._cumulative_label("Q4") != "FY":
        _fail("q4 label")


def main() -> None:
    test_parsed_context_dimensions()
    test_fy_end_fallback_order()
    with tempfile.TemporaryDirectory() as d:
        test_parser_helpers(Path(d))
    test_jobs_store_and_normalize()
    test_match_rate_and_golden_helpers()
    print("ok: xbrl units")


if __name__ == "__main__":
    main()
