"""テクニカル指標・RS・ステージ判定・決算後リターンの一括計算。"""
import pandas as pd
import numpy as np
from sqlalchemy.orm import Session

from .config import STAGE_PARAMS as P
from .models import DailyPrice, Topix, Indicator, Statement, EarningsEvent
from .ingest import upsert


def _price_frame(db: Session) -> pd.DataFrame:
    q = db.query(DailyPrice.code, DailyPrice.date, DailyPrice.adj_close,
                 DailyPrice.close, DailyPrice.adj_volume, DailyPrice.volume)
    df = pd.DataFrame(q.all(), columns=["code", "date", "adj_close", "close", "adj_volume", "volume"])
    df["adj_close"] = df["adj_close"].fillna(df["close"])
    df["volume"] = df["adj_volume"].fillna(df["volume"])
    df = df.dropna(subset=["adj_close"])
    return df.sort_values(["code", "date"])


def compute_indicators(db: Session, keep_days: int = 400):
    """全銘柄のMA/52週/RS/ステージを計算し indicators に保存。"""
    df = _price_frame(db)
    if df.empty:
        return

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

    # RSランク: 日毎にパーセンタイル→1-99
    def rank_day(s: pd.Series) -> pd.Series:
        pct = s.rank(pct=True, method="average")
        return np.ceil(pct * 99).clip(1, 99)
    df["rs_rank"] = df.groupby("date")["rs_raw"].transform(rank_day)
    df.loc[df["rs_raw"].isna(), "rs_rank"] = np.nan

    # Stage1用: MA200±band 帯域内比率 (直近N日)
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

    # 保存は直近keep_days分のみ (容量節約)
    cutoff = sorted(df["date"].unique())[-keep_days] if df["date"].nunique() > keep_days else df["date"].min()
    out = df[df["date"] >= cutoff]
    cols = ["code", "date", "adj_close", "ma50", "ma150", "ma200", "ma200_slope_1m",
            "high_52w", "low_52w", "rs_raw", "rs_rank", "stage", "avg_volume_50"]
    rows = out[cols].rename(columns={"adj_close": "close"}) \
        .replace({np.nan: None}).to_dict("records")
    for r in rows:
        r["rs_rank"] = int(r["rs_rank"]) if r["rs_rank"] is not None else None
        r["stage"] = int(r["stage"]) if r["stage"] is not None else None
    db.query(Indicator).delete()
    upsert(db, Indicator, rows)


def compute_earnings_events(db: Session):
    """決算発表日からの対TOPIXリターンを earnings_events に保存。"""
    px = _price_frame(db)
    tp = pd.DataFrame(db.query(Topix.date, Topix.close).all(), columns=["date", "tclose"]).sort_values("date")
    if px.empty or tp.empty:
        return
    tdates = tp["date"].tolist()
    tclose = dict(zip(tp["date"], tp["tclose"]))
    stmts = db.query(Statement.code, Statement.disclosed_date, Statement.disclosed_time).all()

    px_by_code = {c: g.set_index("date")["adj_close"] for c, g in px.groupby("code")}
    rows = []
    for code, ddate, dtime in stmts:
        s = px_by_code.get(code)
        if s is None:
            continue
        # 引け後(15時以降)発表なら基準は翌営業日、場中なら当日
        after_close = (dtime or "16:00")[:2] >= "15"
        future = [d for d in s.index if d > ddate] if after_close else [d for d in s.index if d >= ddate]
        if not future:
            continue
        base_date = future[0]
        base = s.get(base_date)
        tbase = tclose.get(base_date)
        if not base or not tbase:
            continue
        rec = {"code": code, "disclosed_date": ddate, "base_date": base_date, "base_price": base}
        horizon = {"1d": 1, "5d": 5, "20d": 20, "60d": 60}
        sdates = list(s.index)
        i0 = sdates.index(base_date)
        for label, n in horizon.items():
            if i0 + n < len(sdates):
                d2 = sdates[i0 + n]
                r = s.iloc[i0 + n] / base - 1
                tr = (tclose.get(d2, tbase) / tbase - 1)
                rec[f"return_{label}"] = round(r * 100, 2)
                rec[f"alpha_{label}"] = round((r - tr) * 100, 2)
            else:
                rec[f"return_{label}"] = None
                rec[f"alpha_{label}"] = None
        r = s.iloc[-1] / base - 1
        tr = tclose.get(sdates[-1], tbase) / tbase - 1
        rec["return_to_date"] = round(r * 100, 2)
        rec["alpha_to_date"] = round((r - tr) * 100, 2)
        rows.append(rec)
    if rows:
        upsert(db, EarningsEvent, rows)


# ---------- 決算サマリ (Q単独・YoY・修正判定) ----------

def quarter_summary(db: Session, stmt: Statement) -> dict:
    """1件の開示について 累計/Q単独/YoY/予想/上方修正 を導出。"""
    hist = (db.query(Statement)
            .filter(Statement.code == stmt.code,
                    Statement.disclosed_date <= stmt.disclosed_date)
            .order_by(Statement.disclosed_date).all())

    def metrics(s):
        return {"net_sales": s.net_sales, "operating_profit": s.operating_profit,
                "ordinary_profit": s.ordinary_profit, "profit": s.profit, "eps": s.eps}

    # CurPerType は 1Q/2Q/3Q/4Q/5Q/FY。FYと4Qはどちらも通期累計扱い。
    # 5Q(決算期変更等の変則期)は前後関係が定義できないためQ単独は算出しない。
    ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4, "FY": 4}
    PREV_OF = {2: "1Q", 3: "2Q", 4: "3Q"}
    cur_n = ORDER.get(stmt.type_of_period or "", None)

    def find(fy_end, period):
        """同一事業年度・同一四半期の直近開示 (修正開示より決算短信本体を優先)。"""
        cands = [s for s in hist
                 if s.fiscal_year_end == fy_end and s.type_of_period == period
                 and s.net_sales is not None]
        if not cands:
            return None
        body = [s for s in cands if not s.is_revision]
        return (body or cands)[-1]

    # Q単独 = 当期累計 - 前四半期累計
    single = None
    if cur_n and stmt.net_sales is not None:
        if cur_n == 1:
            single = metrics(stmt)
        else:
            prev = find(stmt.fiscal_year_end, PREV_OF[cur_n])
            if prev:
                single = {k: (v - metrics(prev)[k]) if v is not None and metrics(prev)[k] is not None else None
                          for k, v in metrics(stmt).items()}

    # 前年同期 (累計/単独)
    yoy, yoy_single = {}, {}
    if stmt.fiscal_year_end:
        prev_fy_end = stmt.fiscal_year_end.replace(year=stmt.fiscal_year_end.year - 1)
        ly = find(prev_fy_end, stmt.type_of_period)
        if ly:
            for k, v in metrics(stmt).items():
                lv = metrics(ly)[k]
                yoy[k] = round((v / lv - 1) * 100, 1) if v is not None and lv not in (None, 0) and lv > 0 else None
            if single and cur_n and cur_n > 1:
                lp = find(prev_fy_end, [k for k, v in ORDER.items() if v == cur_n - 1][0])
                if lp:
                    ly_single = {k: (metrics(ly)[k] - metrics(lp)[k])
                                 if metrics(ly)[k] is not None and metrics(lp)[k] is not None else None
                                 for k in metrics(ly)}
                    for k, v in single.items():
                        lv = ly_single.get(k)
                        yoy_single[k] = round((v / lv - 1) * 100, 1) if v is not None and lv not in (None, 0) and lv > 0 else None
            elif single:
                yoy_single = yoy

    # 予想修正: 同一事業年度の直前開示の通期営業利益予想と比較
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
        if abs(diff) >= 0.001:                       # 端数差はノイズとして無視
            revision_pct = round(diff * 100, 1)
            revised_up = diff > 0

    return {
        "revision_pct": revision_pct,
        "cumulative": metrics(stmt),
        "single": single,
        "yoy": yoy or None,
        "yoy_single": yoy_single or None,
        "forecast_h": {"net_sales": stmt.f2_net_sales, "operating_profit": stmt.f2_operating_profit,
                       "ordinary_profit": stmt.f2_ordinary_profit, "profit": stmt.f2_profit, "eps": stmt.f2_eps},
        "forecast_fy": {"net_sales": stmt.fy_net_sales, "operating_profit": stmt.fy_operating_profit,
                        "ordinary_profit": stmt.fy_ordinary_profit, "profit": stmt.fy_profit, "eps": stmt.fy_eps},
        "revised_up": revised_up,
        "is_revision_doc": bool(stmt.is_revision),
    }
