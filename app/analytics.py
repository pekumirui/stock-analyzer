"""テクニカル指標・RS・ステージ判定・決算後リターンの一括計算。"""
from datetime import date, timedelta

import pandas as pd
import numpy as np
from sqlalchemy.orm import Session, defer

from .config import STAGE_PARAMS as P, EARNINGS_PARAMS as EP, RS_PRODUCT_CATEGORIES
from .models import DailyPrice, Topix, Indicator, Statement, EarningsEvent, Stock
from .ingest import upsert


def rs_universe_codes(db: Session) -> set[str] | None:
    """RS/ステージ対象の銘柄コード。ProdCat 未取込なら None (=従来どおり全銘柄)。"""
    has = db.query(Stock.code).filter(Stock.product_category.isnot(None)).limit(1).first()
    if has is None:
        return None
    return {c for (c,) in db.query(Stock.code).filter(
        Stock.product_category.in_(RS_PRODUCT_CATEGORIES)).all()}


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
    universe = rs_universe_codes(db)
    if universe is not None:
        df = df[df["code"].isin(universe)]
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
    # 予想値は比べない: 予想のみの変更は EarnForecastRevision として一覧から除外済みで、
    # 修正率は quarter_summary.revision_pct が別途拾う
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
             and any(v is not None for v in metrics(s).values())]
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
    """UI向け。Q単独は当該3か月 (`26.07-09`)、累計は期首〜当期末 (`26.04-09`)。

    通期累計は `27.3期`。2Q/3Qの累計を3か月と同じラベルにすると YTD を四半期と誤読する。
    """
    if fy_end is None:
        return "–"
    n = ORDER.get(period or "")
    # 通期累計は年度末ラベル。Q単独の4Qだけカレンダー範囲にする
    if not period or (n == 4 and not quarterly):
        return f"{fy_end.year % 100:02d}.{fy_end.month}期"
    if not n:
        # 5Q など ORDER に無い変則期。通期に潰すと累計が本決算に見える
        return f"{fy_end.year % 100:02d}.{fy_end.month}期 {period}"
    if quarterly:
        span = calendar_span(fy_end, period)
    else:
        # 累計: 事業年度開始〜当該四半期末 (3月期2Q → 26.04-09)
        q1 = calendar_span(fy_end, "1Q")
        qn = calendar_span(fy_end, period)
        span = (q1[0], qn[1]) if q1 and qn else None
    if span is None:
        return period_label(fy_end, period, quarterly)
    a, b = span
    # 9月期3Qなど年をまたぐ累計は 25.10-06 だと同年に読めるので年を残す
    if a.year != b.year:
        return f"{a.year % 100:02d}.{a.month:02d}-{b.year % 100:02d}.{b.month:02d}"
    return f"{a.year % 100:02d}.{a.month:02d}-{b.month:02d}"


def _row(fy_end: date | None, period: str | None, kind: str,
         values: dict, growth: dict | None, quarterly: bool = False,
         disclosed: date | None = None) -> dict:
    return {"label": period_label(fy_end, period, quarterly),
            "range": calendar_label(fy_end, period, quarterly), "kind": kind,
            "fiscal_year_end": fy_end.isoformat() if fy_end else None,
            "period": period,
            "disclosed_date": disclosed.isoformat() if disclosed else None,
            "values": values, "growth": growth}


def cumulative_rows(hist: list[Statement], stmt: Statement, years: int, tol_days: int) -> list[dict]:
    """累計テーブル: 過去(years-1)期の同一四半期 → 当期実績 → 中間予想 → 通期予想 → 前期通期実績。

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
    if stmt.fiscal_year_end:
        prev_fy_end = shift_year(stmt.fiscal_year_end, -1)
        ly = find_statement(hist, prev_fy_end, stmt.type_of_period, tol)
        if ly is not None:
            yoy = growth_map(cum, metrics(ly))
            yoy_single = growth_map(single, single_metrics(hist, prev_fy_end,
                                                           stmt.type_of_period, tol))

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
        "forecast_fy": {k: getattr(stmt, "fy_" + k) for k in METRIC_KEYS},
        "revised_up": revised_up,
        "is_revision_doc": bool(stmt.is_revision),
    }
