"""銘柄検索は社名・EDINET の提出者名（ヨミ）・（英字）のどれかで当たる（KAB-448）。"""
from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from app.db import SessionLocal
from app.edinet.codes import yomi_key
from app.models import EdinetFiler, Stock

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 12, 7, 30)  # noqa: DTZ001
FILERS = [
    ("21210", "ＭＩＸＩ", "E05729", "MIXI,Inc.", "カブシキガイシャミクシィ"),
    ("278A0", "Ｔｅｒｒａ　Ｄｒｏｎｅ", "E39011", "Terra Drone Corporation", "テラドローンカブシキガイシャ"),
    ("24320", "ディー・エヌ・エー", "E05460", "DeNA Co.,Ltd.", "カブシキガイシャディー・エヌ・エー"),
    ("72030", "トヨタ自動車", "E02144", "TOYOTA MOTOR CORPORATION", "トヨタジドウシャカブシキカイシャ"),
    ("54470", "ジェイマックス", "E99999", "JMAX Co.,Ltd.", "カブシキガイシャ　ジェイマックス"),
]


def _seed() -> None:
    db = SessionLocal()
    try:
        for code, name, ec, en, yomi in FILERS:
            db.add(Stock(code=code, name=name, market="プライム", product_category="011", edinet_code=ec))
            db.add(EdinetFiler(edinet_code=ec, sec_code=code, filer_name=name, filer_name_en=en,
                               filer_name_yomi=yomi, updated_at=NOW))
        db.add(Stock(code="13060", name="ＮＥＸＴ　ＦＵＮＤＳ　ＴＯＰＩＸ連動型上場投信", market="ETF",
                     product_category="014"))
        db.add(Stock(code="99980", name="ヨミナシ商事", market="スタンダード", product_category="011"))
        db.commit()
    finally:
        db.close()


def _hits(c, q: str) -> list[str]:
    return [h["code"] for h in c.get("/api/stocks/search", params={"q": q}).json()]


def test_yomi_key() -> None:
    assert yomi_key("カブシキガイシャミクシィ") == "ミクシィ"
    assert yomi_key("テラドローンカブシキガイシャ") == "テラドローン"
    assert yomi_key("カブシキガイシャ　ジェイマックス") == "ジェイマックス"
    assert yomi_key("トヨタジドウシャカブシキカイシャ") == "トヨタジドウシャ"
    assert yomi_key(None) == ""


def test_search_by_yomi_and_english(c) -> None:
    _seed()
    assert _hits(c, "みくしぃ") == ["21210"]
    assert _hits(c, "ミクシィ") == ["21210"]
    assert _hits(c, "ﾐｸｼｨ") == ["21210"]
    assert _hits(c, "テラドローン") == ["278A0"]
    assert _hits(c, "てら どろーん") == ["278A0"]
    assert _hits(c, "dena") == ["24320"]
    assert _hits(c, "DENA") == ["24320"]
    assert _hits(c, "ｄｅｎａ") == ["24320"]
    assert _hits(c, "terra drone") == ["278A0"]
    assert _hits(c, "じぇいまっくす") == ["54470"]
    assert _hits(c, "とよたじどうしゃ") == ["72030"]
    assert _hits(c, "かぶしき") == []
    assert _hits(c, "カブシキガイシャ") == []
    assert _hits(c, "かぶしきがいしゃみくしぃ") == ["21210"]

    assert _hits(c, "トヨタ") == ["72030"]
    assert _hits(c, "7203") == ["72030"]
    assert _hits(c, "２７８Ａ") == ["278A0"]
    assert _hits(c, "ヨミナシ") == ["99980"]
    assert _hits(c, "上場投信") == []


def test_monthly_suggest_matches_yomi_and_english() -> None:
    rows = [{"code": code, "name": name, "name_en": en, "name_yomi": yomi_key(yomi),
             "industry": "その他", "sector33": "情報・通信業"}
            for code, name, _ec, en, yomi in FILERS]
    rows.append({"code": "99980", "name": "ヨミナシ商事", "name_en": None, "name_yomi": None,
                 "industry": "その他", "sector33": "卸売業"})
    queries = ["みくしぃ", "テラドローン", "dena", "ＤｅＮＡ", "じぇい まっくす", "かぶしき",
               "カブシキガイシャ", "トヨタ", "2121", "ヨミナシ", "情報"]
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const blank = () => ({innerHTML: "", value: "", textContent: "", style: {}, classList: {toggle() {}}, dataset: {},
  addEventListener() {}});
const sandbox = {console, $: blank, fmtMarket: () => ({cls: "", label: ""}), fmtSector: () => "", window: {addEventListener() {}, matchMedia: () => ({matches: false, addEventListener() {}})},
  document: {body: {dataset: {}}, querySelector: () => null, querySelectorAll: () => [], addEventListener() {}}};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[process.argv.length - 1], "utf8"), sandbox);
const rows = """ + json.dumps(rows, ensure_ascii=False) + r""";
const queries = """ + json.dumps(queries, ensure_ascii=False) + r""";
const out = {};
for(const q of queries) out[q] = rows.filter(r => sandbox.matchMonthlyIssuerQuery(r, q)).map(r => r.code);
console.log(JSON.stringify(out));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write(harness)
        harness_path = tmp.name
    try:
        proc = subprocess.run(["node", harness_path, str(ROOT / "static" / "js" / "monthly.js")],
                              capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), check=False)
    finally:
        Path(harness_path).unlink(missing_ok=True)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    assert got == {
        "みくしぃ": ["21210"],
        "テラドローン": ["278A0"],
        "dena": ["24320"],
        "ＤｅＮＡ": ["24320"],
        "じぇい まっくす": ["54470"],
        "かぶしき": [],
        "カブシキガイシャ": [],
        "トヨタ": ["72030"],
        "2121": ["21210"],
        "ヨミナシ": ["99980"],
        "情報": ["21210", "278A0", "24320", "72030", "54470"],
    }
