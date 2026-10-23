"""EDINET 公開買付け（KAB-79）。ダウンロードはフェイク、XBRL は実物の表の形に合わせた合成。"""
from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from datetime import date, datetime
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-tob-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.tender_offers import (  # noqa: E402
    classify_opinion,
    exit_status,
    parse_document,
    pending_documents,
    refresh_offers,
    run,
)
from app.edinet.textblocks import read_instance  # noqa: E402
from app.models import (  # noqa: E402
    EdinetDocument,
    EdinetFiler,
    EdinetTenderOffer,
    EdinetTenderOfferDoc,
    Stock,
)
from tests.edinet.test_edinet_large_holdings import _ErrClient  # noqa: E402
from tests.edinet.xbrl_fixture import para, table, xbrl_zip  # noqa: E402

FETCHED = datetime(2026, 10, 15, 18, 0)  # noqa: DTZ001
TODAY = date(2026, 10, 22)
TON, TOR, PST, TOI = "jptoo-ton_cor", "jptoo-tor_cor", "jptoo-pst_cor", "jptoi_cor"


def _notification(price="3,000", end="2026年７月16日（木曜日）", lower="34,941,000") -> bytes:
    return xbrl_zip([
        (f"{TON}:DocumentTitleCoverPage", "公開買付届出書"),
        (f"{TON}:NameOfSubjectCompanyTextBlock", para("１【対象者名】", "株式会社サンプル")),
        (f"{TON}:PeriodOfPurchaseEtcTextBlock", para("①【買付け等の期間】") + table(
            ["買付け等の期間", f"2026年５月13日（水曜日）から{end}まで（44営業日）"],
            ["公告日", "2026年５月13日（水曜日）"])),
        (f"{TON}:PriceOfPurchaseEtcTextBlock", para("（２）【買付け等の価格】") + table(
            ["株券", f"普通株式１株につき 金{price}円"],
            ["新株予約権証券", "第８回新株予約権 １個につき 金１円"],
            ["算定の基礎", "1株当たり金2,000円から金4,000円"])),
        (f"{TON}:NumberOfShareCertificatesEtcIntendedToPurchaseTextBlock", para("（３）【買付予定の株券等の数】") + table(
            ["株券等の種類", "買付予定数", "買付予定数の下限", "買付予定数の上限"],
            ["普通株式", "121,905,767（株）", f"{lower}（株）", "―（株）"],
            ["合計", "121,905,767（株）", f"{lower}（株）", "―（株）"])),
        (f"{TON}:HoldingRatioOfShareCertificatesEtcAfterPurchaseEtc", "1.0000"),
    ], name="jptoo020000-ton-001_E88888-000_2026-05-13_01_2026-05-13")


def _report(success=True) -> bytes:
    text = ("応募株券等の数の合計（40,000,000株）が買付予定数の下限（34,941,000株）以上となりましたので、"
            "応募株券等の全部の買付け等を行います。") if success else (
            "応募株券等の数の合計（721,295株）が買付予定数の下限に満たなかったため、応募株券等の全部の買付け等を行いません。")
    bought = "40,000,000" if success else "―"
    return xbrl_zip([
        (f"{TOR}:NameOfSubjectCompanyTextBlock", para("（１）【対象者名】", "株式会社サンプル")),
        (f"{TOR}:TenderOfferPeriodTextBlock", para("（３）【公開買付期間】",
                                                  "2026年５月13日（水曜日）から2026年８月17日（月曜日）まで（66営業日）")),
        (f"{TOR}:SuccessOrFailureOfTenderOfferTextBlock", para("（１）【公開買付けの成否】", text)),
        (f"{TOR}:NumberOfShareCertificatesEtcAcquiredByPurchaseEtcTextBlock", para("（３）【買付け等を行った株券等の数】") + table(
            ["株券等の種類", "株式に換算した応募数", "株式に換算した買付数"],
            ["株券", "40,000,000（株）", f"{bought}（株）"],
            ["新株予約権証券", "―", "―"],
            ["合計", "40,000,000", bought])),
        (f"{TOR}:HoldingRatioOfShareCertificatesEtcAfterPurchaseEtc", "0.8100"),
    ], name="jptoo060000-tor-001_E88888-000_2026-08-18_01_2026-08-18")


def _opinion(text: str) -> bytes:
    return xbrl_zip([
        (f"{PST}:OpinionRegardingSaidTenderOfferAndBasisAndReasonsEtcTextBlock",
         para("４【当該公開買付けに関する意見の内容、根拠及び理由等】", "（１）【意見の内容】", text,
              "（２）【意見の根拠及び理由】", "反対する株主もいます。")),
    ], name="jptoo040000-pst-001_E77777-000_2026-05-13_01_2026-05-13")


SUPPORT = ("当社は、2026年５月12日開催の取締役会において、本公開買付けに賛同の意見を表明するとともに、"
           "当社の株主の皆様に対して、本公開買付けへの応募を推奨する旨の決議をいたしました。")
SUPPORT_NEUTRAL = ("当社は、本公開買付けに賛同する旨の意見を表明すること、また当社の株主の皆様が本公開買付けに"
                   "応募するか否かについては、中立の立場をとり、株主の皆様のご判断に委ねることを決議いたしました。")
# S100Y50I（訂正意見表明報告書）: 要素名が違い、見出しに【】が無い
OPPOSE_AMEND = ("当社は、2026年５月19日開催の取締役会において、公開買付者より開始された当社の普通株式に対する"
                "公開買付けに反対することを決議いたしました。")


def _opinion_alt_block(text: str) -> bytes:
    return xbrl_zip([
        (f"{PST}:OpinionAndBasisAndReasonOfOpinionRegardingSaidTenderOfferTextBlock",
         para("(1) 本公開買付けに関する意見の内容", text,
              "(2) 本公開買付けに関する意見の根拠及び理由",
              "公開買付者の想定する企業価値向上施策は抽象的である。")),
    ], name="jptoo040000-pst-001_E02848-000_2026-04-21_02_2026-05-19")


def _issuer_notification() -> bytes:
    return xbrl_zip([
        (f"{TOI}:PeriodOfPurchaseEtcTextBlock", para("（１）【買付け等の期間】") + table(
            ["買付け等の期間", "2026年８月４日（火曜日）から2026年９月１日（火曜日）まで（20営業日）"])),
        (f"{TOI}:PriceOfPurchaseEtcTextBlock", para("（２）【買付け等の価格等】") + table(
            ["上場株券等の種類", "買付け等の価格"], ["普通株式", "１株につき、金1,813円"])),
        (f"{TOI}:NumberOfListedShareCertificatesEtcIntendedToPurchaseTextBlock", table(
            ["上場株券等の種類", "買付予定数", "超過予定数", "計"],
            ["普通株式", "82,735,700（株）", "―（株）", "82,735,700（株）"],
            ["合計", "82,735,700（株）", "―（株）", "82,735,700（株）"])),
    ], name="jptoi020000-ton-001_E02497-000_2026-08-04_01_2026-08-04")


def _issuer_report() -> bytes:
    return xbrl_zip([
        (f"{TOI}:TenderOfferPeriodTextBlock", para("（２）【公開買付期間】",
                                                  "2026年８月４日（火曜日）から2026年９月１日（火曜日）まで（20営業日）")),
        (f"{TOI}:NumberOfListedShareCertificatesEtcAcquiredByPurchaseEtcTextBlock", table(
            ["上場株券等の種類", "普通株式"], ["応募数（株）", "83,403,785"], ["買付数（株）", "82,735,750"])),
    ], name="jptoi040000-tor-001_E02497-000_2026-09-02_01_2026-09-02")


def test_parse_notification_and_report() -> None:
    v = parse_document("240", read_instance(_notification()), issuer=False)
    assert (v["period_start"], v["period_end"]) == (date(2026, 5, 13), date(2026, 7, 16))
    assert v["price"] == 3000.0
    assert (v["planned_shares"], v["min_shares"], v["max_shares"]) == (121905767, 34941000, None)
    assert (v["holding_ratio_after"], v["target_name"]) == (1.0, "株式会社サンプル")

    r = parse_document("270", read_instance(_report()), issuer=False)
    assert (r["success"], r["tendered_shares"], r["purchased_shares"]) == (True, 40000000, 40000000)
    assert r["period_end"] == date(2026, 8, 17)
    f = parse_document("270", read_instance(_report(success=False)), issuer=False)
    assert (f["success"], f["purchased_shares"]) == (False, 0)


def test_parse_issuer() -> None:
    v = parse_document("240", read_instance(_issuer_notification()), issuer=True)
    assert (v["price"], v["planned_shares"], v["min_shares"], v["max_shares"]) == (1813.0, 82735700, None, 82735700)
    r = parse_document("270", read_instance(_issuer_report()), issuer=True)
    assert (r["tendered_shares"], r["purchased_shares"], r["success"]) == (83403785, 82735750, True)


def test_opinion() -> None:
    v = parse_document("290", read_instance(_opinion(SUPPORT)), issuer=False)
    assert (v["opinion"], v["recommendation"]) == ("support", "recommend")
    assert v["opinion_text"] == SUPPORT              # 意見の根拠（反対する株主…）は読まない
    assert classify_opinion(SUPPORT_NEUTRAL) == ("support", "leave")
    assert classify_opinion("本公開買付けに反対の意見を表明し、応募しないことを推奨いたします。") == (
        "oppose", "not_recommend")
    assert classify_opinion("本公開買付けに対する意見の表明を留保いたします。") == ("reserved", None)
    assert classify_opinion("本公開買付けについて中立の立場をとり、応募するか否かは株主の皆様のご判断に委ねます。") == (
        "neutral", "leave")


def test_opinion_alt_textblock() -> None:
    """S100Y50I: 別要素名・【】なし見出しでも反対意見が読める。"""
    v = parse_document("300", read_instance(_opinion_alt_block(OPPOSE_AMEND)), issuer=False)
    assert (v["opinion"], v["recommendation"]) == ("oppose", None)
    assert v["opinion_text"] == OPPOSE_AMEND


def _doc(seq: int, doc_id: str, doc_type: str, **over) -> EdinetDocument:
    desc = {"240": "公開買付届出書", "250": "訂正公開買付届出書", "260": "公開買付撤回届出書",
            "270": "公開買付報告書", "290": "意見表明報告書", "300": "訂正意見表明報告書"}[doc_type]
    offeror = doc_type not in ("290", "300")
    row = {
        "file_date": date(2026, 5, 13), "seq_number": seq, "doc_id": doc_id,
        "edinet_code": "E88888" if offeror else "E77777",
        "sec_code": None if offeror else "23710", "code": None if offeror else "23710",
        "filer_name": "Ｋａｍｇｒａｓ１株式会社" if offeror else "株式会社サンプル",
        "subject_edinet_code": "E77777" if offeror else "E88888",
        "doc_type_code": doc_type, "ordinance_code": "040", "form_code": "020000",
        "doc_description": desc,
        "submit_datetime": datetime(2026, 5, 13, 9, seq),  # noqa: DTZ001
        "withdrawal_status": "0", "doc_info_edit_status": "0", "disclosure_status": "0",
        "xbrl_flag": True, "pdf_flag": True, "attach_doc_flag": False, "english_doc_flag": False,
        "csv_flag": True, "legal_status": "1", "fetched_at": FETCHED,
    }
    row.update(over)
    return EdinetDocument(**row)


def _reset(db) -> None:
    for model in (EdinetTenderOffer, EdinetTenderOfferDoc, EdinetDocument, EdinetFiler, Stock):
        db.execute(delete(model))
    db.add_all([
        Stock(code="23710", name="サンプル", product_category="011", market="プライム"),
        EdinetFiler(edinet_code="E77777", sec_code="23710", filer_name="株式会社サンプル", updated_at=FETCHED),
    ])
    db.commit()


def _run(db, client, docs):
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        st = run(db, client, docs, today=TODAY)
    return st, buf.getvalue()


def test_run_case_lifecycle() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100NOT1", "240"),
            _doc(2, "S100OPN1", "290", parent_doc_id="S100NOT1"),
        ])
        db.commit()
        client = _ErrClient({"S100NOT1": _notification(), "S100OPN1": _opinion(SUPPORT_NEUTRAL)})
        st, _ = _run(db, client, pending_documents(db, today=TODAY))
        assert (st["parsed"], st["failed"], st["offers"]) == (2, 0, 1)
        case = db.get(EdinetTenderOffer, "S100NOT1")
        assert (case.offeror_edinet_code, case.target_edinet_code, case.target_code) == ("E88888", "E77777", "23710")
        assert (case.status, case.price, case.price_initial, case.opinion, case.recommendation) == (
            "open", 3000.0, 3000.0, "support", "leave")
        assert (case.announced_date, case.period_end, case.doc_count) == (date(2026, 5, 13), date(2026, 7, 16), 2)

        # 訂正届出書で価格の引き上げと期間の延長。訂正意見表明は意見表明（290）の子
        db.add_all([
            _doc(3, "S100AMD1", "250", parent_doc_id="S100NOT1", file_date=date(2026, 7, 7)),
            _doc(4, "S100OPN2", "300", parent_doc_id="S100OPN1", file_date=date(2026, 7, 7)),
        ])
        db.commit()
        client.payloads["S100AMD1"] = _notification(price="3,450", end="2026年８月17日（月曜日）", lower="30,000,000")
        client.payloads["S100OPN2"] = _opinion(SUPPORT)
        _run(db, client, pending_documents(db, today=TODAY))
        db.expire_all()
        case = db.get(EdinetTenderOffer, "S100NOT1")
        assert (case.price, case.price_initial, case.min_shares) == (3450.0, 3000.0, 30000000)
        assert (case.period_end, case.recommendation, case.doc_count) == (date(2026, 8, 17), "recommend", 4)
        assert db.get(EdinetTenderOfferDoc, "S100OPN2").tender_offer_id == "S100NOT1"

        # 報告書で成立
        db.add(_doc(5, "S100RPT1", "270", parent_doc_id="S100NOT1", file_date=date(2026, 8, 18)))
        db.commit()
        client.payloads["S100RPT1"] = _report()
        _run(db, client, pending_documents(db, today=TODAY))
        db.expire_all()
        case = db.get(EdinetTenderOffer, "S100NOT1")
        assert (case.status, case.tendered_shares, case.purchased_shares) == ("succeeded", 40000000, 40000000)
        assert (case.result_date, case.holding_ratio_after, case.latest_doc_id) == (
            date(2026, 8, 18), 0.81, "S100RPT1")

        # 再実行しても重複しない
        assert pending_documents(db, today=TODAY) == []
        _run(db, client, pending_documents(db, reparse=True, today=TODAY))
        assert db.query(EdinetTenderOfferDoc).count() == 5
        assert db.query(EdinetTenderOffer).count() == 1

        # 報告書の取下げ: 案件は open に戻る
        db.query(EdinetDocument).filter_by(doc_id="S100RPT1").update({"withdrawal_status": "2"})
        db.commit()
        refresh_offers(db)
        db.expire_all()
        assert db.get(EdinetTenderOffer, "S100NOT1").status == "open"
        assert db.get(EdinetTenderOfferDoc, "S100RPT1").withdrawn is True
    finally:
        db.close()


def test_withdrawal_issuer_and_doc_edit() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100NOT2", "240", subject_edinet_code="E66666"),
            # 書類情報修正で対象者を付け替え（新しいメタデータは修正の行）
            _doc(2, "S100NOT2", "240", doc_info_edit_status="1", file_date=date(2026, 5, 14),
                 ope_datetime=datetime(2026, 5, 14, 10, 0)),  # noqa: DTZ001
            _doc(3, "S100WDR1", "260", parent_doc_id="S100NOT2", file_date=date(2026, 5, 20)),
            _doc(4, "S100ISS1", "240", ordinance_code="050", edinet_code="E77777", subject_edinet_code="E77777",
                 sec_code="23710", code="23710", filer_name="株式会社サンプル"),
            _doc(5, "S100ISR1", "270", ordinance_code="050", edinet_code="E77777", subject_edinet_code="E77777",
                 parent_doc_id="S100ISS1", file_date=date(2026, 9, 2)),
        ])
        db.commit()
        client = _ErrClient({"S100NOT2": _notification(), "S100ISS1": _issuer_notification(),
                             "S100ISR1": _issuer_report()})
        st, _ = _run(db, client, pending_documents(db, today=TODAY))
        assert (st["parsed"], st["meta"], st["failed"], exit_status(st)) == (3, 1, 0, 0)
        assert "S100WDR1" not in client.calls        # 撤回届出書は中身を読まない
        case = db.get(EdinetTenderOffer, "S100NOT2")
        assert (case.status, case.target_edinet_code, case.target_code) == ("withdrawn", "E77777", "23710")
        iss = db.get(EdinetTenderOffer, "S100ISS1")
        assert (iss.is_issuer, iss.target_code, iss.offeror_code, iss.status) == (True, "23710", "23710", "succeeded")
        assert (iss.price, iss.max_shares, iss.purchased_shares) == (1813.0, 82735700, 82735750)
    finally:
        db.close()


def test_s100y50i_alt_opinion_is_parsed() -> None:
    """S100Y50I 相当の訂正意見表明は failed にせず parsed。cron は exit=0。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add(_doc(1, "S100Y50I", "300", parent_doc_id="S100Y03F", file_date=date(2026, 5, 19),
                    edinet_code="E02848", filer_name="株式会社ティムコ"))
        db.commit()
        client = _ErrClient({"S100Y50I": _opinion_alt_block(OPPOSE_AMEND)})
        st, _ = _run(db, client, pending_documents(db, today=TODAY))
        assert (st["parsed"], st["failed"], st["errors"], exit_status(st)) == (1, 0, 0, 0)
        row = db.get(EdinetTenderOfferDoc, "S100Y50I")
        assert (row.parse_status, row.opinion, row.parse_error) == ("parsed", "oppose", None)
        assert pending_documents(db, today=TODAY) == []
    finally:
        db.close()


def test_orphans_merge_by_parties() -> None:
    """届出書と意見表明が取込の範囲より前: 訂正届出書（親 240）と訂正意見表明（親 290）を同じ案件にする。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100AMD9", "250", parent_doc_id="S100OLD0"),
            _doc(2, "S100OPN9", "300", parent_doc_id="S100OLD1"),
        ])
        db.commit()
        client = _ErrClient({"S100AMD9": _notification(price="3,682"), "S100OPN9": _opinion(SUPPORT)})
        st, _ = _run(db, client, pending_documents(db, today=TODAY))
        assert st["offers"] == 1
        case = db.get(EdinetTenderOffer, "S100OLD0")
        assert (case.price, case.opinion, case.doc_count, case.announced_date) == (3682.0, "support", 2, None)
        assert db.get(EdinetTenderOfferDoc, "S100OPN9").tender_offer_id == "S100OLD0"
    finally:
        db.close()


def main() -> None:
    init_db()
    test_parse_notification_and_report()
    test_parse_issuer()
    test_opinion()
    test_opinion_alt_textblock()
    test_run_case_lifecycle()
    test_s100y50i_alt_opinion_is_parsed()
    test_withdrawal_issuer_and_doc_edit()
    test_orphans_merge_by_parties()
    print("ok")


if __name__ == "__main__":
    main()
