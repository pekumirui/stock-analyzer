"""過去 3 か月の対 TOPIX 超過リターンが、その後 3 か月の超過リターンを予測するかを検証する。

読み取り専用。stock.db（daily_prices / topix / stocks / statements）だけを使う。

    .venv/bin/python analyzer/momentum/backtest.py --db stock.db --out /tmp/momentum
    .venv/bin/python analyzer/momentum/backtest.py --form-months 12 --skip-months 1 --out /tmp/momentum_12_1

定義
- 判定日: 各月の最終営業日（topix の日付で決める）
- 形成期間: 3 か月前の判定日の終値 → 判定日の終値。銘柄リターン − TOPIX リターン
  --form-months / --skip-months で変えられる（例: 12 と 1 で 12-1 モメンタム。直近 1 か月を飛ばす）
- 保有期間: 判定日の終値 → 3 か月後の判定日の終値（= 翌営業日から 3 か月）。同じく超過
- 価格は close と adj_factor から自前で遡及調整する。DB の adj_close は直近の分割が
  過去日へ反映されていないことがある（2026-08-28 効力の 5 件で確認）ため使わない
- 判定日に売買の無い銘柄は除く（形成開始日だけ FORM_START_FFILL_BDAYS 営業日まで前方埋め）
- 保有中に上場廃止した銘柄は最終終値で現金化したとみなす（以降の超過は −TOPIX）
- 判定日ごとに形成期間の超過リターンで 5 分位。Q1 = 最も強い、Q5 = 最も弱い。等ウェイト
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

MONTHS = 3
TURNOVER_WINDOW = 20
FORM_START_FFILL_BDAYS = 5


def connect(db: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def data_report(con: sqlite3.Connection) -> list[str]:
    q = lambda s: con.execute(s).fetchall()  # noqa: E731
    out = ["## データ確認", ""]
    out.append("テーブル（件数）:")
    for (name,) in q("select name from sqlite_master where type='table' order by name"):
        out.append(f"  {name:24s} {q(f'select count(*) from {name}')[0][0]:>10,}")
    cols = ", ".join(r[1] for r in q("pragma table_info(daily_prices)"))
    out.append(f"daily_prices の列: {cols}")
    lo, hi, n_code, n_day = q(
        "select min(date), max(date), count(distinct code), count(distinct date) from daily_prices"
    )[0]
    out.append(f"日足: {lo} 〜 {hi}、{n_day:,} 営業日、{n_code:,} コード（ETF・REIT 等を含む）")
    t_lo, t_hi, t_n = q("select min(date), max(date), count(*) from topix")[0]
    missing = q("select count(distinct date) from daily_prices where date not in (select date from topix)")[0][0]
    out.append(f"TOPIX: topix テーブルに日次終値あり {t_lo} 〜 {t_hi}（{t_n:,} 日、日足に対する欠け {missing} 日）。1306 での代用は不要")
    n_master = q("select count(*) from stocks")[0][0]
    gone = q(
        "select count(*), min(last), max(last) from (select code, max(date) last from daily_prices "
        "where code not in (select code from stocks) group by code)"
    )[0]
    out.append(
        f"生存バイアス: stocks（現在の銘柄マスタ）{n_master:,} 件に無いコードが日足に {gone[0]} 件"
        f"（最終日 {gone[1]} 〜 {gone[2]}）。上場廃止銘柄の株価は廃止日まで残っている"
    )
    out.append(
        "  ただし廃止銘柄は市場区分・商品区分が分からない。statements（決算短信）があり REIT でないものを"
        "普通株とみなして採用し、短信の無い廃止コード（大半は ETF・インフラファンド）は除く"
    )
    out.append("")
    return out


def load_prices(con: sqlite3.Connection) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    eligible = pd.read_sql(
        """
        select code from stocks where product_category = '011' and market != 'TOKYO PRO MARKET'
        union
        select distinct code from statements
        where code not in (select code from stocks)
          and code not in (select code from statements where type_of_document like '%REIT%')
        """,
        con,
    )["code"]
    px = pd.read_sql(
        "select code, date, close, adj_factor, turnover from daily_prices", con, parse_dates=["date"]
    )
    px = px[px["code"].isin(set(eligible))]
    close = px.pivot(index="date", columns="code", values="close").sort_index()
    factor = px.pivot(index="date", columns="code", values="adj_factor").reindex(close.index).fillna(1.0)
    turnover = px.pivot(index="date", columns="code", values="turnover").reindex(close.index).fillna(0.0)
    # adj_factor は効力発生日の行に載る。t の価格には t より後の係数の積を掛ける
    later = np.exp(np.log(factor).iloc[::-1].cumsum().iloc[::-1].shift(-1).fillna(0.0))
    adj = close * later
    topix = pd.read_sql("select date, close from topix", con, parse_dates=["date"]).set_index("date")["close"]
    return adj, turnover, topix.reindex(close.index)


def check_adjustment(con: sqlite3.Connection, adj: pd.DataFrame) -> str:
    """分割・併合の効力日に、自前調整と DB の adj_close で日次 ±40% 超の飛びが残る件数を比べる。"""
    ev = pd.read_sql(
        "select code, date from daily_prices where adj_factor != 1", con, parse_dates=["date"]
    )
    ev = ev[ev["code"].isin(adj.columns)]
    codes = sorted(set(ev["code"]))
    db = pd.read_sql(
        f"select code, date, adj_close from daily_prices where code in ({','.join('?' * len(codes))})",
        con,
        params=codes,
        parse_dates=["date"],
    ).pivot(index="date", columns="code", values="adj_close")
    jumps = []
    for prices in (adj[codes], db.reindex(index=adj.index, columns=codes)):
        r = prices.ffill().pct_change(fill_method=None)
        rv = r.stack().rename("r").reset_index()
        hit = ev.merge(rv, on=["date", "code"])
        jumps.append(hit.loc[hit["r"].abs() > 0.4, "code"].tolist())
    return (
        f"調整検算: 分割・併合の効力日 {len(ev):,} 件で日次 ±40% 超の飛び — 自前調整 {len(jumps[0])} 件 "
        f"{jumps[0]}、DB の adj_close {len(jumps[1])} 件 {jumps[1]}"
    )


def month_end_decision_dates(dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
    me = list(pd.Series(dates, index=dates).groupby(dates.to_period("M")).max())
    if (me[-1] + pd.offsets.BDay(1)).month == me[-1].month:
        me = me[:-1]
    return me


def q1_strong_quintiles(form_ex: pd.Series):
    return pd.qcut(form_ex.rank(method="first", ascending=False), 5, labels=False) + 1


def newey_west_t(values: np.ndarray, hold_months: int = MONTHS) -> float:
    lags = hold_months - 1
    n = len(values)
    mean = values.mean()
    x = values - mean
    s = (x @ x) / n
    for k in range(1, lags + 1):
        s += 2 * (1 - k / (lags + 1)) * (x[k:] @ x[:-k]) / n
    return mean / np.sqrt(s / n)


def build_panel(
    adj: pd.DataFrame,
    turnover: pd.DataFrame,
    topix: pd.Series,
    min_turnover: float,
    form_months: int = MONTHS,
    skip_months: int = 0,
) -> pd.DataFrame:
    dates = adj.index
    me = month_end_decision_dates(dates)
    fresh = adj.ffill(limit=FORM_START_FFILL_BDAYS)
    last_px = adj.ffill()
    liq = turnover.rolling(TURNOVER_WINDOW, min_periods=TURNOVER_WINDOW).mean()

    rows = []
    for i in range(form_months, len(me) - MONTHS):
        d0, de, d, d1 = me[i - form_months], me[i - skip_months], me[i], me[i + MONTHS]
        p0, p, p1 = fresh.loc[d0], adj.loc[d], last_px.loc[d1]
        pe = p if skip_months == 0 else fresh.loc[de]
        ok = p0.notna() & pe.notna() & p.notna() & (liq.loc[d] >= min_turnover)
        codes = ok[ok].index
        tx_form = topix.loc[de] / topix.loc[d0] - 1
        tx_hold = topix.loc[d1] / topix.loc[d] - 1
        delisted = adj.loc[d1:, codes].isna().all()
        rows.append(
            pd.DataFrame(
                {
                    "date": d,
                    "code": codes,
                    "form_ex": pe[codes] / p0[codes] - 1 - tx_form,
                    "hold_ex": p1[codes] / p[codes] - 1 - tx_hold,
                    "turnover20": liq.loc[d, codes],
                    "delisted_in_hold": delisted.to_numpy(),
                }
            )
        )
    panel = pd.concat(rows, ignore_index=True)
    panel["q"] = panel.groupby("date")["form_ex"].transform(q1_strong_quintiles)
    return panel


def stats(monthly: pd.DataFrame) -> dict[str, float]:
    spread = monthly["spread"].to_numpy()
    n = len(spread)
    mean = spread.mean()
    sd = spread.std(ddof=1)
    return {
        "months": n,
        "mean": mean,
        "t": mean / (sd / np.sqrt(n)),
        "t_nw": newey_west_t(spread),
        "win": (spread > 0).mean(),
        "from": monthly.index.min().strftime("%Y-%m"),
        "to": monthly.index.max().strftime("%Y-%m"),
    }


def summarize(panel: pd.DataFrame, split: pd.Timestamp) -> tuple[pd.DataFrame, list[str]]:
    by_q = panel.groupby(["date", "q"])["hold_ex"].mean().unstack("q")
    monthly = by_q.copy()
    monthly.columns = [f"Q{int(c)}" for c in monthly.columns]
    monthly["spread"] = monthly["Q1"] - monthly["Q5"]
    monthly["n"] = panel.groupby("date").size()
    monthly["delisted"] = panel.groupby("date")["delisted_in_hold"].sum()

    periods = {
        "全期間": monthly,
        "分析期間": monthly[monthly.index < split],
        "確認期間": monthly[monthly.index >= split],
    }
    pct = lambda v: f"{v * 100:+.2f}%"  # noqa: E731
    out = ["## 分位ごとの保有期間（3 か月）平均超過リターン", ""]
    header = "| 分位 | " + " | ".join(f"{k} ({v.index.min():%Y-%m}〜{v.index.max():%Y-%m})" for k, v in periods.items()) + " |"
    out += [header, "|---" * (len(periods) + 1) + "|"]
    for q in ["Q1", "Q2", "Q3", "Q4", "Q5"]:
        label = {"Q1": "Q1（強い）", "Q5": "Q5（弱い）"}.get(q, q)
        out.append(f"| {label} | " + " | ".join(pct(v[q].mean()) for v in periods.values()) + " |")
    out += ["", "## Q1 − Q5", ""]
    out += ["| 指標 | " + " | ".join(periods) + " |", "|---" * (len(periods) + 1) + "|"]
    st = {k: stats(v) for k, v in periods.items()}
    out.append("| 判定月数 | " + " | ".join(str(s["months"]) for s in st.values()) + " |")
    out.append("| 平均 | " + " | ".join(pct(s["mean"]) for s in st.values()) + " |")
    out.append("| t 値（素朴） | " + " | ".join(f"{s['t']:+.2f}" for s in st.values()) + " |")
    out.append(f"| t 値（Newey-West, lag {MONTHS - 1}） | " + " | ".join(f"{s['t_nw']:+.2f}" for s in st.values()) + " |")
    out.append("| プラスの月の割合 | " + " | ".join(f"{s['win'] * 100:.0f}%" for s in st.values()) + " |")
    out.append(
        "| 1 判定日あたり銘柄数（平均） | " + " | ".join(f"{v['n'].mean():,.0f}" for v in periods.values()) + " |"
    )
    out.append("")
    return monthly, out


def plot_yearly(monthly: pd.DataFrame, split: pd.Timestamp, path: Path) -> pd.DataFrame:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["font.family"] = ["Noto Sans CJK JP", "sans-serif"]
    yearly = monthly.groupby(monthly.index.year)["spread"].agg(["mean", "count"])
    yearly["period"] = [
        "分析期間" if pd.Timestamp(f"{y}-12-31") < split else ("確認期間" if pd.Timestamp(f"{y}-01-01") >= split else "混在")
        for y in yearly.index
    ]
    colors = {"分析期間": "#2a78d6", "確認期間": "#eb6834", "混在": "#898781"}
    ink, muted, grid = "#0b0b0b", "#52514e", "#e4e3df"

    fig, ax = plt.subplots(figsize=(8, 4.5), dpi=150)
    x = np.arange(len(yearly))
    vals = yearly["mean"].to_numpy() * 100
    ax.bar(x, vals, width=0.5, color=[colors[p] for p in yearly["period"]], zorder=2)
    ax.axhline(0, color=muted, linewidth=1, zorder=3)
    for xi, v, n in zip(x, vals, yearly["count"]):
        ax.annotate(
            f"{v:+.1f}%\n(n={n})",
            (xi, v),
            xytext=(0, 4 if v >= 0 else -4),
            textcoords="offset points",
            ha="center",
            va="bottom" if v >= 0 else "top",
            fontsize=9,
            color=ink,
        )
    ax.set_xticks(x, [str(y) for y in yearly.index], color=ink)
    ax.set_ylabel("Q1 − Q5 の 3 か月超過リターン（%）", color=muted)
    ax.set_title("判定年ごとの Q1（強い）− Q5（弱い）平均", color=ink, loc="left")
    ax.grid(axis="y", color=grid, zorder=0)
    ax.tick_params(colors=muted)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(grid)
    lo, hi = min(vals.min(), 0), max(vals.max(), 0)
    pad = (hi - lo) * 0.25 or 1
    ax.set_ylim(lo - pad, hi + pad)
    from matplotlib.patches import Patch

    used = [p for p in colors if p in set(yearly["period"])]
    ax.legend(handles=[Patch(color=colors[p], label=p) for p in used], frameon=False, loc="upper right")
    fig.text(0.01, 0.01, "n = その年の判定月数。各月は 3 か月保有なので隣の月と期間が重なる", fontsize=8, color=muted)
    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(path)
    plt.close(fig)
    return yearly


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="stock.db")
    ap.add_argument("--out", default="/tmp/momentum_backtest")
    ap.add_argument(
        "--min-turnover",
        type=float,
        default=1e8,
        help="判定日までの 20 営業日平均売買代金の下限（円）。既定 1 億円",
    )
    ap.add_argument("--split", default="2024-01-01", help="この日以降の判定日を確認期間にする")
    ap.add_argument("--form-months", type=int, default=MONTHS, help="形成期間の月数。既定 3")
    ap.add_argument("--skip-months", type=int, default=0, help="形成期間の終わりを判定日から何か月前にするか。既定 0")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    split = pd.Timestamp(args.split)
    con = connect(args.db)

    lines = data_report(con)
    adj, turnover, topix = load_prices(con)
    lines.append(check_adjustment(con, adj))
    lines.append(f"形成期間: 判定日の {args.form_months} か月前 → {args.skip_months} か月前。保有期間: {MONTHS} か月")
    lines.append(f"売買代金フィルタ: 判定日までの {TURNOVER_WINDOW} 営業日平均売買代金 ≥ {args.min_turnover / 1e8:g} 億円")
    lines.append("")

    panel = build_panel(adj, turnover, topix, args.min_turnover, args.form_months, args.skip_months)
    lines.append(
        f"判定日: {panel['date'].min():%Y-%m-%d} 〜 {panel['date'].max():%Y-%m-%d}（{panel['date'].nunique()} 回）、"
        f"保有中に上場廃止 {int(panel['delisted_in_hold'].sum())} 件（最終終値で現金化扱い）"
    )
    lines.append("")
    monthly, table = summarize(panel, split)
    lines += table

    yearly = plot_yearly(monthly, split, out_dir / "spread_by_year.png")
    lines += ["## 年ごとの Q1 − Q5", "", "| 判定年 | 月数 | 平均 | 区分 |", "|---|---|---|---|"]
    for y, r in yearly.iterrows():
        lines.append(f"| {y} | {int(r['count'])} | {r['mean'] * 100:+.2f}% | {r['period']} |")
    lines.append("")

    monthly.to_csv(out_dir / "monthly_quintiles.csv", float_format="%.6f")
    panel.to_csv(out_dir / "panel.csv", index=False, float_format="%.6f")
    report = "\n".join(lines)
    (out_dir / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"出力: {out_dir}/report.md, spread_by_year.png, monthly_quintiles.csv, panel.csv")


if __name__ == "__main__":
    main()
