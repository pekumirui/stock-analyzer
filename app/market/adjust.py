"""調整後株価を DB の調整前の値と AdjFactor から計算する。

J-Quants は権利落ち（分割・併合・ライツイシュー）のたびに過去の調整後の値をさかのぼって直すが、
API は契約プランの期間より前を返さない。調整前の値（O/H/L/C/Vo）は後から変わらないので、
その日より新しい AdjFactor を掛け合わせて自前で出す。手順は J-Quants の「調整済み株価の計算方法」:
途中で丸めず、最後だけ小数第 2 位を四捨五入する。出来高はライツイシュー（ExRT=3）の係数を使わない。
こうすると J-Quants の配信値と一致する。
"""
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..models import DailyPrice

RIGHTS_ISSUE = "3"
_PRICE = (("open", "adj_open"), ("high", "adj_high"), ("low", "adj_low"), ("close", "adj_close"))
_UPDATE = text(
    "UPDATE daily_prices SET adj_open = :adj_open, adj_high = :adj_high, adj_low = :adj_low, "
    "adj_close = :adj_close, adj_volume = :adj_volume WHERE code = :code AND date = :date")


def _r1(x: float | None) -> float | None:
    if x is None:
        return None
    return float(Decimal(repr(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def adjusted_rows(rows: list) -> list[dict]:
    """1 銘柄の日足（新しい順）から、調整後の値が今と違う行の更新を返す。"""
    cum = cum_vol = 1.0
    out = []
    for r in rows:
        new = {adj: _r1(None if getattr(r, raw) is None else getattr(r, raw) * cum)
               for raw, adj in _PRICE}
        new["adj_volume"] = _r1(None if r.volume is None else r.volume / cum_vol)
        if r.close is not None and any(getattr(r, k) != v for k, v in new.items()):
            # SQLAlchemy の Date は SQLite に ISO 文字列で入っている
            out.append({"code": r.code, "date": r.date.isoformat(), **new})
        factor = r.adj_factor or 1.0
        cum *= factor
        if r.ex_rt != RIGHTS_ISSUE:
            cum_vol *= factor
    return out


def readjust(db: Session, codes: list[str] | None = None) -> int:
    """codes（省略で全銘柄）の調整後の値を計算し直す。返り値は書き換えた行数。銘柄ごとに commit。"""
    if codes is None:
        codes = [c for (c,) in db.query(DailyPrice.code).distinct().order_by(DailyPrice.code)]
    n = 0
    for code in codes:
        rows = (db.query(DailyPrice.code, DailyPrice.date, DailyPrice.open, DailyPrice.high,
                         DailyPrice.low, DailyPrice.close, DailyPrice.volume, DailyPrice.adj_factor,
                         DailyPrice.ex_rt, DailyPrice.adj_open, DailyPrice.adj_high, DailyPrice.adj_low,
                         DailyPrice.adj_close, DailyPrice.adj_volume)
                .filter(DailyPrice.code == code).order_by(DailyPrice.date.desc()).all())
        upd = adjusted_rows(rows)
        if upd:
            db.execute(_UPDATE, upd)
            db.commit()
            n += len(upd)
    return n
