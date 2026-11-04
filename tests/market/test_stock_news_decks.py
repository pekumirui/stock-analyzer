"""ニュース一覧に決算説明資料を混ぜる（KAB-352）。

資料がある銘柄で種類が決算の行が出て、説明資料の中身を開く。短信は今までどおり。
設定の中身（出所の優先順など）は期待値に書かない。画面は filings と decks の応答をそのまま並べる。
"""
from __future__ import annotations

import gzip
import json
import subprocess
from datetime import date, datetime
from pathlib import Path

from app.db import SessionLocal
from app.models import BriefingDocument, Stock, TdnetPdfDocument, XbrlDocument, XbrlFiling

ROOT = Path(__file__).resolve().parents[2]
CODE = "68610"
EMPTY = "10000"
DOC = "140120261005512345"
UNSAVED = "140120260101500099"
PDF_NO = "20260915000001"

_NODE = r"""
const payload = JSON.parse(process.argv[2]);
function makeEl(){
  return {
    hidden: true, innerHTML: "", textContent: "", value: "", style: {}, dataset: {},
    clientWidth: 800, className: "",
    classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }},
    listeners: {},
    addEventListener(type, fn){ this.listeners[type] = fn; },
    setAttribute(){}, append(){}, appendChild(){},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
  };
}
const cache = new Map();
function $(sel){
  if(!cache.has(sel)) cache.set(sel, makeEl());
  return cache.get(sel);
}
global.$ = $;
global.document = {
  querySelector: $,
  querySelectorAll(){ return []; },
  addEventListener(){},
  getElementById(id){ return $("#" + id); },
  createElement(){ return makeEl(); },
};
global.window = {addEventListener(){}, innerWidth: 1200, requestAnimationFrame(fn){ fn(); }};
global.localStorage = {getItem(){ return null; }, setItem(){}};
global.stockDeckKey = "";
global.__routes = [];
global.__urls = [];
global.__deckShown = 0;
global.resetStockDecks = () => { global.stockDeckKey = ""; global.__deckShown = 0; };
global.renderStockDecks = () => { global.__deckShown += 1; };
global.writeRoute = (tab, path, from, push) => { global.__routes.push({tab, path, from, push}); };
global.api = (url) => {
  const u = String(url);
  global.__urls.push(u);
  const hit = payload.cases.find(c => u.includes("/api/stock/" + c.code + "/"));
  if(!hit) return Promise.reject(Object.assign(new Error("404"), {message: "404"}));
  if(u.includes("/overview")){
    return Promise.resolve({code: hit.code, name: hit.name, market: null, sector33: null,
      quote: null, next_earnings: null, metrics: {}});
  }
  if(u.includes("/filings")) return Promise.resolve({filings: hit.filings});
  if(u.includes("/decks")) return Promise.resolve({decks: hit.decks});
  return Promise.reject(Object.assign(new Error("404"), {message: "404"}));
};
const stock = require(process.argv[1]);

function parseRows(html){
  return html.split("<tr").slice(1).map(p => {
    const attr = name => {
      const m = new RegExp("data-" + name + '="([^"]*)"').exec(p);
      return m ? m[1] : "";
    };
    const kind = (p.match(/<span class="kind">([^<]*)<\/span>/) || [])[1] || "";
    const title = (p.match(/<td class="l t">([^<]*)<\/td>/) || [])[1] || "";
    const at = (p.match(/<td class="l">([^<]*)<\/td>/) || [])[1] || "";
    return {go: p.includes('class="go"'), id: attr("id"), open: attr("open"), deck: attr("deck"), kind, title, at};
  }).filter(r => r.title || r.id);
}
function click(row){
  const tr = {dataset: {id: row.id, open: row.open, deck: row.deck}, closest(){ return this; }};
  $("#st-news").listeners.click({target: {closest(sel){ return sel === "tr.go" ? tr : null; }}});
}
async function flush(){
  for(let i = 0; i < 8; i++) await new Promise(r => setImmediate(r));
}
async function openList(code){
  await stock.showStock(code, "news", "");
  await flush();
}
function lastRoute(){
  return global.__routes[global.__routes.length - 1] || null;
}
(async () => {
  const main = payload.cases[0];
  const empty = payload.cases[1];
  await openList(main.code);
  const html = $("#st-news").innerHTML;
  const rows = parseRows(html);
  const urls = global.__urls.slice();
  global.__routes = [];
  click(rows.find(r => r.id.startsWith("p")));
  await flush();
  const pdfRoute = lastRoute();
  await openList(main.code);
  global.__routes = [];
  click(parseRows($("#st-news").innerHTML).find(r => r.id.startsWith("x")));
  await flush();
  const xRoute = lastRoute();
  await openList(main.code);
  global.__routes = [];
  click(parseRows($("#st-news").innerHTML).find(r => r.open === "deck"));
  const deckRoute = lastRoute();
  const openedKey = global.stockDeckKey;
  const deckShown = global.__deckShown;
  await openList(empty.code);
  process.stdout.write(JSON.stringify({
    urls, html, rows,
    logical: stock.stockNewsRows(main.filings, main.decks),
    pdfRoute, xRoute, deckRoute, openedKey, deckShown,
    emptyHtml: $("#st-news").innerHTML,
    emptyMsg: $("#st-msg").textContent,
    emptyHidden: $("#st-msg").hidden,
  }));
})().catch(e => { console.error(e); process.exit(1); });
"""


def _shown_at(raw: str | None) -> str:
    text = (raw or "").strip().replace("T", " ")
    if len(text) >= 16 and text[10] == " ":
        return text[:16]
    return text[:10]


def _seed() -> None:
    db = SessionLocal()
    now = datetime(2026, 11, 3, 12, 0)  # noqa: DTZ001
    db.add(Stock(code=CODE, name="説明資料ニュース"))
    db.add(Stock(code=EMPTY, name="空の銘柄"))
    quarterly = XbrlFiling(
        code=CODE, source="tdnet", doc_id="doc-q", doc_type="quarterly", period_type="Q2",
        submitted_at=datetime(2026, 11, 2, 15, 30), title="第2四半期決算短信", raw_path="q.zip",  # noqa: DTZ001
        parse_status="parsed", created_at=now,
    )
    revision = XbrlFiling(
        code=CODE, source="tdnet", doc_id="doc-rev", doc_type="forecast_revision",
        submitted_at=datetime(2026, 8, 1, 10, 0), title="業績予想の修正", raw_path="r.zip",  # noqa: DTZ001
        parse_status="parsed", created_at=now,
    )
    db.add_all([quarterly, revision])
    db.flush()
    db.add(XbrlDocument(
        filing_id=quarterly.id, seq=0, part="summary", file_name="s.htm",
        html_gz=gzip.compress(b"<p>q</p>"), bytes=8, sha256="0" * 64, created_at=now,
    ))
    db.add(TdnetPdfDocument(
        disclosure_no=PDF_NO, code=CODE, disclosed_date=date(2026, 9, 15), disclosed_time="16:00:00",
        title="2026年3月期決算短信", status="ok", html_gz=gzip.compress(b"<p>pdf</p>"),
        bytes=10, sha256="ab" * 32, pages=1, created_at=now,
    ))
    for src in ("tdnet", "irbank"):
        db.add(BriefingDocument(
            source=src, document_id=DOC, code=CODE, company_name="説明資料ニュース",
            pubdate="2026-10-05 15:00:00", title="決算説明資料", rel_path=f"{CODE}/{DOC}.pdf",
            url=f"https://example.com/{src}/{DOC}.pdf", sha256="x", size=1, status="saved", saved_at=now,
        ))
    db.add(BriefingDocument(
        source="homepage", document_id=UNSAVED, code=CODE, company_name="説明資料ニュース",
        pubdate="2026-01-01 09:00:00", title="未保存の説明資料", rel_path=f"{CODE}/{UNSAVED}.pdf",
        url="https://example.com/no.pdf", sha256="y", size=1, status="skipped", saved_at=now,
    ))
    db.commit()
    db.close()


def _render(cases: list[dict]) -> dict:
    proc = subprocess.run(
        ["node", "-e", _NODE, str(ROOT / "static/js/stock.js"), json.dumps({"cases": cases})],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)
    return json.loads(proc.stdout)


def test_news_lists_saved_deck_and_keeps_filings(c) -> None:
    _seed()
    filings = c.get(f"/api/stock/{CODE}/filings").json()["filings"]
    decks = c.get(f"/api/stock/{CODE}/decks").json()["decks"]
    empty_filings = c.get(f"/api/stock/{EMPTY}/filings").json()["filings"]
    empty_decks = c.get(f"/api/stock/{EMPTY}/decks").json()["decks"]
    assert empty_filings == [] and empty_decks == []
    # 保存済みは出所が複数でも 1 行。保存していない資料は一覧に出ない
    assert len(decks) == 1
    assert decks[0]["title"] == "決算説明資料"
    assert "未保存の説明資料" not in {d["title"] for d in decks}
    titles = {f["title"] for f in filings}
    assert {"第2四半期決算短信", "業績予想の修正", "2026年3月期決算短信"} <= titles
    xbrl = next(f for f in filings if f["source"] == "xbrl" and f["documents"])
    pdf = next(f for f in filings if f["source"] == "pdf")
    revision = next(f for f in filings if not f["documents"])
    assert xbrl["documents"][0]["url"].startswith("/api/stock/filings/")
    assert pdf["disclosure_no"] == PDF_NO
    assert pdf["documents"][0]["url"].startswith("/api/stock/pdf/")

    out = _render([
        {"code": CODE, "name": "説明資料ニュース", "filings": filings, "decks": decks},
        {"code": EMPTY, "name": "空の銘柄", "filings": empty_filings, "decks": empty_decks},
    ])
    assert any(f"/api/stock/{CODE}/filings" in u for u in out["urls"])
    assert any(f"/api/stock/{CODE}/decks" in u for u in out["urls"])

    logical = out["logical"]
    assert [r["at"] for r in logical] == sorted((r["at"] for r in logical), reverse=True)
    by_key = {r["key"]: r for r in logical}
    deck_key = "d" + decks[0]["key"]
    assert by_key[deck_key]["kind"] == "決算"
    assert by_key[deck_key]["open"] == "deck"
    assert by_key[deck_key]["deckKey"] == decks[0]["key"]
    assert by_key[deck_key]["title"] == decks[0]["title"]
    assert by_key[deck_key]["at"] == _shown_at(decks[0]["pubdate"])
    assert by_key[f"x{xbrl['id']}"]["kind"] == "決算"
    assert by_key[f"x{xbrl['id']}"]["open"] == "news"
    assert by_key[f"x{xbrl['id']}"]["title"] == xbrl["title"]
    assert by_key[f"x{xbrl['id']}"]["at"] == _shown_at(xbrl["submitted_at"])
    assert by_key[f"x{revision['id']}"]["kind"] == "修正"
    assert by_key[f"x{revision['id']}"]["open"] == ""
    assert by_key[f"x{revision['id']}"]["title"] == revision["title"]
    assert by_key[f"p{pdf['disclosure_no']}"]["open"] == "news"
    assert by_key[f"p{pdf['disclosure_no']}"]["title"] == f"{pdf['title']}（PDF から変換）"
    assert deck_key not in {f"x{xbrl['id']}", f"x{revision['id']}", f"p{pdf['disclosure_no']}"}
    assert "未保存の説明資料" not in out["html"]

    assert len(out["rows"]) == len(logical)
    for shown, row in zip(out["rows"], logical, strict=True):
        assert shown["kind"] == row["kind"]
        assert shown["title"] == row["title"]
        assert shown["at"] == (row["at"] or "")[2:].replace("-", "/")
        if row["open"]:
            assert shown["go"] and shown["id"] == row["key"]
        else:
            assert not shown["go"] and shown["id"] == ""
        if row["open"] == "deck":
            assert shown["open"] == "deck" and shown["deck"] == row["deckKey"]
        else:
            assert shown["open"] == ""

    assert out["pdfRoute"]["path"] == f"{CODE}/news/p{pdf['disclosure_no']}"
    assert out["pdfRoute"]["push"] is True
    assert out["xRoute"]["path"] == f"{CODE}/news/x{xbrl['id']}"
    assert out["xRoute"]["push"] is True
    assert out["deckRoute"]["path"] == f"{CODE}/decks/{decks[0]['key']}"
    assert out["deckRoute"]["push"] is True
    assert out["openedKey"] == decks[0]["key"]
    assert out["deckShown"] == 1
    assert out["emptyHtml"] == ""
    assert out["emptyMsg"] == "ニュースがありません。"
    assert out["emptyHidden"] is False
