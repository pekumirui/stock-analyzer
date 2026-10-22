"""EDINET 大量保有報告書（KAB-74）。ダウンロードはフェイク、CSV は実物の形に合わせた合成。"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-lvh-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete, select  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.documents import CodeLookup  # noqa: E402
from app.edinet.large_holdings import (  # noqa: E402
    exit_status,
    keep_after,
    mark_outside_window,
    parse_csv_zip,
    pending_documents,
    refresh_versions,
    report_row,
    run,
    sec_code5,
)
from app.edinet_wrapper.errors import (  # noqa: E402
    EdinetNotFoundError,
    EdinetServerError,
    EdinetTransportError,
)
from app.models import (  # noqa: E402
    EdinetDocument,
    EdinetFiler,
    LargeHolding,
    LargeHoldingReport,
    Stock,
)
from tests.edinet.test_edinet_xbrl import FakeClient  # noqa: E402

FETCHED = datetime(2026, 10, 15, 18, 0)  # noqa: DTZ001
FDI = "FilingDateInstant"
HEAD = ["要素ID", "項目名", "コンテキストID", "相対年度", "連結・個別", "期間・時点", "ユニットID", "単位", "値"]


def _ctx(no: int) -> str:
    return f"{FDI}_jplvh010000-lvh_E99999-000FilerLargeVolumeHolder{no}Member" if no else FDI


def _csv_zip(facts: list[tuple[str, int, str]], name: str = "jplvh010000-lvh-001_E99999-000_2026-10-07_01_2026-10-15.csv") -> bytes:
    """facts は (要素ID, 保有者番号, 値)。EDINET の type=5 と同じ UTF-16（BOM 付き）・タブ区切り・全列クォート。"""
    lines = ["\t".join(f'"{h}"' for h in HEAD)]
    for el, no, val in facts:
        lines.append("\t".join(f'"{v}"' for v in (el, "", _ctx(no), "提出日時点", "その他", "時点", "", "", val)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"XBRL_TO_CSV/{name}", ("\n".join(lines) + "\n").encode("utf-16"))
    return buf.getvalue()


def _cover(title: str, *, issuer="サンプル株式会社", sec="190A", reason="－", n=2,
           arrangement="連名", amends="－", joint="該当事項なし") -> list[tuple[str, int, str]]:
    return [
        ("jplvh_cor:TotalNumberOfFilersAndJointHoldersCoverPage", 0, str(n)),
        ("jplvh_cor:DocumentTitleCoverPage", 0, title),
        ("jplvh_cor:DateWhenFilingRequirementAroseCoverPage", 0, "2026-10-07"),
        ("jplvh_cor:FilingDateCoverPage", 0, "2026-10-15"),
        ("jplvh_cor:ArrangementOfFilingCoverPage", 0, arrangement),
        ("jplvh_cor:ReasonForFilingChangeReportCoverPage", 0, reason),
        ("jpdei_cor:EDINETCodeDEI", 0, "E99999"),
        ("jpdei_cor:FilerNameInJapaneseDEI", 0, "大量保有証券株式会社"),
        ("jpdei_cor:IdentificationOfDocumentSubjectToAmendmentDEI", 0, amends),
        ("jplvh_cor:NameOfIssuer", 0, issuer),
        ("jplvh_cor:SecurityCodeOfIssuer", 0, sec),
        ("jplvh_cor:InformationAboutJointHoldersTextBlock", 0, joint),
    ]


def _holder(no: int, edinet: str, name: str, shares: str, ratio: str, prev: str = "－",
            purpose: str = "純投資", proposal: str = "該当事項なし。") -> list[tuple[str, int, str]]:
    return [
        ("jplvh_cor:EDINETCodeDEI", no, edinet),
        ("jplvh_cor:FilerNameInJapaneseDEI", no, name),
        ("jplvh_cor:IndividualOrCorporation", no, "法人（株式会社）"),
        ("jplvh_cor:Name", no, name),
        ("jplvh_cor:TotalNumberOfStocksEtcHeld", no, shares),
        ("jplvh_cor:TotalNumberOfOutstandingStocksEtc", no, "10000000"),
        ("jplvh_cor:HoldingRatioOfShareCertificatesEtc", no, ratio),
        ("jplvh_cor:HoldingRatioOfShareCertificatesEtcPerLastReport", no, prev),
        ("jplvh_cor:PurposeOfHolding", no, purpose),
        ("jplvh_cor:ActOfMakingImportantProposalEtc", no, proposal),
        ("jplvh_cor:BaseDate", no, "2026-10-07"),
        ("jplvh_cor:AmountOfOwnFund", no, "5914000"),
        ("jplvh_cor:TotalAmountOfFundingForAcquisition", no, "5914000"),
        # 提出者ごとの集計表にも同じ要素がもう一度出る（最初の値を使う）
        ("jplvh_cor:Name", no, name),
    ]


def _joint_report(title="変更報告書ＮＯ．3", **kw) -> bytes:
    facts = _cover(title, reason="・株券等保有割合の１％以上の減少", **kw)
    facts += _holder(1, "E99999", "大量保有証券株式会社", "600000", "0.0600", "0.0700")
    facts += _holder(2, "E88888", "共同保有アセット株式会社", "150000", "0.0150", "0.0150")
    facts += [
        ("jplvh_cor:TotalNumberOfStocksEtcHeld", 0, "750000"),
        ("jplvh_cor:TotalNumberOfOutstandingStocksEtc", 0, "10000000"),
        ("jplvh_cor:HoldingRatioOfShareCertificatesEtc", 0, "0.0750"),
        ("jplvh_cor:HoldingRatioOfShareCertificatesEtcPerLastReport", 0, "0.0850"),
    ]
    return _csv_zip(facts)


def _single_report(title="大量保有報告書", ratio="0.0512", **kw) -> bytes:
    facts = _cover(title, n=1, arrangement="その他", sec="6516", issuer="既存電機株式会社", **kw)
    facts += _holder(1, "E77777", "アクティビスト・キャピタル", "512000", ratio,
                     purpose="純投資及び状況に応じて重要提案行為等を行うこと。",
                     proposal="状況に応じて経営陣と対話する。")
    return _csv_zip(facts)


def _doc(seq: int, doc_id: str, **over) -> EdinetDocument:
    row = {
        "file_date": date(2026, 10, 15), "seq_number": seq, "doc_id": doc_id,
        "edinet_code": "E99999", "filer_name": "大量保有証券株式会社", "doc_type_code": "350",
        "form_code": "010002", "ordinance_code": "060", "doc_description": "変更報告書",
        "issuer_edinet_code": "E11111",
        "submit_datetime": datetime(2026, 10, 15, 9, 0 + seq),  # noqa: DTZ001
        "withdrawal_status": "0", "doc_info_edit_status": "0", "disclosure_status": "0",
        "xbrl_flag": True, "pdf_flag": True, "attach_doc_flag": False, "english_doc_flag": False,
        "csv_flag": True, "legal_status": "1", "fetched_at": FETCHED,
    }
    row.update(over)
    return EdinetDocument(**row)


def _reset(db) -> None:
    for model in (LargeHolding, LargeHoldingReport, EdinetDocument, EdinetFiler, Stock):
        db.execute(delete(model))
    db.add_all([
        Stock(code="190A0", name="サンプル", product_category="011", market="グロース"),
        Stock(code="65160", name="既存電機", product_category="011", market="プライム"),
        EdinetFiler(edinet_code="E11111", sec_code="190A0", filer_name="サンプル株式会社", updated_at=FETCHED),
    ])
    db.commit()


def test_sec_code5() -> None:
    assert sec_code5("190A") == "190A0"
    assert sec_code5("６５１６") == "65160"
    assert sec_code5("65160") == "65160"
    assert sec_code5(None) is None
    assert sec_code5("－") is None


def test_keep_after() -> None:
    assert keep_after(date(2026, 10, 22)) == date(2021, 10, 22)
    assert keep_after(date(2024, 2, 29)) == date(2019, 2, 28)


def test_parse_joint_report() -> None:
    rep = parse_csv_zip(_joint_report())
    assert rep.holders == [1, 2]
    lookup = CodeLookup({"190A0", "65160"}, {"E11111": "190A0"})
    row = report_row(_doc(1, "S100AAA1"), rep, lookup, FETCHED)
    assert row["code"] == "190A0"
    assert row["issuer_sec_code"] == "190A0"
    assert row["issuer_name"] == "サンプル株式会社"
    assert (row["shares_held"], row["outstanding_shares"]) == (750000, 10000000)
    assert (row["holding_ratio"], row["holding_ratio_prev"]) == (0.075, 0.085)
    assert row["obligation_date"] == date(2026, 10, 7)
    assert row["reason"] == "・株券等保有割合の１％以上の減少"
    assert (row["arrangement"], row["holder_count"]) == ("連名", 2)
    assert row["joint_holders_text"] is None          # 「該当事項なし」は NULL
    assert row["is_amendment"] is False and row["amends_doc_id"] is None


def test_single_holder_totals_from_holder() -> None:
    """保有者が 1 人だと書類レベルの合計が無い。保有者 1 の値を使う。"""
    rep = parse_csv_zip(_single_report())
    lookup = CodeLookup({"65160"}, {})
    row = report_row(_doc(1, "S100AAA2", issuer_edinet_code="E22222"), rep, lookup, FETCHED)
    assert row["code"] == "65160"                     # 発行者の EDINET コードが引けなくても証券コードで付く
    assert (row["shares_held"], row["holding_ratio"]) == (512000, 0.0512)
    assert row["holding_ratio_prev"] is None
    assert row["reason"] is None


def test_run_versions_and_rerun() -> None:
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([
            _doc(1, "S100ORIG"),
            _doc(2, "S100SNGL", doc_type_code="350", form_code="010000", doc_description="大量保有報告書",
                 issuer_edinet_code="E22222"),
            _doc(3, "S100BRKN"),
            _doc(4, "S100NOCV", csv_flag=False),
            _doc(5, "S100OTHR", doc_type_code="120"),
        ])
        db.commit()
        client = FakeClient({
            "S100ORIG": _joint_report(),
            "S100SNGL": _single_report(),
            "S100BRKN": b"not a zip",
        })
        docs = pending_documents(db)
        assert {d.doc_id for d in docs} == {"S100ORIG", "S100SNGL", "S100BRKN"}
        st = run(db, client, docs)
        assert (st["parsed"], st["failed"], st["missing"], st["errors"]) == (2, 1, 0, 0)
        assert exit_status(st) == 1

        holders = db.scalars(select(LargeHolding).where(LargeHolding.doc_id == "S100ORIG")
                             .order_by(LargeHolding.holder_no)).all()
        assert [(h.edinet_code, h.shares_held, h.holding_ratio, h.holding_ratio_prev) for h in holders] == [
            ("E99999", 600000, 0.06, 0.07), ("E88888", 150000, 0.015, 0.015)]
        assert holders[0].important_proposal is None   # 「該当事項なし。」
        assert (holders[0].own_funds, holders[0].total_funds) == (5914000, 5914000)
        sngl = db.get(LargeHolding, ("S100SNGL", 1))
        assert sngl.important_proposal == "状況に応じて経営陣と対話する。"
        assert db.get(LargeHoldingReport, "S100BRKN").parse_status == "failed"
        assert db.get(LargeHoldingReport, "S100ORIG").is_latest is True

        # 再実行: parsed・failed は取り直さない。retry_failed で failed だけ
        assert pending_documents(db) == []
        assert [d.doc_id for d in pending_documents(db, retry_failed=True)] == ["S100BRKN"]
        assert db.query(LargeHolding).count() == 3

        # 訂正（360）: 大元に束ね、最新は訂正の方
        db.add(_doc(6, "S100AMD1", doc_type_code="360", form_code="090001",
                    doc_description="訂正報告書（大量保有報告書・変更報告書）", parent_doc_id="S100ORIG"))
        db.commit()
        client.payloads["S100AMD1"] = _joint_report(title="変更報告書ＮＯ．3（訂正）", amends="S100ORIG")
        run(db, client, pending_documents(db))
        orig, amd = db.get(LargeHoldingReport, "S100ORIG"), db.get(LargeHoldingReport, "S100AMD1")
        db.refresh(orig)
        assert (amd.is_amendment, amd.amends_doc_id, amd.original_doc_id) == (True, "S100ORIG", "S100ORIG")
        assert (orig.is_latest, amd.is_latest) == (False, True)

        # 訂正の訂正も同じ大元に束ねる
        db.add(_doc(7, "S100AMD2", doc_type_code="360", parent_doc_id="S100AMD1"))
        db.commit()
        client.payloads["S100AMD2"] = _joint_report(amends="S100AMD1")
        run(db, client, pending_documents(db))
        amd2 = db.get(LargeHoldingReport, "S100AMD2")
        assert (amd2.original_doc_id, amd2.is_latest) == ("S100ORIG", True)

        # 訂正の取下げ: 一つ前の訂正が最新に戻る
        db.query(EdinetDocument).filter_by(doc_id="S100AMD2").update({"withdrawal_status": "2"})
        db.commit()
        assert refresh_versions(db) == 2
        db.expire_all()
        assert db.get(LargeHoldingReport, "S100AMD2").withdrawn is True
        assert db.get(LargeHoldingReport, "S100AMD2").is_latest is False
        assert db.get(LargeHoldingReport, "S100AMD1").is_latest is True
        assert refresh_versions(db) == 0

        # failed を取り直して parsed になれば、エラーは消える
        client.payloads["S100BRKN"] = _joint_report()
        run(db, client, pending_documents(db, retry_failed=True))
        brkn = db.get(LargeHoldingReport, "S100BRKN")
        db.refresh(brkn)
        assert (brkn.parse_status, brkn.parse_error, brkn.code) == ("parsed", None, "190A0")
    finally:
        db.close()


class _ErrClient(FakeClient):
    """payloads に Exception を置ける。"""

    def download(self, doc_id, file_type):
        got = self.payloads.get(doc_id)
        if isinstance(got, BaseException):
            self.calls.append(doc_id)
            raise got
        return super().download(doc_id, file_type)


def _api_err(cls, status: int, message: str, doc_id: str):
    return cls(message, status=status, endpoint=f"/documents/{doc_id}")


def _run(db, client, docs, *, today: date | None = None):
    buf = StringIO()
    with contextlib.redirect_stdout(buf):
        st = run(db, client, docs, today=today)
    return st, buf.getvalue()


def test_download_404_is_failed_not_error() -> None:
    """CSV が 404 なら failed で保存し、次の pending に出ない。cron は exit=0。ログは集約。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([_doc(1, "S100MZJD"), _doc(2, "S100N24C")])
        db.commit()
        client = _ErrClient({
            "S100MZJD": _api_err(EdinetNotFoundError, 404, "Not Found", "S100MZJD"),
            "S100N24C": _api_err(EdinetNotFoundError, 404, "Not Found", "S100N24C"),
        })
        st, out = _run(db, client, pending_documents(db))
        assert (st["parsed"], st["failed"], st["missing"], st["errors"]) == (0, 0, 2, 0)
        assert exit_status(st) == 0
        row = db.get(LargeHoldingReport, "S100MZJD")
        assert row is not None
        assert row.parse_status == "failed"
        assert "404" in (row.parse_error or "")
        assert pending_documents(db) == []
        assert {d.doc_id for d in pending_documents(db, retry_failed=True)} == {"S100MZJD", "S100N24C"}
        assert db.query(LargeHolding).count() == 0
        assert "download-failed doc_id=" not in out
        assert "failed doc_id=" not in out
        assert "edinet lvh missing n=2 sample=S100MZJD,S100N24C" in out
    finally:
        db.close()


def test_download_5xx_stays_error() -> None:
    """5xx・タイムアウトは行を作らず errors。次の回も取り直し、cron は exit=1。ログは集約。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        db.add_all([_doc(1, "S100SRV1"), _doc(2, "S100TMO1")])
        db.commit()
        client = _ErrClient({
            "S100SRV1": _api_err(EdinetServerError, 500, "Internal Server Error", "S100SRV1"),
            "S100TMO1": EdinetTransportError("timed out"),
        })
        st, out = _run(db, client, pending_documents(db))
        assert (st["parsed"], st["failed"], st["missing"], st["errors"]) == (0, 0, 0, 2)
        assert exit_status(st) == 1
        assert db.get(LargeHoldingReport, "S100SRV1") is None
        assert db.get(LargeHoldingReport, "S100TMO1") is None
        assert {d.doc_id for d in pending_documents(db)} == {"S100SRV1", "S100TMO1"}
        assert "download-failed doc_id=" not in out
        assert "edinet lvh download-error n=2 sample=S100SRV1,S100TMO1" in out
    finally:
        db.close()


def test_outside_window_marked_without_download() -> None:
    """5 年より前はダウンロードせず failed。pending から外れ、次回も選ばれない。"""
    init_db()
    db = SessionLocal()
    try:
        _reset(db)
        # 実行日とずらす。run() にも同じ today を渡さないと ingest が now_jst() を使う。
        today = date(2025, 6, 15)
        floor = keep_after(today)
        old_day = floor - timedelta(days=1)
        db.add_all([
            _doc(1, "S100OLD1", file_date=old_day),
            _doc(2, "S100NEW1", file_date=floor),
        ])
        db.commit()
        assert [d.doc_id for d in pending_documents(db, today=today)] == ["S100NEW1"]
        n = mark_outside_window(db, today=today)
        assert n == 1
        old = db.get(LargeHoldingReport, "S100OLD1")
        assert old is not None
        assert old.parse_status == "failed"
        assert "OutsideEdinetWindow" in (old.parse_error or "")
        assert old_day.isoformat() in (old.parse_error or "")
        client = _ErrClient({"S100NEW1": _joint_report()})
        st, _out = _run(db, client, pending_documents(db, today=today), today=today)
        assert client.calls == ["S100NEW1"]
        assert st["parsed"] == 1
        assert pending_documents(db, today=today) == []
        assert pending_documents(db, today=today, retry_failed=True) == []
    finally:
        db.close()


def main() -> None:
    init_db()
    test_sec_code5()
    test_keep_after()
    test_parse_joint_report()
    test_single_holder_totals_from_holder()
    test_run_versions_and_rerun()
    test_download_404_is_failed_not_error()
    test_download_5xx_stays_error()
    test_outside_window_marked_without_download()
    print("ok")


if __name__ == "__main__":
    main()
