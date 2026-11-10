"""MCP の決算ツール: 画面の API を短い TSV にする・読み取りだけ・loopback だけ。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp"))

os.environ.setdefault("MCP_TOKEN", "test-token-for-ci-only")

import earnings_tools as et  # noqa: E402
import log_mcp as m  # noqa: E402

_G = lambda *v: {k: {"pct": p, "state": "up"} for k, p in zip(("net_sales", "operating_profit", "ordinary_profit", "profit"), v)}  # noqa: E731

ACTUAL = {
    "restated": False, "forecast_revision": {"direction": "up", "pct": 12.5},
    "disclosure_no": "20260806511357", "code": "26740", "name": "ハードオフ", "disclosed_time": "14:00:00",
    "period": "1Q", "fiscal_year_end": "2027-03-31", "fy_label": "FY27.3",
    "qoq": _G(1.6, 9.4, 10.8, 97.6), "yoy": _G(30.0, 34.4, 26.6, 167.6),
    "single": {"net_sales": 11251e6, "operating_profit": 1058e6, "ordinary_profit": 1061e6, "profit": 1456e6},
    "cumulative": {"net_sales": 11251e6, "operating_profit": 1058e6, "ordinary_profit": 1061e6, "profit": 1456e6},
    "forecast_fy": {"net_sales": 45700e6, "operating_profit": 4050e6, "eps": 237.19},
    "progress": {"cur": {"operating_profit": 26.1}, "prev": {"operating_profit": 23.2}},
    "sector33": "小売業", "pts": {"price": 2000, "chg_pct": 8.2, "base_close": 1848, "as_of": "23:45"},
}
ACTUAL2 = {**ACTUAL, "code": "72030", "name": "トヨタ", "disclosure_no": "X2", "sector33": "輸送用機器",
           "period": "FY", "forecast_revision": None, "restated": True, "disclosed_time": "13:25:00",
           "pts": {"price": 1, "chg_pct": -3.0, "base_close": 1, "as_of": "23:45"}}
ACTUAL3 = {**ACTUAL, "code": "99840", "name": "SBG", "disclosure_no": "X3", "sector33": "情報･通信業",
           "pts": None, "disclosed_time": "15:30:00"}
SCHED = {"code": "67580", "name": "ソニー", "sector33": "電気機器", "periods": [{"fq": "2Q", "fye": "0331", "label": "FY27.3"}],
         "latest": {"fiscal_year_end": "2027-03-31", "period": "1Q", "yoy": _G(1, 2), "qoq": _G(3, -4)},
         "pts": None}

DETAIL = {
    "code": "26740", "name": "ハードオフ", "disclosure_no": "20260806511357", "disclosed_date": "2026-08-06",
    "disclosed_time": "14:00:00", "label": "FY27.3_1Q", "notes": {"no_ordinary": True, "single_unavailable": False},
    "revision_source": "jquants",
    "cumulative": [{"range": "27.3", "period": "1Q", "kind": "actual_current", "disclosed_date": "2026-08-06",
                    "values": {"net_sales": 11251e6, "eps": 104.71}, "growth": {"net_sales": {"pct": 30.0}}}],
    "quarterly": [], "revisions": [],
    "progress": [{"range": "27.3", "period": "1Q", "kind": "actual_current", "disclosed_date": "2026-08-06",
                  "values": {"operating_profit": 1058e6}, "base": {"operating_profit": 4050e6},
                  "progress": {"operating_profit": 26.1}}],
}
FILINGS = {"name": "ハードオフ", "sector33": "小売業", "market": "プライム", "filings": [
    {"id": 7, "source": "xbrl", "submitted_at": "2026-08-06 14:00", "title": "決算短信", "doc_type": "決算短信",
     "is_amendment": False, "documents": [{"seq": 1, "part": "qualitative"}]},
    {"id": None, "source": "pdf", "disclosure_no": "20240101", "submitted_at": "2024-01-01 15:00", "title": "古い短信",
     "doc_type": "決算短信", "is_amendment": False, "documents": [{"seq": 0, "part": "pdf", "pages": 12}]},
]}
DECKS = {"decks": [{"key": "tdnet-abc", "pubdate": "2026-08-06", "title": "説明資料", "status": "done", "pages": 2}]}
DECK = {"document": {"pubdate": "2026-08-06", "title": "説明資料", "status": "done"}, "pages": [
    {"page": 3, "kind": "chart_or_table", "title": "業績", "summary": "増収増益。", "unverified": 1, "mismatch": 0,
     "tables": [{"cols": [{"period": "FY25", "kind": "actual"}, {"period": "FY26", "kind": "forecast"}],
                 "rows": [{"metric": "売上高", "segment": "国内", "unit": "億円", "of": "売上高", "values": ["10", None]}]}]}]}
QUAL_HTML = ("<html><head><style>p{}</style><script>x()</script></head><body><h2>1．経営成績の概況</h2>"
             "<p>当第1四半期の売上高は&nbsp;112億円（前年同期比30.0％増）。</p>"
             "<table><tr><td>売上高</td><td>11,251</td></tr></table></body></html>")
OVERVIEW = {"code": "26740", "name": "ハードオフ", "market": "プライム", "sector33": "小売業",
            "quote": {"date": "2026-10-16", "close": 2000.0, "change_pct": 0.01, "market_cap": 28000.0},
            "next_earnings": {"date": "2026-11-10", "fq": "2Q"},
            "metrics": {"per": 8.4, "pbr": 1.5, "dividend_yield": 0.03, "roe": 0.12, "equity_ratio": 0.6}}
DAYS = [f"2025-{m_:02d}-{d:02d}" for m_ in range(1, 13) for d in (5, 12, 19, 26)] * 6
CHART = {"t": sorted(set(DAYS)) + [f"2026-10-{d:02d}" for d in range(1, 17)],
         "c": [100.0 + i for i in range(48 + 16)], "v": [1000] * 64}
PERF = [{"disclosed_date": "2026-08-06", "return_1d": 5.0, "alpha_1d": 4.0}]
TOPIX = {"t": ["2026-10-14", "2026-10-15", "2026-10-16"], "c": [100.0, 98.0, 99.0]}


@contextmanager
def _app_stub():
    seen: list[str] = []

    class H(BaseHTTPRequestHandler):
        def log_message(self, *_a):  # noqa: D401
            pass

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            seen.append(self.path)
            q = parse_qs(u.query)
            routes = {
                "/api/earnings": {"kind": "mixed", "actuals": [ACTUAL, ACTUAL2, ACTUAL3], "scheduled": [SCHED]},
                "/api/earnings/detail/26740": DETAIL,
                "/api/stock/26740/filings": FILINGS,
                "/api/stock/26740/decks": DECKS,
                "/api/stock/26740/decks/tdnet-abc": DECK,
                "/api/stock/26740/overview": OVERVIEW,
                "/api/chart/26740": CHART,
                "/api/earnings-performance/26740": PERF,
                "/api/topix": TOPIX,
            }
            if u.path == "/api/stock/filings/7/documents/1":
                body, ctype = QUAL_HTML.encode(), "text/html"
            elif u.path in routes:
                if u.path == "/api/earnings" and "date" not in q:
                    self.send_response(400)
                    self.end_headers()
                    return
                body, ctype = json.dumps(routes[u.path], ensure_ascii=False).encode(), "application/json"
            else:
                self.send_response(404)
                self.end_headers()
                self.wfile.write(b'{"error":"nf"}')
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        yield srv.server_address[1], seen
    finally:
        srv.shutdown()


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    db = tmp_path / "stock.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE daily_prices (code TEXT, date TEXT, close REAL, market_cap REAL)")
    conn.execute("CREATE TABLE earnings_events (code TEXT, disclosed_date TEXT, return_1d REAL, alpha_1d REAL, "
                 "return_5d REAL, alpha_5d REAL)")
    conn.executemany("INSERT INTO daily_prices VALUES (?,?,?,?)", [
        ("26740", "2026-08-05", 1800.0, 25000.0), ("26740", "2026-08-06", 1848.0, 26000.0),
        ("26740", "2026-08-07", 2100.0, 30000.0), ("72030", "2026-08-06", None, None),
        ("72030", "2026-08-04", 2600.0, 4200000.0),
    ])
    conn.execute("INSERT INTO earnings_events VALUES ('26740','2026-08-06',9.1,8.0,10.0,9.0)")
    conn.execute("CREATE TABLE statements (disclosure_no TEXT PRIMARY KEY, raw_json TEXT)")
    conn.executemany("INSERT INTO statements VALUES (?,?)", [
        ("X2", '{"NxFEPS": "260.0"}'), ("X3", '{"NxFEPS": ""}'), ("X4", "not json")])
    conn.commit()
    conn.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    with _app_stub() as (port, seen):
        monkeypatch.setenv("MCP_APP_HTTP", f"http://127.0.0.1:{port}")
        yield seen


def _call(name, args):
    text, err = m.call_tool(name, args)
    assert not err, text
    return text


def _table(text: str, section: str) -> list[dict[str, str]]:
    block = text.split(f"## {section}\n", 1)[1].split("\n\n", 1)[0].splitlines()
    head = block[0].split("\t")
    return [dict(zip(head, ln.split("\t"))) for ln in block[1:]]


def test_tools_are_listed():
    names = {t["name"] for t in m.tools_list()}
    assert {"earnings_day", "earnings_detail", "earnings_docs", "earnings_doc_read",
            "stock_overview", "market_context"} <= names


def test_earnings_day_rows(app_env):
    text = _call("earnings_day", {"date": "2026-08-06", "today": "2026-08-06"})
    assert "開示=3件 未発表=1社 PTSあり=2" in text
    assert app_env[0].startswith("/api/earnings?date=2026-08-06&today=2026-08-06")
    rows = _table(text, "開示")
    assert [r["code"] for r in rows] == ["2674", "7203", "9984"]
    r = rows[0]
    assert r["通期営利予想修正"] == "上方+12.5"
    assert r["PTS%"] == "+8.2"
    assert r["終値"] == "1848" and r["時価総額億"] == "260"
    assert r["予想PER"] == "7.8"
    assert r["売上累計"] == "11,251" and r["営利YoY%"] == "+34.4" and r["純利QoQ%"] == "+97.6"
    assert r["営利進捗%"] == "26.1" and r["前年同期進捗%"] == "23.2"
    assert r["翌日騰落%"] == "+9.1" and r["翌日対TOPIX%"] == "+8.0"
    assert r["disclosure_no"] == "20260806511357"
    assert rows[1]["Q"] == "4Q" and rows[1]["訂正"] == "訂正"
    assert rows[1]["終値"] == "2600" and rows[1]["予想PER"] == "10.0"
    sched = _table(text, "未発表")
    assert sched[0]["code"] == "6758" and sched[0]["予定の期"] == "27.32Q" and sched[0]["直近営利QoQ%"] == "-4.0"


def test_earnings_day_sort_filter_page(app_env):
    text = _call("earnings_day", {"date": "2026-08-06", "sort": "pts_down"})
    assert [r["code"] for r in _table(text, "開示")] == ["7203", "2674", "9984"]
    text = _call("earnings_day", {"date": "2026-08-06", "sort": "pts_abs"})
    assert [r["code"] for r in _table(text, "開示")] == ["2674", "7203", "9984"]
    text = _call("earnings_day", {"date": "2026-08-06", "quarters": ["4Q"]})
    assert [r["code"] for r in _table(text, "開示")] == ["7203"]
    assert "## 未発表" not in text
    text = _call("earnings_day", {"date": "2026-08-06", "sectors": ["電気機器"]})
    assert "## 開示" not in text and "6758" in text
    text = _call("earnings_day", {"date": "2026-08-06", "codes": ["9984"]})
    assert [r["code"] for r in _table(text, "開示")] == ["9984"]
    text = _call("earnings_day", {"date": "2026-08-06", "limit": 2, "offset": 2})
    assert [r["code"] for r in _table(text, "開示")] == ["9984"]
    assert [r["code"] for r in _table(text, "未発表")] == ["6758"]
    assert "3〜4 行目" in text
    text = _call("earnings_day", {"date": "2026-08-06", "limit": 1})
    assert "続きは offset=1" in text


@pytest.mark.parametrize("args,needle", [
    ({"date": "2026/08/06"}, "YYYY-MM-DD"),
    ({"date": "2026-02-30"}, "valid date"),
    ({"date": "2026-08-06", "sort": "score"}, "sort must be"),
    ({"date": "2026-08-06", "quarters": ["FY"]}, "quarters"),
    ({"date": "2026-08-06", "limit": 0}, "limit"),
])
def test_earnings_day_rejects(app_env, args, needle):
    text, err = m.call_tool("earnings_day", args)
    assert err and needle in text


def test_earnings_detail(app_env):
    text = _call("earnings_detail", {"code": "2674", "disclosure_no": "20260806511357"})
    assert "/api/earnings/detail/26740?disclosure_no=20260806511357" in app_env
    assert "経常利益なし" in text
    cum = _table(text, "累計")
    assert cum[0]["売上"] == "11,251" and cum[0]["売上増減%"] == "+30.0" and cum[0]["EPS"] == "104.71"
    prog = _table(text, "進捗率（累計 ÷ 分母。分母は過去の年度が通期実績、actual_current が当期の通期予想）")
    assert prog[0]["営利進捗%"] == "26.1" and prog[0]["営利分母"] == "4,050"
    text, err = m.call_tool("earnings_detail", {"code": "2674", "disclosure_no": "../x"})
    assert err
    text, err = m.call_tool("earnings_detail", {"code": "7203/../x"})
    assert err


def test_earnings_docs_and_read(app_env):
    text = _call("earnings_docs", {"code": "2674"})
    docs = _table(text, "短信・開示")
    assert docs[0]["kind"] == "filing" and docs[0]["id"] == "7" and docs[0]["seq/部分"] == "1 qualitative"
    assert docs[1]["kind"] == "pdf" and docs[1]["id"] == "20240101"
    assert "tdnet-abc" in text

    text = _call("earnings_doc_read", {"code": "2674", "kind": "filing", "id": "7", "seq": 1})
    assert "1．経営成績の概況" in text and "は 112億円" in text and "売上高\t11,251" in text
    assert "x()" not in text and "p{}" not in text
    assert "終わり" in text

    text = _call("earnings_doc_read", {"code": "2674", "kind": "filing", "id": "7", "seq": 1,
                                       "offset": 2, "length": 1000})
    assert text.startswith("経営成績")

    text = _call("earnings_doc_read", {"code": "2674", "kind": "deck", "id": "tdnet-abc"})
    assert "### p3 [chart_or_table] 業績" in text and "増収増益。" in text
    assert "売上高（売上高 の内訳）\t国内\t億円\t10\t" in text
    assert "未検証 1" in text

    for bad in ({"kind": "filing", "id": "x"}, {"kind": "deck", "id": "../x"}, {"kind": "pdf", "id": "a/b"},
                {"kind": "html", "id": "1"}):
        text, err = m.call_tool("earnings_doc_read", {"code": "2674", **bad})
        assert err, bad


def test_stock_overview(app_env):
    text = _call("stock_overview", {"code": "2674", "today": "2026-10-16"})
    assert "時価総額 280億円" in text
    assert "予想PER 8.4 / PBR 1.50 / 予想配当利回り 3.00% / ROE 12.0% / 自己資本比率 60.0%" in text
    assert "次の決算予定 2026-11-10 2Q" in text
    assert "株価（調整後。2026-10-16 時点）" in text
    assert "2026-08-06\t+5.0\t+4.0" in text


def test_market_context(app_env):
    text = _call("market_context", {"date": "2026-10-18", "days": 2})
    assert "TOPIX 2026-10-16 終値 99.0" in text
    assert "休場か未取込" in text
    rows = _table(text, "日足（終値）")
    assert [r["日付"] for r in rows] == ["2026-10-15", "2026-10-16"]
    assert rows[0]["前日比%"] == "-2.0"


def test_app_get_stays_on_loopback_paths(monkeypatch):
    with pytest.raises(ValueError):
        et.app_get("http://127.0.0.1:1", "/etc/passwd", None, 1)
    with pytest.raises(ValueError):
        et.app_get("http://127.0.0.1:1", "/api/../etc", None, 1)
    monkeypatch.setenv("MCP_APP_HTTP", "http://example.com")
    text, err = m.call_tool("market_context", {})
    assert err and "loopback" in text
