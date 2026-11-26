"""株価チャートの業績の段と PERバンド（KAB-301。docs/screen/stock.md「業績の数字の作り方」）。

開示を古い順に見て、その時点で分かっていた 予想EPS・四半期の営業利益・直近 12 か月の営業利益 を
階段にする。後から出た訂正や予想で過去を書き換えない。
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import date, timedelta
from math import log

from ..config import EARNINGS_PARAMS as EP
from ..models import Statement
from .analytics import ORDER, find_statement, is_after_close, shift_year, single_from
from .forecasts import CORRECTION, REPORT, REVISION, Withdrawn, forecast_points
from .reported import ProfitPerShare, splits_after
from .withdrawals import withdrawn_items


def _ttm_op(hist: list[Statement], s: Statement, tol: int) -> float | None:
    """直近 12 か月の営業利益。本決算は通期、四半期は 当期累計 + 前期通期 − 前期同期累計。"""
    if s.operating_profit is None:
        return None
    if ORDER[s.type_of_period] == 4:
        return s.operating_profit
    prev_fy_end = shift_year(s.fiscal_year_end, -1)
    pfy = find_statement(hist, prev_fy_end, "FY", tol)
    psame = find_statement(hist, prev_fy_end, s.type_of_period, tol)
    if pfy is None or psame is None or pfy.operating_profit is None or psame.operating_profit is None:
        return None
    return s.operating_profit + pfy.operating_profit - psame.operating_profit


def _starts(d: date, t: str | None, days: list[date]) -> date:
    """開示を使い始める日。引け後（時刻なしも）は翌営業日、場中は当日。"""
    if not is_after_close(d, t):
        return d
    i = bisect_right(days, d)
    return days[i] if i < len(days) else d + timedelta(days=1)


# 開示から権利落ちまでがこの日数以内の分割・併合は、会社が分割後の株数で予想を出し直していることがある
SPLIT_LOOKAHEAD_DAYS = 180


def adjust_per_share(eps: float | None, d: date, days: list[date], factors: list[float],
             prev: float | None) -> float | None:
    """開示時点の 1 株あたりの値を今の調整後にそろえる。d より後の adj_factor を掛ける。

    権利落ちが近い分割は織り込み済みのことがあるので、近い分割を掛けない候補も作り、
    直前の調整後の予想EPS に一番近いものを採る（分割の比は予想の修正よりずっと大きい）。
    """
    if eps is None:
        return None
    after = [(days[i], factors[i]) for i in range(bisect_right(days, d), len(days))
             if factors[i] not in (None, 1.0)]
    full = 1.0
    for _, f in after:
        full *= f
    cands, skip = [eps * full], 1.0
    for ex, f in after:
        if (ex - d).days > SPLIT_LOOKAHEAD_DAYS:
            break
        skip *= f
        cands.append(eps * full / skip)
    if prev and prev > 0 and eps > 0 and len(cands) > 1:
        return round(min(cands, key=lambda x: abs(log(x / prev))), 2)
    return round(cands[0], 2)


def fund_steps(stmts: list[Statement], days: list[date], factors: list[float],
               withdrawn: Withdrawn = withdrawn_items) -> dict:
    """{t, eps, op_q, op_ttm}。t は使い始める日（昇順・重複なし）、値は次の点まで続く。

    stmts は 1 銘柄の開示（順不同）、days / factors は日足の日付と adj_factor（昇順）。
    """
    tol = EP["fy_match_tol_days"]
    for s in stmts:
        if not hasattr(s, "gross_profit"):
            s.gross_profit = None  # find_statement は売上総利益も見る（決算速報は XBRL から付ける）。ここでは使わない
    hist: list[Statement] = []
    per_share = ProfitPerShare()
    op_q = op_ttm = None
    out: dict[str, list] = {"t": [], "eps": [], "op_q": [], "op_ttm": []}
    for p in forecast_points(stmts, withdrawn):
        s = p.statement
        hist.append(s)
        per_share.add(s, p.raw)
        if p.role in (REPORT, CORRECTION):
            q = single_from(hist, s, tol)
            op_q = q["operating_profit"] if q else None
            op_ttm = _ttm_op(hist, s, tol)
        elif p.role != REVISION or not p.touched & {"eps", "profit"}:
            continue
        exact = per_share.eps(p.values["profit"])
        start = _starts(s.disclosed_date, s.disclosed_time, days).isoformat()
        if out["t"] and start < out["t"][-1]:
            start = out["t"][-1]  # 引け後の開示の翌営業日より前に、休日付けの開示が来たとき
        prev_eps = next((x for x in reversed(out["eps"]) if x is not None), None)
        if exact is None:
            eps = p.values["eps"]
            adj_eps = adjust_per_share(eps.value, eps.disclosed_date, days, factors, prev_eps)
        else:
            adj_eps = adjust_per_share(exact[0], splits_after(exact[1], days), days, factors, None)
        row = (adj_eps, op_q, op_ttm)
        if out["t"] and out["t"][-1] == start:
            for k, v in zip(("eps", "op_q", "op_ttm"), row):
                out[k][-1] = v
            continue
        if out["t"] and (out["eps"][-1], out["op_q"][-1], out["op_ttm"][-1]) == row:
            continue
        out["t"].append(start)
        for k, v in zip(("eps", "op_q", "op_ttm"), row):
            out[k].append(v)
    return out
