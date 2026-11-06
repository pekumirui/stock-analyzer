from __future__ import annotations

import heapq
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from enum import Enum
from functools import cache
from typing import Any, Iterable, Iterator, Mapping, NamedTuple, Sequence

__all__ = ["SUMMARY_SOURCES", "Source", "compare_text", "diff_summary_text"]

DEFAULT_PERIODS = 8
ROUNDING_YEN = 1_000_000
REL_TOL = 0.001
BUDGET_S = 9.0
_BUDGET_MSG = "xbrl_diff_summary exceeded ~9s; narrow with since=YYYY-MM-DD"
_METRIC_RE = re.compile(r"^[a-z_]{1,64}$")
_REQUIRED_TABLES = ("statements", "xbrl_filings", "xbrl_financials", "xbrl_std_items")


class Source(str, Enum):
    STATEMENTS = "statements"
    TDNET = "tdnet"
    EDINET = "edinet"


_SHORT = {Source.STATEMENTS: "st", Source.TDNET: "td", Source.EDINET: "ed"}
COLUMNS: tuple[Source, ...] = (Source.STATEMENTS, Source.TDNET, Source.EDINET)
SUMMARY_SOURCES: Mapping[str, Source] = {"edinet": Source.EDINET, "tdnet": Source.TDNET}


class Pair(NamedTuple):
    left: Source
    right: Source

    @property
    def label(self) -> str:
        return f"{_SHORT[self.left]}:{_SHORT[self.right]}"

    def applies(self, item: Item) -> bool:
        return item.carried_by(self.left) and item.carried_by(self.right)

    def abs_tolerance(self, item: Item) -> float:
        if Source.STATEMENTS in self and not item.per_share:
            return float(ROUNDING_YEN)
        return 0.0


COMPARE_PAIRS: tuple[Pair, ...] = (
    Pair(Source.STATEMENTS, Source.TDNET),
    Pair(Source.STATEMENTS, Source.EDINET),
    Pair(Source.TDNET, Source.EDINET),
)


class Verdict(str, Enum):
    OK = "OK"
    DIFF = "DIFF"
    MISSING = "MISSING"


@dataclass(frozen=True)
class Item:
    code: str
    label: str
    metric: str | None
    per_share: bool

    def carried_by(self, source: Source) -> bool:
        return source is not Source.STATEMENTS or self.metric is not None


@dataclass(frozen=True)
class Doc:
    source: Source
    doc_id: str
    submitted: str
    period_type: str | None
    consolidated: bool
    amended: bool
    standard: str | None
    values: Mapping[str, float]


@dataclass(frozen=True)
class Period:
    code: str
    period_end: date
    docs: Mapping[Source, Doc]


@dataclass(frozen=True)
class Row:
    period: Period
    item: Item
    values: Mapping[Source, float | None]
    verdicts: Mapping[Pair, Verdict]

    def gap(self, pair: Pair) -> float:
        if self.verdicts.get(pair) is Verdict.MISSING:
            return 1.0
        a = self.values.get(pair.left)
        b = self.values.get(pair.right)
        if a is None or b is None:
            return 1.0
        scale = max(abs(a), abs(b))
        if scale == 0:
            return 0.0
        return abs(a - b) / scale


@dataclass
class _Tally:
    ok: int = 0
    diff: int = 0
    missing: int = 0

    def add(self, verdict: Verdict) -> None:
        if verdict is Verdict.OK:
            self.ok += 1
        elif verdict is Verdict.DIFF:
            self.diff += 1
        else:
            self.missing += 1

    @property
    def rate(self) -> float | None:
        compared = self.ok + self.diff
        if compared == 0:
            return None
        return self.ok / compared


@dataclass(frozen=True)
class Summary:
    source: Source
    since: date | None
    xbrl_periods: int
    paired: int
    restated: int
    by_item: Mapping[str, _Tally]
    by_standard: Mapping[str, _Tally]
    by_sector: Mapping[str, _Tally]
    top_diffs: tuple[Row, ...]


@dataclass(frozen=True)
class _Scope:
    sources: tuple[Source, ...]
    items: tuple[Item, ...]
    code: str | None = None
    period_end: date | None = None
    since: date | None = None


class _XbrlRow(NamedTuple):
    filing_id: int
    code: str
    source: str
    doc_id: str
    submitted_at: str
    period_type: str | None
    is_amendment: int
    accounting_standard: str | None
    is_consolidated: int
    period_end: str
    item: str
    value: float


class _StatementRow(NamedTuple):
    disclosure_no: str
    code: str
    disclosed: str
    type_of_period: str | None
    type_of_document: str
    period_end: str
    metrics: Mapping[str, float | None]


def compare_text(
    conn: sqlite3.Connection,
    code: str,
    *,
    period_end: date | None,
    items: Sequence[str] | None,
) -> str:
    _require_tables(conn)
    chosen = _resolve_items(_catalog(conn), items)
    with _budget(conn) as deadline:
        periods = _load_periods(
            conn, _Scope(COLUMNS, chosen, code=code, period_end=period_end),
        )
        _check_budget(deadline)
    newest = sorted(periods.values(), key=lambda p: p.period_end, reverse=True)
    if period_end is None:
        newest = newest[:DEFAULT_PERIODS]
    if not newest:
        extra = f" for {period_end.isoformat()}" if period_end else ""
        return f"{code}: no statements or XBRL actuals{extra}"
    return _render_compare(code, _stock_name(conn, code), newest, chosen)


def diff_summary_text(
    conn: sqlite3.Connection,
    *,
    source: Source,
    since: date | None,
    limit: int,
) -> str:
    _require_tables(conn)
    catalog = _catalog(conn)
    items = tuple(item for item in catalog if item.carried_by(Source.STATEMENTS))
    pair = Pair(Source.STATEMENTS, source)
    with _budget(conn) as deadline:
        periods = _load_periods(
            conn, _Scope((Source.STATEMENTS, source), items, since=since),
        )
        summary = summarize(
            periods.values(), pair, items, _sectors(conn), limit, since,
        )
        _check_budget(deadline)
    return _render_summary(summary)


def judge(a: float | None, b: float | None, abs_tol: float) -> Verdict | None:
    if a is None and b is None:
        return None
    if a is None or b is None:
        return Verdict.MISSING
    if a * b < 0:
        return Verdict.DIFF
    diff = abs(a - b)
    if diff < abs_tol:
        return Verdict.OK
    scale = max(abs(a), abs(b))
    if scale == 0 or diff == 0:
        return Verdict.OK
    if diff / scale > REL_TOL:
        return Verdict.DIFF
    return Verdict.OK


def compare_period(
    period: Period, items: Sequence[Item], pairs: Sequence[Pair],
) -> list[Row]:
    rows: list[Row] = []
    for item in items:
        values: dict[Source, float | None] = {}
        present = False
        for source, doc in period.docs.items():
            if item.code in doc.values:
                values[source] = doc.values[item.code]
                present = True
            else:
                values[source] = None
        if not present:
            continue
        verdicts: dict[Pair, Verdict] = {}
        for pair in pairs:
            if pair.left not in period.docs or pair.right not in period.docs:
                continue
            if not pair.applies(item):
                continue
            verdict = judge(values.get(pair.left), values.get(pair.right), pair.abs_tolerance(item))
            if verdict is not None:
                verdicts[pair] = verdict
        rows.append(Row(period, item, values, verdicts))
    return rows


def is_restated(period: Period, pair: Pair) -> bool:
    """TDnet の訂正短信が、statements の行より後に別の開示で過去の期を出し直したもの。"""
    if pair.left is not Source.STATEMENTS or pair.right is not Source.TDNET:
        return False
    left = period.docs.get(pair.left)
    right = period.docs.get(pair.right)
    if left is None or right is None or not right.amended:
        return False
    return right.doc_id[-14:] != left.doc_id and right.submitted[:10] > left.submitted[:10]


def summarize(
    periods: Iterable[Period],
    pair: Pair,
    items: Sequence[Item],
    sectors: Mapping[str, str],
    limit: int,
    since: date | None,
) -> Summary:
    by_item = {item.code: _Tally() for item in items}
    by_standard: dict[str, _Tally] = defaultdict(_Tally)
    by_sector: dict[str, _Tally] = defaultdict(_Tally)
    xbrl_periods = 0
    paired = 0
    restated = 0
    heap: list[tuple[float, int, Row]] = []
    seq = 0
    for period in periods:
        if pair.right not in period.docs:
            continue
        xbrl_periods += 1
        if pair.left not in period.docs:
            continue
        paired += 1
        if is_restated(period, pair):
            restated += 1
            continue
        standard = period.docs[pair.right].standard or "-"
        sector = sectors.get(period.code) or "-"
        for row in compare_period(period, items, (pair,)):
            verdict = row.verdicts.get(pair)
            if verdict is None:
                continue
            by_item[row.item.code].add(verdict)
            by_standard[standard].add(verdict)
            by_sector[sector].add(verdict)
            if verdict is Verdict.OK:
                continue
            seq += 1
            entry = (row.gap(pair), seq, row)
            if limit <= 0:
                continue
            if len(heap) < limit:
                heapq.heappush(heap, entry)
            elif entry > heap[0]:
                heapq.heapreplace(heap, entry)
    top = tuple(row for _, _, row in sorted(heap, key=lambda entry: (-entry[0], entry[1])))
    return Summary(
        source=pair.right,
        since=since,
        xbrl_periods=xbrl_periods,
        paired=paired,
        restated=restated,
        by_item=by_item,
        by_standard=by_standard,
        by_sector=by_sector,
        top_diffs=top,
    )


@cache
def _std_to_metric() -> Mapping[str, str]:
    from app.xbrl.revisions import STD_TO_METRIC

    return STD_TO_METRIC


def _catalog(conn: sqlite3.Connection) -> tuple[Item, ...]:
    metric_of = _std_to_metric()
    rows = conn.execute(
        "SELECT code, label_ja, section FROM xbrl_std_items "
        "WHERE statement != 'OTHER' ORDER BY display_order, code"
    ).fetchall()
    items: list[Item] = []
    for code, label, section in rows:
        code_s = str(code)
        items.append(Item(
            code=code_s,
            label=str(label or code_s),
            metric=metric_of.get(code_s),
            per_share=section == "per_share",
        ))
    return tuple(items)


def _resolve_items(catalog: tuple[Item, ...], names: Sequence[str] | None) -> tuple[Item, ...]:
    if not names:
        return catalog
    by_code = {item.code: item for item in catalog}
    by_metric: dict[str, Item] = {}
    for item in catalog:
        if item.metric and item.metric not in by_metric:
            by_metric[item.metric] = item
    chosen: list[Item] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for name in names:
        item = by_code.get(name) or by_metric.get(name)
        if item is None:
            unknown.append(name)
            continue
        if item.code not in seen:
            seen.add(item.code)
            chosen.append(item)
    if unknown:
        accepted = [item.code for item in catalog]
        for item in catalog:
            if item.metric and item.metric not in accepted:
                accepted.append(item.metric)
        raise ValueError(
            f"unknown items: {', '.join(unknown)}; accepted: {', '.join(accepted)}"
        )
    order = {item.code: index for index, item in enumerate(catalog)}
    chosen.sort(key=lambda item: order[item.code])
    return tuple(chosen)


def _load_periods(conn: sqlite3.Connection, scope: _Scope) -> dict[tuple[str, date], Period]:
    found: dict[tuple[str, date], dict[Source, Doc]] = {}

    def put(code: str, period_end: date, doc: Doc) -> None:
        key = (code, period_end)
        bucket = found.get(key)
        if bucket is None:
            found[key] = {doc.source: doc}
        else:
            bucket[doc.source] = doc

    xbrl_buckets: dict[tuple[str, str, str], list[_XbrlRow]] = defaultdict(list)
    for row in _xbrl_rows(conn, scope):
        xbrl_buckets[(row.code, row.period_end, row.source)].append(row)
    for (code, pend, _source), group in xbrl_buckets.items():
        put(code, date.fromisoformat(pend), _pick_filing(group))

    stmt_buckets: dict[tuple[str, str], list[_StatementRow]] = defaultdict(list)
    for row in _statement_rows(conn, scope):
        stmt_buckets[(row.code, row.period_end)].append(row)
    for (code, pend), group in stmt_buckets.items():
        put(code, date.fromisoformat(pend), _pick_statement(group, scope.items))

    return {key: Period(key[0], key[1], docs) for key, docs in found.items()}


def _xbrl_rows(conn: sqlite3.Connection, scope: _Scope) -> Iterator[_XbrlRow]:
    sources = [source.value for source in scope.sources if source is not Source.STATEMENTS]
    codes = [item.code for item in scope.items]
    if not sources or not codes:
        return
    where = [
        "f.parse_status = 'parsed'",
        "x.value_kind = 'actual'",
        "x.range_side = 'point'",
        f"f.source IN ({','.join('?' * len(sources))})",
        f"s.code IN ({','.join('?' * len(codes))})",
    ]
    params: list[Any] = [*sources, *codes]
    if scope.code is not None:
        where.append("f.code = ?")
        params.append(scope.code)
    if scope.period_end is not None:
        where.append("x.period_end = ?")
        params.append(scope.period_end.isoformat())
    if scope.since is not None:
        where.append("f.submitted_at >= ?")
        params.append(scope.since.isoformat())
    sql = (
        "SELECT f.id, f.code, f.source, f.doc_id, f.submitted_at, f.period_type, "
        "f.is_amendment, f.accounting_standard, x.is_consolidated, x.period_end, "
        "s.code, x.value "
        "FROM xbrl_filings f "
        "JOIN xbrl_financials x ON x.filing_id = f.id "
        "JOIN xbrl_std_items s ON s.id = x.std_item_id "
        f"WHERE {' AND '.join(where)}"
    )
    for row in conn.execute(sql, params):
        yield _XbrlRow(
            int(row[0]),
            str(row[1]),
            str(row[2]),
            str(row[3]),
            str(row[4]),
            None if row[5] is None else str(row[5]),
            int(row[6] or 0),
            None if row[7] is None else str(row[7]),
            int(row[8] or 0),
            str(row[9])[:10],
            str(row[10]),
            float(row[11]),
        )


def _statement_rows(conn: sqlite3.Connection, scope: _Scope) -> Iterator[_StatementRow]:
    """FinancialStatements rows only. EarnForecastRevision rows have NULL actuals and would shadow real ones."""
    if Source.STATEMENTS not in scope.sources:
        return
    metrics: list[str] = []
    for item in scope.items:
        name = item.metric
        if not name or name in metrics or _METRIC_RE.fullmatch(name) is None:
            continue
        metrics.append(name)
    select_metrics = (", " + ", ".join(metrics)) if metrics else ""
    where = [
        "type_of_document LIKE '%FinancialStatements%'",
        "period_end IS NOT NULL",
    ]
    params: list[Any] = []
    if scope.code is not None:
        where.append("code = ?")
        params.append(scope.code)
    if scope.period_end is not None:
        where.append("period_end = ?")
        params.append(scope.period_end.isoformat())
    if scope.code is None:
        xbrl_sources = [source.value for source in scope.sources if source is not Source.STATEMENTS]
        if xbrl_sources:
            sub = [
                f"source IN ({','.join('?' * len(xbrl_sources))})",
                "parse_status = 'parsed'",
            ]
            params.extend(xbrl_sources)
            if scope.since is not None:
                sub.append("submitted_at >= ?")
                params.append(scope.since.isoformat())
            where.append(
                "code IN (SELECT code FROM xbrl_filings WHERE " + " AND ".join(sub) + ")"
            )
    sql = (
        "SELECT disclosure_no, code, disclosed_date, disclosed_time, type_of_period, "
        "type_of_document, period_end"
        f"{select_metrics} FROM statements WHERE {' AND '.join(where)}"
    )
    for row in conn.execute(sql, params):
        disclosed_date = str(row[2] or "")
        disclosed_time = str(row[3] or "").strip()
        disclosed = f"{disclosed_date} {disclosed_time}".strip()
        metric_values: dict[str, float | None] = {}
        for index, name in enumerate(metrics):
            raw = row[7 + index]
            if raw is not None:
                metric_values[name] = float(raw)
        yield _StatementRow(
            str(row[0]),
            str(row[1]),
            disclosed,
            None if row[4] is None else str(row[4]),
            str(row[5] or ""),
            str(row[6])[:10],
            metric_values,
        )


def _pick_filing(rows: Sequence[_XbrlRow]) -> Doc:
    winner = max(rows, key=lambda row: (row.submitted_at, row.filing_id))
    same = [row for row in rows if row.filing_id == winner.filing_id]
    consolidated = any(row.is_consolidated for row in same)
    values: dict[str, float] = {}
    for row in same:
        if bool(row.is_consolidated) != consolidated:
            continue
        values[row.item] = float(row.value)
    return Doc(
        source=Source(winner.source),
        doc_id=winner.doc_id,
        submitted=winner.submitted_at,
        period_type=winner.period_type,
        consolidated=consolidated,
        amended=bool(winner.is_amendment),
        standard=winner.accounting_standard,
        values=values,
    )


def _pick_statement(rows: Sequence[_StatementRow], items: Sequence[Item]) -> Doc:
    winner = max(rows, key=lambda row: (row.disclosed, row.disclosure_no))
    values: dict[str, float] = {}
    for item in items:
        if not item.metric:
            continue
        raw = winner.metrics.get(item.metric)
        if raw is None:
            continue
        values[item.code] = float(raw)
    return Doc(
        source=Source.STATEMENTS,
        doc_id=winner.disclosure_no,
        submitted=winner.disclosed,
        period_type=winner.type_of_period,
        consolidated="NonConsolidated" not in winner.type_of_document,
        amended=len(rows) > 1,
        standard=None,
        values=values,
    )


def _require_tables(conn: sqlite3.Connection) -> None:
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = [name for name in _REQUIRED_TABLES if name not in names]
    if missing:
        raise ValueError(f"missing table: {', '.join(missing)}")


@contextmanager
def _budget(conn: sqlite3.Connection, seconds: float = BUDGET_S) -> Iterator[float]:
    deadline = time.monotonic() + seconds

    def handler() -> int:
        return 1 if time.monotonic() >= deadline else 0

    conn.set_progress_handler(handler, 10000)
    try:
        yield deadline
    except sqlite3.OperationalError as exc:
        if "interrupt" in str(exc).lower():
            raise ValueError(_BUDGET_MSG) from exc
        raise
    finally:
        conn.set_progress_handler(None, 0)


def _check_budget(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ValueError(_BUDGET_MSG)


def _stock_name(conn: sqlite3.Connection, code: str) -> str:
    try:
        row = conn.execute("SELECT name FROM stocks WHERE code = ?", (code,)).fetchone()
    except sqlite3.OperationalError:
        return ""
    if row is None or row[0] is None:
        return ""
    return str(row[0])


def _sectors(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT code, sector33 FROM stocks").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(row[0]): (str(row[1]) if row[1] else "-") for row in rows}


def _fmt_value(item: Item, value: float | None) -> str:
    if value is None:
        return "-"
    if item.per_share:
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def _period_type(period: Period) -> str:
    for source in COLUMNS:
        doc = period.docs.get(source)
        if doc is not None and doc.period_type:
            return doc.period_type
    return ""


def _source_line(source: Source, doc: Doc | None) -> str:
    label = f"{source.value:<12}"
    if doc is None:
        return f"  {label}(no filing)"
    kind = "consolidated" if doc.consolidated else "standalone"
    standard = f"  {doc.standard}" if doc.standard else ""
    mark = "  AMENDED*" if doc.amended else ""
    return f"  {label}{doc.doc_id}  {doc.submitted}  {kind}{standard}{mark}"


def _render_compare(
    code: str, name: str, periods: Sequence[Period], items: Sequence[Item],
) -> str:
    lines = [
        f"{code} {name}  periods={len(periods)} (newest first)  items={len(items)}",
        (
            "OK: |diff|<1e6 yen (money vs statements) or rel<=0.1% after that slack  "
            "DIFF: sign or rel>0.1%  "
            "MISSING: one side empty  -: not compared"
        ),
    ]
    head = f"  {'item':<20}" + "".join(f"{source.value:>18}" for source in COLUMNS)
    head += "".join(f"{pair.label:>8}" for pair in COMPARE_PAIRS)
    for period in periods:
        ptype = _period_type(period)
        lines.append(f"{period.period_end.isoformat()} {ptype}".rstrip())
        for source in COLUMNS:
            lines.append(_source_line(source, period.docs.get(source)))
        lines.append(head)
        for row in compare_period(period, items, COMPARE_PAIRS):
            cells = ""
            for source in COLUMNS:
                raw = row.values.get(source) if source in period.docs else None
                cells += f"{_fmt_value(row.item, raw):>18}"
            flags = ""
            for pair in COMPARE_PAIRS:
                verdict = row.verdicts.get(pair)
                flags += f"{(verdict.value if verdict is not None else '-'):>8}"
            lines.append(f"  {row.item.code:<20}{cells}{flags}")
    return "\n".join(lines)


def _pct(tally: _Tally) -> str:
    if tally.rate is None:
        return "-"
    return f"{tally.rate * 100:.1f}%"


def _tally_line(name: str, tally: _Tally) -> str:
    return f"  {name} ok={tally.ok} diff={tally.diff} missing={tally.missing} match={_pct(tally)}"


def _render_summary(summary: Summary) -> str:
    since = summary.since.isoformat() if summary.since else "-"
    xbrl_only = summary.xbrl_periods - summary.paired
    lines = [
        f"xbrl_diff_summary source={summary.source.value} since={since}",
        (
            f"xbrl_periods={summary.xbrl_periods} paired={summary.paired} xbrl_only={xbrl_only} "
            f"restated={summary.restated}"
        ),
        "by item",
    ]
    for code, tally in summary.by_item.items():
        if tally.ok or tally.diff or tally.missing:
            lines.append(_tally_line(code, tally))
    lines.append("by accounting_standard")
    standards = sorted(
        summary.by_standard.items(),
        key=lambda kv: (kv[1].rate if kv[1].rate is not None else 1.0, kv[0]),
    )
    for name, tally in standards:
        lines.append(_tally_line(name, tally))
    lines.append("by sector33")
    sectors = sorted(
        summary.by_sector.items(),
        key=lambda kv: (kv[1].rate if kv[1].rate is not None else 1.0, kv[0]),
    )
    for name, tally in sectors:
        lines.append(_tally_line(name, tally))
    lines.append("top diffs")
    pair = Pair(Source.STATEMENTS, summary.source)
    for row in summary.top_diffs:
        left = row.period.docs[pair.left]
        right = row.period.docs[pair.right]
        lines.append(
            f"  {row.period.code} {row.period.period_end.isoformat()} {row.item.code} "
            f"{row.verdicts[pair].value} gap={row.gap(pair):.3f} "
            f"statements={_fmt_value(row.item, row.values.get(pair.left))} doc={left.doc_id} "
            f"{summary.source.value}={_fmt_value(row.item, row.values.get(pair.right))} doc={right.doc_id}"
        )
    return "\n".join(lines)
