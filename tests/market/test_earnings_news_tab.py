"""決算速報の決算表の枠に 業績 / ニュース のタブ（KAB-392）。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "static/index.html").read_text(encoding="utf-8")
JS = (ROOT / "static/js/earnings.js").read_text(encoding="utf-8")


def test_news_is_a_tab_of_the_fin_panel() -> None:
    i = HTML.index('<div class="a-fin">')
    fin = HTML[i:HTML.index('<div class="panel a-chart">', i)]
    assert fin.index('id="earn-tabs"') < fin.index('<div class="panel">') < fin.index('id="fin-box"')
    assert re.findall(r'data-etab="(\w+)"', fin) == ["fin", "news"]
    assert 'class="nav-tabs" id="earn-tabs"' in fin
    assert re.findall(r'data-fin="(\w+)"', fin) == ["cum", "q", "prog", "rev"]
    assert fin.index('id="fin-box"') < fin.index('id="cum-tbl"') < fin.index('id="news-wrap" hidden')
    assert 'id="en-news" class="sheet"' in fin
    for gone in ("earn-sheet", "earn-news", "xbtn", "toggleExp", "keepRow"):
        assert gone not in HTML and gone not in JS


def test_news_loads_only_while_its_tab_is_open() -> None:
    want = re.search(r"function wantNews\(code\)\{.*?\n\}", JS, re.S).group(0)
    assert '!$("#news-wrap").hidden' in want
    assert "if(news && newsWant) loadNews(newsWant)" in JS
    assert JS.count("wantNews(row.code)") == 2


def test_news_reuses_stock_tab_rows_frames_and_deck() -> None:
    for name in ("stockNewsRows(", "mountFilingFrames(", "deckDocHtml(", "fitStockFrame"):
        assert name in JS
    assert "function mountFilingFrames(host, src, f)" in (ROOT / "static/js/stock.js").read_text(encoding="utf-8")


def test_deck_doc_html_before_extract_shows_status() -> None:
    script = (
        "const m = require(process.argv[1]);"
        "process.stdout.write(m.deckDocHtml({document:{title:'説明<資料>', pubdate:'2026-10-14 15:30:00',"
        " url:'', status:'submitted'}, extract:null, pages:[]}, 400));"
    )
    out = subprocess.run(["node", "-e", script, str(ROOT / "static/js/stock-deck.js")],
                         capture_output=True, text=True, check=True).stdout
    assert "説明&lt;資料&gt;" in out
    assert "読み取り中。" in out
    assert "原本の PDF" not in out
