"""有報の大株主・所有者別（KAB-266）。ZIP は合成。数値は 2026 年提出の実物に合わせた。

東京エレクトロン S100YEOO（日本基準）、日立 S100YGBO（IFRS）、伊藤園 S100YRK7
（議決権ベース + 第一種優先株式）。honbun の表の表示と DB を突き合わせる。
"""
from __future__ import annotations

import io
import os
import re
import sys
import tempfile
import zipfile
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-edinet-sh-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from sqlalchemy import delete  # noqa: E402

from app.db import SessionLocal, init_db  # noqa: E402
from app.edinet.xbrl import pending_documents, run  # noqa: E402
from app.models import (  # noqa: E402
    Stock,
    XbrlMajorShareholder,
    XbrlShareholderFiling,
    XbrlShareholdingByCategory,
)
from app.xbrl.normalize import rebuild_all  # noqa: E402
from app.xbrl.shareholders import rebuild_shareholders  # noqa: E402
from tests.edinet.test_edinet_xbrl import FakeClient, _doc, _reset  # noqa: E402

NS = {
    "jpcrp_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2024-11-01/jpcrp_cor",
    "jpdei_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor",
    "co": "http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E00000-000/2026-03-31/01/2026-06-22",
}
MS_AXIS = "jpcrp_cor:MajorShareholdersAxis"
VR_AXIS = "jpcrp_cor:MajorShareholdersVotingRightsAxis"
CL_AXIS = "jpcrp_cor:ClassesOfSharesAxis"
ORD = "jpcrp_cor:OrdinaryShareMember"
PREF = "co:ClassOnePreferredShareMember"


def _ctx(cid: str, *, instant: str = "2026-03-31", members: list[tuple[str, str]] = ()) -> str:
    scen = "".join(f'<xbrldi:explicitMember dimension="{a}">{m}</xbrldi:explicitMember>'
                   for a, m in members)
    scen = f"<xbrli:scenario>{scen}</xbrli:scenario>" if scen else ""
    return (f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier '
            f'scheme="http://disclosure.edinet-fsa.go.jp">E00000</xbrli:identifier></xbrli:entity>'
            f"<xbrli:period><xbrli:instant>{instant}</xbrli:instant></xbrli:period>{scen}"
            "</xbrli:context>")


def _ms_ctx(n: int, *, voting: bool = False, instant: str = "2026-03-31") -> str:
    if voting:
        return _ctx(f"CurrentYearInstant_No{n}MajorShareholdersVotingRightsMember", instant=instant,
                    members=[(VR_AXIS, f"jpcrp_cor:No{n}MajorShareholdersVotingRightsMember")])
    return _ctx(f"CurrentYearInstant_No{n}MajorShareholdersMember", instant=instant,
                members=[(MS_AXIS, f"jpcrp_cor:No{n}MajorShareholdersMember")])


def _num(name: str, ctx: str, shown: str, *, unit: str, scale: str, decimals: str = "0") -> str:
    return (f'<ix:nonFraction name="{name}" contextRef="{ctx}" unitRef="{unit}" scale="{scale}" '
            f'decimals="{decimals}" format="ixt:num-dot-decimal">{shown}</ix:nonFraction>')


def _str(name: str, ctx: str, text: str) -> str:
    return f'<ix:nonNumeric name="{name}" contextRef="{ctx}" escape="false">{text}</ix:nonNumeric>'


def _html(*, standard: str, extra_ctx: str, table: str, dei: str = "",
          instant: str = "2026-03-31") -> str:
    attrs = " ".join(f'xmlns:{p}="{u}"' for p, u in NS.items())
    units = "".join(
        f'<xbrli:unit id="{u}"><xbrli:measure>{m}</xbrli:measure></xbrli:unit>'
        for u, m in (("pure", "xbrli:pure"), ("shares", "xbrli:shares"))
    )
    ctx = "".join([
        _ctx("CurrentYearInstant", instant=instant),
        _ctx("FilingDateInstant", instant="2026-06-22"),
        _ctx("CurrentYearInstant_OrdinaryShareMember", instant=instant, members=[(CL_AXIS, ORD)]),
        extra_ctx,
    ])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:ixt="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12"
      xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi"
      {attrs}>
<head><ix:header><ix:hidden>
<ix:nonNumeric name="jpdei_cor:AccountingStandardsDEI" contextRef="FilingDateInstant">{standard}</ix:nonNumeric>
<ix:nonNumeric name="jpdei_cor:CurrentPeriodEndDateDEI" contextRef="FilingDateInstant">{instant}</ix:nonNumeric>
{dei}
</ix:hidden><ix:resources>{ctx}{units}</ix:resources></ix:header></head>
<body>{table}</body></html>
"""


def _zip(html: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("XBRL/PublicDoc/0101010_honbun_jpcrp030000-asr-001_ixbrl.htm", html)
    return buf.getvalue()


class _TdText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_td = False
        self._buf: list[str] = []
        self.cells: list[str] = []

    def handle_starttag(self, tag, attrs):  # noqa: ARG002
        if tag == "td":
            self._in_td = True
            self._buf = []

    def handle_endtag(self, tag):
        if tag == "td" and self._in_td:
            self.cells.append(re.sub(r"\s+", "", "".join(self._buf)))
            self._in_td = False

    def handle_data(self, data):
        if self._in_td:
            self._buf.append(data)


def _cells(html: str) -> list[str]:
    p = _TdText()
    p.feed(html)
    return p.cells


def _holder_row(n: int, name: str, addr: str, shown_shares: str, shown_pct: str,
                *, scale: str = "0", voting: bool = False) -> str:
    ctx = (f"CurrentYearInstant_No{n}MajorShareholdersVotingRightsMember" if voting
           else f"CurrentYearInstant_No{n}MajorShareholdersMember")
    if voting:
        return (f"<tr><td>{_str('jpcrp_cor:NameMajorShareholdersVotingRights', ctx, name)}</td>"
                f"<td>{_str('jpcrp_cor:AddressMajorShareholdersVotingRights', ctx, addr)}</td>"
                f"<td>{_num('jpcrp_cor:NumberOfVotingRightsHeld', ctx, shown_shares, unit='pure', scale=scale)}</td>"
                f"<td>{_num('jpcrp_cor:RatioOfVotingRightsHeld', ctx, shown_pct, unit='pure', scale='-2', decimals='2')}</td></tr>")
    return (f"<tr><td>{_str('jpcrp_cor:NameMajorShareholders', ctx, name)}</td>"
            f"<td>{_str('jpcrp_cor:AddressMajorShareholders', ctx, addr)}</td>"
            f"<td>{_num('jpcrp_cor:NumberOfSharesHeld', ctx, shown_shares, unit='shares', scale=scale)}</td>"
            f"<td>{_num('jpcrp_cor:ShareholdingRatio', ctx, shown_pct, unit='pure', scale='-2', decimals='2')}</td></tr>")


def _cat_cell(prefix: str, shown: str | None, *, scale: str = "0", unit: str = "pure") -> str:
    ctx = "CurrentYearInstant_OrdinaryShareMember"
    if shown is None:
        return (f'<ix:nonFraction name="jpcrp_cor:{prefix}" contextRef="{ctx}" unitRef="{unit}" '
                f'scale="{scale}" decimals="0" xsi:nil="true"/>')
    return _num(f"jpcrp_cor:{prefix}", ctx, shown, unit=unit, scale=scale)


def _category_table(*, banks_n: str, banks_u: str, banks_p: str, total_n: str, total_u: str,
                    less: str, unit_share: str = "100") -> str:
    ctx = "CurrentYearInstant_OrdinaryShareMember"
    return f"""<table>
<tr><td>区分</td><td>株主数</td><td>所有株式数（単元）</td><td>割合（％）</td></tr>
<tr><td>金融機関</td><td>{_cat_cell('NumberOfShareholdersFinancialInstitutions', banks_n)}</td>
<td>{_cat_cell('NumberOfSharesHeldNumberOfUnitsFinancialInstitutions', banks_u)}</td>
<td>{_cat_cell('NumberOfShareholdersFinancialInstitutions'.replace('NumberOfShareholders', 'PercentageOfShareholdings'), banks_p, scale='-2', unit='pure')}</td></tr>
<tr><td>計</td><td>{_cat_cell('NumberOfShareholdersTotal', total_n)}</td>
<td>{_cat_cell('NumberOfSharesHeldNumberOfUnitsTotal', total_u)}</td><td>100</td></tr>
</table>
<p>単元未満{_num('jpcrp_cor:NumberOfSharesHeldSharesLessThanOneUnit', ctx, less, unit='shares', scale='0')}</p>
<p>単元{_num('jpcrp_cor:NumberOfSharesConstitutingOneUnit', ctx, unit_share, unit='shares', scale='0')}</p>
"""


def _ingest(code: str, doc_id: str, payload: bytes, **doc_over) -> tuple:
    init_db()
    db = SessionLocal()
    _reset(db)
    db.execute(delete(Stock))
    db.add(Stock(code=code, name="テスト", product_category="011", market="プライム"))
    db.add(_doc(1, doc_id, code=code, **doc_over))
    db.commit()
    st = run(db, FakeClient({doc_id: payload}), pending_documents(db))
    assert st["parsed"] == 1, st
    return db, doc_id


def _header(db, doc_id: str) -> XbrlShareholderFiling:
    return db.query(XbrlShareholderFiling).filter_by(doc_id=doc_id).one()


def _holders(db, filing_id: int, basis: str = "shares") -> list[XbrlMajorShareholder]:
    return (db.query(XbrlMajorShareholder)
            .filter_by(filing_id=filing_id, basis=basis)
            .order_by(XbrlMajorShareholder.rank).all())


# 東京エレクトロン S100YEOO 有報（日本基準、2026-03-31）の 1 位と所有者別
TEL_HTML = _html(
    standard="Japan GAAP",
    extra_ctx=_ms_ctx(1) + _ms_ctx(2),
    table="<table><tr><td>氏名又は名称</td><td>住所</td><td>所有株式数（株）</td><td>割合（％）</td></tr>"
          + _holder_row(1, "日本マスタートラスト信託銀行株式会社(信託口)",
                        "東京都港区赤坂1丁目8番1号赤坂インターシティAIR", "111,481", "24.43", scale="3")
          + _holder_row(2, "株式会社日本カストディ銀行(信託口)",
                        "東京都中央区晴海1丁目8番12号", "46,224", "10.13", scale="3")
          + "</table>"
          + _category_table(banks_n="143", banks_u="1,712,829", banks_p="36.37",
                            total_n="58,966", total_u="4,709,685", less="664,233"),
)


def test_jpgaap_matches_honbun_table() -> None:
    """日本基準・実物（東京エレクトロン S100YEOO）の表示と DB が一致する。"""
    db, doc_id = _ingest("80350", "S100YEOO", _zip(TEL_HTML),
                         doc_description="有価証券報告書－第63期(2025/04/01－2026/03/31)")
    hdr = _header(db, doc_id)
    assert (hdr.basis, hdr.shares_per_unit, hdr.is_latest, hdr.as_of) == (
        "shares", 100, True, date(2026, 3, 31))
    rows = _holders(db, hdr.filing_id)
    assert len(rows) == 2
    assert (rows[0].name, rows[0].shares_held, rows[0].shareholding_ratio) == (
        "日本マスタートラスト信託銀行株式会社(信託口)", Decimal("111481000"), Decimal("0.2443"))
    assert rows[1].shares_held == Decimal("46224000")
    cat = {(r.category, r.share_class): r for r in db.query(XbrlShareholdingByCategory)
           .filter_by(filing_id=hdr.filing_id)}
    banks = cat[("financial_institutions", ORD)]
    assert (banks.shareholder_count, banks.units_held, banks.shareholding_ratio) == (
        143, 1712829, Decimal("0.3637"))
    total = cat[("total", ORD)]
    assert (total.shareholder_count, total.units_held, total.shares_less_than_one_unit) == (
        58966, 4709685, Decimal("664233"))
    cells = _cells(TEL_HTML)
    assert "日本マスタートラスト信託銀行株式会社(信託口)" in cells
    assert "111,481" in cells
    assert "24.43" in cells
    db.close()


# 日立製作所 S100YGBO 有報（IFRS）の 1 位
HITACHI_HTML = _html(
    standard="IFRS",
    extra_ctx=_ms_ctx(1),
    table="<table><tr><td>氏名又は名称</td><td>住所</td><td>所有株式数（株）</td><td>割合（％）</td></tr>"
          + _holder_row(1, "日本マスタートラスト信託銀行株式会社（信託口）",
                        "東京都港区赤坂一丁目８番１号", "742,663,700", "16.50", scale="0")
          + "</table>"
          + _category_table(banks_n="221", banks_u="12,599,693", banks_p="27.80",
                            total_n="363,532", total_u="45,317,660", less="3,794,985"),
)


def test_ifrs_matches_honbun_table() -> None:
    """IFRS・実物（日立 S100YGBO）の表示と DB が一致する。"""
    db, doc_id = _ingest("65010", "S100YGBO", _zip(HITACHI_HTML))
    hdr = _header(db, doc_id)
    row = _holders(db, hdr.filing_id)[0]
    assert (row.name, row.shares_held, row.shareholding_ratio) == (
        "日本マスタートラスト信託銀行株式会社（信託口）", Decimal("742663700"), Decimal("0.1650"))
    banks = db.query(XbrlShareholdingByCategory).filter_by(
        filing_id=hdr.filing_id, category="financial_institutions").one()
    assert (banks.shareholder_count, banks.shareholding_ratio) == (221, Decimal("0.2780"))
    assert "742,663,700" in _cells(HITACHI_HTML)
    db.close()


def _itoen_pref_ctx() -> str:
    return _ctx("CurrentYearInstant_Pref", instant="2026-04-30", members=[(CL_AXIS, PREF)])


ITOEN_HTML = _html(
    standard="Japan GAAP",
    extra_ctx=_ms_ctx(1, instant="2026-04-30") + _ms_ctx(1, voting=True, instant="2026-04-30")
              + _itoen_pref_ctx(),
    table="<table><tr><td>氏名又は名称</td><td>住所</td><td>所有株式数（千株）</td><td>割合（％）</td></tr>"
          + _holder_row(1, "グリーンコア株式会社", "東京都渋谷区富ヶ谷一丁目14番９号",
                        "22,676", "19.68", scale="3")
          + "</table>"
          + "<table><tr><td>氏名又は名称</td><td>所有議決権数（個）</td><td>割合（％）</td></tr>"
          + _holder_row(1, "グリーンコア株式会社", "東京都渋谷区富ヶ谷一丁目14番９号",
                        "167,812", "19.93", scale="0", voting=True)
          + "</table>"
          + _category_table(banks_n="44", banks_u="121,673", banks_p="14.30",
                            total_n="100,135", total_u="850,955", less="116,880")
          + f"<p>優先{_num('jpcrp_cor:NumberOfShareholdersFinancialInstitutions', 'CurrentYearInstant_Pref', '15', unit='pure', scale='0')}"
          f"{_num('jpcrp_cor:NumberOfSharesHeldNumberOfUnitsFinancialInstitutions', 'CurrentYearInstant_Pref', '7,848', unit='pure', scale='0')}"
          f"{_num('jpcrp_cor:PercentageOfShareholdingsFinancialInstitutions', 'CurrentYearInstant_Pref', '2.48', unit='pure', scale='-2')}</p>",
    instant="2026-04-30",
)


def test_voting_rights_and_preferred_class() -> None:
    """議決権ベースと種類株式（伊藤園 S100YRK7）を区別して持つ。"""
    db, doc_id = _ingest("25930", "S100YRK7", _zip(ITOEN_HTML),
                         doc_description="有価証券報告書－第61期(2025/05/01－2026/04/30)")
    hdr = _header(db, doc_id)
    assert hdr.basis == "both"
    shares = _holders(db, hdr.filing_id, "shares")[0]
    voting = _holders(db, hdr.filing_id, "voting_rights")[0]
    assert shares.name == "グリーンコア株式会社"
    assert (shares.shares_held, shares.shareholding_ratio) == (
        Decimal("22676000"), Decimal("0.1968"))
    assert (voting.voting_rights, voting.voting_rights_ratio) == (
        Decimal("167812"), Decimal("0.1993"))
    cats = {(r.category, r.share_class): r for r in db.query(XbrlShareholdingByCategory)
            .filter_by(filing_id=hdr.filing_id)}
    assert cats[("financial_institutions", ORD)].shareholder_count == 44
    pref = cats[("financial_institutions", PREF)]
    assert (pref.shareholder_count, pref.units_held, pref.shareholding_ratio) == (
        15, 7848, Decimal("0.0248"))
    assert "22,676" in _cells(ITOEN_HTML)
    assert "167,812" in _cells(ITOEN_HTML)
    db.close()


def test_total_without_axis_is_not_a_holder() -> None:
    html = _html(
        standard="Japan GAAP",
        extra_ctx=_ms_ctx(1),
        table="<p>" + _str("jpcrp_cor:NameMajorShareholders", "CurrentYearInstant", "計")
              + _holder_row(1, "A社", "東京都", "100", "10.00", scale="0") + "</p>",
    )
    db, doc_id = _ingest("80350", "S100TOTL", _zip(html))
    rows = _holders(db, _header(db, doc_id).filing_id)
    assert [r.name for r in rows] == ["A社"]
    db.close()


def test_amendment_prefers_latest() -> None:
    orig = _html(standard="Japan GAAP", extra_ctx=_ms_ctx(1),
                 table=_holder_row(1, "旧株主", "東京都", "100", "10.00"))
    amd_dei = ('<ix:nonNumeric name="jpdei_cor:IdentificationOfDocumentSubjectToAmendmentDEI" '
               'contextRef="FilingDateInstant">S100ORIG</ix:nonNumeric>')
    amd = _html(standard="Japan GAAP", extra_ctx=_ms_ctx(1), dei=amd_dei,
                table=_holder_row(1, "新株主", "東京都", "200", "20.00"))
    init_db()
    db = SessionLocal()
    _reset(db)
    db.execute(delete(Stock))
    db.add(Stock(code="80350", name="テスト", product_category="011", market="プライム"))
    db.add(_doc(1, "S100ORIG", code="80350", doc_type_code="120",
                submit_datetime=datetime(2026, 6, 1, 15, 0),  # noqa: DTZ001
                doc_description="有価証券報告書－第63期(2025/04/01－2026/03/31)"))
    db.add(_doc(2, "S100AMND", code="80350", doc_type_code="130",
                submit_datetime=datetime(2026, 7, 1, 15, 0),  # noqa: DTZ001
                parent_doc_id="S100ORIG",
                doc_description="訂正有価証券報告書－第63期(2025/04/01－2026/03/31)"))
    db.commit()
    st = run(db, FakeClient({"S100ORIG": _zip(orig), "S100AMND": _zip(amd)}),
             pending_documents(db))
    assert st["parsed"] == 2, st
    by_id = {r.doc_id: r for r in db.query(XbrlShareholderFiling).all()}
    assert by_id["S100ORIG"].is_latest is False
    assert by_id["S100AMND"].is_latest is True
    assert by_id["S100AMND"].amends_doc_id == "S100ORIG"
    assert by_id["S100AMND"].original_doc_id == "S100ORIG"
    name = (db.query(XbrlMajorShareholder)
            .filter_by(filing_id=by_id["S100AMND"].filing_id).one().name)
    assert name == "新株主"
    db.close()


def test_rebuild_from_existing_facts() -> None:
    db, doc_id = _ingest("80350", "S100YEOO", _zip(TEL_HTML))
    hdr = _header(db, doc_id)
    db.query(XbrlMajorShareholder).delete()
    db.query(XbrlShareholdingByCategory).delete()
    db.query(XbrlShareholderFiling).delete()
    db.commit()
    stats = rebuild_shareholders(db, code="80350")
    assert stats["errors"] == 0 and stats["filings"] == 1
    assert _header(db, doc_id).is_latest is True
    assert _holders(db, hdr.filing_id)[0].shares_held == Decimal("111481000")
    db.close()


def test_rebuild_all_respects_code() -> None:
    """xbrl_normalize --rebuild --code は他銘柄の大株主を消さない。"""
    db, tel_id = _ingest("80350", "S100YEOO", _zip(TEL_HTML))
    db.add(Stock(code="65010", name="日立", product_category="011", market="プライム"))
    db.add(_doc(2, "S100YGBO", code="65010"))
    db.commit()
    st = run(db, FakeClient({"S100YGBO": _zip(HITACHI_HTML)}), pending_documents(db))
    assert st["parsed"] == 1, st
    tel_fid = _header(db, tel_id).filing_id
    hit_fid = _header(db, "S100YGBO").filing_id
    rebuilt = rebuild_all(db, code="80350")
    assert rebuilt["errors"] == 0
    assert _holders(db, tel_fid)[0].shares_held == Decimal("111481000")
    assert _holders(db, hit_fid)[0].shares_held == Decimal("742663700")
    db.close()
