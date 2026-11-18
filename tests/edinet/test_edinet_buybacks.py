"""EDINET 自己株券買付状況報告書（KAB-79）。ダウンロードはフェイク、XBRL は実物の表の形に合わせた合成。"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-buyback-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.buybacks import (  # noqa: E402
    _format_parse_error,
    exit_status,
    keep_after,
    log_parse_error,
    mark_outside_window,
    parse_buyback,
    pending_documents,
    refresh_versions,
    run,
)
from app.edinet.textblocks import (  # noqa: E402
    edinet_doc_id,
    honbun_html,
    integer,
    is_dash,
    ixbrl_html,
    jp_dates,
    public_html,
    read_instance,
    squash,
)
from app.edinet_wrapper.errors import EdinetNotFoundError  # noqa: E402
from app.models import (  # noqa: E402
    EdinetBuyback,
    EdinetBuybackResolution,
    EdinetDocument,
    Stock,
)
from tests.edinet.test_edinet_large_holdings import _ErrClient  # noqa: E402
from tests.edinet.xbrl_fixture import para, table, xbrl_zip  # noqa: E402

FETCHED = datetime(2026, 10, 15, 18, 0)  # noqa: DTZ001
SBR = "jpcrp-sbr_cor"


def _holding(issued="165,356,289", treasury="2,119,557") -> str:
    return para("３【保有状況】", "2026年9月30日現在") + table(
        ["報告月末日における保有状況", "株式数（株）"], ["発行済株式総数", issued], ["保有自己株式数", treasury])


def _board_two() -> str:
    """決議 2 件（終わりかけの決議と始まった決議）。取得日の行・計・累計・進捗。"""
    return para("（２）【取締役会決議による取得の状況】") + table(
        ["2026年９月30日現在"],
        ["区分", "株式数(株)", "価額の総額(円)"],
        ["取締役会(2025年８月１日)での決議状況 (取得期間 2025年11月１日～2026年10月31日)", "", "15,000,000",
         "30,000,000,000"],
        ["報告月における取得自己株式(取得日)", "９月１日", "224,000", "566,903,300"],
        ["", "９月２日", "273,400", "1,331,204,005"],
        ["計", "―", "497,400", "1,898,107,305"],
        ["報告月末現在の累計取得自己株式", "", "7,683,700", "27,134,681,260"],
        ["自己株式取得の進捗状況(％)", "", "51.22", "90.45"],
        ["取締役会(2026年７月30日)での決議状況 (取得期間 2026年11月１日～2027年10月31日)", "", "14,000,000",
         "40,000,000,000"],
        ["報告月における取得自己株式(取得日)", "―", "―", "―"],
        ["計", "―", "―", "―"],
        ["報告月末現在の累計取得自己株式", "", "―", "―"],
        ["自己株式取得の進捗状況(％)", "", "―", "―"],
    )


def _board_one(month_shares="189,200", cum="1,520,000") -> str:
    """期間が決議の次の行・枠に（上限）・累積の表記・末日。"""
    return para("（２）【取締役会決議による取得の状況】", "2026年9月30日現在") + table(
        ["区分", "株式数（株）", "価額の総額（円）"],
        ["取締役会（2026年６月30日）での決議状況", "1,520,000（上限）", "7,000,000,000（上限）"],
        ["（取得期間 2026年７月１日～2026年12月末日）"],
        ["報告月における取得自己株式（取得日）", "9月1日", month_shares, "731,179,500"],
        ["計", "－", month_shares, "731,179,500"],
        ["報告月末現在の累積取得自己株式", cum, "6,067,616,000"],
        ["自己株式取得の進捗状況（％）", "100.00", "86.68"],
    )


def _report(board: str, *, amends: str = "－", shareholders: str | None = None) -> bytes:
    return xbrl_zip([
        (f"{SBR}:DocumentTitleCoverPage", "自己株券買付状況報告書"),
        (f"{SBR}:ReportingPeriodCoverPage", "自　2026年9月1日　至　2026年9月30日"),
        ("jpdei_cor:IdentificationOfDocumentSubjectToAmendmentDEI", amends),
        (f"{SBR}:AcquisitionsByResolutionOfShareholdersMeetingTextBlock",
         shareholders or para("（１）【株主総会決議による取得の状況】", "該当事項はありません。")),
        (f"{SBR}:AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock", board),
        (f"{SBR}:DisposalsOfTreasurySharesTextBlock", para("２【処理状況】", "該当事項はありません。")),
        (f"{SBR}:HoldingOfTreasurySharesTextBlock", _holding()),
    ])


def _doc(seq: int, doc_id: str, **over) -> EdinetDocument:
    row = {
        "file_date": date(2026, 10, 14), "seq_number": seq, "doc_id": doc_id, "code": "43850",
        "edinet_code": "E34064", "sec_code": "43850", "filer_name": "株式会社メルカリ",
        "doc_type_code": "220", "form_code": "170000", "ordinance_code": "010",
        "doc_description": "自己株券買付状況報告書（法２４条の６第１項に基づくもの）",
        "submit_datetime": datetime(2026, 10, 14, 15, seq),  # noqa: DTZ001
        "withdrawal_status": "0", "doc_info_edit_status": "0", "disclosure_status": "0",
        "xbrl_flag": True, "pdf_flag": True, "attach_doc_flag": False, "english_doc_flag": False,
        "csv_flag": True, "legal_status": "1", "fetched_at": FETCHED,
    }
    row.update(over)
    return EdinetDocument(**row)


def _reset(db) -> None:
    for model in (EdinetBuybackResolution, EdinetBuyback, EdinetDocument, Stock):
        db.execute(delete(model))
    db.add(Stock(code="43850", name="メルカリ", product_category="011", market="プライム"))
    db.commit()


def _run(db, client, docs, *, today: date | None = None):
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        st = run(db, client, docs, today=today)
    return st, buf.getvalue()


TODAY = date(2026, 10, 22)


def test_helpers() -> None:
    assert edinet_doc_id("S100YQ5K") == "S100YQ5K"
    assert edinet_doc_id("\u200aS100YQ5K") == "S100YQ5K"
    assert edinet_doc_id("\u200aS100YQ5") is None
    assert edinet_doc_id("S100YQ5") is None
    assert edinet_doc_id("－") is None
    assert jp_dates("2026年９月末日") == [date(2026, 9, 30)]
    assert jp_dates("2026年12月末日") == [date(2026, 12, 31)]
    assert jp_dates("令和８年２月末日まで、令和元年5月1日") == [date(2026, 2, 28), date(2019, 5, 1)]
    assert jp_dates("取得期間 2026年８月６日～2026年10月30日") == [date(2026, 8, 6), date(2026, 10, 30)]
    assert is_dash("―") and is_dash("－（株）") and is_dash("ー") and not is_dash("") and not is_dash("1")
    assert integer("1,520,000（上限）") == 1520000
    assert integer("―（株）") is None
    assert keep_after(TODAY) == date(2025, 10, 22)


def test_parse_two_resolutions() -> None:
    rep = parse_buyback(read_instance(_report(_board_two())))
    assert (rep.period_start, rep.period_end) == (date(2026, 9, 1), date(2026, 9, 30))
    assert (rep.issued_shares, rep.treasury_shares) == (165356289, 2119557)
    a, b = rep.resolutions
    assert (a.kind, a.body, a.resolution_date) == ("board", "取締役会", date(2025, 8, 1))
    assert (a.acquire_from, a.acquire_to) == (date(2025, 11, 1), date(2026, 10, 31))
    assert (a.limit_shares, a.limit_amount) == (15000000, 30000000000)
    assert (a.month_shares, a.month_amount) == (497400, 1898107305)
    assert (a.cumulative_shares, a.cumulative_amount) == (7683700, 27134681260)
    assert (a.progress_shares, a.progress_amount) == (0.5122, 0.9045)
    assert (b.resolution_date, b.limit_shares) == (date(2026, 7, 30), 14000000)
    assert (b.month_shares, b.cumulative_shares, b.progress_shares) == (0, 0, None)


def test_parse_period_on_next_row_and_shareholders() -> None:
    sh = para("（１）【株主総会決議による取得の状況】") + table(
        ["区分", "株式数（株）", "価額の総額（円）"],
        ["定時株主総会（2026年６月25日）での決議状況 （取得日 2026年９月15日）", "100,000", "300,000,000"],
        ["報告月における取得自己株式", "100,000", "290,000,000"],
        ["計", "100,000", "290,000,000"],
        ["報告月末現在の累計取得自己株式", "100,000", "290,000,000"],
        ["自己株式取得の進捗状況（％）", "100.0", "96.7"],
    )
    rep = parse_buyback(read_instance(_report(_board_one(), shareholders=sh)))
    s, b = rep.resolutions
    assert (s.kind, s.body, s.resolution_date) == ("shareholders", "定時株主総会", date(2026, 6, 25))
    assert (s.acquire_from, s.acquire_to) == (date(2026, 9, 15), date(2026, 9, 15))
    assert (s.month_shares, s.progress_amount) == (100000, 0.967)
    assert (b.acquire_from, b.acquire_to) == (date(2026, 7, 1), date(2026, 12, 31))
    assert (b.limit_shares, b.limit_amount) == (1520000, 7000000000)
    assert (b.cumulative_shares, b.progress_shares) == (1520000, 1.0)


def _board_no_kei(*, month_s="6,610,000", month_a="5,003,770,000",
                  cum_s="6,610,000", cum_a="5,003,770,000",
                  extra_rows: list | None = None) -> str:
    """S100YUHP 形。報告月の行に実数があり、計の行が無い。"""
    rows = [
        ["2026年7月31日現在"],
        ["区分", "株式数（株）", "価額の総額（円）"],
        ["取締役会（2026年7月29日）での決議状況（取得期間　2026年7月30日）", "6,610,000", "5,003,770,000"],
        ["報告月における取得自己株式（取得日 2026年7月30日）", month_s, month_a],
    ]
    if extra_rows:
        rows.extend(extra_rows)
    rows += [
        ["報告月末現在の累計取得自己株式", cum_s, cum_a],
        ["自己株式取得の進捗状況", "100.00", "100.00"],
    ]
    return para("（２）【取締役会決議による取得の状況】") + table(*rows)


def test_parse_month_from_report_row_without_kei() -> None:
    """計が無ければ報告月の行の実数を当月にする。計がある書類は test_parse_two_resolutions。"""
    rep = parse_buyback(read_instance(_report(_board_no_kei())))
    assert len(rep.resolutions) == 1
    r = rep.resolutions[0]
    assert (r.resolution_date, r.acquire_from, r.acquire_to) == (
        date(2026, 7, 29), date(2026, 7, 30), date(2026, 7, 30))
    assert (r.limit_shares, r.limit_amount) == (6610000, 5003770000)
    assert (r.month_shares, r.month_amount) == (6610000, 5003770000)
    assert (r.cumulative_shares, r.progress_shares) == (6610000, 1.0)


def test_parse_month_dash_without_kei_is_zero() -> None:
    rep = parse_buyback(read_instance(_report(_board_no_kei(
        month_s="―", month_a="―", cum_s="―", cum_a="―"))))
    r = rep.resolutions[0]
    assert (r.month_shares, r.month_amount) == (0, 0)
    assert (r.cumulative_shares, r.cumulative_amount) == (0, 0)


def test_parse_kei_row_not_summed_with_report_row() -> None:
    """計がある通常の表は、報告月の初日の数ではなく計を使う。"""
    rep = parse_buyback(read_instance(_report(_board_two())))
    assert rep.resolutions[0].month_shares == 497400
    assert rep.resolutions[0].month_amount == 1898107305
    assert rep.resolutions[1].month_shares == 0


def test_parse_month_details_without_kei_is_error() -> None:
    try:
        parse_buyback(read_instance(_report(_board_no_kei(
            extra_rows=[["７月31日", "100", "200"]]))))
    except ValueError as exc:
        assert "計の行が無く、報告月の内訳がある" in str(exc), exc
        return
    raise AssertionError("expected ValueError")


def test_parse_month_over_cumulative_is_error() -> None:
    try:
        parse_buyback(read_instance(_report(_board_no_kei(
            month_s="6,610,000", cum_s="1,000"))))
    except ValueError as exc:
        assert "報告月の取得が累計を超えている" in str(exc), exc
        return
    raise AssertionError("expected ValueError")


def _board_empty_kei_with_days() -> str:
    """S100YBMN 形。計は空欄、日付の内訳の合計が当月。"""
    return para("（２）【取締役会決議による取得の状況】") + table(
        ["区分", "株式数(株)", "価額の総額(円)"],
        ["取締役会(2026年５月13日)での決議状況 (取得期間2026年５月14日～2026年12月31日)",
         "", "16,000,000", "20,000,000,000"],
        ["報告月における取得自己株式(取得日)", "５月14日", "96,600", "164,962,950"],
        ["", "５月15日", "97,900", "166,747,098"],
        ["計", "―", "", ""],
        ["報告月末現在の累計取得自己株式", "", "194,500", "331,710,048"],
        ["自己株式取得の進捗状況(％)", "", "1.22", "1.66"],
    )


def _board_kei_over_cum(*, kei_a="92,096,000", month_a="92,086,000", cum_a="92,086,000",
                        extra_rows: list | None = None) -> str:
    """S100Y8WI 形。計の金額が累計より 1 万円多い。"""
    rows = [
        ["区分", "株式数(株)", "価額の総額(円)"],
        ["取締役会(2026年５月15日)での決議状況 (取得期間2026年５月18日～2026年５月18日)",
         "", "45,000", "101,070,000"],
        ["報告月における取得自己株式(取得日)", "５月18日", "41,000", month_a],
    ]
    if extra_rows:
        rows.extend(extra_rows)
    rows += [
        ["計", "―", "41,000", kei_a],
        ["報告月末現在の累計取得自己株式", "", "41,000", cum_a],
        ["自己株式取得の進捗状況(％)", "", "91.1", "91.1"],
    ]
    return para("（２）【取締役会決議による取得の状況】") + table(*rows)


def test_parse_empty_kei_sums_date_rows() -> None:
    """計が空欄なら、報告月の初日と日付の内訳を足して当月にする。"""
    rep = parse_buyback(read_instance(_report(_board_empty_kei_with_days())))
    r = rep.resolutions[0]
    assert (r.month_shares, r.month_amount) == (194500, 331710048)
    assert (r.cumulative_shares, r.cumulative_amount) == (194500, 331710048)
    assert (r.limit_shares, r.progress_shares) == (16000000, 0.0122)


def test_parse_kei_over_cumulative_uses_report_row() -> None:
    """計が累計を超え、日付の内訳が無く、報告月が超えなければ報告月を当月にする。"""
    rep = parse_buyback(read_instance(_report(_board_kei_over_cum())))
    r = rep.resolutions[0]
    assert (r.month_shares, r.month_amount) == (41000, 92086000)
    assert r.month_amount != 92096000
    assert (r.cumulative_shares, r.progress_amount) == (41000, 0.911)


def test_parse_kei_over_cumulative_with_details_is_error() -> None:
    """計が累計を超え、日付の内訳もあるときは推測しない。"""
    try:
        parse_buyback(read_instance(_report(_board_kei_over_cum(
            extra_rows=[["", "５月19日", "1", "10"]]))))
    except ValueError as exc:
        assert "報告月の取得が累計を超えている" in str(exc), exc
        return
    raise AssertionError("expected ValueError")


def test_parse_kei_and_report_over_cumulative_is_error() -> None:
    try:
        parse_buyback(read_instance(_report(_board_kei_over_cum(
            kei_a="92,096,000", month_a="92,096,000", cum_a="92,086,000"))))
    except ValueError as exc:
        assert "報告月の取得が累計を超えている" in str(exc), exc
        return
    raise AssertionError("expected ValueError")


def _board_zero_cum(*, month_s="80,000", month_a="106,640,000",
                    cum_s="0", cum_a="0", prog_s="88.89", prog_a="88.89") -> str:
    """S100XZ69 形。当月は正、累計は 0（同月消却）。"""
    return para("（２）【取締役会決議による取得の状況】") + table(
        ["区分", "株式数(株)", "価額の総額(円)"],
        ["取締役会(2026年３月13日)での決議状況 (取得期間2026年３月14日～2026年３月31日)",
         "", "90,000", "119,970,000"],
        ["報告月における取得自己株式(取得日)", "3月16日", month_s, month_a],
        ["計", "―", month_s, month_a],
        ["報告月末現在の累計取得自己株式", "", cum_s, cum_a],
        ["自己株式取得の進捗状況(％)", "", prog_s, prog_a],
    )


def test_parse_zero_cum_when_progress_matches_month() -> None:
    """累計 0・当月正・進捗が当月÷枠なら、当月も累計も書類の値のまま。"""
    rep = parse_buyback(read_instance(_report(_board_zero_cum())))
    r = rep.resolutions[0]
    assert (r.month_shares, r.month_amount) == (80000, 106640000)
    assert (r.cumulative_shares, r.cumulative_amount) == (0, 0)
    assert (r.limit_shares, r.progress_shares, r.progress_amount) == (90000, 0.8889, 0.8889)


def test_parse_zero_cum_wrong_progress_is_error() -> None:
    try:
        parse_buyback(read_instance(_report(_board_zero_cum(prog_s="10.00", prog_a="10.00"))))
    except ValueError as exc:
        assert "報告月の取得が累計を超えている" in str(exc), exc
        return
    raise AssertionError("expected ValueError")


def test_parse_zero_cum_without_progress_is_error() -> None:
    try:
        parse_buyback(read_instance(_report(_board_zero_cum(prog_s="―", prog_a="―"))))
    except ValueError as exc:
        assert "報告月の取得が累計を超えている" in str(exc), exc
        return
    raise AssertionError("expected ValueError")


def _honbun_board_table(*, share_unit: str = "株", amount_unit: str = "円",
                        limit_s: str = "1,100,000", limit_a: str = "500,000,000",
                        month_s: str = "422,000", month_a: str = "299,292,100",
                        cum_s: str = "422,000", cum_a: str = "299,292,100",
                        prog_s: str = "38.36%", prog_a: str = "59.86%") -> str:
    return table(
        ["2026年８月31日現在"],
        ["区分", f"株式数({share_unit})", f"価額の総額({amount_unit})"],
        ["取締役会(2026年８月13日)での決議状況 (取得期間2026年８月14日～2026年８月31日)",
         "", limit_s, limit_a],
        ["報告月における取得自己株式(取得日)", "８月14日", "37,700", "24,997,200"],
        ["計", "―", month_s, month_a],
        ["報告月末現在の累計取得自己株式", "", cum_s, cum_a],
        ["自己株式取得の進捗状況(%)", "", prog_s, prog_a],
    )


def _untagged_honbun(*, board_heading: bool = True, none: bool = False, after: str = "",
                     shareholders: bool = True, **table_kw) -> str:
    """S100Z1HS 形。none なら取得状況は該当事項のみ。"""
    body = "<h3>１【取得状況】</h3>"
    if shareholders:
        body += "<p>(1) 【株主総会決議による取得の状況】</p><p>該当事項はありません。</p>"
    if none:
        body += "<p>(2) 【取締役会決議による取得の状況】</p><p>該当事項はありません。</p>"
    else:
        if board_heading:
            body += "<p>(2) 【取締役会決議による取得の状況】</p>"
        body += _honbun_board_table(**table_kw)
    return (body + "<p>２【処理状況】</p>" + after
            + "<p>該当事項はありません。</p><p>３【保有状況】</p>"
            + _holding("41,270,663", "607,858"))


def _report_untagged_acq(honbun: str | None = None, extra_honbun: str | None = None) -> bytes:
    return xbrl_zip([
        (f"{SBR}:DocumentTitleCoverPage", "自己株券買付状況報告書"),
        (f"{SBR}:ReportingPeriodCoverPage", "自　2026年8月14日　至　2026年8月31日"),
        (f"{SBR}:HoldingOfTreasurySharesTextBlock", _holding("41,270,663", "607,858")),
    ], name="jpcrp170000-sbr-001_E34973-000_2026-09-10_01_2026-09-10",
        honbun=_untagged_honbun() if honbun is None else honbun, extra_honbun=extra_honbun)


def _assert_honbun_error(honbun: str, needle: str, extra_honbun: str | None = None) -> None:
    zip_bytes = _report_untagged_acq(honbun, extra_honbun=extra_honbun)
    try:
        parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    except ValueError as exc:
        assert needle in str(exc), exc
        return
    raise AssertionError(f"expected ValueError containing {needle!r}")


def test_parse_untagged_honbun_s100z1hs() -> None:
    zip_bytes = _report_untagged_acq()
    tagged_only = False
    try:
        parse_buyback(read_instance(zip_bytes))
    except ValueError as exc:
        tagged_only = "取得状況のテキストブロックが無い" in str(exc)
    assert tagged_only
    rep = parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    assert (rep.period_start, rep.period_end) == (date(2026, 8, 14), date(2026, 8, 31))
    assert (rep.issued_shares, rep.treasury_shares) == (41270663, 607858)
    assert len(rep.resolutions) == 1
    r = rep.resolutions[0]
    assert (r.kind, r.body, r.resolution_date) == ("board", "取締役会", date(2026, 8, 13))
    assert (r.acquire_from, r.acquire_to) == (date(2026, 8, 14), date(2026, 8, 31))
    assert (r.limit_shares, r.limit_amount) == (1100000, 500000000)
    assert (r.month_shares, r.month_amount) == (422000, 299292100)
    assert (r.cumulative_shares, r.progress_shares, r.progress_amount) == (422000, 0.3836, 0.5986)


def test_run_untagged_honbun_is_parsed() -> None:
    """S100Z1HS 相当: 未タグの本文表でも parsed、cron は exit=0。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(1, "S100Z1HS", file_date=date(2026, 9, 10), edinet_code="E34973",
                    filer_name="株式会社プレイド", sec_code="41650", code="41650"))
        db.add(Stock(code="41650", name="プレイド", product_category="011", market="グロース"))
        db.commit()
        client = _ErrClient({"S100Z1HS": _report_untagged_acq()})
        st, _ = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["failed"], st["errors"], exit_status(st)) == (1, 0, 0, 0)
        row = db.get(EdinetBuyback, "S100Z1HS")
        assert (row.parse_status, row.parse_error, row.month_shares) == ("parsed", None, 422000)
        assert pending_documents(db, today=TODAY) == []
    finally:
        db.close()


def test_honbun_none_is_zero_resolutions() -> None:
    zip_bytes = _report_untagged_acq(_untagged_honbun(none=True))
    rep = parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    assert rep.resolutions == []
    assert (rep.issued_shares, rep.treasury_shares) == (41270663, 607858)


def test_honbun_broken_table_is_error() -> None:
    html = (
        "<h3>１【取得状況】</h3>"
        "<p>(2) 【取締役会決議による取得の状況】</p>"
        + table(["区分", "株式数(株)", "価額の総額(円)"], ["あいう", "1", "2"])
        + "<p>２【処理状況】</p>"
    )
    _assert_honbun_error(html, "取得状況の決議が読めない")


def test_honbun_ignores_disposal_chapter() -> None:
    fake = ("<p>(2) 【取締役会決議による取得の状況】</p>"
            + _honbun_board_table(limit_s="9,999,999", limit_a="9,999,999,999",
                                  month_s="999,999", month_a="999,999",
                                  cum_s="999,999", cum_a="999,999",
                                  prog_s="10.00%", prog_a="0.01%"))
    zip_bytes = _report_untagged_acq(_untagged_honbun(after=fake))
    rep = parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    assert len(rep.resolutions) == 1
    assert (rep.resolutions[0].month_shares, rep.resolutions[0].limit_shares) == (422000, 1100000)


def test_honbun_scaled_units_is_error() -> None:
    _assert_honbun_error(_untagged_honbun(share_unit="千株", amount_unit="千円"),
                         "取得状況の単位が株・円ではない")


def test_honbun_no_heading_is_error() -> None:
    _assert_honbun_error(_untagged_honbun(board_heading=False, shareholders=False),
                         "取得状況の表に株主総会/取締役会の見出しが無い")


def test_honbun_cumulative_over_limit_is_error() -> None:
    _assert_honbun_error(_untagged_honbun(cum_s="2,000,000"), "累計が枠を超えている")


def test_honbun_zero_cum_when_progress_matches_month() -> None:
    zip_bytes = _report_untagged_acq(_untagged_honbun(cum_s="0", cum_a="0"))
    rep = parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    r = rep.resolutions[0]
    assert (r.month_shares, r.month_amount) == (422000, 299292100)
    assert (r.cumulative_shares, r.cumulative_amount) == (0, 0)
    assert (r.progress_shares, r.progress_amount) == (0.3836, 0.5986)


def test_honbun_duplicate_kind_date_is_error() -> None:
    html = (
        "<h3>１【取得状況】</h3>"
        "<p>(2) 【取締役会決議による取得の状況】</p>"
        + _honbun_board_table() + _honbun_board_table()
        + "<p>２【処理状況】</p>"
    )
    _assert_honbun_error(html, "同じ区分・同じ決議日の決議が重複")


def test_honbun_picks_file_with_acq_section() -> None:
    zip_bytes = _report_untagged_acq("<p>表紙だけ</p>", extra_honbun=_untagged_honbun())
    rep = parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    assert len(rep.resolutions) == 1
    assert rep.resolutions[0].month_shares == 422000


def test_honbun_multiple_acq_files_is_error() -> None:
    _assert_honbun_error(_untagged_honbun(), "取得状況の本文 iXBRL が複数ある",
                         extra_honbun=_untagged_honbun())


def test_run_versions_and_rerun() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100ORIG"),
            _doc(2, "S100BRKN"),
            _doc(3, "S100NOXB", xbrl_flag=False),
            _doc(4, "S100LVH1", doc_type_code="350"),
        ])
        db.commit()
        client = _ErrClient({"S100ORIG": _report(_board_two()), "S100BRKN": b"not a zip"})
        docs = pending_documents(db, today=TODAY)
        assert {d.doc_id for d in docs} == {"S100ORIG", "S100BRKN"}
        st, out = _run(db, client, docs, today=TODAY)
        assert (st["parsed"], st["failed"], st["missing"], st["errors"]) == (1, 1, 0, 0)
        assert exit_status(st) == 1
        assert "edinet buyback parse-error n=1 sample=S100BRKN" in out

        orig = db.get(EdinetBuyback, "S100ORIG")
        assert (orig.code, orig.report_month, orig.resolution_count) == ("43850", "2026-09", 2)
        assert (orig.month_shares, orig.month_amount) == (497400, 1898107305)
        assert (orig.is_latest, orig.original_doc_id) == (True, "S100ORIG")
        res = db.scalars(select(EdinetBuybackResolution).where(EdinetBuybackResolution.doc_id == "S100ORIG")
                         .order_by(EdinetBuybackResolution.resolution_no)).all()
        assert [r.resolution_date for r in res] == [date(2025, 8, 1), date(2026, 7, 30)]

        assert pending_documents(db, today=TODAY) == []
        _run(db, client, pending_documents(db, reparse=True, today=TODAY), today=TODAY)
        assert db.query(EdinetBuybackResolution).count() == 2
        assert db.query(EdinetBuyback).count() == 2

        db.add(_doc(5, "S100AMD1", doc_type_code="230", form_code="170001", parent_doc_id="S100ORIG",
                    doc_description="訂正自己株券買付状況報告書（法２４条の６第１項に基づくもの）"))
        db.commit()
        client.payloads["S100AMD1"] = _report(_board_one(), amends="S100ORIG")
        _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        db.expire_all()
        amd = db.get(EdinetBuyback, "S100AMD1")
        assert (amd.is_amendment, amd.amends_doc_id, amd.original_doc_id) == (True, "S100ORIG", "S100ORIG")
        assert (db.get(EdinetBuyback, "S100ORIG").is_latest, amd.is_latest) == (False, True)
        assert amd.month_shares == 189200

        db.query(EdinetDocument).filter_by(doc_id="S100AMD1").update({"withdrawal_status": "2"})
        db.commit()
        assert refresh_versions(db) == 2
        db.expire_all()
        assert (db.get(EdinetBuyback, "S100AMD1").withdrawn, db.get(EdinetBuyback, "S100AMD1").is_latest) == (True, False)
        assert db.get(EdinetBuyback, "S100ORIG").is_latest is True
        assert refresh_versions(db) == 0
    finally:
        db.close()


def _amendment_honbun(*, items: str = "２【処理状況】</p><p>３【保有状況】",
                      acq_chapter: bool = False, cover: bool = True) -> str:
    """S100YWV5 形。訂正事項に取得状況が無く、取得状況の章も無い（acq_chapter で章を足せる）。"""
    head = ""
    if cover:
        head += "<p>【提出書類】自己株券買付状況報告書の【訂正報告書】</p>"
    head += f"<p>【訂正事項】</p><p>{items}</p>"
    acq = _untagged_honbun() if acq_chapter else ""
    body = acq or (
        "<p>２【処理状況】</p>"
        + table(["区分", "報告月における処分株式数(株)", "処分価額の総額(円)"],
                ["その他", "86,663", "132,487,153"])
        + "<p>３【保有状況】</p>" + _holding("36,961,950", "1,118,553")
    )
    return head + body


def _amendment_zip(*, honbun: str | None = None, extra_facts: list | None = None,
                   extra_honbun: str | None = None) -> bytes:
    facts = [
        (f"{SBR}:DocumentTitleCoverPage", "自己株券買付状況報告書の訂正報告書"),
        (f"{SBR}:ReportingPeriodCoverPage", "自　2026年6月1日　至　2026年6月30日"),
        ("jpdei_cor:AmendmentFlagDEI", "true"),
        ("jpdei_cor:IdentificationOfDocumentSubjectToAmendmentDEI", "S100ORIG"),
        (f"{SBR}:AmendmentsCoverPageTextBlock",
         para("【訂正事項】", "２【処理状況】", "３【保有状況】")),
        (f"{SBR}:HoldingOfTreasurySharesTextBlock", _holding("36,961,950", "1,118,553")),
        (f"{SBR}:DisposalsOfTreasurySharesTextBlock", para("２【処理状況】", "訂正後")),
    ]
    if extra_facts:
        facts.extend(extra_facts)
    return xbrl_zip(
        facts,
        name="jpcrp170000-sbr-001_E02512-000_2026-08-14_01_2026-08-14",
        honbun=_amendment_honbun() if honbun is None else honbun,
        extra_honbun=extra_honbun,
    )


def test_amendment_without_acq_chapter_is_omitted() -> None:
    """S100YWV5 相当: 訂正・取得状況なし → 失敗にならず、取得値も書かない。"""
    zip_bytes = _amendment_zip()
    try:
        parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    except ValueError as exc:
        raise AssertionError(exc) from exc
    tagged_only = parse_buyback(read_instance(zip_bytes))
    assert tagged_only.acq_omitted is True
    assert tagged_only.resolutions == []
    assert (tagged_only.issued_shares, tagged_only.treasury_shares) == (36961950, 1118553)
    rep = parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes))
    assert rep.acq_omitted is True
    assert rep.resolutions == []
    assert (rep.period_start, rep.period_end) == (date(2026, 6, 1), date(2026, 6, 30))
    assert (rep.issued_shares, rep.treasury_shares) == (36961950, 1118553)


def test_run_amendment_without_acq_does_not_write_shares() -> None:
    """訂正を meta で残し、元の取得値を 0 で上書きしない。cron は exit=0。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100ORIG"),
            _doc(2, "S100YWV5", doc_type_code="230", form_code="170001", parent_doc_id="S100ORIG",
                 edinet_code="E02512", filer_name="西華産業株式会社", sec_code="80600", code="80600",
                 doc_description="訂正自己株券買付状況報告書（法２４条の６第１項に基づくもの）",
                 file_date=date(2026, 8, 14)),
        ])
        db.add(Stock(code="80600", name="西華産業", product_category="011", market="プライム"))
        db.commit()
        client = _ErrClient({
            "S100ORIG": _report(_board_one()),
            "S100YWV5": _amendment_zip(),
        })
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["meta"], st["failed"], st["errors"], exit_status(st)) == (1, 1, 0, 0, 0)
        assert "parse-error" not in out
        orig = db.get(EdinetBuyback, "S100ORIG")
        amd = db.get(EdinetBuyback, "S100YWV5")
        assert (orig.parse_status, orig.month_shares) == ("parsed", 189200)
        assert (amd.parse_status, amd.parse_error, amd.is_amendment) == ("meta", None, True)
        assert amd.month_shares is None and amd.month_amount is None
        assert amd.month_shares != 0
        assert amd.resolution_count == 0
        assert db.query(EdinetBuybackResolution).filter_by(doc_id="S100YWV5").count() == 0
        assert orig.month_shares == 189200
        assert (orig.is_latest, amd.is_latest) == (True, False)
        assert pending_documents(db, today=TODAY) == []
        assert pending_documents(db, retry_failed=True, today=TODAY) == []
    finally:
        db.close()


def test_amendment_claims_acq_but_chapter_missing_is_error() -> None:
    honbun = _amendment_honbun(items="１【取得状況】</p><p>２【処理状況】")
    zip_bytes = _amendment_zip(honbun=honbun)
    try:
        parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes), amendment=True)
    except ValueError as exc:
        assert "取得状況の章が無い" in str(exc), exc
        return
    raise AssertionError("expected ValueError containing '取得状況の章が無い'")


def test_regular_report_without_acq_chapter_is_error() -> None:
    html = "<p>２【処理状況】</p><p>該当事項はありません。</p><p>３【保有状況】</p>" + _holding()
    _assert_honbun_error(html, "取得状況の章が無い")


_S100YWV5_ZIP = Path(__file__).resolve().parent / "testdata" / "S100YWV5.zip"
_S100YUHP_ZIP = Path(__file__).resolve().parent / "testdata" / "S100YUHP.zip"
_S100YBMN_ZIP = Path(__file__).resolve().parent / "testdata" / "S100YBMN.zip"
_S100Y8WI_ZIP = Path(__file__).resolve().parent / "testdata" / "S100Y8WI.zip"
_S100XZ69_ZIP = Path(__file__).resolve().parent / "testdata" / "S100XZ69.zip"


def _amendment_body_no_acq() -> str:
    """実物の本文 iXBRL と同じく、処理状況と保有状況だけで取得状況の章が無い。"""
    return (
        "<p>２【処理状況】</p>"
        + table(["区分", "報告月における処分株式数(株)", "処分価額の総額(円)"],
                ["その他", "86,663", "132,487,153"])
        + "<p>３【保有状況】</p>" + _holding("36,961,950", "1,118,553")
    )


def _s100ywv5_layout_zip(*, items_html: str) -> bytes:
    """S100YWV5 実物のファイル配置。訂正事項は PublicDoc/0001000.htm にだけある。"""
    return xbrl_zip(
        [
            (f"{SBR}:DocumentTitleCoverPage",
             "自己株券買付状況報告書（2026年８月14日付け訂正報告書の添付インラインXBRL）"),
            (f"{SBR}:ReportingPeriodCoverPage", "自　2026年6月1日　至　2026年6月30日"),
            ("jpdei_cor:AmendmentFlagDEI", "true"),
            ("jpdei_cor:IdentificationOfDocumentSubjectToAmendmentDEI", "S100YQ5"),
            (f"{SBR}:HoldingOfTreasurySharesTextBlock", _holding("36,961,950", "1,118,553")),
            (f"{SBR}:DisposalsOfTreasurySharesTextBlock", para("２【処理状況】", "訂正後")),
        ],
        name="jpcrp170000-sbr-001_E02512-000_2026-07-13_02_2026-08-14",
        honbun=_amendment_body_no_acq(),
        extra_files={
            "PublicDoc/0000000_header.htm":
                "<p>【提出書類】自己株券買付状況報告書の訂正報告書</p>",
            "PublicDoc/0001000_0318500293808.htm": items_html,
        },
    )


def _parse_like_ingest(zip_bytes: bytes, *, amendment: bool = True):
    return parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes),
                         amendment=amendment, texts=public_html(zip_bytes))


def test_amendment_items_only_in_public_html_is_omitted() -> None:
    """#891 の合成 ZIP は honbun/facts に訂正事項を入れていた。本番は非 iXBRL にだけある。"""
    items = (
        "<p>１ 【自己株券買付状況報告書の訂正報告書の提出理由】</p>"
        "<p>記載事項の一部に誤りがありました。</p>"
        "<p>２ 【訂正事項】</p>"
        "<p>２　処理状況</p>"
        "<p>３　保有状況</p>"
        "<p>３ 【訂正箇所】</p>"
    )
    zip_bytes = _s100ywv5_layout_zip(items_html=items)
    assert not any("訂正事項" in h for h in honbun_html(zip_bytes))
    assert not any("訂正事項" in h for h in ixbrl_html(zip_bytes))
    assert any("訂正事項" in h for h in public_html(zip_bytes))
    try:
        parse_buyback(read_instance(zip_bytes), honbun_html(zip_bytes),
                      amendment=True, texts=ixbrl_html(zip_bytes))
    except ValueError as exc:
        assert "取得状況の章が無い" in str(exc), exc
    else:
        raise AssertionError("ixbrl_html だけでは訂正事項が読めず、失敗するはず")
    rep = _parse_like_ingest(zip_bytes)
    assert rep.acq_omitted is True
    assert rep.resolutions == []
    assert (rep.issued_shares, rep.treasury_shares) == (36961950, 1118553)


def test_amendment_public_html_claims_acq_but_chapter_missing_is_error() -> None:
    items = "<p>２ 【訂正事項】</p><p>１【取得状況】</p><p>２　処理状況</p>"
    zip_bytes = _s100ywv5_layout_zip(items_html=items)
    try:
        _parse_like_ingest(zip_bytes)
    except ValueError as exc:
        assert "取得状況の章が無い" in str(exc), exc
        return
    raise AssertionError("expected ValueError containing '取得状況の章が無い'")


def test_regular_report_public_html_without_acq_chapter_is_error() -> None:
    zip_bytes = xbrl_zip(
        [
            (f"{SBR}:DocumentTitleCoverPage", "自己株券買付状況報告書"),
            (f"{SBR}:ReportingPeriodCoverPage", "自　2026年6月1日　至　2026年6月30日"),
            (f"{SBR}:HoldingOfTreasurySharesTextBlock", _holding()),
        ],
        honbun="<p>２【処理状況】</p><p>該当事項はありません。</p><p>３【保有状況】</p>" + _holding(),
        extra_files={"PublicDoc/0000000_header.htm": "<p>【提出書類】自己株券買付状況報告書</p>"},
    )
    try:
        _parse_like_ingest(zip_bytes, amendment=False)
    except ValueError as exc:
        assert "取得状況の章が無い" in str(exc), exc
        return
    raise AssertionError("expected ValueError containing '取得状況の章が無い'")


def test_real_s100ywv5_zip_is_meta() -> None:
    """本番の type=1 ZIP。訂正事項は PublicDoc/0001000、本文 iXBRL には無い。"""
    content = _S100YWV5_ZIP.read_bytes()
    inst = read_instance(content)
    honbun = honbun_html(content)
    ix = ixbrl_html(content)
    pub = public_html(content)
    assert inst.value("AmendmentFlagDEI") == "true"
    assert "訂正報告書" in (inst.value("DocumentTitleCoverPage") or "")
    assert not any("訂正事項" in h for h in honbun)
    assert not any("訂正事項" in h for h in ix)
    assert any("２ 【訂正事項】" in h for h in pub)
    assert any("２　処理状況" in h and "３　保有状況" in h for h in pub)
    try:
        parse_buyback(inst, honbun, amendment=True, texts=ix)
    except ValueError as exc:
        assert "取得状況の章が無い" in str(exc), exc
    else:
        raise AssertionError("本番と同じ読み口（ixbrl のみ）では失敗するはず")
    rep = parse_buyback(inst, honbun, amendment=True, texts=pub)
    assert rep.acq_omitted is True
    assert rep.resolutions == []
    assert (rep.period_start, rep.period_end) == (date(2026, 6, 1), date(2026, 6, 30))
    assert (rep.issued_shares, rep.treasury_shares) == (36961950, 1118553)
    assert inst.value("IdentificationOfDocumentSubjectToAmendmentDEI") == "S100YQ5"
    assert rep.amends is None


def test_run_real_s100ywv5_zip_is_meta() -> None:
    """実物 ZIP をジョブと同じ入口で meta。取得値は書かない。cron は exit=0。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(
            1, "S100YWV5", doc_type_code="230", form_code="170001", parent_doc_id="S100YQ5K",
            edinet_code="E02512", filer_name="西華産業株式会社", sec_code="80610", code="80610",
            doc_description="訂正自己株券買付状況報告書（法２４条の６第１項に基づくもの）",
            file_date=date(2026, 8, 14),
        ))
        db.add(Stock(code="80610", name="西華産業", product_category="011", market="プライム"))
        db.commit()
        client = _ErrClient({"S100YWV5": _S100YWV5_ZIP.read_bytes()})
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["meta"], st["failed"], st["errors"], exit_status(st)) == (0, 1, 0, 0, 0)
        assert "parse-error" not in out
        amd = db.get(EdinetBuyback, "S100YWV5")
        assert (amd.parse_status, amd.parse_error, amd.is_amendment) == ("meta", None, True)
        assert amd.month_shares is None and amd.month_amount is None
        assert amd.month_shares != 0
        assert amd.resolution_count == 0
        assert amd.issued_shares == 36961950 and amd.treasury_shares == 1118553
        assert (amd.amends_doc_id, amd.original_doc_id) == ("S100YQ5K", "S100YQ5K")
        assert amd.amends_doc_id != "S100YQ5"
        assert db.query(EdinetBuybackResolution).filter_by(doc_id="S100YWV5").count() == 0
        assert amd.is_latest is False
        assert pending_documents(db, retry_failed=True, today=TODAY) == []
    finally:
        db.close()


def test_run_truncated_dei_uses_parent_doc_id() -> None:
    """DEI が S100YQ5（7 文字）でも parentDocID の S100YQ5K に紐付ける。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100YQ5K"),
            _doc(2, "S100YWV5", doc_type_code="230", form_code="170001", parent_doc_id="S100YQ5K",
                 edinet_code="E02512", filer_name="西華産業株式会社", sec_code="80610", code="80610",
                 file_date=date(2026, 8, 14)),
        ])
        db.commit()
        items = "<p>２ 【訂正事項】</p><p>２　処理状況</p><p>３　保有状況</p>"
        client = _ErrClient({
            "S100YQ5K": _report(_board_one()),
            "S100YWV5": _s100ywv5_layout_zip(items_html=items),
        })
        st, _ = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["meta"], st["failed"], exit_status(st)) == (1, 1, 0, 0)
        orig = db.get(EdinetBuyback, "S100YQ5K")
        amd = db.get(EdinetBuyback, "S100YWV5")
        assert (amd.amends_doc_id, amd.original_doc_id) == ("S100YQ5K", "S100YQ5K")
        assert orig.original_doc_id == "S100YQ5K"
        assert (orig.is_latest, amd.is_latest) == (True, False)
        assert orig.month_shares == 189200
    finally:
        db.close()


def test_real_s100yuhp_zip_reads_month_from_report_row() -> None:
    """本番の type=1 ZIP。計の行が無く、報告月の行に 6,610,000 株。"""
    content = _S100YUHP_ZIP.read_bytes()
    inst = read_instance(content)
    block = inst.block("AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock")
    labels = [squash(row[0]) if row else "" for row in block.rows()]
    assert any("報告月における取得" in lab for lab in labels)
    assert not any(lab == "計" for lab in labels)
    rep = parse_buyback(inst, honbun_html(content))
    assert len(rep.resolutions) == 1
    r = rep.resolutions[0]
    assert (r.kind, r.resolution_date) == ("board", date(2026, 7, 29))
    assert (r.acquire_from, r.acquire_to) == (date(2026, 7, 30), date(2026, 7, 30))
    assert (r.limit_shares, r.limit_amount) == (6610000, 5003770000)
    assert (r.month_shares, r.month_amount) == (6610000, 5003770000)
    assert (r.cumulative_shares, r.cumulative_amount) == (6610000, 5003770000)
    assert (r.progress_shares, r.progress_amount) == (1.0, 1.0)
    assert (rep.issued_shares, rep.treasury_shares) == (27120000, 6610000)


def test_run_real_s100yuhp_zip_is_parsed() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(
            1, "S100YUHP", edinet_code="E31267",
            filer_name="株式会社シンプレクス・ファイナンシャル・ホールディングス",
            file_date=date(2026, 8, 5),
        ))
        db.commit()
        client = _ErrClient({"S100YUHP": _S100YUHP_ZIP.read_bytes()})
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["failed"], st["errors"], exit_status(st)) == (1, 0, 0, 0)
        assert "parse-error" not in out
        row = db.get(EdinetBuyback, "S100YUHP")
        assert (row.parse_status, row.parse_error, row.month_shares, row.month_amount) == (
            "parsed", None, 6610000, 5003770000)
        assert row.resolution_count == 1
        res = db.get(EdinetBuybackResolution, ("S100YUHP", 1))
        assert (res.month_shares, res.month_amount) == (6610000, 5003770000)
        assert (res.cumulative_shares, res.progress_shares) == (6610000, 1.0)
    finally:
        db.close()


def test_real_s100ybmn_zip_sums_date_rows() -> None:
    """本番の type=1 ZIP。計は空欄、日付 12 日の合計 1,266,400 株。"""
    content = _S100YBMN_ZIP.read_bytes()
    inst = read_instance(content)
    block = inst.block("AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock")
    labels = [squash(row[0]) if row else "" for row in block.rows()]
    assert any(lab == "計" for lab in labels)
    kei = next(row for row in block.rows() if squash(row[0]) == "計")
    assert integer(kei[1]) is None
    rep = parse_buyback(inst, honbun_html(content))
    assert (rep.period_start, rep.period_end) == (date(2026, 5, 1), date(2026, 5, 31))
    assert (rep.issued_shares, rep.treasury_shares) == (368497717, 21921688)
    assert len(rep.resolutions) == 1
    r = rep.resolutions[0]
    assert (r.kind, r.resolution_date) == ("board", date(2026, 5, 13))
    assert (r.acquire_from, r.acquire_to) == (date(2026, 5, 14), date(2026, 12, 31))
    assert (r.limit_shares, r.limit_amount) == (16000000, 20000000000)
    assert (r.month_shares, r.month_amount) == (1266400, 2114465097)
    assert (r.cumulative_shares, r.cumulative_amount) == (1266400, 2114465097)
    assert (r.progress_shares, r.progress_amount) == (0.079, 0.106)


def test_run_real_s100ybmn_zip_is_parsed() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(
            1, "S100YBMN", edinet_code="E04088", filer_name="小田急電鉄株式会社",
            sec_code="90070", code="90070", file_date=date(2026, 6, 15),
        ))
        db.commit()
        client = _ErrClient({"S100YBMN": _S100YBMN_ZIP.read_bytes()})
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["failed"], st["errors"], exit_status(st)) == (1, 0, 0, 0)
        assert "parse-error" not in out
        row = db.get(EdinetBuyback, "S100YBMN")
        assert (row.parse_status, row.parse_error, row.month_shares, row.month_amount) == (
            "parsed", None, 1266400, 2114465097)
        res = db.get(EdinetBuybackResolution, ("S100YBMN", 1))
        assert (res.month_shares, res.month_amount) == (1266400, 2114465097)
    finally:
        db.close()


def test_real_s100y8wi_zip_uses_report_row_not_kei_typo() -> None:
    """本番の type=1 ZIP。計の金額 92,096,000 は累計より 1 万円多く、報告月を使う。"""
    content = _S100Y8WI_ZIP.read_bytes()
    inst = read_instance(content)
    block = inst.block("AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock")
    kei = next(row for row in block.rows() if squash(row[0]) == "計")
    assert integer(kei[-1]) == 92096000
    rep = parse_buyback(inst, honbun_html(content))
    assert (rep.period_start, rep.period_end) == (date(2026, 5, 1), date(2026, 5, 31))
    assert (rep.issued_shares, rep.treasury_shares) == (1876588, 61018)
    assert len(rep.resolutions) == 1
    r = rep.resolutions[0]
    assert (r.kind, r.resolution_date) == ("board", date(2026, 5, 15))
    assert (r.acquire_from, r.acquire_to) == (date(2026, 5, 18), date(2026, 5, 18))
    assert (r.limit_shares, r.limit_amount) == (45000, 101070000)
    assert (r.month_shares, r.month_amount) == (41000, 92086000)
    assert r.month_amount != 92096000
    assert (r.cumulative_shares, r.cumulative_amount) == (41000, 92086000)
    assert (r.progress_shares, r.progress_amount) == (0.911, 0.911)


def test_run_real_s100y8wi_zip_is_parsed() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(
            1, "S100Y8WI", edinet_code="E00487", filer_name="旭松食品株式会社",
            sec_code="29110", code="29110", file_date=date(2026, 6, 5),
        ))
        db.commit()
        client = _ErrClient({"S100Y8WI": _S100Y8WI_ZIP.read_bytes()})
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["failed"], st["errors"], exit_status(st)) == (1, 0, 0, 0)
        assert "parse-error" not in out
        row = db.get(EdinetBuyback, "S100Y8WI")
        assert (row.parse_status, row.parse_error, row.month_shares, row.month_amount) == (
            "parsed", None, 41000, 92086000)
        res = db.get(EdinetBuybackResolution, ("S100Y8WI", 1))
        assert (res.month_shares, res.month_amount) == (41000, 92086000)
        assert res.month_amount != 92096000
    finally:
        db.close()


def test_real_s100xz69_zip_keeps_month_when_cum_is_zero() -> None:
    """本番の type=1 ZIP。当月 80,000 株、累計 0、進捗 88.89% = 当月÷枠。"""
    content = _S100XZ69_ZIP.read_bytes()
    inst = read_instance(content)
    block = inst.block("AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock")
    cum = next(row for row in block.rows() if "累計" in squash(row[0]))
    assert integer(cum[-2]) == 0 and integer(cum[-1]) == 0
    rep = parse_buyback(inst, honbun_html(content))
    assert (rep.period_start, rep.period_end) == (date(2026, 3, 1), date(2026, 3, 31))
    assert (rep.issued_shares, rep.treasury_shares) == (2794662, 0)
    assert len(rep.resolutions) == 1
    r = rep.resolutions[0]
    assert (r.kind, r.resolution_date) == ("board", date(2026, 3, 13))
    assert (r.acquire_from, r.acquire_to) == (date(2026, 3, 14), date(2026, 3, 31))
    assert (r.limit_shares, r.limit_amount) == (90000, 119970000)
    assert (r.month_shares, r.month_amount) == (80000, 106640000)
    assert (r.cumulative_shares, r.cumulative_amount) == (0, 0)
    assert (r.progress_shares, r.progress_amount) == (0.8889, 0.8889)


def test_run_real_s100xz69_zip_is_parsed() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(
            1, "S100XZ69", edinet_code="E04197", filer_name="京極運輸商事株式会社",
            sec_code="90730", code="90730", file_date=date(2026, 4, 15),
        ))
        db.commit()
        client = _ErrClient({"S100XZ69": _S100XZ69_ZIP.read_bytes()})
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["failed"], st["errors"], exit_status(st)) == (1, 0, 0, 0)
        assert "parse-error" not in out
        row = db.get(EdinetBuyback, "S100XZ69")
        assert (row.parse_status, row.parse_error, row.month_shares, row.month_amount) == (
            "parsed", None, 80000, 106640000)
        res = db.get(EdinetBuybackResolution, ("S100XZ69", 1))
        assert (res.month_shares, res.month_amount) == (80000, 106640000)
        assert (res.cumulative_shares, res.cumulative_amount) == (0, 0)
    finally:
        db.close()


_VITAL_ZIPS = {d: Path(__file__).resolve().parent / "testdata" / f"{d}.zip"
               for d in ("S100YVSC", "S100Z275", "S100Z80G", "S100ZHJ0")}
_VITAL_FILED = {"S100YVSC": date(2026, 8, 10), "S100Z275": date(2026, 9, 14),
                "S100Z80G": date(2026, 10, 14), "S100ZHJ0": date(2026, 11, 13)}
_VITAL_TODAY = date(2026, 11, 13)


def _vital_docs(*doc_ids: str) -> list[EdinetDocument]:
    return [_doc(i, d, edinet_code="E22440", sec_code="31510", code="31510",
                 filer_name="株式会社バイタルケーエスケー・ホールディングス", file_date=_VITAL_FILED[d])
            for i, d in enumerate(doc_ids, start=1)]


def _vital_client(*doc_ids: str) -> _ErrClient:
    return _ErrClient({d: _VITAL_ZIPS[d].read_bytes() for d in doc_ids})


def _vital_run(db, *doc_ids: str):
    for d in _vital_docs(*doc_ids):
        db.add(d)
    db.commit()
    return _run(db, _vital_client(*doc_ids), pending_documents(db, today=_VITAL_TODAY),
                today=_VITAL_TODAY)


def test_real_s100yvsc_zip_reads_written_cumulative() -> None:
    """本番の type=1 ZIP（3151 の 7 月分）。累計の行は株数・金額。"""
    content = _VITAL_ZIPS["S100YVSC"].read_bytes()
    rep = parse_buyback(read_instance(content), honbun_html(content))
    assert (rep.period_start, rep.period_end) == (date(2026, 7, 1), date(2026, 7, 31))
    r = rep.resolutions[0]
    assert (r.limit_shares, r.limit_amount) == (2000000, 4000000000)
    assert (r.month_shares, r.month_amount) == (241500, 386741190)
    assert (r.cumulative_shares, r.cumulative_amount) == (579700, 908445472)
    assert (r.progress_shares, r.progress_amount) == (0.2899, 0.2271)


def test_real_ratio_cumulative_zips_are_not_read_as_counts() -> None:
    """本番の type=1 ZIP（3151 の 8〜10 月分）。累計の行が当月÷枠の割合。株数にしない。"""
    for doc_id, cells, month, progress in (
        ("S100Z275", ("9.76%", "7.93%"), (195100, 317019391), (0.4341, 0.3441)),
        ("S100Z80G", ("7.02%", "5.67%"), (140400, 226973996), (0.4576, 0.3631)),
        ("S100ZHJ0", ("16.35%", "12.47%"), (326900, 498675989), (0.6211, 0.4878)),
    ):
        content = _VITAL_ZIPS[doc_id].read_bytes()
        inst = read_instance(content)
        block = inst.block("AcquisitionsByResolutionOfBoardOfDirectorsMeetingTextBlock")
        cum = next(row for row in block.rows() if "累計" in squash(row[0]))
        assert (cum[-2], cum[-1]) == cells, doc_id
        rep = parse_buyback(inst, honbun_html(content))
        r = rep.resolutions[0]
        assert (r.kind, r.resolution_date) == ("board", date(2026, 5, 14)), doc_id
        assert (r.month_shares, r.month_amount) == month, doc_id
        assert (r.cumulative_shares, r.cumulative_amount) == (None, None), doc_id
        assert (r.progress_shares, r.progress_amount) == progress, doc_id


def test_run_real_ratio_cumulative_chain_sums_previous_months() -> None:
    """7 月の累計に当月を足していく。8 月の書類の進捗率は累計÷枠と合わないが落とさない。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        st, out = _vital_run(db, "S100YVSC", "S100Z275", "S100Z80G", "S100ZHJ0")
        assert (st["parsed"], st["failed"], st["errors"], exit_status(st)) == (4, 0, 0, 0), out
        assert "parse-error" not in out
        want = {
            "S100YVSC": (241500, 579700, 908445472),
            "S100Z275": (195100, 774800, 1225464863),
            "S100Z80G": (140400, 915200, 1452438859),
            "S100ZHJ0": (326900, 1242100, 1951114848),
        }
        for doc_id, (month, cum_s, cum_a) in want.items():
            row = db.get(EdinetBuyback, doc_id)
            assert (row.parse_status, row.parse_error, row.month_shares) == ("parsed", None, month), doc_id
            res = db.get(EdinetBuybackResolution, (doc_id, 1))
            assert (res.month_shares, res.cumulative_shares, res.cumulative_amount) == (
                month, cum_s, cum_a), doc_id
        assert "cum-derived doc_id=S100YVSC" not in out
        assert ("cum-derived doc_id=S100Z275 resolution=2026-05-14 shares=774800 "
                "amount=1225464863 progress-check=mismatch/mismatch") in out
        assert ("cum-derived doc_id=S100Z80G resolution=2026-05-14 shares=915200 "
                "amount=1452438859 progress-check=ok/ok") in out
        assert ("cum-derived doc_id=S100ZHJ0 resolution=2026-05-14 shares=1242100 "
                "amount=1951114848 progress-check=ok/ok") in out
    finally:
        db.close()


def test_run_ratio_cumulative_without_previous_month_is_failed() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        err = StringIO()
        with contextlib.redirect_stderr(err):
            st, _ = _vital_run(db, "S100ZHJ0")
        assert (st["parsed"], st["failed"], exit_status(st)) == (0, 1, 1)
        row = db.get(EdinetBuyback, "S100ZHJ0")
        assert row.parse_status == "failed" and "前月の累計が無い" in row.parse_error
        assert db.get(EdinetBuybackResolution, ("S100ZHJ0", 1)) is None
    finally:
        db.close()


def test_run_ratio_cumulative_rejects_broken_previous_month() -> None:
    """前月の行が「累計 7 株」（割合を株数に読んだ値）のままなら足さない。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        st, _ = _vital_run(db, "S100YVSC", "S100Z275", "S100Z80G")
        assert st["parsed"] == 3
        prev = db.get(EdinetBuybackResolution, ("S100Z80G", 1))
        prev.cumulative_shares, prev.cumulative_amount = 7, 5
        db.commit()
        err = StringIO()
        with contextlib.redirect_stderr(err):
            st, _ = _vital_run(db, "S100ZHJ0")
        assert (st["parsed"], st["failed"]) == (0, 1)
        row = db.get(EdinetBuyback, "S100ZHJ0")
        assert row.parse_status == "failed" and "前月の累計が使えない(株): 7" in row.parse_error
    finally:
        db.close()


def _board_ratio(*, cum_s="12.45%", cum_a="10.45%", kei_s="189,200",
                 period="2026年７月１日～2026年12月末日") -> str:
    return para("（２）【取締役会決議による取得の状況】", "2026年9月30日現在") + table(
        ["区分", "株式数（株）", "価額の総額（円）"],
        ["取締役会（2026年６月30日）での決議状況", "1,520,000（上限）", "7,000,000,000（上限）"],
        [f"（取得期間 {period}）"],
        ["報告月における取得自己株式（取得日）", "9月1日", "189,200", "731,179,500"],
        ["計", "－", kei_s, "731,179,500"],
        ["報告月末現在の累積取得自己株式", cum_s, cum_a],
        ["自己株式取得の進捗状況（％）", "12.45", "10.45"],
    )


def _assert_parse_error(board: str, needle: str) -> None:
    try:
        parse_buyback(read_instance(_report(board)))
    except ValueError as exc:
        assert needle in str(exc), exc
        return
    raise AssertionError("expected ValueError")


def test_parse_ratio_cumulative_must_match_month_over_limit() -> None:
    r = parse_buyback(read_instance(_report(_board_ratio()))).resolutions[0]
    assert (r.month_shares, r.cumulative_shares, r.cumulative_amount) == (189200, None, None)
    _assert_parse_error(_board_ratio(cum_s="62.11％"), "累計の行の割合が当月÷枠と合わない(株)")
    _assert_parse_error(_board_ratio(cum_a="6,067,616,000"), "累計の行の割合が当月÷枠と合わない(円)")


def test_parse_percent_in_count_cell_is_error() -> None:
    _assert_parse_error(_board_ratio(kei_s="12.45%"), "株数・金額の欄が割合で書かれている: 12.45%")


def test_run_ratio_cumulative_in_first_month_is_month() -> None:
    """取得期間が報告月に始まる決議は、前月の行が無くても累計 = 当月。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(1, "S100RAT1"))
        db.commit()
        client = _ErrClient({"S100RAT1": _report(_board_ratio(period="2026年９月１日～2026年12月末日"))})
        st, out = _run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        assert (st["parsed"], st["failed"]) == (1, 0), out
        res = db.get(EdinetBuybackResolution, ("S100RAT1", 1))
        assert (res.cumulative_shares, res.cumulative_amount) == (189200, 731179500)
        assert "cum-derived doc_id=S100RAT1" in out and "progress-check=ok/ok" in out
    finally:
        db.close()


def test_missing_and_outside_window() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        old_day = keep_after(TODAY) - timedelta(days=1)
        db.add_all([_doc(1, "S100GONE"), _doc(2, "S100OLD1", file_date=old_day)])
        db.commit()
        assert [d.doc_id for d in pending_documents(db, today=TODAY)] == ["S100GONE"]
        assert mark_outside_window(db, today=TODAY) == 1
        old = db.get(EdinetBuyback, "S100OLD1")
        assert old.parse_status == "failed" and "OutsideEdinetWindow" in old.parse_error
        client = _ErrClient({"S100GONE": EdinetNotFoundError("Not Found", status=404, endpoint="/documents/S100GONE")})
        out, err = StringIO(), StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            st = run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        stdout, stderr = out.getvalue(), err.getvalue()
        assert (st["missing"], exit_status(st)) == (1, 0)
        assert "edinet buyback missing n=1 sample=S100GONE" in stdout
        assert "parse-error doc_id=S100GONE" not in stdout
        assert "parse-error doc_id=S100GONE" not in stderr
        assert pending_documents(db, today=TODAY) == []
        assert client.calls == ["S100GONE"]
    finally:
        db.close()


def test_parse_error_logs_to_stderr() -> None:
    """壊れた ZIP の parse 失敗は stderr に doc_id・型・メッセージ・短いトレース。stdout の集約行は残す。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(1, "S100BRKN"))
        db.commit()
        client = _ErrClient({"S100BRKN": b"not a zip"})
        out, err = StringIO(), StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            st = run(db, client, pending_documents(db, today=TODAY), today=TODAY)
        stdout, stderr = out.getvalue(), err.getvalue()
        assert (st["parsed"], st["failed"], exit_status(st)) == (0, 1, 1)
        assert "edinet buyback parse-error n=1 sample=S100BRKN" in stdout
        assert "parse-error doc_id=S100BRKN" not in stdout
        assert "parse-error doc_id=S100BRKN" in stderr
        assert "type=" in stderr and "msg=" in stderr
        assert "ingest_document" in stderr or "read_instance" in stderr
        assert "failed doc_id=" not in stdout
        assert "failed doc_id=" not in stderr
        row = db.get(EdinetBuyback, "S100BRKN")
        assert row.parse_status == "failed" and row.parse_error
    finally:
        db.close()


def test_parse_error_redacts_secrets() -> None:
    secret = "super-secret-edinet-key-do-not-leak"
    try:
        raise ValueError(
            f"EDINET_API_KEY={secret} "
            f"https://api.edinet-fsa.go.jp/api/v2/documents/S100X?type=1&Subscription-Key={secret}"
        )
    except ValueError as exc:
        lines = _format_parse_error("S100FAKE", exc)
        buf = StringIO()
        with contextlib.redirect_stderr(buf):
            log_parse_error("S100FAKE", exc)
        logged = buf.getvalue()
    text = "\n".join(lines)
    assert secret not in text
    assert secret not in logged
    assert "EDINET_API_KEY=<redacted>" in text
    assert "Subscription-Key=***" in text or "Subscription-Key=<redacted>" in text
    assert "doc_id=S100FAKE" in text and "type=ValueError" in text
    assert "failed doc_id=" not in text
    assert "parse-error doc_id=S100FAKE" in logged
    assert any("test_parse_error_redacts_secrets" in line for line in lines)


def main() -> None:
    init_db()
    test_helpers()
    test_parse_two_resolutions()
    test_parse_period_on_next_row_and_shareholders()
    test_parse_month_from_report_row_without_kei()
    test_parse_month_dash_without_kei_is_zero()
    test_parse_kei_row_not_summed_with_report_row()
    test_parse_month_details_without_kei_is_error()
    test_parse_month_over_cumulative_is_error()
    test_parse_empty_kei_sums_date_rows()
    test_parse_kei_over_cumulative_uses_report_row()
    test_parse_kei_over_cumulative_with_details_is_error()
    test_parse_kei_and_report_over_cumulative_is_error()
    test_parse_zero_cum_when_progress_matches_month()
    test_parse_zero_cum_wrong_progress_is_error()
    test_parse_zero_cum_without_progress_is_error()
    test_parse_untagged_honbun_s100z1hs()
    test_run_untagged_honbun_is_parsed()
    test_honbun_none_is_zero_resolutions()
    test_honbun_broken_table_is_error()
    test_honbun_ignores_disposal_chapter()
    test_honbun_scaled_units_is_error()
    test_honbun_no_heading_is_error()
    test_honbun_cumulative_over_limit_is_error()
    test_honbun_zero_cum_when_progress_matches_month()
    test_honbun_duplicate_kind_date_is_error()
    test_honbun_picks_file_with_acq_section()
    test_honbun_multiple_acq_files_is_error()
    test_amendment_without_acq_chapter_is_omitted()
    test_run_amendment_without_acq_does_not_write_shares()
    test_amendment_claims_acq_but_chapter_missing_is_error()
    test_regular_report_without_acq_chapter_is_error()
    test_amendment_items_only_in_public_html_is_omitted()
    test_amendment_public_html_claims_acq_but_chapter_missing_is_error()
    test_regular_report_public_html_without_acq_chapter_is_error()
    test_real_s100ywv5_zip_is_meta()
    test_run_real_s100ywv5_zip_is_meta()
    test_run_truncated_dei_uses_parent_doc_id()
    test_real_s100yuhp_zip_reads_month_from_report_row()
    test_run_real_s100yuhp_zip_is_parsed()
    test_real_s100ybmn_zip_sums_date_rows()
    test_run_real_s100ybmn_zip_is_parsed()
    test_real_s100y8wi_zip_uses_report_row_not_kei_typo()
    test_run_real_s100y8wi_zip_is_parsed()
    test_real_s100xz69_zip_keeps_month_when_cum_is_zero()
    test_run_real_s100xz69_zip_is_parsed()
    test_real_s100yvsc_zip_reads_written_cumulative()
    test_real_ratio_cumulative_zips_are_not_read_as_counts()
    test_run_real_ratio_cumulative_chain_sums_previous_months()
    test_run_ratio_cumulative_without_previous_month_is_failed()
    test_run_ratio_cumulative_rejects_broken_previous_month()
    test_parse_ratio_cumulative_must_match_month_over_limit()
    test_parse_percent_in_count_cell_is_error()
    test_run_ratio_cumulative_in_first_month_is_month()
    test_run_versions_and_rerun()
    test_missing_and_outside_window()
    test_parse_error_logs_to_stderr()
    test_parse_error_redacts_secrets()
    print("ok")


if __name__ == "__main__":
    main()
