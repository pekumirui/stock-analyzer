"""J-Quants V2 → DB 取込 (UPSERT・冪等)。

重要: 公式ベストプラクティスに従い「1銘柄ずつ×全日付」ではなく
「日付指定で全銘柄を1リクエスト」で取得する。
5年バックフィル = 営業日約1,220日 × (株価1 + 財務1) ≒ 2,450リクエスト。
"""
import json
from datetime import date, datetime, timedelta

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .jquants import JQuantsClient
from .models import Stock, DailyPrice, Topix, Statement


def _d(s):
    if not s:
        return None
    s = str(s).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _f(v):
    """V2の数値項目は文字列で返ることがあり、欠損は空文字。"""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    if s in ("", "-", "－"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def upsert(db: Session, model, rows: list[dict], *, commit: bool = True) -> None:
    if not rows:
        return
    keys = [c.name for c in model.__table__.primary_key.columns]
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        stmt = sqlite_insert(model).values(chunk)
        upd = {c: stmt.excluded[c] for c in chunk[0] if c not in keys}
        db.execute(stmt.on_conflict_do_update(index_elements=keys, set_=upd))
    if commit:
        db.commit()


def ingest_master(db: Session, jq: JQuantsClient, day: str | None = None):
    rows = [
        {"code": r["Code"], "name": r.get("CoName"),
         "sector17": r.get("S17Nm"), "sector33": r.get("S33Nm"),
         "market": r.get("MktNm"), "scale_category": r.get("ScaleCat"),
         "margin_type": r.get("MrgnNm"),
         "product_category": str(r.get("ProdCat") or "").strip() or None}
        for r in jq.equities_master(date=day)
    ]
    upsert(db, Stock, rows)
    return len(rows)


def _price_row(q: dict) -> dict | None:
    if q.get("C") is None and q.get("AdjC") is None:
        return None  # 売買停止日など
    return {
        "code": q["Code"], "date": _d(q["Date"]),
        "open": _f(q.get("O")), "high": _f(q.get("H")),
        "low": _f(q.get("L")), "close": _f(q.get("C")),
        "volume": _f(q.get("Vo")), "turnover": _f(q.get("Va")),
        "adj_factor": _f(q.get("AdjFactor")) or 1.0,
        "adj_open": _f(q.get("AdjO")), "adj_high": _f(q.get("AdjH")),
        "adj_low": _f(q.get("AdjL")), "adj_close": _f(q.get("AdjC")),
        "adj_volume": _f(q.get("AdjVo")),
        "market_cap": _f(q.get("MktCap")),
    }


def ingest_prices_for_date(
    db: Session, jq: JQuantsClient, day: str, *, commit: bool = True,
) -> int:
    """指定日の全上場銘柄の株価を1リクエストで取得。"""
    rows = [r for r in (_price_row(q) for q in jq.bars_daily(date=day)) if r]
    upsert(db, DailyPrice, rows, commit=commit)
    return len(rows)


def ingest_prices_for_code(db: Session, jq: JQuantsClient, code: str,
                           from_: str | None = None, to: str | None = None) -> int:
    """1銘柄の期間データ (個別銘柄の穴埋め用)。"""
    rows = [r for r in (_price_row(q) for q in jq.bars_daily(code=code, from_=from_, to=to)) if r]
    upsert(db, DailyPrice, rows)
    return len(rows)


def ingest_topix(db: Session, jq: JQuantsClient, from_: str | None = None, to: str | None = None) -> int:
    rows = [
        {"date": _d(t["Date"]), "open": _f(t.get("O")), "high": _f(t.get("H")),
         "low": _f(t.get("L")), "close": _f(t.get("C"))}
        for t in jq.topix(from_=from_, to=to)
    ]
    rows = [r for r in rows if r["date"]]
    upsert(db, Topix, rows)
    return len(rows)


def _statement_row(s: dict) -> dict:
    doc = s.get("DocType", "") or ""
    return {
        "disclosure_no": str(s["DiscNo"]),
        "code": s["Code"],
        "disclosed_date": _d(s.get("DiscDate")),
        "disclosed_time": s.get("DiscTime"),
        "type_of_period": s.get("CurPerType"),
        "type_of_document": doc,
        "period_start": _d(s.get("CurPerSt")),
        "period_end": _d(s.get("CurPerEn")),
        "fiscal_year_start": _d(s.get("CurFYSt")),
        "fiscal_year_end": _d(s.get("CurFYEn")),
        "net_sales": _f(s.get("Sales")),
        "operating_profit": _f(s.get("OP")),
        "ordinary_profit": _f(s.get("OdP")),   # IFRS/USGAAPは空欄
        "profit": _f(s.get("NP")),
        "eps": _f(s.get("EPS")),
        "f2_net_sales": _f(s.get("FSales2Q")),
        "f2_operating_profit": _f(s.get("FOP2Q")),
        "f2_ordinary_profit": _f(s.get("FOdP2Q")),
        "f2_profit": _f(s.get("FNP2Q")),
        "f2_eps": _f(s.get("FEPS2Q")),
        "fy_net_sales": _f(s.get("FSales")),
        "fy_operating_profit": _f(s.get("FOP")),
        "fy_ordinary_profit": _f(s.get("FOdP")),
        "fy_profit": _f(s.get("FNP")),
        "fy_eps": _f(s.get("FEPS")),
        # 業績予想の修正のみの開示 (決算短信本体ではない)
        "is_revision": ("Revision" in doc or "Forecast" in doc)
                       and "FinancialStatements" not in doc,
        "raw_json": json.dumps(s, ensure_ascii=False),
    }


def ingest_statements_for_date(
    db: Session, jq: JQuantsClient, day: str, *, commit: bool = True,
) -> int:
    rows = [_statement_row(s) for s in jq.fins_summary(date=day)]
    rows = [r for r in rows if r["disclosed_date"]]
    upsert(db, Statement, rows, commit=commit)
    return len(rows)


def ingest_statements_for_code(db: Session, jq: JQuantsClient, code: str) -> int:
    rows = [_statement_row(s) for s in jq.fins_summary(code=code)]
    rows = [r for r in rows if r["disclosed_date"]]
    upsert(db, Statement, rows)
    return len(rows)


def run_daily(db: Session, day: str | None = None):
    """日次バッチ: 当日の全銘柄株価・財務・TOPIXを取得 (計4リクエスト)。"""
    day = day or date.today().strftime("%Y-%m-%d")
    with JQuantsClient() as jq:
        n_m = ingest_master(db, jq)
        n_p = ingest_prices_for_date(db, jq, day)
        n_t = ingest_topix(db, jq, from_=day, to=day)
        n_s = ingest_statements_for_date(db, jq, day)
    print(f"{day}: 銘柄{n_m} / 株価{n_p} / TOPIX{n_t} / 決算{n_s}")


class BackfillError(RuntimeError):
    def __init__(self, code: str, message: str, *, failed_day: str | None = None) -> None:
        self.code = code
        self.failed_day = failed_day
        super().__init__(message)


def run_backfill(db: Session, years: int = 5, progress_every: int = 20) -> None:
    """初回バックフィル。営業日ごとに全銘柄を1リクエストで取得。

    Lightプラン(60req/分)で5年分 ≒ 2,450リクエスト ≒ 45分。
    UPSERTなので途中で止めても再実行すれば埋まる。
    1日は株価と決算をまとめて commit する。失敗した日は rollback して中断する。
    """
    start_d = date.today() - timedelta(days=int(365.25 * years))
    start = start_d.strftime("%Y-%m-%d")
    today = date.today().strftime("%Y-%m-%d")

    with JQuantsClient() as jq:
        print("銘柄マスタを取得中...")
        ingest_master(db, jq)

        print("TOPIXを取得中...")
        n = ingest_topix(db, jq, from_=start, to=today)
        print(f"  TOPIX {n}件")

        print("取引カレンダーを取得中...")
        try:
            days = jq.trading_days(start, today)
        except RuntimeError as exc:
            raise BackfillError(
                "CALENDAR_EMPTY" if "CALENDAR_EMPTY" in str(exc) else "BACKFILL_INCOMPLETE",
                str(exc),
            ) from exc
        if not days:
            raise BackfillError(
                "BACKFILL_INCOMPLETE",
                "CALENDAR_EMPTY: trading day list is empty",
            )
        print(f"  営業日 {len(days)}日分を取得します (推定 {len(days) * 2 / 60:.0f}分)")

        done = {d for (d,) in db.query(DailyPrice.date).distinct().all()}
        todo = [d for d in days if _d(d) not in done]
        print(f"  未取得: {len(todo)}日分")

        for i, d in enumerate(todo, 1):
            try:
                n_p = ingest_prices_for_date(db, jq, d, commit=False)
                n_s = ingest_statements_for_date(db, jq, d, commit=False)
                db.commit()
            except Exception as exc:
                db.rollback()
                raise BackfillError(
                    "BACKFILL_INCOMPLETE",
                    f"{d} の取込に失敗しました: {exc}",
                    failed_day=d,
                ) from exc
            if i % progress_every == 0 or i == len(todo):
                pct = i / len(todo) * 100
                print(f"  [{i}/{len(todo)} {pct:.0f}%] {d}  株価{n_p} 決算{n_s}")
    print("バックフィル完了")
