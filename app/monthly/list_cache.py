"""月次一覧のプロセス内キャッシュ。キーは (版, JST の今日)。

ワーカーごとに持つ。別プロセスの書き込みは data_versions の版が変わって捨てる。
"""
from __future__ import annotations

from datetime import date

_cache: dict[tuple[int, date], list[dict]] = {}


def get(version: int, today: date) -> list[dict] | None:
    return _cache.get((version, today))


def put(version: int, today: date, payload: list[dict]) -> None:
    # 日付か版が変わった古い結果は残さない。
    _cache.clear()
    _cache[(version, today)] = payload


def clear() -> None:
    _cache.clear()
