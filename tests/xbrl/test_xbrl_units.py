"""XBRL context / parser / job helpers. ネットワークなし。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "xbrl"))

# 直接走らせたときは自分の空 DB。pytest では conftest がファイルごとに DB を空にする
os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp(prefix='xbrl-units-')}/stock.db")

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
    if other.only_consol_or_result_dimensions() is not False:
        _fail("segment is not result")
    result = _ctx("d", dims={
        "tse-ed-t:ConsolidatedOrNonConsolidatedAxis": "tse-ed-t:ConsolidatedMember",
        "tse-ed-t:ResultForecastAxis": "tse-ed-t:ResultMember",
    })
    if result.only_consol_dimensions() is not False:
        _fail("ResultMember is not consol-only")
    if result.only_consol_or_result_dimensions() is not True:
        _fail("ResultMember + consol should be allowed")
    forecast = _ctx("e", dims={
        "tse-ed-t:ConsolidatedOrNonConsolidatedAxis": "tse-ed-t:ConsolidatedMember",
        "tse-ed-t:ResultForecastAxis": "tse-ed-t:ForecastMember",
    })
    if forecast.only_consol_or_result_dimensions() is not False:
        _fail("ForecastMember must be excluded")


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
    got = resolve_fiscal_year_end(
        [duration], [dei, pl_fact], title="2027年3月期 決算短信〔日本基準〕（連結）",
    )
    if got != date(2026, 6, 30):
        _fail(f"dei first {got}")
    got = resolve_fiscal_year_end(
        [duration], [dei], title="2027年3月期 決算短信〔日本基準〕（連結）",
        exclude_dates={date(2026, 6, 30)},
    )
    if got != date(2027, 3, 31):
        _fail(f"title after excluded dei {got}")
    got = resolve_fiscal_year_end(
        [duration], [pl_fact], title="2027年3月期 第1四半期決算短信",
    )
    if got != date(2026, 9, 30):
        _fail(f"pl after quarterly title {got}")


def test_fy_end_from_summary_result_member() -> None:
    """本表も DEI も無いとき ResultMember の期末を使い、Forecast の通期末は採らない。"""
    result = _ctx(
        "CurrentAccumulatedQ2Duration_ConsolidatedMember_ResultMember",
        start=date(2026, 1, 1),
        end=date(2026, 6, 30),
        dims={
            "tse-ed-t:ConsolidatedOrNonConsolidatedAxis": "tse-ed-t:ConsolidatedMember",
            "tse-ed-t:ResultForecastAxis": "tse-ed-t:ResultMember",
        },
    )
    forecast = _ctx(
        "CurrentYearDuration_ConsolidatedMember_ForecastMember",
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        dims={
            "tse-ed-t:ConsolidatedOrNonConsolidatedAxis": "tse-ed-t:ConsolidatedMember",
            "tse-ed-t:ResultForecastAxis": "tse-ed-t:ForecastMember",
        },
    )
    result_fact = SimpleNamespace(
        is_numeric=True, value_num=10462, local_name="SalesIFRS",
        context_ref=result.context_ref,
    )
    forecast_fact = SimpleNamespace(
        is_numeric=True, value_num=20000, local_name="SalesIFRS",
        context_ref=forecast.context_ref,
    )
    got = fiscal_year_end_from_pl_contexts(
        [result, forecast], [result_fact, forecast_fact],
    )
    if got != date(2026, 6, 30):
        _fail(f"summary result fy_end {got}")
    got = resolve_fiscal_year_end(
        [result, forecast], [result_fact, forecast_fact],
        title="2026年12月期 第2四半期決算短信〔ＳＦＲＳ(Ｉ)及びＩＦＲＳ〕（連結）",
    )
    if got != date(2026, 6, 30):
        _fail(f"resolve summary-only {got}")
    if fiscal_year_end_from_pl_contexts([forecast], [forecast_fact]) is not None:
        _fail("forecast-only must not yield PL fy_end")

    primary = _ctx("CurrentYTDDuration", start=date(2026, 1, 1), end=date(2026, 6, 30))
    later_result = _ctx(
        "CurrentAccumulatedQ3Duration_ConsolidatedMember_ResultMember",
        start=date(2026, 1, 1),
        end=date(2026, 9, 30),
        dims={
            "tse-ed-t:ConsolidatedOrNonConsolidatedAxis": "tse-ed-t:ConsolidatedMember",
            "tse-ed-t:ResultForecastAxis": "tse-ed-t:ResultMember",
        },
    )
    primary_fact = SimpleNamespace(
        is_numeric=True, value_num=1, local_name="NetSales",
        context_ref=primary.context_ref,
    )
    later_fact = SimpleNamespace(
        is_numeric=True, value_num=2, local_name="SalesIFRS",
        context_ref=later_result.context_ref,
    )
    got = fiscal_year_end_from_pl_contexts(
        [primary, later_result], [primary_fact, later_fact],
    )
    if got != date(2026, 6, 30):
        _fail(f"primary must win over later ResultMember {got}")
    dei = SimpleNamespace(
        local_name="CurrentPeriodEndDateDEI", value_text="2026-06-30", value_num=None,
    )
    got = resolve_fiscal_year_end(
        [later_result, forecast], [dei, later_fact, forecast_fact],
        title="2026年12月期 第2四半期決算短信",
    )
    if got != date(2026, 6, 30):
        _fail(f"DEI must still win {got}")


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


_IXBRL_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"'
    ' xmlns:xbrli="http://www.xbrl.org/2003/instance"'
    ' xmlns:xbrldi="http://xbrl.org/2006/xbrldi"><body><ix:header><ix:resources>'
)
_IXBRL_CONTEXTS = (
    '<xbrli:context id="CurrentYTDDuration"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>'
    "</xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate>"
    "<xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>"
    '<xbrli:context id="CurrentYTDDuration_NonConsolidatedMember"><xbrli:entity>'
    '<xbrli:identifier scheme="x">1</xbrli:identifier><xbrli:segment>'
    '<xbrldi:explicitMember dimension="jppfs_cor:ConsolidatedOrNonConsolidatedAxis">'
    "jppfs_cor:NonConsolidatedMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity>"
    "<xbrli:period><xbrli:instant>2026-09-30</xbrli:instant></xbrli:period></xbrli:context>"
)
_IXBRL_TAIL = '</ix:resources></ix:header><p><ix:nonFraction contextRef="CurrentYTDDuration">1</ix:nonFraction></p></body></html>'


def test_context_fast_path(tmp: Path) -> None:
    """Context-block-only parse == full iterparse; odd files take the full parse."""
    import xml.etree.ElementTree as ET

    from app.xbrl.context import (
        _context_elements_fast,
        local_name,
        parse_context_element,
        parse_contexts_from_xml,
    )

    def full(path: Path) -> list:
        out = []
        for _ev, elem in ET.iterparse(str(path), events=["end"]):
            if local_name(elem.tag) == "context":
                parsed = parse_context_element(elem)
                if parsed is not None:
                    out.append(parsed)
        return out

    plain = tmp / "plain.htm"
    plain.write_text(_IXBRL_HEAD + _IXBRL_CONTEXTS + _IXBRL_TAIL, encoding="utf-8")
    if _context_elements_fast(plain.read_bytes()) is None:
        _fail("plain file should take the fast path")
    got = parse_contexts_from_xml(plain)
    if got != full(plain) or len(got) != 2:
        _fail(f"fast path differs: {got}")
    if got[1].is_consolidated != 0 or got[1].period_type != "instant":
        _fail(f"dimensions/period lost: {got[1]}")

    no_ctx = tmp / "attachment.htm"
    no_ctx.write_text(_IXBRL_HEAD + _IXBRL_TAIL, encoding="utf-8")
    if _context_elements_fast(no_ctx.read_bytes()) != [] or parse_contexts_from_xml(no_ctx) != []:
        _fail("contextRef-only file should give no contexts")

    commented = tmp / "commented.htm"
    commented.write_text(
        _IXBRL_HEAD + "<!--" + _IXBRL_CONTEXTS + "-->" + _IXBRL_TAIL, encoding="utf-8",
    )
    if _context_elements_fast(commented.read_bytes()) is not None:
        _fail("comments must fall back to the full parse")
    if parse_contexts_from_xml(commented) != []:
        _fail("commented-out contexts must not be read")

    self_closing = tmp / "self-closing.htm"
    self_closing.write_text(
        _IXBRL_HEAD + _IXBRL_CONTEXTS + '<xbrli:context id="Empty"/>' + _IXBRL_TAIL, encoding="utf-8",
    )
    if _context_elements_fast(self_closing.read_bytes()) is not None:
        _fail("unpaired context tag must fall back to the full parse")
    if parse_contexts_from_xml(self_closing) != full(self_closing):
        _fail("fallback differs from full parse")


def main() -> None:
    test_parsed_context_dimensions()
    test_fy_end_fallback_order()
    test_fy_end_from_summary_result_member()
    with tempfile.TemporaryDirectory() as d:
        test_parser_helpers(Path(d))
    with tempfile.TemporaryDirectory() as d:
        test_context_fast_path(Path(d))
    test_jobs_store_and_normalize()
    test_match_rate_and_golden_helpers()
    print("ok: xbrl units")


if __name__ == "__main__":
    main()
