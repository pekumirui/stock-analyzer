"""J-Quants API V2 クライアント。

V1 (token/auth_user → idToken) は閉鎖予定のため V2 (APIキー方式) を使用。
- 認証: x-api-key ヘッダーにダッシュボード発行のAPIキー。有効期限なし。
- レスポンス: 一律 {"data": [...], "pagination_key": "..."}
- レートリミット: Light=60req/分。/fins/summary は全プラン共通で別枠60req/分。
  → エンドポイント群ごとにトークンバケットで自主規制し、429は指数バックオフで再試行。
"""
import time
import threading
from collections import deque

import httpx

from . import config


class RateLimiter:
    """1分あたり max_per_min 回に制限するスライディングウィンドウ。"""

    def __init__(self, max_per_min: int):
        self.max = max_per_min
        self.calls: deque[float] = deque()
        self.lock = threading.Lock()

    def acquire(self):
        with self.lock:
            now = time.monotonic()
            while self.calls and now - self.calls[0] > 60:
                self.calls.popleft()
            if len(self.calls) >= self.max:
                sleep_for = 60 - (now - self.calls[0]) + 0.05
                time.sleep(max(sleep_for, 0))
                now = time.monotonic()
                while self.calls and now - self.calls[0] > 60:
                    self.calls.popleft()
            self.calls.append(time.monotonic())


class JQuantsClient:
    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.JQUANTS_API_KEY
        if not self.api_key:
            raise RuntimeError(
                "JQUANTS_API_KEY が未設定です。\n"
                "https://jpx-jquants.com のダッシュボードでAPIキーを発行し .env に記入してください。"
            )
        # /fins/summary はプラン枠とは別枠のため独立したバケットを持つ
        self._limiters = {
            "default": RateLimiter(config.RATE_LIMIT_PER_MIN),
            "fins": RateLimiter(config.RATE_LIMIT_FINS_PER_MIN),
        }
        self._client = httpx.Client(
            base_url=config.JQUANTS_BASE,
            headers={"x-api-key": self.api_key},
            timeout=60,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _bucket(self, path: str) -> str:
        return "fins" if path.startswith("/fins/") else "default"

    def get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """pagination_key を辿って全件取得。レスポンスの 'data' 配列を返す。"""
        params = dict(params or {})
        limiter = self._limiters[self._bucket(path)]
        out: list[dict] = []
        while True:
            data = None
            for attempt in range(6):
                limiter.acquire()
                r = self._client.get(path, params=params)
                if r.status_code == 429:
                    wait = min(60, 5 * 2 ** attempt)
                    print(f"  429 Too Many Requests → {wait}秒待機して再試行 ({path})")
                    time.sleep(wait)
                    continue
                if r.status_code in (500, 502, 503, 504):
                    time.sleep(min(30, 2 ** attempt))
                    continue
                r.raise_for_status()
                data = r.json()
                break
            if data is None:
                raise RuntimeError(f"リトライ上限に達しました: {path} {params}")

            out.extend(data.get("data", []))
            pk = data.get("pagination_key")
            if not pk:
                return out
            params["pagination_key"] = pk

    def equities_master(self, date: str | None = None, code: str | None = None) -> list[dict]:
        """上場銘柄一覧 /v2/equities/master"""
        p = {}
        if date: p["date"] = date
        if code: p["code"] = code
        return self.get_all("/equities/master", p)

    def bars_daily(self, date: str | None = None, code: str | None = None,
                   from_: str | None = None, to: str | None = None) -> list[dict]:
        """株価四本値 /v2/equities/bars/daily

        date のみ指定 → 全上場銘柄のその日のデータ (バックフィルはこちらを使う)
        code + from/to → 1銘柄の期間データ
        """
        p = {}
        if date: p["date"] = date
        if code: p["code"] = code
        if from_: p["from"] = from_
        if to: p["to"] = to
        return self.get_all("/equities/bars/daily", p)

    def topix(self, from_: str | None = None, to: str | None = None) -> list[dict]:
        """TOPIX四本値 /v2/indices/bars/daily/topix (from/to 未指定なら全期間)"""
        p = {}
        if from_: p["from"] = from_
        if to: p["to"] = to
        return self.get_all("/indices/bars/daily/topix", p)

    def fins_summary(self, date: str | None = None, code: str | None = None) -> list[dict]:
        """財務情報 /v2/fins/summary (date のみ指定で全銘柄のその日の開示)"""
        p = {}
        if date: p["date"] = date
        if code: p["code"] = code
        return self.get_all("/fins/summary", p)

    def calendar(self, from_: str, to: str) -> list[dict]:
        """取引カレンダー /v2/markets/calendar"""
        return self.get_all("/markets/calendar", {"from": from_, "to": to})

    def trading_days(self, from_: str, to: str) -> list[str]:
        """東証の営業日リスト (YYYY-MM-DD)。HolDiv=='1' が営業日。"""
        try:
            rows = self.calendar(from_, to)
            days = [r["Date"] for r in rows if str(r.get("HolDiv")) == "1"]
            if days:
                return sorted(days)
        except Exception as e:  # カレンダー取得失敗時は平日で代替 (休日は空応答になるだけ)
            print(f"  取引カレンダー取得に失敗、平日で代替します: {e}")
        from datetime import datetime, timedelta
        d = datetime.strptime(from_, "%Y-%m-%d").date()
        end = datetime.strptime(to, "%Y-%m-%d").date()
        out = []
        while d <= end:
            if d.weekday() < 5:
                out.append(d.strftime("%Y-%m-%d"))
            d += timedelta(days=1)
        return out
