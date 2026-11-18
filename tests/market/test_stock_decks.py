"""銘柄タブの「説明資料」（KAB-350）。一覧と中身の API、グラフの組み立て（node で static/js/stock-deck.js を呼ぶ）。"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from app.db import SessionLocal
from app.market.briefing_batch import JST, request_key
from app.models import BriefingDocument, BriefingExtract, BriefingFact, BriefingPage, Stock

ROOT = Path(__file__).resolve().parents[2]
CODE = "68570"
DONE, TODO = "140120261028512345", "140120260729500001"
TABLE = {
    "cols": [{"period": "FY24 Q2", "kind": "actual"}, {"period": "FY24 Q3", "kind": "actual"},
             {"period": "FY24 Q4", "kind": "actual"}, {"period": "FY25 Q1", "kind": "forecast"}],
    "rows": [{"metric": "売上高", "segment": "合計", "unit": "10億円", "of": None, "values": ["190.5", "218.2", "232.3", "263.8"]},
             {"metric": "売上高", "segment": "TS", "unit": "10億円", "of": "売上高|合計", "values": ["167.3", "194.1", "204.1", "240.6"]},
             {"metric": "売上高", "segment": "サービス", "unit": "10億円", "of": "売上高|合計", "values": ["23.2", "24.1", "28.2", "23.2"]},
             {"metric": "営業利益率", "segment": None, "unit": "%", "of": None, "values": ["33.4", "31.8", "27.6", "47.0"]},
             {"metric": "営業CF", "segment": None, "unit": "10億円", "of": "FCF", "values": ["65.2", "77.5", "115.6", "46.9"]},
             {"metric": "投資CF", "segment": None, "unit": "10億円", "of": "FCF", "values": ["-3.4", "-4.6", "-25.8", "-3.4"]},
             {"metric": "FCF", "segment": None, "unit": "10億円", "of": None, "values": ["61.8", "72.9", "89.8", "43.5"]}],
}


def _now() -> datetime:
    return datetime.now(JST).replace(tzinfo=None)


@pytest.fixture(scope="module", autouse=True)
def seed():
    db = SessionLocal()
    db.add(Stock(code=CODE, name="アドバンテスト"))
    for src, doc, day in (("tdnet", DONE, "2026-10-28"), ("irbank", DONE, "2026-10-28"), ("irbank", TODO, "2026-07-29")):
        db.add(BriefingDocument(source=src, document_id=doc, code=CODE, company_name="アドバンテスト",
                                pubdate=f"{day} 15:00:00", title=f"決算説明会資料 {day}", rel_path=f"6857/{doc}.pdf",
                                url=f"https://example.com/{src}/{doc}.pdf", sha256="x", size=1, status="saved", saved_at=_now()))
    db.add(BriefingExtract(document_id=DONE, source="tdnet", code=CODE, status="done", batch_id="b1",
                           request_key=request_key(DONE), model="claude-haiku-5-5", prompt_version="deck-v3", pages=3,
                           llm_pages=2, error_pages=1, facts=3, verified=2, sum_checks=1, sum_mismatch=0,
                           input_tokens=1, output_tokens=1, submitted_at=_now(), extracted_at=_now()))
    db.add_all([
        BriefingPage(document_id=DONE, page=1, kind="divider"),
        BriefingPage(document_id=DONE, page=2, kind="chart_or_table", title="四半期売上高", summary="増えた。",
                     tables_json=json.dumps([TABLE], ensure_ascii=False)),
        BriefingPage(document_id=DONE, page=3, kind="text", error="errored: api_error"),
    ])
    db.add_all([
        BriefingFact(document_id=DONE, page=2, metric="売上高", segment="合計", period="FY24 Q2", period_kind="actual",
                     value="190.5", value_num=190.5, unit="10億円", verified=True, sum_status="mismatch"),
        BriefingFact(document_id=DONE, page=2, metric="売上高", segment="TS", period="FY24 Q2", period_kind="actual",
                     value="167.3", value_num=167.3, unit="10億円", verified=True),
        BriefingFact(document_id=DONE, page=2, metric="営業利益率", period="FY24 Q2", period_kind="actual",
                     value="33.4", value_num=33.4, unit="%", verified=False),
    ])
    db.commit()
    db.close()


def test_list(c) -> None:
    r = c.get("/api/stock/6857/decks")
    assert r.status_code == 200
    decks = r.json()["decks"]
    assert [d["key"] for d in decks] == [request_key(DONE), request_key(TODO)]
    assert decks[0]["url"].startswith("https://example.com/tdnet/")
    assert (decks[0]["status"], decks[0]["pages"], decks[1]["status"]) == ("done", 3, None)
    assert c.get("/api/stock/9999/decks").status_code == 404


def test_detail(c) -> None:
    d = c.get(f"/api/stock/6857/decks/{request_key(DONE)}").json()
    assert d["document"]["title"].startswith("決算説明会資料") and d["extract"]["facts"] == 3
    p = {x["page"]: x for x in d["pages"]}
    assert p[2]["tables"] == [TABLE] and p[2]["summary"] == "増えた。"
    assert (p[2]["unverified"], p[2]["mismatch"]) == (1, 1)
    assert p[3]["error"] and p[1]["tables"] == []
    todo = c.get(f"/api/stock/6857/decks/{request_key(TODO)}").json()
    assert todo["extract"] is None and todo["pages"] == []
    assert c.get("/api/stock/6857/decks/d0000000000000000000a").status_code == 404


def _deck(table: dict) -> dict:
    script = (
        "const m = require(process.argv[1]);"
        "const t = JSON.parse(process.argv[2]);"
        "process.stdout.write(JSON.stringify({charts: m.deckCharts(t), table: m.deckTable(t),"
        " num: [m.deckNum('△1,026'), m.deckNum('(731)'), m.deckNum('－')]}));"
    )
    out = subprocess.run(["node", "-e", script, str(ROOT / "static/js/stock-deck.js"), json.dumps(table)],
                         capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


def _caption(html: str) -> str:
    return re.search(r"<figcaption>(.*?)</figcaption>", html).group(1)


def test_charts_js() -> None:
    res = _deck(TABLE)
    charts = res["charts"]
    titles = [re.search(r"<figcaption>([^<]*)<", h).group(1) for h in charts]
    assert titles == ["売上高（合計）の内訳", "FCFの内訳", "営業利益率"]
    stacked, cf, rate = charts
    for v in ("190.5", "218.2", "232.3", "263.8", "23.2", "167.3"):
        assert f">{v}<" in stacked
    assert "斜線は予想・計画" in stacked and "url(#dk" in stacked
    assert "−25.8" in cf and "<circle" not in cf and "FCF" in cf
    assert "<circle" in rate and ">47.0<" in rate
    assert "白抜きは予想・計画" in rate and "stroke-dasharray" not in rate
    assert "<table class=\"sheet dk-tbl\">" in res["table"] and "−3.4" in res["table"]
    assert res["num"] == [-1026, 731, None]


def test_fx_rows_are_lines_and_money_stays_bars() -> None:
    cols = [{"period": "FY24", "kind": "actual"}, {"period": "FY25", "kind": "actual"},
            {"period": "FY26 Q1", "kind": "forecast"}, {"period": "FY26 通期", "kind": "plan"}]

    def row(metric: str, unit: str, values: list[str]) -> dict:
        return {"metric": metric, "segment": None, "unit": unit, "of": None, "values": values}

    charts = _deck({"cols": cols, "rows": [
        row("売上高", "10億円", ["100", "110", "120", "230"]),
        row("営業利益", "10億円", ["10", "12", "14", "25"]),
        row("基本的一株当たり利益", "円", ["500.5", "510.5", "520.5", "530.5"]),
        row("為替レート/米ドル", "円", ["150", "152", "159", "161"]),
        row("為替レート/ユーロ", "円", ["170", "176", "185", "188"]),
    ]})["charts"]

    def figs(name: str) -> list[str]:
        return [h for h in charts if name in _caption(h)]

    bars = figs("売上高") + figs("営業利益") + figs("一株当たり")
    assert bars
    for html in bars:
        assert "<circle" not in html and "stroke-dasharray" not in html
        assert "斜線は予想・計画" in html
    fx = figs("為替レート")
    assert len(fx) == 1 and "米ドル" in _caption(fx[0]) and "ユーロ" in _caption(fx[0])
    html = fx[0]
    assert "<circle" in html and "dk-sw line" in html
    assert "破線・白抜きは予想・計画" in html and "斜線は予想・計画" not in html
    paths = re.findall(r"<path\b[^>]*>", html)
    assert any("stroke-dasharray" in p for p in paths)
    assert any("stroke=" in p and "stroke-dasharray" not in p for p in paths)
    circles = re.findall(r"<circle\b[^>]*>", html)
    assert len(circles) == 8
    assert any('fill="#fff"' in c for c in circles) and any('fill="#fff"' not in c for c in circles)
    labels = re.findall(r'class="val"[^>]*>([^<]+)<', html)
    for v in ("150", "152", "159", "161", "170", "176", "185", "188"):
        assert v in labels
