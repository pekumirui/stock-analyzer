"""株探の PTS ナイトタイムセッション ランキングを取って pts_snapshots に残す。

株価は 15 分ディレイ。ランキングは通常取引の終値との比較で、見出しの「通常取引 16日終値」が
比べている営業日。夕方の開始前や休日は前のセッションのまま出るので、取得日ではなく見出しの日で
session_date を決める。同じセッション・同じ「HH:MM現在」をもう持っていれば書かない。
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser

import httpx
from sqlalchemy.orm import Session

from ..db import run_sqlite_write, upsert
from ..models import PtsSnapshot

BASE_URL = "https://kabutan.jp/warning/"
RANKINGS = {
    "night_up": "pts_night_price_increase",
    "night_down": "pts_night_price_decrease",
    "night_volume": "pts_night_volume_ranking",
}
MAX_PAGES = 5      # 1 ページ 15 銘柄。1 ランキング 75 銘柄まで
PAUSE_S = 3.0
JST = timezone(timedelta(hours=9))
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36")

_CODE_RE = re.compile(r"[?&]code=([0-9][0-9A-Z]{2}[0-9A-Z])\b")
_BASE_DAY_RE = re.compile(r"通常取引\s*(?:<br\s*/?>)?\s*(\d{1,2})日終値")
_AS_OF_RE = re.compile(r"<li>\s*(\d{1,2}:\d{2})現在\s*</li>")


class _Rows(HTMLParser):
    """<tr> ごとにセルの文字と、行の最初の /stock/?code= を集める。"""

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str | None, list[str]]] = []
        self._code: str | None = None
        self._cells: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._code, self._cells, self._cell = None, [], None
        elif tag in ("td", "th") and self._cells is not None:
            self._cell = []
        elif tag == "a" and self._cells is not None and self._code is None:
            m = _CODE_RE.search(dict(attrs).get("href") or "")
            if m and (dict(attrs).get("href") or "").startswith("/stock/?"):
                self._code = m.group(1)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._cells is not None:
            self._cells.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._cells is not None:
            self.rows.append((self._code, self._cells))
            self._cells = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _num(text: str) -> float | None:
    t = text.replace(",", "").replace("%", "").replace("+", "").strip()
    try:
        return float(t)
    except ValueError:
        return None


def parse_page(html: str) -> list[dict]:
    """ランキング表の行。列は コード, 銘柄名, 市場, (アイコン2), 終値, PTS株価, 前日比, 前日比%, 出来高, PER, PBR, 利回り。"""
    p = _Rows()
    p.feed(html)
    out = []
    for code4, cells in p.rows:
        if not code4 or not cells or cells[0] != code4:
            continue  # 指数の帯など、コード列で始まらない行
        vals = [c for c in cells[1:] if c]
        if len(vals) < 7:
            continue
        name, market = vals[0], vals[1]
        nums = [_num(c) for c in vals[2:7]]
        if nums[0] is None or nums[1] is None:
            continue
        out.append({
            "code": code4 + "0", "name": name, "market": market,
            "base_close": nums[0], "pts_price": nums[1],
            "pts_chg_pct": nums[3], "volume": int(nums[4] or 0),
        })
    return out


def page_base_day(html: str) -> int | None:
    m = _BASE_DAY_RE.search(html)
    return int(m.group(1)) if m else None


def page_as_of(html: str) -> str | None:
    m = _AS_OF_RE.search(html)
    return m.group(1).zfill(5) if m else None


def session_date_for(day: int, today: date) -> date:
    """見出しの「D日終値」の D を、today 以前で一番近いその日付にする。"""
    y, m = today.year, today.month
    for _ in range(3):
        try:
            d = date(y, m, day)
        except ValueError:
            d = None
        if d is not None and d <= today:
            return d
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    raise ValueError(f"session day {day} not found before {today}")


def fetch_page(http: httpx.Client, slug: str, page: int) -> str:
    r = http.get(BASE_URL + slug, params={"page": page})
    r.raise_for_status()
    return r.text


def fetch_ranking(http: httpx.Client, slug: str, first: str, *, max_pages: int = MAX_PAGES,
                  pause: float = PAUSE_S) -> list[dict]:
    """1 ページ目の HTML から始めて、ページを送って行を集める。"""
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(pause)
        html = first if page == 1 else fetch_page(http, slug, page)
        rows = [x for x in parse_page(html) if x["code"] not in seen]
        if not rows:
            break  # 最終ページの先、またはページ送りが効かず同じ中身
        seen.update(x["code"] for x in rows)
        out.extend(rows)
    return out


def has_snapshot(db: Session, ranking: str, session_date: date, as_of: str | None) -> bool:
    return as_of is not None and db.query(PtsSnapshot.fetched_at).filter(
        PtsSnapshot.session_date == session_date, PtsSnapshot.ranking == ranking,
        PtsSnapshot.as_of == as_of).first() is not None


def save_snapshot(db: Session, ranking: str, rows: list[dict], *, fetched_at: datetime,
                  session_date: date, as_of: str | None) -> int:
    """同じセッションで同じ as_of を持っていれば書かず 0 を返す。"""
    if has_snapshot(db, ranking, session_date, as_of):
        return 0
    recs = [{"fetched_at": fetched_at, "ranking": ranking, "code": x["code"],
             "session_date": session_date, "as_of": as_of, "rank": n,
             "name": x["name"], "market": x["market"], "base_close": x["base_close"],
             "pts_price": x["pts_price"], "pts_chg_pct": x["pts_chg_pct"],
             "volume": x["volume"]}
            for n, x in enumerate(rows, start=1)]
    run_sqlite_write(db, lambda: upsert(db, PtsSnapshot, recs, commit=False))
    return len(recs)


def snapshot_all(db: Session, http: httpx.Client, *, now: datetime | None = None,
                 pause: float = PAUSE_S) -> dict[str, int]:
    """3 ランキングを取って書く。戻り値はランキングごとの書いた行数（-1 は取得失敗）。"""
    now = (now or datetime.now(JST)).astimezone(JST)
    fetched_at = now.replace(tzinfo=None, microsecond=0)
    stats: dict[str, int] = {}
    for i, (key, slug) in enumerate(RANKINGS.items()):
        if i:
            time.sleep(pause)
        try:
            html = fetch_page(http, slug, 1)
            day = page_base_day(html)
            if day is None:
                print(f"pts {key} no table")
                stats[key] = 0
                continue
            sd = session_date_for(day, now.date())
            as_of = page_as_of(html)
            if has_snapshot(db, key, sd, as_of):
                print(f"pts {key} session={sd.isoformat()} as_of={as_of} unchanged")
                stats[key] = 0
                continue
            rows = fetch_ranking(http, slug, html, pause=pause)
        except httpx.HTTPError as exc:
            print(f"pts {key} fetch failed: {exc}")
            stats[key] = -1
            continue
        n = save_snapshot(db, key, rows, fetched_at=fetched_at, session_date=sd, as_of=as_of)
        print(f"pts {key} session={sd.isoformat()} as_of={as_of} rows={n}")
        stats[key] = n
    return stats


def client() -> httpx.Client:
    return httpx.Client(timeout=20, follow_redirects=True,
                        headers={"User-Agent": USER_AGENT, "Accept-Language": "ja"})
