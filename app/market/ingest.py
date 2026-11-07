"""J-Quants V2 → DB 取込 (UPSERT・冪等)。

重要: 公式ベストプラクティスに従い「1銘柄ずつ×全日付」ではなく
「日付指定で全銘柄を1リクエスト」で取得する。
5年バックフィル = 営業日約1,220日 × (株価1 + 財務1) ≒ 2,450リクエスト。
"""
import json
from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .adjust import readjust
from .jquants import JQuantsClient
from ..db import upsert
from ..models import Stock, DailyPrice, Topix, Statement
from ..xbrl.statements import NUMERIC_COLUMNS as STATEMENT_NUMERIC_COLUMNS, SOURCE_JQUANTS, SOURCE_XBRL


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


def _flag(v) -> int | None:
    return None if v in (None, "") else int(str(v) == "1")


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
        "upper_limit": _flag(q.get("UL")), "lower_limit": _flag(q.get("LL")),
        "ex_rt": None if q.get("ExRT") in (None, "") else str(q["ExRT"]),
    }


def ingest_prices_for_date(
    db: Session, jq: JQuantsClient, day: str, *, commit: bool = True,
) -> int:
    """指定日の全上場銘柄の株価を1リクエストで取得。"""
    return len(_ingest_day(db, jq, day, commit=commit))


def _ingest_day(db: Session, jq: JQuantsClient, day: str, *, commit: bool = True) -> list[dict]:
    rows = [r for r in (_price_row(q) for q in jq.bars_daily(date=day)) if r]
    upsert(db, DailyPrice, rows, commit=commit)
    return rows


def refresh_adjusted(db: Session, rows: list[dict]) -> list[str]:
    """権利落ち日（AdjFactor ≠ 1。分割・併合・ライツイシュー）がある銘柄は、調整後の値を計算し直す。

    日付指定の差分取込では DB の過去分が古いまま残る。API で取り直すとプランの期間より前が
    取れないので、DB の中で計算する（app/market/adjust.py）。
    """
    codes = sorted({r["code"] for r in rows if r["adj_factor"] not in (None, 1.0)})
    readjust(db, codes)
    return codes


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


def upsert_statements(db: Session, rows: list[dict], *, commit: bool = True) -> None:
    """XBRL から作った行（source='xbrl'）は数値を上書きしない。raw_json と空の数値だけ J-Quants で埋める。"""
    rows = [{**r, "source": SOURCE_JQUANTS} for r in rows if r["disclosed_date"]]
    nos = [r["disclosure_no"] for r in rows]
    xbrl = {}
    for i in range(0, len(nos), 500):
        xbrl.update({s.disclosure_no: s for s in db.query(Statement).filter(
            Statement.disclosure_no.in_(nos[i:i + 500]), Statement.source == SOURCE_XBRL)})
    plain = []
    for r in rows:
        s = xbrl.get(r["disclosure_no"])
        if s is None:
            plain.append(r)
            continue
        s.raw_json = r["raw_json"]
        for col in STATEMENT_NUMERIC_COLUMNS:
            if getattr(s, col) is None and r[col] is not None:
                setattr(s, col, r[col])
    upsert(db, Statement, plain, commit=False)
    if commit:
        db.commit()


def ingest_statements_for_date(
    db: Session, jq: JQuantsClient, day: str, *, commit: bool = True,
) -> int:
    rows = [_statement_row(s) for s in jq.fins_summary(date=day)]
    rows = [r for r in rows if r["disclosed_date"]]
    upsert_statements(db, rows, commit=commit)
    return len(rows)


def ingest_statements_for_code(db: Session, jq: JQuantsClient, code: str) -> int:
    rows = [_statement_row(s) for s in jq.fins_summary(code=code)]
    rows = [r for r in rows if r["disclosed_date"]]
    upsert_statements(db, rows)
    return len(rows)


def _prev_trading_day(db: Session, day: str) -> date | None:
    """day より前で株価がある最後の日。カレンダーを引かずに前営業日とみなす。"""
    return db.query(func.max(DailyPrice.date)).filter(DailyPrice.date < _d(day)).scalar()


def run_daily(db: Session, day: str | None = None) -> int:
    """当日の全銘柄株価・財務・TOPIXと前営業日の財務を取得。マスタを入れて 5 リクエスト。

    前営業日の財務は 19:30 より後の開示を拾うための取り直し。
    発表予定はここではなく jobs/market/run_batch.py が指標の後に取る。
    返り値は株価・TOPIX・当日決算の件数と、前営業日に新しく増えた決算の件数の和。
    """
    day = day or date.today().strftime("%Y-%m-%d")
    prev = _prev_trading_day(db, day)
    with JQuantsClient() as jq:
        n_m = ingest_master(db, jq)
        px_rows = _ingest_day(db, jq, day)
        n_p = len(px_rows)
        adjusted = refresh_adjusted(db, px_rows)
        n_t = ingest_topix(db, jq, from_=day, to=day)
        n_s = ingest_statements_for_date(db, jq, day)
        n_late = 0
        if prev is not None:
            before = db.query(func.count()).filter(Statement.disclosed_date == prev).scalar()
            ingest_statements_for_date(db, jq, prev.isoformat())
            n_late = db.query(func.count()).filter(Statement.disclosed_date == prev).scalar() - before
    late = f" / {prev}の追加決算{n_late}" if prev is not None else ""
    adj = f" / 権利落ちで調整後を計算し直し {','.join(adjusted)}" if adjusted else ""
    print(f"{day}: 銘柄{n_m} / 株価{n_p} / TOPIX{n_t} / 決算{n_s}{late}{adj}")
    return n_p + n_t + n_s + n_late


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


def run_refetch_prices(db: Session, progress_every: int = 20) -> int:
    """DB にある日足を日付ごとに全部取り直す。返り値は取り直せなかった日数。

    たまった調整後株価のずれと、UL / LL が無い過去の行を埋める。取り直した行は UL / LL が
    0 か 1 になるので、NULL の行が残る日だけを対象にする（止めても再実行で続きから）。
    1日ごとに commit する。失敗した日は飛ばして最後に数を返す。
    """
    days = [d for (d,) in db.query(DailyPrice.date)
            .filter(DailyPrice.upper_limit.is_(None), DailyPrice.lower_limit.is_(None))
            .distinct().order_by(DailyPrice.date).all()]
    print(f"取り直す日 {len(days)}日分 (推定 {len(days) / 60:.0f}分)")
    failed = []
    with JQuantsClient() as jq:
        for i, d in enumerate(days, 1):
            try:
                n = ingest_prices_for_date(db, jq, d.isoformat())
            except Exception as exc:
                db.rollback()
                print(f"fail refetch {d}: {exc}")
                failed.append(d)
                continue
            if i % progress_every == 0 or i == len(days):
                print(f"  [{i}/{len(days)} {i / len(days) * 100:.0f}%] {d}  株価{n}")
    print(f"取り直し完了 failed={len(failed)}")
    return len(failed)
