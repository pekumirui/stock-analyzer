from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from ..config import FORECAST_WITHDRAWALS_PATH
from ..models import WITHDRAWABLE_ITEMS

Withdrawals = Mapping[str, frozenset[str]]
NONE: frozenset[str] = frozenset()


def load_withdrawals(path: Path | None = None) -> dict[str, frozenset[str]]:
    src = FORECAST_WITHDRAWALS_PATH if path is None else path
    raw = json.loads(src.read_text(encoding="utf-8"))
    out: dict[str, frozenset[str]] = {}
    for entry in raw.get("withdrawals") or []:
        no = str(entry["disclosure_no"]).strip()
        if no in out:
            raise ValueError(f"duplicate disclosure_no in forecast withdrawals: {no}")
        items = frozenset(entry.get("items") or ())
        unknown = items - set(WITHDRAWABLE_ITEMS)
        if unknown:
            raise ValueError(f"unknown items in forecast withdrawals: {no} {sorted(unknown)}")
        if not items:
            raise ValueError(f"no items in forecast withdrawals: {no}")
        out[no] = items
    return out


@lru_cache(maxsize=1)
def default_withdrawals() -> dict[str, frozenset[str]]:
    return load_withdrawals()


def withdrawn_items(s, listed: Withdrawals | None = None) -> frozenset[str]:
    table = default_withdrawals() if listed is None else listed
    marked = getattr(s, "withdrawn_forecasts", None) or ()
    if isinstance(marked, str):
        marked = marked.split(",")
    return frozenset(marked) | table.get(getattr(s, "disclosure_no", None), NONE)


def no_withdrawals(s) -> frozenset[str]:
    return NONE
