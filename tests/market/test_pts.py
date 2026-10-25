"""PTS ランキング。株探の表の読み取り、セッション日、保存の重複除け、/api/pts の翌始値ギャップ。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-pts-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db  # noqa: E402
from app.market.pts import (  # noqa: E402
    JST,
    page_as_of, page_base_day, parse_page, session_date_for, snapshot_all,
)
from app.models import DailyPrice, PtsSnapshot  # noqa: E402

# 株探の表の形（2026-10 時点）。先頭は指数の帯、コード列で始まらない
PAGE = """
<ul><li>06:02現在</li></ul>
<table><tr><th colspan="2"><div><a href="/stock/?code=0000">日経平均</a><time>15:30</time></div></th></tr></table>
<table class="stock_table">
<tr><th rowspan="2">コード</th><th rowspan="2">銘柄名</th><th rowspan="2">市場</th><th colspan="2">&nbsp;</th>
<th rowspan="2">通常取引<br>16日終値<div class="sortmark"></div></th><th colspan="7">PTS</th></tr>
<tr><th>株価</th><th colspan="2">通常取引<br>16日終値比</th><th>出来高</th><th>ＰＥＲ</th><th>ＰＢＲ</th><th>利回り</th></tr>
<tr><td class="tac"><a href="/stock/?code=7360">7360</a></td><th class="tal" scope="row">オンデック</th>
<td class="tac">東Ｇ</td><td class="gaiyou_icon"><a href="/stock/?code=7360"></a></td>
<td class="chart_icon"><a href="/stock/chart?code=7360"></a></td>
<td>711</td><td>861</td><td class="w61"><span class="up">+150</span></td>
<td class="w50"><span class="up">+21.10</span>%</td><td>1,700</td><td>17.2</td><td>2.03</td><td>－</td></tr>
<tr><td class="tac"><a href="/stock/?code=262A">262A</a></td><th class="tal" scope="row">ベイシス&lt;x&gt;</th>
<td class="tac">東Ｓ</td><td class="gaiyou_icon"><a href="/stock/?code=262A"></a></td>
<td class="chart_icon"><a href="/stock/chart?code=262A"></a></td>
<td>1,000</td><td>1,100</td><td><span class="up">+100</span></td>
<td><span class="up">+10.00</span>%</td><td>300</td><td>－</td><td>－</td><td>－</td></tr>
</table>
"""


def test_parse_page() -> None:
    rows = parse_page(PAGE)
    assert [r["code"] for r in rows] == ["73600", "262A0"]
    assert rows[0] == {"code": "73600", "name": "オンデック", "market": "東Ｇ", "base_close": 711.0,
                       "pts_price": 861.0, "pts_chg_pct": 21.1, "volume": 1700}
    assert rows[1]["name"] == "ベイシス<x>"
    assert page_base_day(PAGE) == 16
    assert page_as_of(PAGE) == "06:02"
    assert page_base_day("<p>no table</p>") is None


def test_session_date_for() -> None:
    assert session_date_for(16, date(2026, 10, 17)) == date(2026, 10, 16)
    assert session_date_for(16, date(2026, 10, 16)) == date(2026, 10, 16)
    assert session_date_for(30, date(2026, 10, 1)) == date(2026, 9, 30)
    assert session_date_for(31, date(2026, 10, 1)) == date(2026, 8, 31)
    assert session_date_for(31, date(2027, 1, 2)) == date(2026, 12, 31)


def _client(pages: dict[str, str]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        slug = req.url.path.rsplit("/", 1)[-1]
        page = req.url.params.get("page")
        return httpx.Response(200, text=pages.get(slug, PAGE) if page == "1" else "<html></html>")
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_snapshot_dedup_and_api(c) -> None:
    init_db()
    db = SessionLocal()
    try:
        now = datetime(2026, 10, 16, 23, 30, tzinfo=JST)
        with _client({}) as http:
            stats = snapshot_all(db, http, now=now, pause=0)
        assert stats == {"night_up": 2, "night_down": 2, "night_volume": 2}
        with _client({}) as http:
            stats = snapshot_all(db, http, now=datetime(2026, 10, 17, 0, 0, tzinfo=JST), pause=0)
        assert stats == {"night_up": 0, "night_down": 0, "night_volume": 0}
        assert db.query(PtsSnapshot).count() == 6
        later = PAGE.replace("06:02現在", "23:45現在").replace(">861<", ">900<")
        with _client({"pts_night_price_increase": later}) as http:
            stats = snapshot_all(db, http, now=datetime(2026, 10, 17, 0, 0, tzinfo=JST), pause=0)
        assert stats["night_up"] == 2 and stats["night_down"] == 0
        r = db.query(PtsSnapshot).filter_by(ranking="night_up", code="73600", pts_price=900).one()
        assert r.session_date == date(2026, 10, 16) and r.as_of == "23:45"
        db.add_all([
            DailyPrice(code="73600", date=date(2026, 10, 16), open=700, close=711),
            DailyPrice(code="73600", date=date(2026, 10, 19), open=850, close=860),
            DailyPrice(code="73600", date=date(2026, 10, 20), open=1, close=1),
        ])
        db.commit()
    finally:
        db.close()

    d = c.get("/api/pts").json()
    assert d["dates"] == ["2026-10-16"] and d["date"] == "2026-10-16"
    assert d["fetched_at"] == "2026-10-17 00:00:00" and d["as_of"] == "23:45"
    assert [s["as_of"] for s in d["snapshots"]] == ["23:45", "06:02"]
    top = d["rows"][0]
    assert top["code"] == "73600" and top["pts_price"] == 900
    assert top["next_date"] == "2026-10-19" and top["next_open"] == 850
    assert top["open_gap_pct"] == round((850 / 711 - 1) * 100, 2)
    assert top["open_vs_pts_pct"] == round((850 / 900 - 1) * 100, 2)
    assert top["follow_ratio"] == round((850 - 711) / (900 - 711), 2)
    assert d["rows"][1]["next_open"] is None and d["rows"][1]["follow_ratio"] is None
    assert d["summary"]["n"] == 1

    old = c.get("/api/pts", params={"at": "2026-10-16 23:30:00"}).json()
    assert old["as_of"] == "06:02" and old["rows"][0]["pts_price"] == 861
    assert c.get("/api/pts", params={"ranking": "night_down"}).json()["fetched_at"] == "2026-10-16 23:30:00"
    empty = c.get("/api/pts", params={"date": "2026-10-15"}).json()
    assert empty["rows"] == [] and empty["dates"] == ["2026-10-16"]
    assert c.get("/api/pts", params={"ranking": "x"}).status_code == 400


def main() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    test_parse_page()
    test_session_date_for()
    test_snapshot_dedup_and_api(TestClient(app))
    print("ok: pts")


if __name__ == "__main__":
    main()


def test_snapshot_job_does_not_create_tables() -> None:
    """表はデプロイの migrate が作る。cron は作らず、無ければ落ちる（KAB-166）。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "jobs/market/pts_snapshot.py").read_text(encoding="utf-8")
    assert "init_db" not in src and "create_all" not in src

