"""大量保有の保有者画面と銘柄の株主タブ（KAB-267 / KAB-272）。読み取りのみ。"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import or_, text
from sqlalchemy.orm import Session

from .config import STOCK_PRODUCT_CATEGORIES
from .models import (
    EdinetBuyback,
    EdinetBuybackResolution,
    LargeHolding,
    LargeHoldingReport,
    Stock,
)

BELOW_RATIO = 0.05
HISTORY_YEARS = 5
SEARCH_LIMIT = 20
ACTIVITY_DEFAULT = 80
ACTIVITY_MAX = 1000
SEARCH_ACTIVITY_MAX = 200
DAY_FETCH_CAP = 2000
INITIAL_FORMS = {"010000", "030000"}
_EDINET_PREFIX = "E"
JST = ZoneInfo("Asia/Tokyo")
_CODE_Q_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
_EDINET_PREFIX_RE = re.compile(r"^E[0-9]{1,5}$")


def purpose_line(text: str | None) -> str | None:
    if not text:
        return None
    line = text.replace("\r\n", "\n").replace("\r", "\n").split("\n", 1)[0].strip()
    return line or None


def parse_edinet(raw: str | None) -> str | None:
    s = (raw or "").strip().upper()
    if len(s) == 6 and s[0] == _EDINET_PREFIX and s[1:].isdigit():
        return s
    return None


def _ok_report():
    return (
        LargeHoldingReport.is_latest.is_(True),
        LargeHoldingReport.withdrawn.is_(False),
        LargeHoldingReport.parse_status == "parsed",
    )


def _stock_issuer(db: Session):
    if db.query(Stock.code).filter(Stock.product_category.in_(STOCK_PRODUCT_CATEGORIES)).first() is None:
        return ()
    return (or_(Stock.code.is_(None), Stock.product_category.in_(STOCK_PRODUCT_CATEGORIES)),)


def _has_holder_code():
    return (
        LargeHolding.edinet_code.isnot(None),
        LargeHolding.edinet_code != "",
    )


def classify_move(ratio: float | None, prev: float | None, form_code: str | None) -> str | None:
    if ratio is None:
        return None
    form = form_code or ""
    if prev is not None and prev >= BELOW_RATIO and ratio < BELOW_RATIO:
        return "below"
    initial = form in INITIAL_FORMS or prev is None
    if initial and (ratio >= BELOW_RATIO or form in INITIAL_FORMS):
        return "new"
    if prev is None:
        return None
    if ratio > prev:
        return "buy"
    if ratio < prev:
        return "sell"
    return None


_DT_MIN = datetime(1, 1, 1)


def _report_key(file_date: date | None, submitted: datetime | None, obligation: date | None):
    return (
        file_date or date.min,
        submitted or _DT_MIN,
        obligation or date.min,
    )


def today_jst() -> date:
    return datetime.now(JST).date()


def _cutoff(today: date) -> date:
    try:
        return today.replace(year=today.year - HISTORY_YEARS)
    except ValueError:
        return today.replace(year=today.year - HISTORY_YEARS, day=28)


def _fold_q(q: str) -> list[str]:
    folded = unicodedata.normalize("NFKC", q)
    kata = "".join(chr(ord(ch) + 0x60) if "\u3041" <= ch <= "\u3096" else ch for ch in folded)
    out = [q]
    if folded != q:
        out.append(folded)
    if kata != folded:
        out.append(kata)
    return list(dict.fromkeys(s for s in out if s))


def is_activity_search(q: str) -> bool:
    raw = (q or "").strip()
    if not raw:
        return False
    if parse_edinet(raw):
        return True
    folded = unicodedata.normalize("NFKC", raw).strip().upper()
    if _CODE_Q_RE.fullmatch(folded) or _EDINET_PREFIX_RE.fullmatch(folded):
        return True
    return len(raw) >= 2


def _activity_search_filter(raw: str):
    """保有者名・EDINET・発行者名・銘柄コードの OR。日付は掛けない。"""
    conds: list[Any] = []
    folded_upper = unicodedata.normalize("NFKC", raw).strip().upper()
    edinet = parse_edinet(raw)
    if edinet:
        conds.append(LargeHolding.edinet_code == edinet)
    elif _EDINET_PREFIX_RE.fullmatch(folded_upper):
        conds.append(LargeHolding.edinet_code.like(f"{_like_escape(folded_upper)}%", escape="\\"))
    if _CODE_Q_RE.fullmatch(folded_upper):
        if len(folded_upper) == 4:
            conds.append(LargeHoldingReport.code.like(f"{_like_escape(folded_upper)}%", escape="\\"))
        else:
            conds.append(LargeHoldingReport.code == folded_upper)
    for term in _fold_q(raw):
        like = f"%{_like_escape(term)}%"
        conds.append(LargeHolding.name.like(like, escape="\\"))
        conds.append(LargeHoldingReport.issuer_name.like(like, escape="\\"))
        conds.append(Stock.name.like(like, escape="\\"))
    return or_(*conds) if conds else None


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _codes_union_sql(codes: list[str], params: dict[str, str], *, prefix: str = "c") -> str:
    selects = []
    for j, code in enumerate(codes):
        key = f"{prefix}{j}"
        selects.append(f"SELECT :{key} AS code")
        params[key] = code
    return " UNION ALL ".join(selects)


def _latest_prices(db: Session, codes: list[str]) -> dict[str, tuple[date, float, float | None]]:
    """銘柄ごとの最新終値。(code, date) PK への相関 MAX。IN + GROUP BY は日足全件を読む。"""
    codes = [c for c in dict.fromkeys(codes) if c]
    out: dict[str, tuple[date, float, float | None]] = {}
    if not codes:
        return out
    for i in range(0, len(codes), 80):
        chunk = codes[i:i + 80]
        params: dict[str, str] = {}
        sql = text(
            "SELECT a.code, p.date, p.close, p.adj_factor FROM ("
            + _codes_union_sql(chunk, params)
            + ") a JOIN daily_prices p ON p.code = a.code AND p.date = ("
            "SELECT MAX(p2.date) FROM daily_prices p2 "
            "WHERE p2.code = a.code AND p2.close IS NOT NULL)"
        )
        for code, d, close, adj in db.execute(sql, params):
            if isinstance(d, str):
                d = date.fromisoformat(d[:10])
            out[code] = (d, close, adj)
    return out


def _adj_asof(db: Session, pairs: list[tuple[str, date]]) -> dict[tuple[str, date], float | None]:
    uniq = [(c, d) for c, d in dict.fromkeys(pairs) if c and d]
    out: dict[tuple[str, date], float | None] = dict.fromkeys(uniq)
    if not uniq:
        return out
    for i in range(0, len(uniq), 80):
        chunk = uniq[i:i + 80]
        selects = []
        params: dict[str, str] = {}
        for j, (code, asof) in enumerate(chunk):
            selects.append(f"SELECT :c{j} AS code, date(:d{j}) AS asof")
            params[f"c{j}"] = code
            params[f"d{j}"] = asof.isoformat()
        sql = text(
            "SELECT a.code, a.asof, p.adj_factor FROM (" + " UNION ALL ".join(selects) + ") a "
            "LEFT JOIN daily_prices p ON p.code = a.code AND p.date = ("
            "SELECT MAX(p2.date) FROM daily_prices p2 "
            "WHERE p2.code = a.code AND p2.date <= a.asof)"
        )
        for code, asof, adj in db.execute(sql, params):
            if isinstance(asof, str):
                asof = date.fromisoformat(asof[:10])
            out[(code, asof)] = adj
    return out


def _estimates(
    shares: int | None,
    total_funds: int | None,
    close: float | None,
    adj_now: float | None,
    adj_then: float | None,
) -> tuple[float | None, float | None, float | None]:
    """時価、平均取得単価、評価損益。出せないものは None。"""
    mv = close * shares if close and close > 0 and shares and shares > 0 else None
    if not shares or shares <= 0 or not total_funds or total_funds <= 0:
        return mv, None, None
    if close is None or close <= 0:
        return mv, None, None
    if adj_now is None or adj_then is None or adj_then == 0:
        return mv, None, None
    if abs(adj_now / adj_then - 1) > 1e-6:
        return mv, None, None
    avg = total_funds / shares
    if avg <= 0:
        return mv, None, None
    return mv, avg, (close - avg) * shares


def search_holders(db: Session, q: str) -> dict[str, Any]:
    raw = (q or "").strip()
    edinet = parse_edinet(raw)
    if not raw or (len(raw) < 2 and not edinet):
        return {"q": raw, "results": []}
    conds = []
    if edinet:
        conds.append(LargeHolding.edinet_code == edinet)
    else:
        like = f"%{_like_escape(raw)}%"
        prefix = f"{_like_escape(raw.upper())}%"
        conds.append(LargeHolding.name.like(like, escape="\\"))
        conds.append(LargeHolding.edinet_code.like(prefix, escape="\\"))
    rows = (
        db.query(
            LargeHolding.edinet_code, LargeHolding.name,
            LargeHoldingReport.file_date, LargeHoldingReport.submit_datetime,
        )
        .join(LargeHoldingReport, LargeHoldingReport.doc_id == LargeHolding.doc_id)
        .outerjoin(Stock, Stock.code == LargeHoldingReport.code)
        .filter(*_ok_report(), *_has_holder_code(), *_stock_issuer(db), or_(*conds))
        .order_by(LargeHoldingReport.file_date.desc(), LargeHoldingReport.submit_datetime.desc())
        .limit(400)
        .all()
    )
    seen: dict[str, str] = {}
    for code, name, _fd, _st in rows:
        if code in seen:
            continue
        seen[code] = name or code
        if len(seen) >= SEARCH_LIMIT:
            break
    return {"q": raw, "results": [{"edinet_code": c, "name": n} for c, n in seen.items()]}


def _activity_move_sql():
    """割合が変わらない変更報告（住所変更など）を SQL で落とす。classify_move と同じ条件。"""
    return (
        LargeHolding.holding_ratio.isnot(None),
        or_(
            LargeHoldingReport.form_code.in_(tuple(INITIAL_FORMS)),
            LargeHolding.holding_ratio_prev.is_(None),
            LargeHolding.holding_ratio != LargeHolding.holding_ratio_prev,
        ),
    )


def _activity_item(h: LargeHolding, r: LargeHoldingReport, stocks: dict[str, Stock]) -> dict[str, Any] | None:
    kind = classify_move(h.holding_ratio, h.holding_ratio_prev, r.form_code)
    if kind is None:
        return None
    st = stocks.get(r.code) if r.code else None
    return {
        "kind": kind,
        "edinet_code": h.edinet_code,
        "holder_name": h.name,
        "code": r.code,
        "issuer_name": (st.name if st and st.name else None) or r.issuer_name,
        "market": st.market if st else None,
        "sector33": st.sector33 if st else None,
        "obligation_date": r.obligation_date.isoformat() if r.obligation_date else None,
        "file_date": r.file_date.isoformat() if r.file_date else None,
        "holding_ratio": h.holding_ratio,
        "holding_ratio_prev": h.holding_ratio_prev,
        "shares_held": h.shares_held,
    }


def activity_feed(
    db: Session,
    *,
    limit: int = ACTIVITY_DEFAULT,
    offset: int = 0,
    file_date: date | None = None,
    q: str = "",
) -> dict[str, Any]:
    raw = (q or "").strip()
    searching = is_activity_search(raw)
    max_n = SEARCH_ACTIVITY_MAX if searching else ACTIVITY_MAX
    n = max(1, min(int(limit or ACTIVITY_DEFAULT), max_n))
    off = max(0, int(offset or 0))
    filters = [*_ok_report(), *_has_holder_code(), *_activity_move_sql(), *_stock_issuer(db)]
    day: date | None = None
    if searching:
        search_f = _activity_search_filter(raw)
        if search_f is not None:
            filters.append(search_f)
        fetch_n = min(DAY_FETCH_CAP, (n + off) * 4 + 1)
    else:
        day = file_date or today_jst()
        # ix_large_holding_reports_file_date / ix_large_holding_reports_latest_file
        filters.append(LargeHoldingReport.file_date == day)
        fetch_n = DAY_FETCH_CAP
    rows = (
        db.query(LargeHolding, LargeHoldingReport, Stock)
        .join(LargeHoldingReport, LargeHoldingReport.doc_id == LargeHolding.doc_id)
        .outerjoin(Stock, Stock.code == LargeHoldingReport.code)
        .filter(*filters)
        .order_by(
            LargeHoldingReport.file_date.desc(),
            LargeHoldingReport.submit_datetime.desc(),
            LargeHolding.doc_id.desc(),
            LargeHolding.holder_no,
        )
        .limit(fetch_n)
        .all()
    )
    stocks = {st.code: st for _h, _r, st in rows if st is not None}
    classified: list[dict[str, Any]] = []
    for h, r, _st in rows:
        item = _activity_item(h, r, stocks)
        if item is None:
            continue
        classified.append(item)
    sliced = classified[off:off + n]
    has_more = len(classified) > off + n or (searching and len(rows) >= fetch_n)
    return {
        "items": sliced,
        "file_date": None if searching else (day.isoformat() if day else None),
        "q": raw if searching else "",
        "has_more": has_more,
    }


def _history_ok(r: LargeHoldingReport, cutoff: date) -> bool:
    if r.obligation_date and r.obligation_date >= cutoff:
        return True
    return bool(r.file_date and r.file_date >= cutoff)


def holder_page(db: Session, edinet: str, *, today: date | None = None) -> dict[str, Any] | None:
    code = parse_edinet(edinet)
    if code is None:
        return None
    day = today or today_jst()
    cutoff = _cutoff(day)
    rows = (
        db.query(LargeHolding, LargeHoldingReport, Stock)
        .join(LargeHoldingReport, LargeHoldingReport.doc_id == LargeHolding.doc_id)
        .outerjoin(Stock, Stock.code == LargeHoldingReport.code)
        .filter(*_ok_report(), *_stock_issuer(db), LargeHolding.edinet_code == code)
        .all()
    )
    if not rows:
        return None
    by_issuer: dict[str, list[tuple[LargeHolding, LargeHoldingReport, Stock | None]]] = {}
    for h, r, st in rows:
        key = r.code or r.issuer_edinet_code or r.issuer_name or r.doc_id
        by_issuer.setdefault(key, []).append((h, r, st))
    latest_pairs = []
    for group in by_issuer.values():
        group.sort(key=lambda x: _report_key(x[1].file_date, x[1].submit_datetime, x[1].obligation_date))
        latest_pairs.append(group[-1])
    latest_pairs.sort(
        key=lambda x: (
            (x[0].holding_ratio or 0) < BELOW_RATIO,
            -(x[0].holding_ratio or 0),
            x[1].code or "",
        )
    )
    codes = [r.code for _h, r, _st in latest_pairs if r.code]
    prices = _latest_prices(db, codes)
    adj_then = _adj_asof(db, [(r.code, r.obligation_date) for _h, r, _st in latest_pairs if r.code and r.obligation_date])
    newest = max(latest_pairs, key=lambda x: _report_key(x[1].file_date, x[1].submit_datetime, x[1].obligation_date))
    name = newest[0].name or code
    holdings = []
    for h, r, st in latest_pairs:
        px = prices.get(r.code) if r.code else None
        close = px[1] if px else None
        adj_now = px[2] if px else None
        then = adj_then.get((r.code, r.obligation_date)) if r.code and r.obligation_date else None
        mv, avg, unreal = _estimates(h.shares_held, h.total_funds, close, adj_now, then)
        hist = []
        key = r.code or r.issuer_edinet_code or r.issuer_name or r.doc_id
        for hh, rr, _st in by_issuer[key]:
            if not _history_ok(rr, cutoff):
                continue
            hist.append({
                "date": rr.obligation_date.isoformat() if rr.obligation_date else None,
                "shares": hh.shares_held,
                "ratio": hh.holding_ratio,
                "file_date": rr.file_date.isoformat() if rr.file_date else None,
            })
        hist.sort(key=lambda p: (p["date"] is None, p["date"] or "", p["file_date"] or ""))
        holdings.append({
            "code": r.code,
            "name": (st.name if st and st.name else None) or r.issuer_name,
            "market": st.market if st else None,
            "sector33": st.sector33 if st else None,
            "shares_held": h.shares_held,
            "holding_ratio": h.holding_ratio,
            "holding_ratio_prev": h.holding_ratio_prev,
            "obligation_date": r.obligation_date.isoformat() if r.obligation_date else None,
            "file_date": r.file_date.isoformat() if r.file_date else None,
            "purpose": purpose_line(h.purpose),
            "price": close,
            "price_date": px[0].isoformat() if px else None,
            "market_value": mv,
            "avg_cost": avg,
            "unrealized": unreal,
            "history": hist,
        })
    activity_rows = sorted(
        rows,
        key=lambda x: _report_key(x[1].file_date, x[1].submit_datetime, x[1].obligation_date),
        reverse=True,
    )
    stocks = {st.code: st for _h, _r, st in rows if st is not None}
    activity = []
    for h, r, _st in activity_rows:
        item = _activity_item(h, r, stocks)
        if item is None:
            continue
        activity.append(item)
        if len(activity) >= ACTIVITY_DEFAULT:
            break
    return {"edinet_code": code, "name": name, "holdings": holdings, "activity": activity}


def _filer_key(r: LargeHoldingReport) -> str:
    return r.filer_edinet_code or r.filer_name or r.doc_id


def _purpose_for_report(holders: list[LargeHolding], r: LargeHoldingReport) -> str | None:
    if not holders:
        return None
    for h in holders:
        if r.filer_edinet_code and h.edinet_code == r.filer_edinet_code:
            return purpose_line(h.purpose)
    holders_sorted = sorted(holders, key=lambda x: x.holder_no or 0)
    return purpose_line(holders_sorted[0].purpose)


def stock_holders(db: Session, code5: str, *, today: date | None = None) -> dict[str, Any]:
    day = today or today_jst()
    cutoff = _cutoff(day)
    reports = (
        db.query(LargeHoldingReport)
        .filter(LargeHoldingReport.code == code5, *_ok_report())
        .all()
    )
    by_filer: dict[str, list[LargeHoldingReport]] = {}
    for r in reports:
        by_filer.setdefault(_filer_key(r), []).append(r)
    latest: list[LargeHoldingReport] = []
    for group in by_filer.values():
        group.sort(key=lambda r: _report_key(r.file_date, r.submit_datetime, r.obligation_date))
        latest.append(group[-1])
    latest.sort(key=lambda r: ((r.holding_ratio or 0) < BELOW_RATIO, -(r.holding_ratio or 0), r.filer_name or ""))
    doc_ids = {r.doc_id for r in latest}
    people: dict[str, list[LargeHolding]] = {}
    if doc_ids:
        for h in db.query(LargeHolding).filter(LargeHolding.doc_id.in_(doc_ids)).all():
            people.setdefault(h.doc_id, []).append(h)
    large = []
    for r in latest[:80]:
        hist = []
        for rr in sorted(by_filer[_filer_key(r)], key=lambda x: x.obligation_date or date.min):
            if not _history_ok(rr, cutoff):
                continue
            hist.append({
                "date": rr.obligation_date.isoformat() if rr.obligation_date else None,
                "ratio": rr.holding_ratio,
            })
        hist.sort(key=lambda p: (p["date"] is None, p["date"] or ""))
        large.append({
            "filer_edinet_code": r.filer_edinet_code,
            "filer_name": r.filer_name,
            "holding_ratio": r.holding_ratio,
            "holding_ratio_prev": r.holding_ratio_prev,
            "obligation_date": r.obligation_date.isoformat() if r.obligation_date else None,
            "file_date": r.file_date.isoformat() if r.file_date else None,
            "purpose": _purpose_for_report(people.get(r.doc_id, []), r),
            "history": hist,
        })
    return {"code": code5, "large_holders": large, "treasury": stock_treasury(db, code5, today=day)}


def stock_treasury(db: Session, code5: str, *, today: date | None = None) -> dict[str, Any] | None:
    day = today or today_jst()
    row = (
        db.query(EdinetBuyback)
        .filter(
            EdinetBuyback.code == code5,
            EdinetBuyback.is_latest.is_(True),
            EdinetBuyback.withdrawn.is_(False),
            EdinetBuyback.parse_status == "parsed",
        )
        .order_by(EdinetBuyback.report_month.desc(), EdinetBuyback.file_date.desc())
        .first()
    )
    if row is None:
        return None
    active = []
    for res in (
        db.query(EdinetBuybackResolution)
        .filter(EdinetBuybackResolution.doc_id == row.doc_id)
        .order_by(EdinetBuybackResolution.resolution_no)
        .all()
    ):
        if res.acquire_to is None or res.acquire_to < day:
            continue
        active.append({
            "resolution_date": res.resolution_date.isoformat() if res.resolution_date else None,
            "acquire_to": res.acquire_to.isoformat(),
            "limit_shares": res.limit_shares,
            "cumulative_shares": res.cumulative_shares,
            "progress_shares": res.progress_shares,
        })
    issued = row.issued_shares
    treasury = row.treasury_shares
    ratio = (treasury / issued) if issued and treasury is not None else None
    return {
        "report_month": row.report_month,
        "issued_shares": issued,
        "treasury_shares": treasury,
        "treasury_ratio": ratio,
        "active": active,
    }
