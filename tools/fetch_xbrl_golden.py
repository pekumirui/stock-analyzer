#!/usr/bin/env python3
"""Normalize IRBANK / MCP JSON into data/xbrl_golden/{code4}_{period_end}.json.

Default is **file input only** (no network):

  python tools/fetch_xbrl_golden.py --input PATH --out-dir data/xbrl_golden

PATH is a JSON/JSONL file or a directory of those. Nested MCP tool wrappers
(`result`, `content[].text`, `structuredContent`) and IRBANK field aliases
(売上高, sales, …) are accepted. Metrics are stored in million JPY.

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

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
    for key in WRAP_KEYS:
        if key in obj and obj[key] is not obj:
            inner = unwrap(obj[key], depth + 1)
            if inner:
                return inner
    if "metrics" in obj or (
        ("code" in obj or "code4" in obj or "ticker" in obj)
        and ("period_end" in obj or "period" in obj or "fiscal_year" in obj)
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


def normalize_record(obj: dict[str, Any], fetched_at: str) -> dict[str, Any] | None:
    raw_code = _code_from(obj)
    raw_period = _period_from(obj)
    if raw_code is None or raw_period is None:
        return None
    try:
        code, code4 = to_codes(raw_code)
        period_end = period_end_iso(raw_period)
    except ValueError:
        return None
    metrics = extract_metrics(obj)
    if not metrics:
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
    return {
        "code": code,
        "code4": code4,
        "period_end": period_end,
        "accounting_standard": std,
        "is_consolidated": is_consolidated,
        "metrics": {k: metrics[k] for k in GOLDEN_METRIC_KEYS if k in metrics},
        "unit": "million_jpy",
        "source": str(obj.get("source") or "irbank"),
        "fetched_at": str(obj.get("fetched_at") or fetched_at),
    }


def merge_goldens(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["code"], row["period_end"])
        if key not in by:
            by[key] = row
            continue
        cur = by[key]
        cur["metrics"].update(row["metrics"])
        if cur.get("accounting_standard") is None:
            cur["accounting_standard"] = row.get("accounting_standard")
        if row.get("is_consolidated"):
            cur["is_consolidated"] = True
    return [by[k] for k in sorted(by)]


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
            blobs.append(load_json_path(p))
        return blobs
    return [load_json_path(path)]


def records_from_blobs(blobs: list[Any], fetched_at: str) -> list[dict[str, Any]]:
    raw_objs: list[Any] = []
    for blob in blobs:
        raw_objs.extend(unwrap(blob))
    out: list[dict[str, Any]] = []
    for obj in raw_objs:
        if not isinstance(obj, dict):
            continue
        rec = normalize_record(obj, fetched_at)
        if rec is not None:
            out.append(rec)
    return merge_goldens(out)


def write_goldens(rows: list[dict[str, Any]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for row in rows:
        dest = out_dir / f"{row['code4']}_{row['period_end']}.json"
        dest.write_text(
            json.dumps(row, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(dest)
    return written


def live_get_json(url: str) -> Any:
    import urllib.request

    headers = {
        "User-Agent": "stock-analyzer-xbrl-golden/1.0",
        "Accept": "application/json",
    }
    token = os.environ.get("IRBANK_API_KEY") or os.environ.get("IRBANK_TOKEN")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
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
        raise SystemExit("no golden records found in input")
    written = write_goldens(rows, Path(args.out_dir))
    print(f"wrote {len(written)} golden files under {Path(args.out_dir)}")
    for p in written:
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
