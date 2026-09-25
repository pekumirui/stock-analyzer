"""ロイヤルHD。ロイヤルホストとてんや。上期∪下期。yoy は yoy_index（number は触らない）。"""
from __future__ import annotations

from html_issuers._branded import parse_branded
from html_issuers._tile import tile_segment_series
from html_monthly import parse_tables

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ロイヤルホスト", "ロイヤルホスト"),
    ("てんや", "てんや"),
)
_BRANDS = ("ロイヤルホスト", "てんや")


def parse(html: str, *, as_of: str | None = None) -> dict:
    text = html or ""
    # 1表の速報（既存店売上、客数）はブランド見出し無しで通す。
    if len(parse_tables(text)) >= 2 and not any(brand in text for brand in _BRANDS):
        raise ValueError("8179 page does not match declaration")
    return parse_branded(
        html,
        "8179",
        _PREFIXES,
        (),
        as_of=as_of,
        force_unit="yoy_index",
        series_fn=tile_segment_series,
    )
