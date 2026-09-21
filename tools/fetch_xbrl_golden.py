"""Normalize IRBANK / MCP JSON into data/xbrl_golden/{code4}_{period_end}.json.

Default is **file input only** (no network):

  python tools/fetch_xbrl_golden.py --input PATH --out-dir data/xbrl_golden

PATH is a JSON/JSONL file or a directory of those. Nested MCP tool wrappers
(`result`, `content[].text`, `structuredContent`, IRBANK `results[].periods`)
and IRBANK field aliases (売上高, sales, …) are accepted. Metrics are stored
in million JPY.

**Period basis (KPI):** goldens are always **cumulative YTD**, matching
`statements` and TDnet flash PL. Never store IRBANK quarterly standalone
quarters as matchable goldens.

  - Q1 cumulative = that quarter (= YTD)
  - Q2 cumulative = H1 / semiannual (API H1 if present, else Q1+Q2 standalone)
  - Q3 cumulative = 9-month (Q1+Q2+Q3 standalone, or API cumulative if present)
  - FY  cumulative = full year / annual

IRBANK Connect `get_financials` with `period_type=quarterly` returns
**single-quarter** rows (Q1..Q4). This tool converts them to cumulative before
write. Output always has `period_basis: "cumulative"`. Standalone contamination
is not written as matchable current.

Live HTTP is opt-in and requires both flags:

  python tools/fetch_xbrl_golden.py --live --url URL --out-dir data/xbrl_golden

No secrets in this file. Optional `IRBANK_API_KEY` / `IRBANK_TOKEN` are sent
as Bearer only for --live. Do not log them.
"""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
JST = timezone(timedelta(hours=9))

GOLDEN_METRIC_KEYS = (
    "revenue",
    "operating_income",
    "ordinary_income",
    "net_income",
    "total_assets",
    "equity",
    "total_liabilities",
    "operating_cf",
    "investing_cf",
)

FLOW_METRIC_KEYS = (
    "revenue",
    "operating_income",
    "ordinary_income",
    "net_income",
    "operating_cf",
    "investing_cf",
)
STOCK_METRIC_KEYS = (
    "total_assets",
    "equity",
    "total_liabilities",
)

PERIOD_BASIS_CUMULATIVE = "cumulative"
PERIOD_BASIS_SINGLE = "single_quarter"
SINGLE_ALIASES = {
    "single_quarter", "standalone", "single", "quarter_only", "単四半期", "単独四半期",
}
CUMULATIVE_ALIASES = {
    "cumulative", "ytd", "current_ytd", "累計", "通期累計", "ytd_cumulative",
}
QUARTER_STANDALONE = {"Q1", "Q2", "Q3", "Q4"}

METRIC_ALIASES: dict[str, str] = {
    "revenue": "revenue",
    "net_sales": "revenue",
    "sales": "revenue",
    "売上高": "revenue",
    "売上": "revenue",
    "営業収益": "revenue",
    "operating_income": "operating_income",
    "operating_profit": "operating_income",
    "営業利益": "operating_income",
    "ordinary_income": "ordinary_income",
    "ordinary_profit": "ordinary_income",
    "経常利益": "ordinary_income",
    "net_income": "net_income",
    "profit": "net_income",
    "profit_loss": "net_income",
    "当期純利益": "net_income",
    "親会社株主に帰属する当期純利益": "net_income",
    "純利益": "net_income",
    "total_assets": "total_assets",
    "assets": "total_assets",
    "総資産": "total_assets",
    "資産合計": "total_assets",
    "equity": "equity",
    "net_assets": "equity",
    "純資産": "equity",
    "純資産合計": "equity",
    "total_liabilities": "total_liabilities",
    "liabilities": "total_liabilities",
    "総負債": "total_liabilities",
    "負債合計": "total_liabilities",
    "operating_cf": "operating_cf",
    "operating_cash_flow": "operating_cf",
    "営業cf": "operating_cf",
    "営業キャッシュフロー": "operating_cf",
    "営業活動によるキャッシュフロー": "operating_cf",
    "営業活動によるcf": "operating_cf",
    "investing_cf": "investing_cf",
    "investing_cash_flow": "investing_cf",
    "投資cf": "investing_cf",
    "投資キャッシュフロー": "investing_cf",
    "投資活動によるキャッシュフロー": "investing_cf",
    "投資活動によるcf": "investing_cf",
}

CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$", re.IGNORECASE)
WRAP_KEYS = (
    "result",
    "structuredContent",
    "structured_content",
    "data",
    "filings",
    "records",
    "items",
    "goldens",
    "output",
    "body",
)


def _norm_key(raw: str) -> str:
    return re.sub(r"\s+", "", str(raw)).strip().lower()


def _alias_metric(name: str) -> str | None:
    s = str(name).strip()
    if s in METRIC_ALIASES:
        return METRIC_ALIASES[s]
    low = _norm_key(s)
    for k, v in METRIC_ALIASES.items():
        if _norm_key(k) == low:
            return v
    return None


def to_codes(raw: str) -> tuple[str, str]:
    s = str(raw).strip().upper().replace(".T", "")
    s = re.sub(r"[^0-9A-Z]", "", s)
    if len(s) == 5:
        return s, s[:4]
    if len(s) == 4:
        return s + "0", s
    raise ValueError(f"not a stock code: {raw!r}")


def period_end_iso(raw: Any) -> str:
    s = str(raw).strip()
    s = s.replace(".", "-").replace("/", "-")
    if re.fullmatch(r"\d{8}", s):
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    parts = s.split("-")
    if len(parts) == 3:
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{y:04d}-{m:02d}-{d:02d}"
    if len(parts) == 2:
        y, m = int(parts[0]), int(parts[1])
        d = calendar.monthrange(y, m)[1]
        return f"{y:04d}-{m:02d}-{d:02d}"
    raise ValueError(f"not a period_end: {raw!r}")


def parse_fiscal_year_end(raw: Any) -> date:
    """Parse IRBANK fiscal_year like '2026/03' or '2026-03' → FY end date."""
    s = str(raw).strip().replace(".", "/").replace("-", "/")
    parts = s.split("/")
    if len(parts) != 2:
        raise ValueError(f"not a fiscal_year: {raw!r}")
    y, m = int(parts[0]), int(parts[1])
    return date(y, m, calendar.monthrange(y, m)[1])


def add_months(d: date, months: int) -> date:
    m0 = d.month - 1 + months
    y = d.year + m0 // 12
    m = m0 % 12 + 1
    day = min(d.day, calendar.monthrange(y, m)[1])
    return date(y, m, day)


def normalize_quarter_label(raw: Any) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip().upper().replace(" ", "")
    if not s or s in {"NULL", "NONE", "-"}:
        return None
    aliases = {
        "1": "Q1", "1Q": "Q1", "Q1": "Q1",
        "2": "Q2", "2Q": "Q2", "Q2": "Q2",
        "3": "Q3", "3Q": "Q3", "Q3": "Q3",
        "4": "Q4", "4Q": "Q4", "Q4": "Q4",
        "H1": "H1", "1H": "H1", "上期": "H1", "中間": "H1", "SEMIANNUAL": "H1",
        "FY": "FY", "FULL_YEAR": "FULL_YEAR", "FULLYEAR": "FULL_YEAR",
        "ANNUAL": "ANNUAL", "通期": "FY", "本決算": "FY",
    }
    return aliases.get(s, s if s in QUARTER_STANDALONE or s in {"H1", "FY", "FULL_YEAR", "ANNUAL"} else s)


def period_end_for_quarter(fy_end: date, quarter: str | None) -> date:
    """Map FY-end + Q1/Q2/Q3/H1/FY → period_end.

    March FY (fy_end=2026-03-31): Q1→2025-06-30, Q2/H1→2025-09-30, Q3→2025-12-31, FY→2026-03-31.
    """
    q = normalize_quarter_label(quarter)
    if q is None or q in {"FY", "FULL_YEAR", "ANNUAL", "Q4"}:
        return fy_end
    offset = {"Q1": -9, "Q2": -6, "H1": -6, "Q3": -3}.get(q)
    if offset is None:
        return fy_end
    start = date(fy_end.year, fy_end.month, 1)
    shifted = add_months(start, offset)
    return date(shifted.year, shifted.month, calendar.monthrange(shifted.year, shifted.month)[1])


def parse_number(raw: Any) -> float | None:
    if raw is None or raw is False:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s or s.upper() in {"NA", "N/A", "NULL", "-", "—"}:
        return None
    s = s.replace(",", "").replace("，", "").replace("△", "-").replace("▲", "-")
    s = s.replace("%", "").replace("％", "")
    try:
        return float(s)
    except ValueError:
        return None


def extract_metrics(obj: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    nested = obj.get("metrics")
    src = nested if isinstance(nested, dict) else obj
    for key, val in src.items():
        mk = _alias_metric(str(key))
        if mk is None:
            continue
        num = parse_number(val)
        if num is None:
            continue
        out[mk] = num
    return out


def _looks_like_code(key: str) -> bool:
    s = str(key).strip().upper().replace(".T", "")
    s = re.sub(r"[^0-9A-Z]", "", s)
    return bool(CODE_RE.fullmatch(s))


def _expand_irbank_map(obj: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for code_k, periods in obj.items():
        if not _looks_like_code(str(code_k)) or not isinstance(periods, dict):
            continue
        if any(k in periods for k in ("metrics", "period_end", "code")):
            rec = dict(periods)
            rec.setdefault("code", code_k)
            rows.append(rec)
            continue
        for period_k, metrics in periods.items():
            if not isinstance(metrics, dict):
                continue
            rec = dict(metrics)
            rec.setdefault("code", code_k)
            rec.setdefault("period_end", period_k)
            rows.append(rec)
    return rows


def _expand_irbank_results(obj: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten IRBANK Connect get_financials: results[].periods[]."""
    results = obj.get("results")
    if not isinstance(results, list):
        return []
    rows: list[dict[str, Any]] = []
    top_source = obj.get("source")
    for block in results:
        if not isinstance(block, dict):
            continue
        periods = block.get("periods")
        if not isinstance(periods, list):
            continue
        parent_meta = {
            k: block[k]
            for k in (
                "code", "code4", "ticker", "security_code", "stock_code",
                "edinet_code", "name", "is_consolidated", "accounting_standard",
            )
            if k in block
        }
        for per in periods:
            if not isinstance(per, dict):
                continue
            rec = dict(per)
            for k, v in parent_meta.items():
                rec.setdefault(k, v)
            if top_source and "source" not in rec:
                rec["source"] = top_source
            if "period_type" not in rec and "quarter" in rec:
                q = normalize_quarter_label(rec.get("quarter"))
                if q in QUARTER_STANDALONE:
                    rec["_irbank_mode"] = "quarterly"
                elif q == "H1":
                    rec["_irbank_mode"] = "semiannual"
                elif q in {"FY", "FULL_YEAR", "ANNUAL"}:
                    rec["_irbank_mode"] = "annual"
            rows.append(rec)
    return rows


def unwrap(obj: Any, depth: int = 0) -> list[Any]:
    if obj is None or depth > 8:
        return []
    if isinstance(obj, list):
        out: list[Any] = []
        for item in obj:
            out.extend(unwrap(item, depth + 1))
        return out
    if isinstance(obj, str):
        s = obj.strip()
        if s[:1] in "{[":
            try:
                return unwrap(json.loads(s), depth + 1)
            except json.JSONDecodeError:
                return []
        return []
    if not isinstance(obj, dict):
        return []
    if "content" in obj and isinstance(obj["content"], list):
        texts: list[Any] = []
        for c in obj["content"]:
            if isinstance(c, dict) and "text" in c:
                texts.extend(unwrap(c["text"], depth + 1))
            else:
                texts.extend(unwrap(c, depth + 1))
        if texts:
            return texts
    expanded_res = _expand_irbank_results(obj)
    if expanded_res:
        return expanded_res
    for key in WRAP_KEYS:
        if key in obj and obj[key] is not obj:
            inner = unwrap(obj[key], depth + 1)
            if inner:
                return inner
    if "metrics" in obj or (
        ("code" in obj or "code4" in obj or "ticker" in obj or "edinet_code" in obj)
        and (
            "period_end" in obj
            or "period" in obj
            or "fiscal_year" in obj
            or "quarter" in obj
        )
    ):
        return [obj]
    expanded = _expand_irbank_map(obj)
    if expanded:
        return expanded
    return [obj]


def _code_from(obj: dict[str, Any]) -> str | None:
    for key in ("code", "code4", "ticker", "security_code", "stock_code"):
        if obj.get(key):
            try:
                return str(obj[key])
            except Exception:
                continue
    return None


def _period_from(obj: dict[str, Any]) -> str | None:
    for key in ("period_end", "period", "fiscal_period", "fy", "fiscal_year"):
        if obj.get(key):
            return str(obj[key])
    return None


def _fiscal_year_from(obj: dict[str, Any]) -> str | None:
    for key in ("fiscal_year", "fy", "fiscal_year_end"):
        if obj.get(key):
            return str(obj[key])
    return None


def detect_period_basis(obj: dict[str, Any], quarter: str | None) -> str | None:
    raw = obj.get("period_basis") or obj.get("basis") or obj.get("ytd_basis")
    if raw is not None:
        low = _norm_key(raw)
        raw_s = str(raw).strip().lower()
        if low in {_norm_key(x) for x in SINGLE_ALIASES} or raw_s in SINGLE_ALIASES:
            return PERIOD_BASIS_SINGLE
        if low in {_norm_key(x) for x in CUMULATIVE_ALIASES} or raw_s in CUMULATIVE_ALIASES:
            return PERIOD_BASIS_CUMULATIVE
    q = normalize_quarter_label(quarter)
    mode = str(obj.get("_irbank_mode") or obj.get("period_type") or "").strip().lower()
    if mode in {"quarterly", "quarter"} and q in QUARTER_STANDALONE:
        return PERIOD_BASIS_SINGLE
    if mode in {"semiannual", "semi", "half"}:
        return PERIOD_BASIS_CUMULATIVE
    if mode in {"annual", "year", "fy"} or q in {"FY", "FULL_YEAR", "ANNUAL"}:
        return PERIOD_BASIS_CUMULATIVE
    if q == "H1":
        return PERIOD_BASIS_CUMULATIVE
    if q in QUARTER_STANDALONE:
        return PERIOD_BASIS_SINGLE
    return None


def normalize_raw_period(obj: dict[str, Any], fetched_at: str) -> dict[str, Any] | None:
    raw_code = _code_from(obj)
    if raw_code is None:
        return None
    try:
        code, code4 = to_codes(raw_code)
    except ValueError:
        return None

    fy_raw = _fiscal_year_from(obj)
    pt_raw = obj.get("period_type")
    pt = str(pt_raw or "").strip().lower()
    quarter = normalize_quarter_label(obj.get("quarter"))
    if quarter is None:
        q_try = normalize_quarter_label(pt_raw)
        if q_try in QUARTER_STANDALONE or q_try in {"H1", "FY", "FULL_YEAR", "ANNUAL"}:
            quarter = q_try
    if quarter is None and pt in {"annual", "year"}:
        quarter = "FY"

    period_end: str | None = None
    raw_period = _period_from(obj)
    if raw_period:
        try:
            period_end = period_end_iso(raw_period)
        except ValueError:
            period_end = None
    if period_end is None and fy_raw:
        try:
            fy_end = parse_fiscal_year_end(fy_raw)
            period_end = period_end_for_quarter(fy_end, quarter).isoformat()
        except ValueError:
            return None
    if period_end is None:
        return None

    metrics = extract_metrics(obj)
    if not metrics and not (fy_raw and quarter):
        return None

    std = obj.get("accounting_standard")
    if isinstance(std, str):
        std = std.strip() or None
    else:
        std = None
    consol = obj.get("is_consolidated")
    if consol is None:
        is_consolidated = True
    else:
        is_consolidated = bool(consol) and str(consol).lower() not in {"0", "false", "no"}

    basis = detect_period_basis(obj, quarter)
    if basis is None:
        if quarter in QUARTER_STANDALONE:
            basis = PERIOD_BASIS_SINGLE
        else:
            basis = PERIOD_BASIS_CUMULATIVE

    fy_key = None
    if fy_raw:
        try:
            fy_key = parse_fiscal_year_end(fy_raw).isoformat()
        except ValueError:
            fy_key = str(fy_raw)

    return {
        "code": code,
        "code4": code4,
        "period_end": period_end,
        "fiscal_year_end": fy_key,
        "quarter": quarter,
        "period_basis": basis,
        "accounting_standard": std,
        "is_consolidated": is_consolidated,
        "metrics": {k: metrics[k] for k in GOLDEN_METRIC_KEYS if k in metrics},
        "unit": "million_jpy",
        "source": str(obj.get("source") or "irbank"),
        "fetched_at": str(obj.get("fetched_at") or fetched_at),
        "edinet_code": obj.get("edinet_code"),
    }


def _sum_flow(metrics_list: list[dict[str, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key in FLOW_METRIC_KEYS:
        vals = [m[key] for m in metrics_list if key in m]
        if vals:
            out[key] = float(sum(vals))
    return out


def _pick_stock(metrics: dict[str, float] | None) -> dict[str, float]:
    if not metrics:
        return {}
    return {k: metrics[k] for k in STOCK_METRIC_KEYS if k in metrics}


def _cumulative_label(quarter: str | None) -> str:
    q = normalize_quarter_label(quarter)
    if q == "Q1":
        return "Q1"
    if q in {"Q2", "H1"}:
        return "Q2"
    if q == "Q3":
        return "Q3"
    return "FY"


def _public_golden(row: dict[str, Any], cumulative_build: str | None = None) -> dict[str, Any]:
    rec = {
        "code": row["code"],
        "code4": row["code4"],
        "period_end": row["period_end"],
        "period_basis": PERIOD_BASIS_CUMULATIVE,
        "accounting_standard": row.get("accounting_standard"),
        "is_consolidated": row.get("is_consolidated", True),
        "metrics": {k: row["metrics"][k] for k in GOLDEN_METRIC_KEYS if k in row.get("metrics", {})},
        "unit": "million_jpy",
        "source": row.get("source") or "irbank",
        "fetched_at": row.get("fetched_at"),
    }
    if row.get("fiscal_year_end"):
        rec["fiscal_year_end"] = row["fiscal_year_end"]
    if row.get("quarter"):
        rec["quarter"] = _cumulative_label(row.get("quarter"))
    build = cumulative_build or row.get("cumulative_build")
    if build:
        rec["cumulative_build"] = build
    return rec


def merge_goldens(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        pub = row if row.get("period_basis") == PERIOD_BASIS_CUMULATIVE else _public_golden(row)
        if pub.get("period_basis") != PERIOD_BASIS_CUMULATIVE:
            continue
        key = (pub["code"], pub["period_end"])
        if key not in by:
            by[key] = pub
            continue
        cur = by[key]
        cur["metrics"].update(pub["metrics"])
        if cur.get("accounting_standard") is None:
            cur["accounting_standard"] = pub.get("accounting_standard")
        if pub.get("is_consolidated"):
            cur["is_consolidated"] = True
        if not cur.get("cumulative_build") and pub.get("cumulative_build"):
            cur["cumulative_build"] = pub.get("cumulative_build")
    return [by[k] for k in sorted(by)]


def build_cumulative_goldens(raw_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert intermediate rows to cumulative-only goldens."""
    by_fy: dict[tuple[str, str], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    skipped: list[str] = []

    for row in raw_rows:
        fy = row.get("fiscal_year_end")
        if fy:
            by_fy.setdefault((row["code"], str(fy)), []).append(row)
        elif row.get("period_basis") == PERIOD_BASIS_CUMULATIVE:
            passthrough.append(row)
        else:
            skipped.append(
                f"{row.get('code')} {row.get('period_end')} single_quarter_without_fiscal_year"
            )

    out: list[dict[str, Any]] = []

    for (code, fy_end_s), rows in sorted(by_fy.items()):
        by_q: dict[str, dict[str, Any]] = {}
        for r in rows:
            q = normalize_quarter_label(r.get("quarter")) or "FY"
            if q in {"FULL_YEAR", "ANNUAL"}:
                q = "FY"
            prev = by_q.get(q)
            if prev is None:
                by_q[q] = r
            elif (
                r.get("period_basis") == PERIOD_BASIS_CUMULATIVE
                and prev.get("period_basis") != PERIOD_BASIS_CUMULATIVE
            ):
                by_q[q] = r
            else:
                prev["metrics"] = {**prev.get("metrics", {}), **r.get("metrics", {})}

        fy_end = date.fromisoformat(fy_end_s[:10])
        sample = rows[0]

        def emit(
            label: str,
            metrics: dict[str, float],
            note: str,
            *,
            fy_end: date = fy_end,
            code: str = code,
            sample: dict[str, Any] = sample,
        ) -> None:
            if not metrics:
                return
            pe = period_end_for_quarter(fy_end, label).isoformat()
            out.append(
                {
                    "code": code,
                    "code4": sample["code4"],
                    "period_end": pe,
                    "fiscal_year_end": fy_end.isoformat(),
                    "quarter": _cumulative_label(label),
                    "period_basis": PERIOD_BASIS_CUMULATIVE,
                    "accounting_standard": sample.get("accounting_standard"),
                    "is_consolidated": sample.get("is_consolidated", True),
                    "metrics": {k: metrics[k] for k in GOLDEN_METRIC_KEYS if k in metrics},
                    "unit": "million_jpy",
                    "source": sample.get("source") or "irbank",
                    "fetched_at": sample.get("fetched_at"),
                    "cumulative_build": note,
                }
            )

        q1 = by_q.get("Q1")
        if q1 and q1.get("metrics"):
            emit("Q1", dict(q1["metrics"]), "q1_equals_ytd")

        h1 = by_q.get("H1")
        q2 = by_q.get("Q2")
        if h1 and h1.get("period_basis") == PERIOD_BASIS_CUMULATIVE and h1.get("metrics"):
            emit("Q2", dict(h1["metrics"]), "api_h1_cumulative")
        elif q2 and q2.get("period_basis") == PERIOD_BASIS_CUMULATIVE and q2.get("metrics"):
            emit("Q2", dict(q2["metrics"]), "api_q2_cumulative")
        elif q1 and q2:
            flowed = _sum_flow([q1.get("metrics") or {}, q2.get("metrics") or {}])
            flowed.update(_pick_stock(h1.get("metrics") if h1 else None))
            emit("Q2", flowed, "sum_q1_q2_standalone")
        elif q2 and q2.get("period_basis") == PERIOD_BASIS_SINGLE:
            skipped.append(f"{code} FY:{fy_end_s} Q2_standalone_missing_q1")

        q3 = by_q.get("Q3")
        if q3 and q3.get("period_basis") == PERIOD_BASIS_CUMULATIVE and q3.get("metrics"):
            emit("Q3", dict(q3["metrics"]), "api_q3_cumulative")
        elif q1 and q2 and q3:
            flowed = _sum_flow(
                [q1.get("metrics") or {}, q2.get("metrics") or {}, q3.get("metrics") or {}]
            )
            emit("Q3", flowed, "sum_q1_q2_q3_standalone")
        elif q3 and q3.get("period_basis") == PERIOD_BASIS_SINGLE:
            skipped.append(f"{code} FY:{fy_end_s} Q3_standalone_incomplete_quarters")

        fy_row = by_q.get("FY")
        if fy_row and fy_row.get("metrics"):
            emit("FY", dict(fy_row["metrics"]), "full_year")
        elif q1 and q2 and q3 and by_q.get("Q4"):
            flowed = _sum_flow(
                [
                    q1.get("metrics") or {},
                    q2.get("metrics") or {},
                    q3.get("metrics") or {},
                    (by_q["Q4"].get("metrics") or {}),
                ]
            )
            flowed.update(_pick_stock(by_q["Q4"].get("metrics")))
            emit("FY", flowed, "sum_q1_q2_q3_q4_standalone")

    for row in passthrough:
        out.append(_public_golden(row, cumulative_build="passthrough_cumulative"))

    for msg in skipped:
        print(f"skip: {msg}", file=sys.stderr)

    return merge_goldens(out)



def load_json_path(path: Path) -> Any:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".jsonl":
        rows = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
        return rows
    return json.loads(text)


def collect_from_input(path: Path) -> list[Any]:
    if path.is_dir():
        blobs: list[Any] = []
        files = sorted(path.rglob("*"))
        for p in files:
            if p.suffix.lower() not in {".json", ".jsonl"}:
                continue
            if p.name.startswith("schema"):
                continue
            if p.name in {"INDEX.json", "seed.sql"}:
                continue
            blobs.append(load_json_path(p))
        return blobs
    return [load_json_path(path)]


def records_from_blobs(blobs: list[Any], fetched_at: str) -> list[dict[str, Any]]:
    raw_objs: list[Any] = []
    for blob in blobs:
        raw_objs.extend(unwrap(blob))
    intermediate: list[dict[str, Any]] = []
    for obj in raw_objs:
        if not isinstance(obj, dict):
            continue
        rec = normalize_raw_period(obj, fetched_at)
        if rec is not None:
            intermediate.append(rec)
    return build_cumulative_goldens(intermediate)


def write_goldens(rows: list[dict[str, Any]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for row in rows:
        if row.get("period_basis") != PERIOD_BASIS_CUMULATIVE:
            print(
                f"refuse non-cumulative golden {row.get('code')} {row.get('period_end')}",
                file=sys.stderr,
            )
            continue
        dest = out_dir / f"{row['code4']}_{row['period_end']}.json"
        dest.write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(dest)
    return written



def live_get_json(url: str) -> Any:
    import urllib.request

    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {scheme or 'missing'}")
    headers = {
        "User-Agent": "stock-analyzer-xbrl-golden/1.0",
        "Accept": "application/json",
    }
    token = os.environ.get("IRBANK_API_KEY") or os.environ.get("IRBANK_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310
        raw = resp.read()
    return json.loads(raw.decode("utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", help="JSON/JSONL file or directory (default path)")
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "xbrl_golden"))
    ap.add_argument("--live", action="store_true", help="fetch URL (requires --url)")
    ap.add_argument("--url", help="JSON URL used only with --live")
    args = ap.parse_args(argv)

    fetched_at = datetime.now(JST).isoformat(timespec="seconds")
    blobs: list[Any] = []
    if args.live:
        if not args.url:
            raise SystemExit("--live requires --url (default is --input file; no URL is hardcoded)")
        blobs.append(live_get_json(args.url))
    if args.input:
        blobs.extend(collect_from_input(Path(args.input)))
    if not blobs:
        raise SystemExit("pass --input PATH (file or dir). --live --url is optional")

    rows = records_from_blobs(blobs, fetched_at)
    if not rows:
        raise SystemExit("no cumulative golden records found in input")
    written = write_goldens(rows, Path(args.out_dir))
    print(f"wrote {len(written)} cumulative golden files under {Path(args.out_dir)}")
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
