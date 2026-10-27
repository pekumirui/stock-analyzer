"""テクニカル指標・RS・ステージ判定・決算後リターンの一括計算。"""
import json
from bisect import bisect_right
from datetime import date, timedelta

import pandas as pd
import numpy as np
from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session, defer

from ..config import STAGE_PARAMS as P, EARNINGS_PARAMS as EP, RS_PRODUCT_CATEGORIES
from ..models import DailyPrice, Topix, Indicator, Statement, EarningsEvent, Stock
from ..db import WRITE_CHUNK_SIZE, delete_all_chunked, upsert
from .ingest import _f
from .price_limit import at_limit


def rs_universe_codes(db: Session) -> set[str] | None:
    """RS/ステージ対象の銘柄コード。ProdCat 未取込なら None (=従来どおり全銘柄)。"""
    has = db.query(Stock.code).filter(Stock.product_category.isnot(None)).limit(1).first()
    if has is None:
        return None
    return {c for (c,) in db.query(Stock.code).filter(
        Stock.product_category.in_(RS_PRODUCT_CATEGORIES)).all()}


def price_frame(db: Session) -> pd.DataFrame:
    """全銘柄の株価を code, date 順で返す。

    全行を Row で受けてから DataFrame にすると、518 万行で一時的に 3GB を超える。
    カーソルから区切って読み、同じ code / date は 1 つのオブジェクトを共有させる。
    """
    q = select(DailyPrice.code, DailyPrice.date, DailyPrice.adj_close,
               DailyPrice.close, DailyPrice.adj_volume, DailyPrice.volume,
               DailyPrice.adj_open, DailyPrice.open) \
        .order_by(DailyPrice.code, DailyPrice.date)
    shared: dict = {}
    parts = []
    for rows in db.execute(q).yield_per(200_000).partitions():
        code, d, adj_close, close, adj_volume, volume, adj_open, open_ = zip(*rows)
        parts.append(pd.DataFrame({
            "code": [shared.setdefault(x, x) for x in code],
            "date": [shared.setdefault(x, x) for x in d],
            "adj_close": np.array(adj_close, dtype=float),
            "close": np.array(close, dtype=float),
            "adj_volume": np.array(adj_volume, dtype=float),
            "volume": np.array(volume, dtype=float),
            "adj_open": np.array(adj_open, dtype=float),
            "open": np.array(open_, dtype=float),
        }))
        del rows, code, d, adj_close, close, adj_volume, volume, adj_open, open_
    if not parts:
        return pd.DataFrame(columns=["code", "date", "adj_close", "close", "adj_volume", "volume",
                                     "adj_open", "open"])
    df = pd.concat(parts, ignore_index=True)
    del parts
    df["adj_close"] = df["adj_close"].fillna(df["close"])
    df["volume"] = df["adj_volume"].fillna(df["volume"])
    df["adj_open"] = df["adj_open"].fillna(df["open"])
    return df.dropna(subset=["adj_close"])


def compute_indicators(
    db: Session,
    keep_days: int = 400,
    px: pd.DataFrame | None = None,
    chunk_size: int = WRITE_CHUNK_SIZE,
):
    """全銘柄のMA/52週/RS/ステージを計算し indicators に保存。px は price_frame の結果を使い回すとき。"""
    df = price_frame(db) if px is None else px
    if df.empty:
        return
    universe = rs_universe_codes(db)
    if universe is not None:
        df = df[df["code"].isin(universe)]
        if df.empty:
            return
    elif px is not None:
        df = df.copy()  # 呼び出し元の px に列を足さない

    g = df.groupby("code", group_keys=False)
    df["ma50"] = g["adj_close"].transform(lambda s: s.rolling(50).mean())
    df["ma150"] = g["adj_close"].transform(lambda s: s.rolling(150).mean())
    df["ma200"] = g["adj_close"].transform(lambda s: s.rolling(200).mean())
    df["ma200_slope_1m"] = g["ma200"].transform(lambda s: s / s.shift(21) - 1)
    df["high_52w"] = g["adj_close"].transform(lambda s: s.rolling(252, min_periods=60).max())
    df["low_52w"] = g["adj_close"].transform(lambda s: s.rolling(252, min_periods=60).min())
    df["avg_volume_50"] = g["volume"].transform(lambda s: s.rolling(50).mean())
    for n in (63, 126, 189, 252):
        df[f"r{n}"] = g["adj_close"].transform(lambda s, n=n: s / s.shift(n) - 1)
    df["rs_raw"] = (df["r63"] * 0.4 + df["r126"] * 0.2 + df["r189"] * 0.2 + df["r252"] * 0.2) * 100

    def rank_day(s: pd.Series) -> pd.Series:
        pct = s.rank(pct=True, method="average")
        return np.ceil(pct * 99).clip(1, 99)
    df["rs_rank"] = df.groupby("date")["rs_raw"].transform(rank_day)
    df.loc[df["rs_raw"].isna(), "rs_rank"] = np.nan

    in_band = ((df["adj_close"] >= df["ma200"] * (1 - P["stage1_band"])) &
               (df["adj_close"] <= df["ma200"] * (1 + P["stage1_band"]))).astype(float)
    df["band_ratio"] = in_band.groupby(df["code"]).transform(
        lambda s: s.rolling(P["stage1_band_days"]).mean())

    c = df["adj_close"]
    stage2 = (
        (c > df["ma150"]) & (c > df["ma200"]) &
        (df["ma150"] > df["ma200"]) &
        (df["ma200_slope_1m"] > 0) &
        (df["ma50"] > df["ma150"]) & (df["ma50"] > df["ma200"]) &
        (c > df["ma50"]) &
        (c >= df["low_52w"] * P["low52_margin"]) &
        (c >= df["high_52w"] * P["high52_margin"]) &
        (df["rs_rank"] >= P["rs_min_stage2"])
    )
    stage4 = (
        (c < df["ma200"]) &
        (df["ma200_slope_1m"] < P["ma200_down_th"]) &
        (c <= df["low_52w"] * P["stage4_near_low"])
    )
    stage1 = (
        (~stage2) & (~stage4) &
        (df["ma200_slope_1m"].abs() < P["ma200_flat_th"]) &
        (df["band_ratio"] >= P["stage1_band_ratio"]) &
        (c < df["low_52w"] * P["low52_margin"])
    )
    stage3 = (
        (~stage2) & (~stage4) & (~stage1) &
        (c > df["ma200"] * (1 - P["stage1_band"])) &
        (df["ma200_slope_1m"] < P["ma200_flat_th"]) &
        (c >= df["low_52w"] * P["low52_margin"])
    )
    df["stage"] = np.select([stage2, stage4, stage1, stage3], [2, 4, 1, 3], default=np.nan)

    cutoff = sorted(df["date"].unique())[-keep_days] if df["date"].nunique() > keep_days else df["date"].min()
    out = df[df["date"] >= cutoff]
    cols = ["code", "date", "adj_close", "ma50", "ma150", "ma200", "ma200_slope_1m",
            "high_52w", "low_52w", "rs_raw", "rs_rank", "stage", "avg_volume_50"]
    out = out[cols].rename(columns={"adj_close": "close"})
    # 読取スナップショットを解放してから書く。全置換を 1 トランザクションにすると
    # 本番規模で busy_timeout(30s) を超え、月次 PUT が locked になる (#522)。
    db.commit()
    delete_all_chunked(db, Indicator, chunk_size=chunk_size)
    # 全行を一度に dict にすると 1GB 超になるので区切る。チャンクごとに commit。
    for i in range(0, len(out), chunk_size):
        rows = out.iloc[i:i + chunk_size].replace({np.nan: None}).to_dict("records")
        for r in rows:
            r["rs_rank"] = int(r["rs_rank"]) if r["rs_rank"] is not None else None
            r["stage"] = int(r["stage"]) if r["stage"] is not None else None
        upsert(db, Indicator, rows, commit=True)


def no_open_days(db: Session) -> set[tuple[str, date]]:
    """寄らずのストップ高・安の日（code, date）。

    比例配分の約定が引けに入るので行はあり、四本値が同じになる。
    寄ってすぐ張り付いた日とは日足では区別できないので、同じく寄らずとみなす。
    UL / LL があればそれで、無い過去の行は前日終値からの値幅で判定する。
    """
    one_price = (DailyPrice.open == DailyPrice.high) & (DailyPrice.high == DailyPrice.low) \
        & (DailyPrice.low == DailyPrice.close)
    cand = db.query(DailyPrice.code, DailyPrice.date, DailyPrice.close, DailyPrice.adj_factor,
                    DailyPrice.upper_limit, DailyPrice.lower_limit).filter(one_price).all()
    need_prev = [(c, d) for c, d, _, _, ul, ll in cand if ul is None and ll is None]
    prev: dict[tuple[str, date], float] = {}
    for code in {c for c, _ in need_prev}:
        days = sorted(d for c, d in need_prev if c == code)
        rows = (db.query(DailyPrice.date, DailyPrice.close)
                .filter(DailyPrice.code == code, DailyPrice.date < days[-1],
                        DailyPrice.date >= days[0] - timedelta(days=30))
                .order_by(DailyPrice.date).all())
        ds = [r[0] for r in rows]
        for d in days:
            i = int(np.searchsorted(ds, d)) - 1
            if i >= 0:
                prev[(code, d)] = rows[i][1]
    out = set()
    for code, d, close, factor, ul, ll in cand:
        if ul is not None or ll is not None:
            hit = bool(ul) or bool(ll)
        else:
            p = prev.get((code, d))
            # 分割の日は前日終値を当日の株数に直してから比べる
            hit = at_limit(p * (factor or 1.0) if p else None, close)
        if hit:
            out.add((code, d))
    return out


def is_after_close(ddate: date, dtime: str | None) -> bool:
    """大引け以降の発表か。時刻が無ければ引け後とみなす。"""
    close = EP["market_close"] if ddate >= date.fromisoformat(EP["market_close_since"]) \
        else EP["market_close_old"]
    return (dtime or "16:00")[:5] >= close


def limit_up_closes(db: Session, keys: set[tuple[str, date]],
                    prev_close: dict[tuple[str, date], float]) -> set[tuple[str, date]]:
    """keys のうち、ストップ高で引けた日（引けでは張り付いていて買えない）。

    終値が高値で、UL が 1。UL が無い過去の行は前日終値 prev_close から値幅いっぱいか。
    """
    out = set()
    keys = sorted(keys)
    for i in range(0, len(keys), 500):
        rows = (db.query(DailyPrice.code, DailyPrice.date, DailyPrice.high, DailyPrice.close,
                         DailyPrice.adj_factor, DailyPrice.upper_limit)
                .filter(tuple_(DailyPrice.code, DailyPrice.date).in_(keys[i:i + 500])).all())
        for code, d, high, close, factor, ul in rows:
            if close is None or close != high:
                continue
            if ul is not None:
                hit = bool(ul)
            else:
                p = prev_close.get((code, d))
                hit = p is not None and close > p * (factor or 1.0) \
                    and at_limit(p * (factor or 1.0), close)
            if hit:
                out.add((code, d))
    return out


def first_disclosures(stmts) -> dict[tuple[str, date], str]:
    """1 期（銘柄 × 決算期 × 四半期）につき最初の決算短信の (銘柄, 発表日) → 発表時刻。

    stmts は (code, disclosed_date, disclosed_time, fiscal_year_end, type_of_period, is_revision)。
    訂正は起点にしない。業績予想の修正だけの開示は入れない。
    """
    first: dict[tuple, tuple[date, str]] = {}
    for code, d, t, fye, period, revision in stmts:
        if revision or d is None:
            continue
        key, cur = (code, fye, period), (d, t or "")
        if key not in first or cur < first[key]:
            first[key] = cur
    out: dict[tuple[str, date], str] = {}
    for (code, _, _), (d, t) in first.items():
        out[(code, d)] = min(out.get((code, d), t), t)
    return out


def compute_earnings_events(db: Session, px: pd.DataFrame | None = None):
    """決算発表日からの対TOPIXリターンを earnings_events に保存。

    決算後に買えた最初の値を基準にする。
    - 15:30 以降（引け後）の発表は翌営業日の始値
    - 場中の発表は当日の終値。ストップ高で引けた日は引けで買えないので翌営業日の始値
    - 始値で買う日が寄らずのストップ高・安なら、寄った日まで先送りする
    +Nd は始値で買ったなら基準日を 1 日目、終値で買ったなら翌営業日を 1 日目として N 日目の終値。
    """
    px = price_frame(db) if px is None else px
    tp = pd.DataFrame(db.query(Topix.date, Topix.open, Topix.close).all(),
                      columns=["date", "topen", "tclose"]).sort_values("date")
    if px.empty or tp.empty:
        return
    tclose = dict(zip(tp["date"], tp["tclose"]))
    topen = dict(zip(tp["date"], tp["topen"]))
    stuck = no_open_days(db)
    stmts = db.query(Statement.code, Statement.disclosed_date, Statement.disclosed_time,
                     Statement.fiscal_year_end, Statement.type_of_period, Statement.is_revision).all()
    firsts = first_disclosures(stmts)
    # 同じ日に複数の開示がある銘柄は 1 件にまとめる。時刻は最初の決算短信、無ければ一番早いもの
    times: dict[tuple[str, date], str | None] = {}
    for code, d, t, *_ in stmts:
        k = (code, d)
        times[k] = firsts[k] if k in firsts else min((x for x in (times.get(k), t) if x), default=None)
    stmts = [(code, d, t) for (code, d), t in times.items()]
    # 開示ごとに、同じ銘柄の次の期の最初の決算短信の日
    first_days: dict[str, list[date]] = {}
    for code, d in sorted(firsts):
        first_days.setdefault(code, []).append(d)
    next_first = {}
    for code, d, _ in stmts:
        days = first_days.get(code, [])
        k = bisect_right(days, d)
        next_first[(code, d)] = days[k] if k < len(days) else None

    # px は code, date 順なので各銘柄の日付列は昇順
    px_by_code = {c: (g["date"].to_numpy(), g["adj_open"].to_numpy(), g["adj_close"].to_numpy(),
                      g["close"].to_numpy())
                  for c, g in px.groupby("code")}

    # 場中の発表で当日に取引がある日が、ストップ高引けか
    intraday, prev_close = set(), {}
    for code, ddate, dtime in stmts:
        arr = px_by_code.get(code)
        if arr is None or is_after_close(ddate, dtime):
            continue
        i = int(np.searchsorted(arr[0], ddate))
        if i < len(arr[0]) and arr[0][i] == ddate:
            intraday.add((code, ddate))
            if i > 0:
                prev_close[(code, ddate)] = arr[3][i - 1]
    capped = limit_up_closes(db, intraday, prev_close)

    horizon = {"1d": 1, "5d": 5, "20d": 20, "60d": 60}
    rows = []
    for code, ddate, dtime in stmts:
        arr = px_by_code.get(code)
        if arr is None:
            continue
        dates, opens, closes, _ = arr
        i0 = int(np.searchsorted(dates, ddate, side="right" if is_after_close(ddate, dtime) else "left"))
        at_close = (code, ddate) in intraday and (code, ddate) not in stuck \
            and (code, ddate) not in capped
        if not at_close:
            if (code, ddate) in intraday:
                i0 += 1
            while i0 < len(dates) and (code, dates[i0]) in stuck:
                i0 += 1
        if i0 >= len(dates):
            continue
        base_date = dates[i0]
        if at_close:
            base, tbase, first = closes[i0], tclose.get(base_date), i0 + 1
        else:
            base, tbase, first = opens[i0], topen.get(base_date) or tclose.get(base_date), i0
        if not base or np.isnan(base) or not tbase:
            continue
        rec = {"code": code, "disclosed_date": ddate, "base_date": base_date, "base_price": base,
               "is_first": (code, ddate) in firsts, "disclosed_time": dtime or None}
        for label, n in horizon.items():
            j = first + n - 1
            if j < len(dates):
                r = closes[j] / base - 1
                tr = (tclose.get(dates[j], tbase) / tbase - 1)
                rec[f"return_{label}"] = round(r * 100, 2)
                rec[f"alpha_{label}"] = round((r - tr) * 100, 2)
            else:
                rec[f"return_{label}"] = None
                rec[f"alpha_{label}"] = None
        r = closes[-1] / base - 1
        tr = tclose.get(dates[-1], tbase) / tbase - 1
        rec["return_to_date"] = round(r * 100, 2)
        rec["alpha_to_date"] = round((r - tr) * 100, 2)
        # 次の決算（次の期の最初の決算短信）の前営業日の終値まで。まだ無ければ現在まで
        nxt = next_first.get((code, ddate))
        j = int(np.searchsorted(dates, nxt)) - 1 if nxt else len(dates) - 1
        rec["next_disclosed_date"] = nxt
        rec["end_date"] = rec["return_to_next"] = rec["alpha_to_next"] = None
        if j >= i0:
            r = closes[j] / base - 1
            tr = tclose.get(dates[j], tbase) / tbase - 1
            rec["end_date"] = dates[j]
            rec["return_to_next"] = round(r * 100, 2)
            rec["alpha_to_next"] = round((r - tr) * 100, 2)
        rows.append(rec)
    if rows:
        for i in range(0, len(rows), WRITE_CHUNK_SIZE):
            upsert(db, EarningsEvent, rows[i:i + WRITE_CHUNK_SIZE], commit=True)


def indicators_current(db: Session) -> bool:
    """indicators が株価の最終日まで計算済みか。"""
    last_px = db.query(func.max(DailyPrice.date)).scalar()
    last_ind = db.query(func.max(Indicator.date)).scalar()
    return last_px is None or (last_ind is not None and last_ind >= last_px)


METRIC_KEYS = ("net_sales", "operating_profit", "ordinary_profit", "profit", "eps")
# 再提出が実質的な訂正かを見るときに比べる指標。EPSは分割・増資で遡及修正されるだけの
# ことがあり (損益は不変)、それを訂正扱いすると再提出が速報一覧に残ってしまうので除く。
PL_KEYS = ("net_sales", "operating_profit", "ordinary_profit", "profit")

# CurPerType は 1Q/2Q/3Q/4Q/5Q/FY。FYと4Qはどちらも通期累計扱い。
# 5Q(決算期変更等の変則期)は前後関係が定義できないためQ単独は算出しない。
ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4, "FY": 4}
PERIODS_OF = {1: ("1Q",), 2: ("2Q",), 3: ("3Q",), 4: ("FY", "4Q")}

# 売上はマイナスを取らないため黒字転換の概念がない
SIGNED = {"net_sales": False, "operating_profit": True, "ordinary_profit": True,
          "profit": True, "eps": True}


def metrics(s: Statement) -> dict[str, float | None]:
    return {k: getattr(s, k) for k in METRIC_KEYS}


def shift_year(d: date, n: int) -> date:
    """年を n ずらす。2/29決算は 2/28 に丸める (replace が ValueError を投げるため)。"""
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def is_actual(s: Statement) -> bool:
    """実績値を含む決算短信本体か (予想修正のみの開示は実績が空なので除く)。"""
    return "FinancialStatements" in (s.type_of_document or "")


def statement_history(db: Session, code: str, upto: date, days: int) -> list[Statement]:
    """指定日から遡った開示履歴。raw_json は使わないので defer する。"""
    return (db.query(Statement).options(defer(Statement.raw_json))
            .filter(Statement.code == code,
                    Statement.disclosed_date <= upto,
                    Statement.disclosed_date >= upto - timedelta(days=days))
            .order_by(Statement.disclosed_date).all())


def disclosure_state(hist: list[Statement], stmt: Statement) -> str:
    """その開示が first(初出) / restated(既出だが数値が変化) / duplicate(既出で数値も同一) か。

    遅延企業は過去の期を数値そのままで再提出することがあり、そのまま速報一覧に出すと
    1銘柄で何行も並ぶ。同一銘柄の同一期間の再提出を見るので、find_statement の
    ±tol_days 最近傍ではなく fiscal_year_end の完全一致で引く。
    同一期間を同じ日に2回出す銘柄があるため、前後は (日付, 開示番号) の組で比べる。
    """
    key = (stmt.disclosed_date, stmt.disclosure_no)
    cands = [s for s in hist
             if s.fiscal_year_end == stmt.fiscal_year_end
             and s.type_of_period == stmt.type_of_period
             and is_actual(s) and (s.disclosed_date, s.disclosure_no) < key]
    if not cands:
        return "first"
    prev = max(cands, key=lambda s: (s.disclosed_date, s.disclosure_no))
    same = all(getattr(prev, k) == getattr(stmt, k) for k in PL_KEYS)
    return "duplicate" if same else "restated"


def find_statement(hist: list[Statement], fy_end: date | None, period: str | None,
                   tol_days: int) -> Statement | None:
    """事業年度末が fy_end に最も近い(±tol_days)同一四半期の開示。

    完全一致ではなく最近傍にするのは、決算期変更やうるう年で年度末がずれるため。
    修正開示より決算短信本体を優先し、同条件なら後の開示 (訂正後の値) を採る。
    """
    if fy_end is None or not period:
        return None
    periods = PERIODS_OF.get(ORDER.get(period, 0), (period,))
    cands = [s for s in hist
             if s.fiscal_year_end is not None and s.type_of_period in periods
             and abs((s.fiscal_year_end - fy_end).days) <= tol_days
             and any(getattr(s, k) is not None for k in METRIC_KEYS)]
    if not cands:
        return None
    cands = [s for s in cands if not s.is_revision] or cands
    return min(cands, key=lambda s: (abs((s.fiscal_year_end - fy_end).days),
                                     -s.disclosed_date.toordinal()))


def growth_cell(cur: float | None, prev: float | None, signed: bool = True) -> dict:
    """増減率セル。state は up/down/turn_profit(黒転)/turn_loss(赤転)/loss(赤字継続)/na。

    前期が0以下だと比率に意味がないため、単純な None ではなく状態で返す
    (営業赤字からの黒字転換が「–」に潰れるのを避ける)。
    """
    if cur is None or prev is None:
        return {"pct": None, "state": "na"}
    if prev > 0:
        pct = round((cur / prev - 1) * 100, 1)
        if signed and cur <= 0:
            return {"pct": pct, "state": "turn_loss"}
        return {"pct": pct, "state": "up" if pct >= 0 else "down"}
    if not signed:
        return {"pct": None, "state": "na"}
    return {"pct": None, "state": "turn_profit" if cur > 0 else "loss"}


def growth_map(cur: dict | None, prev: dict | None) -> dict | None:
    if not cur or not prev:
        return None
    return {k: growth_cell(cur.get(k), prev.get(k), SIGNED[k]) for k in METRIC_KEYS}


def prev_quarter(fy_end: date | None, period: str | None) -> tuple[date, str] | None:
    """直前四半期の (事業年度末, 四半期)。1Qの前は前事業年度の通期。5Q・不明は None。"""
    n = ORDER.get(period or "")
    if not n or fy_end is None:
        return None
    if n == 1:
        return (shift_year(fy_end, -1), "FY")
    return (fy_end, PERIODS_OF[n - 1][0])


def single_from(hist: list[Statement], stmt: Statement | None, tol_days: int) -> dict | None:
    """Q単独 = 当該累計 - 直前四半期累計。1Q は累計そのもの。"""
    if stmt is None:
        return None
    if ORDER.get(stmt.type_of_period or "") == 1:
        return metrics(stmt)
    pq = prev_quarter(stmt.fiscal_year_end, stmt.type_of_period)
    prev = find_statement(hist, pq[0], pq[1], tol_days) if pq else None
    if prev is None:
        return None
    cm, pm = metrics(stmt), metrics(prev)
    return {k: (cm[k] - pm[k]) if cm[k] is not None and pm[k] is not None else None
            for k in METRIC_KEYS}


def single_metrics(hist: list[Statement], fy_end: date | None, period: str | None,
                   tol_days: int) -> dict | None:
    return single_from(hist, find_statement(hist, fy_end, period, tol_days), tol_days)


def qoq_map(hist: list[Statement], stmt: Statement, tol_days: int) -> dict | None:
    """Q単独(当四半期) vs Q単独(直前四半期)。1Q は前事業年度の4Q単独が相手になる。"""
    cur = single_from(hist, stmt, tol_days)
    pq = prev_quarter(stmt.fiscal_year_end, stmt.type_of_period)
    if cur is None or pq is None:
        return None
    return growth_map(cur, single_metrics(hist, pq[0], pq[1], tol_days))


def period_label(fy_end: date | None, period: str | None = None, quarterly: bool = False) -> str:
    """FY27.3 / FY27.3_2Q 形式のラベル。

    通期は累計表では `FY27.3` だが、3か月決算表では第4四半期単独なので `FY27.3_4Q` と書く。
    """
    if fy_end is None:
        return "–"
    base = f"FY{fy_end.year % 100:02d}.{fy_end.month}"
    if not period:
        return base
    if ORDER.get(period) == 4:
        return f"{base}_4Q" if quarterly else base
    return f"{base}_{period}"


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def calendar_span(fy_end: date | None, period: str | None) -> tuple[date, date] | None:
    """当該四半期のカレンダー範囲 (月初〜月末)。5Q・不明は None。"""
    n = ORDER.get(period or "")
    if not n or fy_end is None:
        return None
    # 事業年度開始月 = 決算月の翌月。3月期なら4月、12月期なら1月
    start_month = fy_end.month % 12 + 1
    start_year = fy_end.year - 1 if start_month > fy_end.month else fy_end.year
    raw = start_month + (n - 1) * 3
    q_year = start_year + (raw - 1) // 12
    q_month = (raw - 1) % 12 + 1
    end_raw = q_month + 2
    end_year = q_year + (end_raw - 1) // 12
    end_month = (end_raw - 1) % 12 + 1
    return date(q_year, q_month, 1), _month_end(end_year, end_month)


def calendar_label(fy_end: date | None, period: str | None = None,
                   quarterly: bool = False) -> str:
    """UI向け。累計は会計期末 (`26.11`)。Q単独は当該3か月の末 (`25.08`)。

    列見出しが決算期なので「期」は付けない。Q は下のバッジ。
    """
    if fy_end is None:
        return "–"
    n = ORDER.get(period or "")
    if not quarterly:
        return f"{fy_end.year % 100:02d}.{fy_end.month}"
    if not n:
        return f"{fy_end.year % 100:02d}.{fy_end.month:02d}"
    span = calendar_span(fy_end, period)
    if span is None:
        return period_label(fy_end, period, quarterly)
    _, b = span
    return f"{b.year % 100:02d}.{b.month:02d}"


def _row(fy_end: date | None, period: str | None, kind: str,
         values: dict, growth: dict | None, quarterly: bool = False,
         disclosed: date | None = None) -> dict:
    return {"label": period_label(fy_end, period, quarterly),
            "range": calendar_label(fy_end, period, quarterly), "kind": kind,
            "fiscal_year_end": fy_end.isoformat() if fy_end else None,
            "period": period,
            "disclosed_date": disclosed.isoformat() if disclosed else None,
            "values": values, "growth": growth}


# 本決算の開示は当期予想 (FSales 等) が空で、来期予想は NxF* にだけ入る。列には保存していない
_NEXT_FC_KEYS = {"net_sales": "NxFSales", "operating_profit": "NxFOP",
                 "ordinary_profit": "NxFOdP", "profit": "NxFNp", "eps": "NxFEPS"}


def next_year_forecast(stmt: Statement) -> tuple[date | None, dict, dict]:
    """本決算の raw_json から (来期の年度末, 来期中間予想, 来期通期予想)。"""
    try:
        raw = json.loads(stmt.raw_json or "{}")
    except ValueError:
        raw = {}
    try:
        fy_end = date.fromisoformat(raw.get("NxtFYEn") or "")
    except ValueError:
        fy_end = shift_year(stmt.fiscal_year_end, 1) if stmt.fiscal_year_end else None
    half = {k: _f(raw.get(v + "2Q")) for k, v in _NEXT_FC_KEYS.items()}
    full = {k: _f(raw.get(v)) for k, v in _NEXT_FC_KEYS.items()}
    return fy_end, half, full


def cumulative_rows(hist: list[Statement], stmt: Statement, years: int, tol_days: int) -> list[dict]:
    """累計テーブル: 過去(years-1)期の同一四半期 → 当期実績 → 中間予想 → 通期予想 → 前期通期実績。

    本決算 (FY/4Q) は予想が来期のもの。前期通期は上の実績行と同じなので出さない。
    データの無い期は行ごと省略する (バックフィル年数によって行数が変わる)。
    """
    fy_end, period = stmt.fiscal_year_end, stmt.type_of_period
    if fy_end is None:
        return []
    rows = []
    for back in range(years - 1, 0, -1):
        s = find_statement(hist, shift_year(fy_end, -back), period, tol_days)
        if s is None:
            continue
        ly = find_statement(hist, shift_year(s.fiscal_year_end, -1), period, tol_days)
        rows.append(_row(s.fiscal_year_end, period, "actual", metrics(s),
                         growth_map(metrics(s), metrics(ly) if ly else None),
                         disclosed=s.disclosed_date))
    prev_fy_end = shift_year(fy_end, -1)
    ly = find_statement(hist, prev_fy_end, period, tol_days)
    rows.append(_row(fy_end, period, "actual_current", metrics(stmt),
                     growth_map(metrics(stmt), metrics(ly) if ly else None),
                     disclosed=stmt.disclosed_date))

    if period in ("FY", "4Q"):
        nx_end, f2, fyf = next_year_forecast(stmt)
        if any(v is not None for v in f2.values()):
            h1 = find_statement(hist, fy_end, "2Q", tol_days)
            rows.append(_row(nx_end, None, "forecast_h", f2,
                             growth_map(f2, metrics(h1) if h1 else None),
                             disclosed=stmt.disclosed_date))
        if any(v is not None for v in fyf.values()):
            rows.append(_row(nx_end, None, "forecast_fy", fyf, growth_map(fyf, metrics(stmt)),
                             disclosed=stmt.disclosed_date))
        return rows

    prev_fy = find_statement(hist, prev_fy_end, "FY", tol_days)
    f2 = {k: getattr(stmt, "f2_" + k) for k in METRIC_KEYS}
    if any(v is not None for v in f2.values()):
        ly2 = find_statement(hist, prev_fy_end, "2Q", tol_days)
        rows.append(_row(fy_end, None, "forecast_h", f2,
                         growth_map(f2, metrics(ly2) if ly2 else None),
                         disclosed=stmt.disclosed_date))
    fyf = {k: getattr(stmt, "fy_" + k) for k in METRIC_KEYS}
    if any(v is not None for v in fyf.values()):
        rows.append(_row(fy_end, None, "forecast_fy", fyf,
                         growth_map(fyf, metrics(prev_fy) if prev_fy else None),
                         disclosed=stmt.disclosed_date))
    if prev_fy is not None:
        rows.append(_row(prev_fy.fiscal_year_end, None, "actual_fy_prev", metrics(prev_fy), None,
                         disclosed=prev_fy.disclosed_date))
    return rows


_MID_PERIODS = ("1Q", "2Q", "3Q")


def progress_map(cum: dict | None, base: dict | None) -> dict | None:
    """累計 / 通期 の進捗率 (%)。通期が 0 以下や欠けている指標は None。"""
    if cum is None or base is None:
        return None
    out = {}
    for k in METRIC_KEYS:
        c, b = cum.get(k), base.get(k)
        out[k] = round(c / b * 100, 1) if c is not None and b and b > 0 else None
    return out


def progress_rows(hist: list[Statement], stmt: Statement, years: int, tol_days: int) -> list[dict]:
    """進捗率テーブル: 同一四半期の累計を、過去は通期実績で、当期は通期予想で割る。

    1Q〜3Q だけ。本決算は通期そのものなので行を作らない。
    """
    fy_end, period = stmt.fiscal_year_end, stmt.type_of_period
    if fy_end is None or period not in _MID_PERIODS:
        return []
    rows = []
    for back in range(years - 1, 0, -1):
        s = find_statement(hist, shift_year(fy_end, -back), period, tol_days)
        if s is None:
            continue
        fy = find_statement(hist, s.fiscal_year_end, "FY", tol_days)
        base = metrics(fy) if fy else None
        row = _row(s.fiscal_year_end, period, "actual", metrics(s), None,
                   disclosed=s.disclosed_date)
        rows.append({**row, "base": base, "progress": progress_map(metrics(s), base)})
    fyf = {k: getattr(stmt, "fy_" + k) for k in METRIC_KEYS}
    row = _row(fy_end, period, "actual_current", metrics(stmt), None,
               disclosed=stmt.disclosed_date)
    rows.append({**row, "base": fyf, "progress": progress_map(metrics(stmt), fyf)})
    return rows


def quarterly_rows(hist: list[Statement], stmt: Statement, n: int, tol_days: int) -> list[dict]:
    """3か月決算テーブル: 当四半期から n 個ぶん遡ったQ単独実績 (古い順)。"""
    cursor = (stmt.fiscal_year_end, stmt.type_of_period) if stmt.fiscal_year_end else None
    rows, first = [], True
    for _ in range(n):
        if cursor is None:
            break
        fy_end, period = cursor
        s = stmt if first else find_statement(hist, fy_end, period, tol_days)
        first = False
        cur = single_from(hist, s, tol_days)
        if cur is not None:
            ly = single_metrics(hist, shift_year(fy_end, -1), period, tol_days)
            disc = s.disclosed_date if s is not None else None
            rows.append(_row(fy_end, period, "single", cur, growth_map(cur, ly),
                             quarterly=True, disclosed=disc))
        cursor = prev_quarter(fy_end, period)
    rows.reverse()
    return rows


def earnings_detail(db: Session, stmt: Statement) -> dict:
    """1件の開示について 累計テーブル・3か月決算テーブルを組み立てる。"""
    tol = EP["fy_match_tol_days"]
    hist = statement_history(db, stmt.code, stmt.disclosed_date,
                             366 * (EP["cum_history_years"] + 1))
    cum = cumulative_rows(hist, stmt, EP["cum_history_years"], tol)
    qtr = quarterly_rows(hist, stmt, EP["single_history_quarters"], tol)
    return {
        "progress": progress_rows(hist, stmt, EP["cum_history_years"], tol),
        "label": period_label(stmt.fiscal_year_end, stmt.type_of_period),
        "cumulative": cum,
        "quarterly": qtr,
        "notes": {
            # 全行で経常利益が空 = IFRS/米国基準。UI側で列を淡色化する
            "no_ordinary": all(r["values"].get("ordinary_profit") is None for r in cum + qtr),
            "single_unavailable": not qtr,
        },
    }


def quarter_summary(db: Session, stmt: Statement, hist: list[Statement] | None = None) -> dict:
    """1件の開示について 累計/Q単独/YoY/QoQ/予想/上方修正 を導出。

    hist を渡すと追加クエリを発行しない (一覧APIの一括プリフェッチ用)。
    """
    tol = EP["fy_match_tol_days"]
    if hist is None:
        hist = statement_history(db, stmt.code, stmt.disclosed_date, EP["list_history_days"])

    cum = metrics(stmt)
    single = single_from(hist, stmt, tol)

    yoy = yoy_single = None
    fyf = {k: getattr(stmt, "fy_" + k) for k in METRIC_KEYS}
    progress = None
    if stmt.type_of_period in _MID_PERIODS:
        progress = {"cur": progress_map(cum, fyf), "prev": None}
    if stmt.fiscal_year_end:
        prev_fy_end = shift_year(stmt.fiscal_year_end, -1)
        ly = find_statement(hist, prev_fy_end, stmt.type_of_period, tol)
        if ly is not None:
            yoy = growth_map(cum, metrics(ly))
            if progress is not None:
                ly_fy = find_statement(hist, ly.fiscal_year_end, "FY", tol)
                progress["prev"] = progress_map(metrics(ly), metrics(ly_fy) if ly_fy else None)
            yoy_single = growth_map(single, single_metrics(hist, prev_fy_end,
                                                           stmt.type_of_period, tol))

    revised_up = False
    revision_pct = None
    prev_fc = None
    for s in reversed(hist):
        if (s.disclosure_no != stmt.disclosure_no
                and s.fiscal_year_end == stmt.fiscal_year_end
                and s.fy_operating_profit is not None):
            prev_fc = s
            break
    if prev_fc and stmt.fy_operating_profit is not None and prev_fc.fy_operating_profit:
        diff = stmt.fy_operating_profit / prev_fc.fy_operating_profit - 1
        if abs(diff) >= EP["revision_noise_th"]:
            revision_pct = round(diff * 100, 1)
            revised_up = diff > 0

    return {
        "revision_pct": revision_pct,
        "cumulative": cum,
        "single": single,
        "yoy": yoy,
        "yoy_single": yoy_single,
        "qoq": qoq_map(hist, stmt, tol),
        "forecast_h": {k: getattr(stmt, "f2_" + k) for k in METRIC_KEYS},
        "forecast_fy": fyf,
        "progress": progress,
        "revised_up": revised_up,
        "is_revision_doc": bool(stmt.is_revision),
    }
