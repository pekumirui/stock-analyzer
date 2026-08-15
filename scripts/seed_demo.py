"""J-Quants不要のデモデータ投入 (合成株価4年+四半期決算5期)。"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal, init_db
from app.models import Stock, DailyPrice, Topix, Statement
from app.ingest import upsert
from app import analytics

rng = np.random.default_rng(42)

# (code, name, sector, 傾向: trend=年率ドリフト, vol)
# コードは必ず "0" 始まり: J-Quantsの実コード(4桁+末尾0)は1-9始まりなので絶対に衝突しない
COMPANIES = [
    ("00010", "デモ自動車", "輸送用機器", 0.45, 0.25),
    ("00020", "デモエレク", "電気機器", 0.60, 0.30),
    ("00030", "デモGHD", "情報・通信業", 0.35, 0.28),
    ("00040", "デモ化学", "化学", 0.50, 0.22),
    ("00050", "デモ製作所", "電気機器", 0.80, 0.35),
    ("00060", "デモ重工", "機械", 0.30, 0.25),
    ("00070", "デモファーマ", "医薬品", -0.30, 0.30),
    ("00080", "デモ銀行", "銀行業", 0.05, 0.15),
    ("00090", "デモ食品", "食料品", 0.02, 0.10),
    ("00100", "デモ精密", "精密機器", -0.45, 0.35),
    ("00110", "デモリテール", "小売業", 0.55, 0.28),
    ("00120", "デモネット", "サービス業", -0.15, 0.40),
    ("00130", "デモセンサー", "電気機器", 0.70, 0.30),
    ("00140", "デモゲームス", "情報・通信業", 0.40, 0.33),
    ("00150", "デモ鉄鋼", "鉄鋼", 0.00, 0.20),
]

START = date(2022, 4, 1)   # FY23.3 の開示日に株価がある必要がある (決算後リターンの基準日)
END = date(2026, 8, 14)

# J-Quants の財務値は円単位。デモも同じ単位で入れる (表示側の百万円換算と揃える)
YEN = 1_000_000

# 決算表示の分岐を検証するためのデモ専用フラグ
NO_SALES = {"00080"}       # 売上非開示 (銀行など)
NO_ORDINARY = {"00010"}    # IFRS: 経常利益が無い
LOSS_TURN = {"00070"}      # 赤字が続いた後に黒字転換する


def biz_days(a, b):
    d, out = a, []
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def main():
    init_db()
    db = SessionLocal()
    days = biz_days(START, END)
    n = len(days)

    upsert(db, Stock, [{"code": c, "name": nm, "sector17": sec, "sector33": sec,
                        "market": "プライム", "scale_category": "TOPIX Mid400",
                        "margin_type": "貸借"}
                       for c, nm, sec, _, _ in COMPANIES])

    # TOPIX: 緩やかな上昇
    tret = rng.normal(0.08 / 250, 0.011, n)
    tpx = 2500 * np.exp(np.cumsum(tret))
    upsert(db, Topix, [{"date": d, "open": round(p * 0.998, 2), "high": round(p * 1.005, 2),
                        "low": round(p * 0.994, 2), "close": round(p, 2)}
                       for d, p in zip(days, tpx)])

    stmt_rows = []
    for code, name, sec, drift, vol in COMPANIES:
        ret = rng.normal(drift / 250, vol / np.sqrt(250), n)
        px = rng.uniform(800, 6000) * np.exp(np.cumsum(ret))
        rows = []
        for d, p in zip(days, px):
            o = p * (1 + rng.normal(0, 0.004))
            h = max(o, p) * (1 + abs(rng.normal(0, 0.005)))
            l = min(o, p) * (1 - abs(rng.normal(0, 0.005)))
            v = max(1000, rng.normal(500_000, 150_000))
            rows.append({"code": code, "date": d,
                         "open": round(o, 1), "high": round(h, 1),
                         "low": round(l, 1), "close": round(p, 1),
                         "volume": round(v), "turnover": round(v * p),
                         "adj_factor": 1.0,
                         "adj_open": round(o, 1), "adj_high": round(h, 1),
                         "adj_low": round(l, 1), "adj_close": round(p, 1),
                         "adj_volume": round(v),
                         "market_cap": round(p * 50_000_000 / 1_000_000)})
        upsert(db, DailyPrice, rows)

        # 四半期決算: 3月期決算想定。売上成長は株価ドリフトに連動+ノイズ
        base_sales = rng.uniform(50_000, 300_000)  # 百万円
        opm = rng.uniform(0.06, 0.18)
        growth = max(-0.2, drift * 0.5) + 0.05
        for fy_end_year in range(2023, 2028):  # FY23.3〜FY27.3 (累計テーブルの過去4期+当期)
            fy_start = date(fy_end_year - 1, 4, 1)
            fy_end = date(fy_end_year, 3, 31)
            year_sales = base_sales * (1 + growth) ** (fy_end_year - 2025)
            cum = 0.0
            for qi, (m, dd) in enumerate([(5, 10), (8, 8), (11, 10), (2, 8)], start=1):
                # 発表日: 1Q=5月,2Q=8月,3Q=11月,FY=翌々年2月...簡略化: FYは5月に本決算
                yy = fy_start.year if m >= 4 else fy_start.year + 1
                ddate = date(yy, m, dd) + timedelta(days=int(rng.integers(0, 4)))
                while ddate.weekday() >= 5:
                    ddate += timedelta(days=1)
                if ddate > END:
                    continue
                period = ["1Q", "2Q", "3Q", "FY"][qi - 1]
                q_sales = year_sales / 4 * (1 + rng.normal(0, 0.06))
                cum += q_sales
                # 赤字継続→黒字転換のデモ (黒転/赤転バッジの検証用)
                sign = -1.0 if (code in LOSS_TURN and fy_end_year <= 2026) else 1.0
                q_op = cum * opm * (1 + rng.normal(0, 0.08)) * sign
                fy_fc_sales = year_sales * (1 + rng.normal(0.01, 0.02))
                fy_fc_op = fy_fc_sales * opm * sign
                # デモ: 8月開示の一部を上方修正に
                revise_boost = 1.10 if (m == 8 and rng.random() < 0.4) else 1.0
                no_sales, ifrs = code in NO_SALES, code in NO_ORDINARY
                yen = lambda v: None if v is None else round(v * YEN)
                stmt_rows.append({
                    "disclosure_no": f"{code}-{fy_end_year}-{period}",
                    "code": code, "disclosed_date": ddate,
                    "disclosed_time": "15:30:00" if rng.random() < 0.8 else "11:30:00",
                    "type_of_period": period,
                    "type_of_document": f"{period}FinancialStatements_Consolidated_"
                                        + ("IFRS" if ifrs else "JP"),
                    "period_start": fy_start, "period_end": fy_end,
                    "fiscal_year_start": fy_start, "fiscal_year_end": fy_end,
                    "net_sales": None if no_sales else yen(cum),
                    "operating_profit": yen(q_op),
                    "ordinary_profit": None if ifrs else yen(q_op * 1.02),
                    "profit": yen(q_op * 0.7),
                    "eps": round(q_op * 0.7 / 50, 1),
                    "f2_net_sales": yen(year_sales / 2) if qi < 2 and not no_sales else None,
                    "f2_operating_profit": yen(year_sales / 2 * opm * sign) if qi < 2 else None,
                    "f2_ordinary_profit": None, "f2_profit": None, "f2_eps": None,
                    "fy_net_sales": None if no_sales else yen(fy_fc_sales * revise_boost),
                    "fy_operating_profit": yen(fy_fc_op * revise_boost),
                    "fy_ordinary_profit": None if ifrs else yen(fy_fc_op * 1.02 * revise_boost),
                    "fy_profit": yen(fy_fc_op * 0.7 * revise_boost),
                    "fy_eps": round(fy_fc_op * 0.7 / 50, 1),
                    "is_revision": False,
                    "raw_json": json.dumps({"demo": True}),
                })
    upsert(db, Statement, stmt_rows)

    print("computing indicators / earnings events...")
    analytics.compute_indicators(db)
    analytics.compute_earnings_events(db)
    db.close()
    print(f"done: {len(COMPANIES)} stocks, {n} days, {len(stmt_rows)} statements")


if __name__ == "__main__":
    main()
