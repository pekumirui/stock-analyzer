"""ロイヤルHD。ロイヤルホストとてんや。上期∪下期。yoy は yoy_index（number は触らない）。"""
from __future__ import annotations

from jobs.monthly.html_issuers._branded import parse_branded
from jobs.monthly.html_issuers._tile import tile_segment_series
from jobs.monthly.html_monthly import parse_tables

_PREFIXES: tuple[tuple[str, str], ...] = (
    ("ロイヤルホスト", "ロイヤルホスト"),
    ("てんや", "てんや"),
)
_BRANDS = ("ロイヤルホスト", "てんや")


def _is_branded_or_flash(html: str) -> bool:
    text = html or ""
    if any(brand in text for brand in _BRANDS):
        return True
    return len(parse_tables(text)) < 2


def parse(html: str, *, as_of: str | None = None) -> dict:
    if not _is_branded_or_flash(html or ""):
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
