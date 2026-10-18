"""通期業績予想の修正履歴。データ源 (J-Quants / XBRL) に依らない行の組み立て。

データ源は `RevisionPoint` の列を作るだけ (`app/market/revisions.py` / `app/xbrl/revisions.py`)。
期初予想 → 数値が変わった修正 → 実績 の並びと増減率はここで決める。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from .market.analytics import (METRIC_KEYS, PL_KEYS, SIGNED, calendar_label, growth_cell, period_label,
                               shift_year)

FORECAST = "forecast"
ACTUAL = "actual"
# 前期末からこの日数までに出た予想を期初予想とみなす (本決算の発表は期末から 45〜90 日)
INITIAL_WITHIN_DAYS = 100


@dataclass(frozen=True)
class RevisionPoint:
    """1 開示が持つ、ある年度の通期の数値 (予想または実績)。金額は円。"""
    fy_end: date
    kind: str                      # forecast | actual
    disclosed: date
    values: dict
    order: tuple = field(default=())   # 同じ日の開示の前後 (開示番号など)


def revision_cell(cur: float | None, prev: float | None, signed: bool = True) -> dict:
    """前の行からの修正率。数値が同じなら state=flat (画面は →)。"""
    if cur is not None and prev is not None and cur == prev:
        return {"pct": 0.0, "state": "flat"}
    return growth_cell(cur, prev, signed)


def _changed(a: dict, b: dict) -> bool:
    # EPS は分割で遡及修正されるだけのことがあるので、修正かどうかは損益で見る
    return any(a.get(k) != b.get(k) for k in PL_KEYS)


def _has_values(v: dict) -> bool:
    return any(v.get(k) is not None for k in PL_KEYS)


def _row(fy_end: date, kind: str, p: RevisionPoint, prev: dict | None) -> dict:
    values = {k: p.values.get(k) for k in METRIC_KEYS}
    growth = None if prev is None else {
        k: revision_cell(values[k], prev.get(k), SIGNED[k]) for k in METRIC_KEYS}
    return {"label": period_label(fy_end), "range": calendar_label(fy_end), "kind": kind,
            "fiscal_year_end": fy_end.isoformat(), "period": None,
            "disclosed_date": p.disclosed.isoformat(), "values": values, "growth": growth}


def _year_rows(points: list[RevisionPoint]) -> list[dict]:
    """1 年度ぶん: 期初予想 (forecast_initial) → 修正 (forecast_revised) → 実績 (actual_fy)。

    最初の予想が期初より後 (前期の本決算が履歴に無い) なら期初とは呼ばず forecast にする。
    """
    key = lambda p: (p.disclosed, p.order)  # noqa: E731
    actual = min((p for p in points if p.kind == ACTUAL and _has_values(p.values)),
                 key=key, default=None)
    fy_end = actual.fy_end if actual else max(p.fy_end for p in points)
    rows, last = [], None
    for p in sorted((p for p in points if p.kind == FORECAST and _has_values(p.values)), key=key):
        if actual is not None and key(p) >= key(actual):
            break  # 実績の開示とその後の予想は履歴に入れない
        if last is None:
            initial = p.disclosed <= shift_year(fy_end, -1) + timedelta(days=INITIAL_WITHIN_DAYS)
            rows.append(_row(fy_end, "forecast_initial" if initial else FORECAST, p, None))
        elif _changed(p.values, last):
            rows.append(_row(fy_end, "forecast_revised", p, last))
        else:
            continue
        last = p.values
    if actual is not None:
        rows.append(_row(fy_end, "actual_fy", actual, last))
    return rows


def revision_rows(points: list[RevisionPoint], years: int) -> list[dict]:
    """直近 years 年度の修正履歴 (古い年度が上)。年度は会計期末の年月で束ねる。"""
    by_year: dict[tuple[int, int], list[RevisionPoint]] = {}
    for p in points:
        by_year.setdefault((p.fy_end.year, p.fy_end.month), []).append(p)
    groups = [g for g in (_year_rows(by_year[ym]) for ym in sorted(by_year)) if g]
    return [r for g in (groups[-years:] if years > 0 else []) for r in g]
