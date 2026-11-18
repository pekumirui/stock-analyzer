"""決算説明資料の読み取り（KAB-349）。照合・検算の規則と、Batch に出して書くまで。API には出ない（偽のクライアント）。"""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.db import SessionLocal
from app.market import briefing_batch as bb
from app.market import briefing_extract as bx
from app.models import BriefingDocument, BriefingExtract, BriefingFact, BriefingPage
from jobs.market import briefing_extract as job

DOC_ID = "140120261028512345.pdf"
CODE = "68570"
REL = "6857/20261028_6857_アドバンテス_決算説明会資料.pdf"
TEXT = "四半期売上高 190.5 218.2 167.3 194.1 23.2 24.1 FY24 Q2 FY24 Q3"


def _fact(metric, value, of=None, period="Q1", seg=None, unit="円"):
    return {"metric": metric, "segment": seg, "period": period, "period_kind": "actual", "value": value,
            "unit": unit, "part_of": of, "value_num": bx.parse_number(value)}


def test_numbers_split_overlapping_labels() -> None:
    assert {34307.0, 34155.0} <= bx.numbers_in("34,30734,155")
    assert bx.parse_number("△1,026") == -1026.0
    assert bx.strip_unit("55.9%", "%") == "55.9"


@pytest.mark.parametrize(("facts", "status"), [
    ([_fact("A", "10"), _fact("B", "4", "A"), _fact("C", "6", "A")], "ok"),
    ([_fact("A", "10"), _fact("B", "4", "A"), _fact("C", "6", "A"), _fact("B", "4", "A")], "ok"),  # 同じ行が 2 表
    ([_fact("A", "1250"), _fact("B", "377", "A"), _fact("C", "165", "A")], "partial"),            # 主要製品だけ
    ([_fact("A", "31"), _fact("B", "484", "A"), _fact("C", "277", "A"), _fact("D", "(731)", "A")], "paren"),
    ([_fact("T", "279.2"), _fact("GM", "235.7", "T"), _fact("IB", "43.5", "T"), _fact("JP", "79.0", "T"),
      _fact("AS", "49.1", "T"), _fact("EU", "43.7", "T"), _fact("US", "107.4", "T")], "split"),  # 部門別と地域別
    ([_fact("A", "10"), _fact("B", "4", "A"), _fact("C", "9", "A")], "mismatch"),
])
def test_check_sums(facts, status) -> None:
    assert [c["status"] for c in bx.check_sums(facts)] == [status]


def test_rate_columns_are_not_summed() -> None:
    facts = [_fact("A", "11.5", period="前同(%)"), _fact("B", "17.5", "A", "前同(%)"), _fact("C", "3", "A", "前同(%)")]
    assert bx.check_sums(facts) == []


def test_verify_expands_tables_and_keeps_them() -> None:
    out = {"title": "t", "summary": "s", "tables": [{
        "cols": [{"period": "FY24 Q2", "kind": "actual"}, {"period": "FY24 Q3", "kind": "actual"}],
        "rows": [{"metric": "売上高", "segment": None, "unit": "10億円", "of": None, "values": ["190.5", "218.2"]},
                 {"metric": "売上高", "segment": "TS", "unit": "10億円", "of": "売上高|null", "values": ["167.3", "194.1"]},
                 {"metric": "売上高", "segment": "サービス", "unit": "10億円", "of": "売上高", "values": ["23.2", "24.1"]},
                 {"metric": "比率", "segment": None, "unit": "%", "of": None, "values": ["99.9%", None]}]}]}
    r = bx.verify({"text": TEXT}, out)
    assert len(r["facts"]) == 7 and r["tables"] == out["tables"]
    assert [f["verified"] for f in r["facts"]].count(False) == 1
    assert [c["status"] for c in r["sum_checks"]] == ["ok", "ok"]
    assert bb.parse_custom_id(bb.custom_id(bb.request_key(DOC_ID), 12)) == (bb.request_key(DOC_ID), 12)


def test_verify_drops_same_value_in_two_scales() -> None:
    text = "SoC Testers / SoCテスタ $ 4.1B / 41億ドル $ 8.7 - 9.5B / 87 - 95億ドル メモリテスタ $ 1.9B / 19億ドル"
    cols = [{"period": "CY24 実績", "kind": "actual"}, {"period": "CY26 推定", "kind": "forecast"}]
    out = {"tables": [
        {"cols": cols, "rows": [{"metric": "市場規模/SoCテスタ", "segment": None, "unit": "億ドル", "values": ["41", "87-95"]},
                                {"metric": "市場規模/メモリテスタ", "segment": None, "unit": "億ドル", "values": ["19", None]}]},
        {"cols": cols, "rows": [{"metric": "市場規模/SoCテスタ", "segment": None, "unit": "十億ドル", "values": ["4.1", "8.7-9.5"]},
                                {"metric": "市場規模/メモリテスタ", "segment": None, "unit": "十億ドル", "values": ["1.8", None]}]}]}
    r = bx.verify({"text": text}, out)
    assert [(f["metric"], f["value"], f["unit"]) for f in r["facts"]] == [
        ("市場規模/SoCテスタ", "41", "億ドル"), ("市場規模/SoCテスタ", "87-95", "億ドル"),
        ("市場規模/メモリテスタ", "19", "億ドル"), ("市場規模/メモリテスタ", "1.8", "十億ドル")]


@pytest.mark.parametrize("a, b", [
    (("億円", "1,128.6"), ("億円", "1,128.6")),
    (("億円", "11,286"), ("億ドル", "112.86")),
    (("億円", "41"), ("%", "4.1")),
    (("億円", "1,286"), ("円", "128,600,000,000")),
])
def test_scaled_duplicates_need_scale_prefix_and_same_base(a, b) -> None:
    facts = [{"metric": "売上高", "segment": None, "period": "FY25", "period_kind": "actual", "unit": u, "value": v,
              "value_num": bx.parse_number(v)} for u, v in (a, b)]
    assert len(bx.drop_scaled_duplicates(facts)) == 2


def test_unit_scale() -> None:
    assert bx.unit_scale("十億ドル") == ("ドル", 1e9)
    assert bx.unit_scale("百万円") == ("円", 1e6)
    assert bx.unit_scale("10億円") == ("円", 1e9)
    assert bx.unit_scale("%") is None


def test_parse_json_joins_split_pages() -> None:
    seg = {"title": "売上高 事業セグメント別", "summary": "増えた。", "tables": [{"cols": [], "rows": [{"metric": "a"}]}]}
    area = {"title": "売上高 地域(出荷先)別", "summary": "中国が伸びた。", "tables": [{"cols": [], "rows": [{"metric": "b"}]}]}
    joined = {"title": "売上高 事業セグメント別 / 売上高 地域(出荷先)別", "summary": "増えた。 中国が伸びた。",
              "tables": seg["tables"] + area["tables"]}
    as_list = json.dumps([seg, area], ensure_ascii=False, indent=2)
    assert bx.parse_json("```json\n" + as_list + "\n```") == joined
    assert bx.parse_json(json.dumps(seg, ensure_ascii=False) + ",\n" + json.dumps(area, ensure_ascii=False)) == joined
    assert bx.parse_json(json.dumps([seg], ensure_ascii=False)) == seg
    assert bx.parse_json(json.dumps(seg) + "\n}") == seg
    with pytest.raises(ValueError, match="表が無い"):
        bx.parse_json('{"title": "業績", "summary": "s"}, "tables": [{"cols": [], "rows": []}]}')
    with pytest.raises(json.JSONDecodeError):
        bx.parse_json('{"title": "業績", "tables": [')


ANSWER = {"title": "四半期売上高", "summary": "増えた。", "tables": [{
    "cols": [{"period": "FY24 Q2", "kind": "actual"}, {"period": "FY24 Q3", "kind": "actual"}],
    "rows": [{"metric": "四半期売上高", "segment": "合計", "unit": "10億円", "of": None, "values": ["190.5", "218.2"]},
             {"metric": "四半期売上高", "segment": "TS", "unit": "10億円", "of": "四半期売上高|合計",
              "values": ["167.3", "194.1"]},
             {"metric": "四半期売上高", "segment": "サービス", "unit": "10億円", "of": "四半期売上高|合計",
              "values": ["23.2", "24.1"]}]}]}


class FakeBatches:
    def __init__(self):
        self.created: list[list[dict]] = []
        self.status = "in_progress"
        self.answers: dict[int, object] = {}

    def create(self, requests):
        self.created.append(list(requests))
        return SimpleNamespace(id=f"msgbatch_{len(self.created)}")

    def retrieve(self, batch_id):
        return SimpleNamespace(id=batch_id, processing_status=self.status)

    def results(self, batch_id):
        for req in self.created[int(batch_id.split("_")[1]) - 1]:
            _key, page = bb.parse_custom_id(req["custom_id"])
            ans = self.answers.get(page, ANSWER)
            if ans == "errored":
                yield SimpleNamespace(custom_id=req["custom_id"], result=SimpleNamespace(
                    type="errored", error=SimpleNamespace(type="api_error")))
                continue
            msg = SimpleNamespace(
                content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(
                    type="text", text=ans if isinstance(ans, str) else json.dumps(ans, ensure_ascii=False))],
                stop_reason="end_turn",
                usage=SimpleNamespace(input_tokens=100, cache_read_input_tokens=2000, cache_creation_input_tokens=0, output_tokens=500))
            yield SimpleNamespace(custom_id=req["custom_id"], result=SimpleNamespace(type="succeeded", message=msg))


def _pages(_pdf_bytes, dpi=110, render=True):
    pages = [{"page": 1, "kind": "divider", "text": "表紙", "png": None},
             {"page": 2, "kind": "chart_or_table", "text": TEXT, "png": b"PNG" if render else None},
             {"page": 3, "kind": "chart_or_table", "text": TEXT, "png": b"PNG" if render else None}]
    return pages, {"pages": 3}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SHARE_ROOT", str(tmp_path))
    pdf = tmp_path / "briefing" / REL
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.7 dummy")
    monkeypatch.setattr(bx, "load_pages", _pages)
    db = SessionLocal()
    for t in (BriefingFact, BriefingPage, BriefingExtract, BriefingDocument):
        db.query(t).delete()
    for source in ("irbank", "tdnet"):
        db.add(BriefingDocument(source=source, document_id=DOC_ID, code=CODE, company_name="アドバンテスト",
                                pubdate="2026-10-28 15:00:00", title="決算説明会資料", rel_path=REL, url=None,
                                sha256="x", size=10, status="saved", saved_at=datetime.now(bb.JST).replace(tzinfo=None)))
    db.commit()
    db.close()
    return FakeBatches()


def _client(batches):
    return SimpleNamespace(messages=SimpleNamespace(batches=batches))


def test_submit_dry_run_writes_nothing(setup, capsys) -> None:
    assert job.main(["submit", "--code", "6857", "--dry-run"], client=_client(setup)) == 0
    assert "documents=1 requests=2" in capsys.readouterr().out
    assert setup.created == []
    db = SessionLocal()
    assert db.query(BriefingExtract).count() == 0
    db.close()


def test_submit_and_collect(setup, capsys) -> None:
    client = _client(setup)
    setup.answers[2] = json.dumps(ANSWER, ensure_ascii=False) + '\n{"note": "余分"}'
    setup.answers[3] = "errored"
    assert job.main(["submit", "--code", "6857"], client=client) == 0
    (reqs,) = setup.created
    assert [r["custom_id"] for r in reqs] == [bb.custom_id(bb.request_key(DOC_ID), p) for p in (2, 3)]
    p = reqs[0]["params"]
    assert p["model"] == bb.MODEL and p["output_config"] == {"effort": "low"} and "thinking" not in p
    assert p["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert p["messages"][0]["content"][0]["type"] == "image"

    job.main(["collect"], client=client)
    db = SessionLocal()
    ext = db.get(BriefingExtract, DOC_ID)
    assert (ext.status, ext.source, ext.batch_id) == ("submitted", "tdnet", "msgbatch_1")
    db.close()
    assert "batches_waiting=1" in capsys.readouterr().out

    setup.status = "ended"
    job.main(["collect"], client=client)
    db = SessionLocal()
    ext = db.get(BriefingExtract, DOC_ID)
    assert (ext.status, ext.pages, ext.llm_pages, ext.error_pages) == ("done", 3, 2, 1)
    assert (ext.facts, ext.verified, ext.sum_checks, ext.sum_mismatch) == (6, 6, 2, 0)
    assert (ext.input_tokens, ext.output_tokens) == (2100, 500)
    pages = {r.page: r for r in db.query(BriefingPage).filter_by(document_id=DOC_ID)}
    assert pages[1].kind == "divider" and pages[1].tables_json is None
    assert json.loads(pages[2].tables_json) == ANSWER["tables"] and pages[2].summary == "増えた。"
    assert pages[3].error.startswith("errored") and pages[3].raw_text is None
    assert pages[2].raw_text is None
    out = capsys.readouterr().out
    assert "unparsed=1/2" in out and "errors=" not in out
    assert f"{DOC_ID} page 3: errored" in out
    facts = db.query(BriefingFact).filter_by(document_id=DOC_ID).all()
    assert len(facts) == 6 and all(f.verified for f in facts)
    assert {f.sum_status for f in facts if f.segment == "合計"} == {"ok"}
    db.close()

    job.main(["submit", "--code", "6857"], client=client)
    assert len(setup.created) == 1
    setup.answers.clear()
    setup.answers[3] = '{"title": "業績", "summary": "s"}, "tables": []}'
    setup.status = "in_progress"
    job.main(["submit", "--code", "6857", "--redo"], client=client)
    setup.status = "ended"
    job.main(["collect"], client=client)
    db = SessionLocal()
    ext = db.get(BriefingExtract, DOC_ID)
    assert (ext.batch_id, ext.error_pages, ext.facts) == ("msgbatch_2", 1, 6)
    assert db.query(BriefingFact).filter_by(document_id=DOC_ID).count() == 6
    page3 = db.get(BriefingPage, (DOC_ID, 3))
    assert page3.error.startswith("JSON が読めない") and page3.raw_text == setup.answers[3]
    db.close()
    job.main(["status"], client=client)
    assert "done: 1" in capsys.readouterr().out


def test_no_thinking_and_missing_pdf(setup, tmp_path) -> None:
    client = _client(setup)
    job.main(["submit", "--code", "6857", "--no-thinking"], client=client)
    assert setup.created[0][0]["params"]["thinking"] == {"type": "disabled"}
    (tmp_path / "briefing" / REL).unlink()
    setup.status = "ended"
    job.main(["collect"], client=client)
    db = SessionLocal()
    ext = db.get(BriefingExtract, DOC_ID)
    assert ext.status == "failed" and "PDF" in ext.error
    db.close()


def test_script_versions_are_not_read(setup) -> None:
    db = SessionLocal()
    try:
        for doc_id, title in (
            ("hpscript1", "2027年6月期 第1四半期 ノーツ付き決算説明資料"),
            ("hpscript2", "第2四半期 決算説明資料(スクリプト付)"),
            ("hpscript3", "第2四半期決算 説明会資料(ノート付き)"),
            ("hpqa", "2027年3月期 第2四半期 書き起こし(プレゼンテーション / 質疑応答)"),
        ):
            db.add(BriefingDocument(source="homepage", document_id=doc_id, code=CODE, company_name="アドバンテスト",
                                    pubdate="2026-11-10 15:00:00", title=title, rel_path=REL, url=None,
                                    sha256="x", size=10, status="saved",
                                    saved_at=datetime.now(bb.JST).replace(tzinfo=None)))
        db.commit()
        assert {p.document_id for p in bb.pending_documents(db)} == {DOC_ID, "hpqa"}
        assert {p.document_id for p in bb.pending_documents(db, redo=True)} == {DOC_ID, "hpqa"}
    finally:
        db.close()
