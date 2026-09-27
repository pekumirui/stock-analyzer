"""TDnet の XBRL ZIP を Cloudflare R2 mount へ保存する。

使い方:
  python jobs/tdnet_xbrl_r2.py 1
  python jobs/tdnet_xbrl_r2.py 30 --dest /tmp/xbrl

N は今日(JST)から遡る日数（当日を含む）。ネットワークと app/ はテストから分離する。

skip の正本は dest/manifest/{YYYY-MM-DD}.jsonl の xbrl_id。ファイル名や .part は skip に使わない。
マスタ無し・インフラは落とさず _unmatched/ へ置く（TDnet の約30日保持）。
ZIP URL は list の url_xbrl、または .zip の document_url。取れなければその開示は skip する。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _r2common import (  # noqa: E402
    date_window,
    http_get as _http_get,
    load_stock_product_map as _load_stock_product_map,
    parse_list_body,
    sanitize_filename_part,
    yyyymmdd_from_pubdate,
)
from app.config import XBRL_DEST  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_DEST = XBRL_DEST
LIST_BASE = "https://webapi.yanoshin.jp/webapi/tdnet/list"
ZIP_BASE = "https://www.release.tdnet.info/inbs"
USER_AGENT = "stock-analyzer-tdnet-xbrl/1.0"
LIST_LIMIT = 1000
SLEEP_SEC = 0.4
HTTP_TIMEOUT = 30.0
STEM_MAX = 180
UNMATCHED_DIR = "_unmatched"
NOCODE_DIR = "_nocode"

_INBS_ZIP_RE = re.compile(r"/inbs/([0-9A-Za-z]+)\.zip", re.I)
_ZIP_NAME_RE = re.compile(r"([0-9A-Za-z]+)\.zip(?:\?|$)", re.I)
_CODE4_HEAD_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")
_NON_SQLITE_UNMATCHED = "warn stocks db not sqlite; infra unmatched uses company_name only"

# Infra funds share ProdCat 013/022 with REITs; J-Quants has no dedicated category.
# Monthly skips these; XBRL still downloads them under _unmatched/.
INFRA_FUND_PRODUCT_CATEGORIES = ("013", "022")
INFRA_FUND_NAME_NEEDLE = "インフラ"
INFRA_FUND_FALLBACK_NEEDLE = "インフラ投資法人"


class ManifestRow(TypedDict):
    xbrl_id: str
    code: str
    code4: str
    pubdate: str
    time: str
    title: str
    path: str
    bytes: int
    sha256: str
    fetched_at: str


@dataclass(frozen=True)
class DestRoute:
    rel_dir: Path
    code4: str
    reason: str | None


def to_code4(company_code: str) -> str:
    """TDnet/J-Quants の発行体コードを表示4桁にする。末尾0を無条件には落とさない。

    - 空白除去。空なら ""（呼び出し側が _unmatched/_nocode）
    - 既に4文字ならそのまま（7203, 262A）
    - 5文字以上で先頭4が [0-9][0-9A-Z]{3} なら先頭4
      （92790→9279, 72030→7203, 269A0→269A, 386A0→386A）
    - それ以外は除去後の文字列をそのまま返す
    """
    code = re.sub(r"\s+", "", company_code or "")
    if not code:
        return ""
    if len(code) == 4:
        return code
    if len(code) >= 5:
        head = code[:4]
        if _CODE4_HEAD_RE.match(head):
            return head
    return code


def jq_lookup_code(company_code: str) -> str:
    """stocks 照合専用。to_code4 に末尾 0 を付けて J-Quants 5桁にする。"""
    code4 = to_code4(company_code)
    if not code4:
        return ""
    return code4 + "0"


def should_skip_infra_fund(
    product_category: str | None,
    stock_name: str | None,
    company_name: str | None = None,
) -> bool:
    """インフラファンドか。REIT 全除外や 011 は対象外。"""
    cat = (product_category or "").strip()
    names = (stock_name or "", company_name or "")
    has_infra = any(INFRA_FUND_NAME_NEEDLE in n for n in names)
    if cat in INFRA_FUND_PRODUCT_CATEGORIES:
        return has_infra
    if not cat:
        return INFRA_FUND_FALLBACK_NEEDLE in (company_name or "")
    return False


def unmatched_reason(
    company_code: str,
    company_name: str | None,
    stock_map: Mapping[str, tuple[str | None, str | None]],
) -> str | None:
    """保存先を _unmatched にする理由。None なら通常の {code4}/。

    戻りは "unknown-stock" | "infra-fund" | None。ダウンロードはどちらでも行う。
    stock_map が空なら unknown-stock 判定はしない。インフラは空 map でも
    company_name フォールバックする。
    """
    jq = jq_lookup_code(company_code)
    if stock_map and jq not in stock_map:
        return "unknown-stock"
    cat, stock_name = stock_map.get(jq, (None, None))
    if should_skip_infra_fund(cat, stock_name, company_name):
        return "infra-fund"
    return None


def dest_route(
    company_code: str,
    company_name: str | None,
    stock_map: Mapping[str, tuple[str | None, str | None]],
) -> DestRoute:
    raw = re.sub(r"\s+", "", company_code or "")
    if not raw:
        return DestRoute(
            rel_dir=Path(UNMATCHED_DIR) / NOCODE_DIR,
            code4=NOCODE_DIR,
            reason="nocode",
        )
    code4 = to_code4(raw)
    reason = unmatched_reason(raw, company_name, stock_map)
    if reason is None:
        return DestRoute(rel_dir=Path(code4), code4=code4, reason=None)
    return DestRoute(
        rel_dir=Path(UNMATCHED_DIR) / code4,
        code4=code4,
        reason=reason,
    )


def load_stock_product_map(
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
    repo_root: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """XBRL の非 sqlite 警告だけ月次と違う。中身は _r2common。"""
    return _load_stock_product_map(
        environ=environ,
        dotenv_path=dotenv_path,
        repo_root=repo_root,
        db_path=db_path,
        non_sqlite_warn=_NON_SQLITE_UNMATCHED,
    )


def pubdate_iso_date(pubdate: str) -> str:
    ymd = yyyymmdd_from_pubdate(pubdate)
    return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"


def pubdate_time(pubdate: str) -> str:
    s = (pubdate or "").strip()
    if len(s) >= 19 and s[10] in " T":
        return s[11:19]
    return ""


def now_jst_iso(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(JST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)
    return now.isoformat()


def dest_filename(pubdate: str, code4: str, title: str, xbrl_id: str) -> str:
    ymd = yyyymmdd_from_pubdate(pubdate)
    code = sanitize_filename_part(code4) or NOCODE_DIR
    short = sanitize_filename_part(title)
    xid = sanitize_filename_part(xbrl_id) or "unknown"
    prefix = f"{ymd}_{code}_"
    suffix = f"_{xid}"
    budget = STEM_MAX - len(prefix) - len(suffix)
    if budget < 1:
        stem = f"{ymd}_{code}_{xid}"
        if len(stem) > STEM_MAX:
            stem = stem[:STEM_MAX].rstrip(" ._")
        return f"{stem}.zip"
    if len(short) > budget:
        short = short[:budget].rstrip(" ._") or "x"
    return f"{prefix}{short}{suffix}.zip"


def dest_path(
    dest: Path,
    pubdate: str,
    company_code: str,
    title: str,
    xbrl_id: str,
    company_name: str | None = None,
    stock_map: Mapping[str, tuple[str | None, str | None]] | None = None,
) -> Path:
    route = dest_route(company_code, company_name, stock_map or {})
    return dest / route.rel_dir / dest_filename(pubdate, route.code4, title, xbrl_id)


def part_path(final: Path) -> Path:
    return final.with_name(final.name + ".part")


def tdnet_xbrl_id(url: str) -> str | None:
    """TDnet の inbs ZIP ID。やのしん numeric id は使わない。"""
    if not url:
        return None
    m = _INBS_ZIP_RE.search(url)
    if m:
        return m.group(1) or None
    m = _ZIP_NAME_RE.search(url)
    if m:
        return m.group(1) or None
    return None


def xbrl_id_from_record(rec: Mapping[str, object]) -> str | None:
    for key in ("url_xbrl", "xbrl_url", "document_url"):
        got = tdnet_xbrl_id(str(rec.get(key) or ""))
        if got:
            return got
    return None


def list_url(start: date, end: date, by_id: int | None = None) -> str:
    span = f"{start:%Y%m%d}-{end:%Y%m%d}"
    q = {"hasXBRL": "1", "limit": str(LIST_LIMIT)}
    if by_id is not None:
        q["by_id"] = str(by_id)
    return f"{LIST_BASE}/{span}.json?{urlencode(q)}"


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    """このジョブの識別 UA。403 と AIA は _r2common.http_get。"""
    return _http_get(url, timeout=timeout, user_agent=USER_AGENT)


def fetch_xbrl_records(
    start: date,
    end: date,
    get: Callable[[str], bytes] | None = None,
) -> list[dict]:
    """List one calendar day at a time. Multi-day span URLs and by_id paging are not used.

    Yanoshin truncates at `limit` (and reports that cap as total_count). by_id next
    pages come back empty, so a 30-day span with limit=300 stops at 300 items.
    """
    getter = http_get if get is None else get
    out: list[dict] = []
    cur = start
    while cur <= end:
        url = list_url(cur, cur)
        body = getter(url).decode("utf-8", errors="replace")
        time.sleep(SLEEP_SEC)
        batch = parse_list_body(body)
        if len(batch) == LIST_LIMIT:
            print(
                f"warn list truncated for {cur.isoformat()}: "
                f"got {LIST_LIMIT} items (day may be incomplete)",
                flush=True,
            )
        out.extend(batch)
        cur += timedelta(days=1)
    rows: list[dict] = []
    seen_ids: set[str] = set()
    for rec in out:
        xid = xbrl_id_from_record(rec)
        title = str(rec.get("title") or "")
        if not xid:
            print(
                f"skip no-xbrl-id {rec.get('company_code')} {title}",
                flush=True,
            )
            continue
        if xid in seen_ids:
            continue
        seen_ids.add(xid)
        rows.append({"_xbrl_id": xid, **rec})
    rows.sort(key=lambda r: str(r.get("pubdate") or ""))
    return rows


def validate_zip(path: Path) -> None:
    """ZipFile.testzip() が None なら OK。壊れていれば ValueError。"""
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
    except zipfile.BadZipFile as e:
        raise ValueError(f"corrupt zip: {e}") from e
    if bad is not None:
        raise ValueError(f"corrupt zip member {bad}")


def save_zip_bytes(data: bytes, dest: Path) -> int:
    """bytes を dest.name.zip.part に書き、testzip 成功後に dest へ replace。

    壊れていれば .part を消し、最終名には置かない。
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = part_path(dest)
    try:
        part.write_bytes(data)
        validate_zip(part)
        part.replace(dest)
    except Exception:
        try:
            part.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return len(data)


def manifest_path(dest: Path, day: date | str) -> Path:
    if isinstance(day, date):
        name = day.isoformat()
    else:
        name = day
    return dest / "manifest" / f"{name}.jsonl"


def load_seen_xbrl_ids(dest: Path, start: date, end: date) -> set[str]:
    seen: set[str] = set()
    cur = start
    while cur <= end:
        path = manifest_path(dest, cur)
        cur += timedelta(days=1)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            xid = obj.get("xbrl_id")
            if xid:
                seen.add(str(xid))
    return seen


def build_manifest_row(
    *,
    xbrl_id: str,
    code: str,
    code4: str,
    pubdate: str,
    title: str,
    rel_path: str,
    n_bytes: int,
    sha256: str,
    fetched_at: str | None = None,
) -> ManifestRow:
    return {
        "xbrl_id": xbrl_id,
        "code": code,
        "code4": code4,
        "pubdate": pubdate,
        "time": pubdate_time(pubdate),
        "title": title,
        "path": rel_path,
        "bytes": n_bytes,
        "sha256": sha256,
        "fetched_at": fetched_at or now_jst_iso(),
    }


def append_manifest(dest: Path, row: Mapping[str, object]) -> None:
    day = pubdate_iso_date(str(row.get("pubdate") or ""))
    path = manifest_path(dest, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        f.flush()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run(days: int, dest: Path) -> int:
    start, end = date_window(days)
    dest.mkdir(parents=True, exist_ok=True)
    print(
        f"tdnet_xbrl_r2 start days={days} dest={dest} "
        f"range={start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}",
        flush=True,
    )
    records = fetch_xbrl_records(start, end)
    stock_map = load_stock_product_map()
    if not stock_map:
        print("warn stocks map empty; unknown-stock unmatched disabled", flush=True)
    seen = load_seen_xbrl_ids(dest, start, end)
    saved = skipped = failed = 0
    for rec in records:
        xid = str(rec["_xbrl_id"])
        if xid in seen:
            skipped += 1
            print(f"skip seen {xid}", flush=True)
            continue
        company_code = str(rec.get("company_code") or "")
        company_name = str(rec.get("company_name") or "")
        title = str(rec.get("title") or "")
        pubdate = str(rec.get("pubdate") or "")
        route = dest_route(company_code, company_name, stock_map)
        try:
            path = dest / route.rel_dir / dest_filename(
                pubdate, route.code4, title, xid
            )
            rel = path.relative_to(dest).as_posix()
        except Exception as e:
            failed += 1
            print(f"fail path id={xid}: {e}", flush=True)
            continue
        try:
            data = http_get(f"{ZIP_BASE}/{xid}.zip")
            n = save_zip_bytes(data, path)
            row = build_manifest_row(
                xbrl_id=xid,
                code=company_code,
                code4=route.code4,
                pubdate=pubdate,
                title=title,
                rel_path=rel,
                n_bytes=n,
                sha256=sha256_bytes(data),
            )
            append_manifest(dest, row)
            seen.add(xid)
            saved += 1
            tag = f" {route.reason}" if route.reason else ""
            print(f"saved {rel} ({n} bytes){tag}", flush=True)
        except Exception as e:
            failed += 1
            print(f"fail {rel} id={xid}: {e}", flush=True)
        time.sleep(SLEEP_SEC)
    print(
        f"tdnet_xbrl_r2 done listed={len(records)} "
        f"saved={saved} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch TDnet XBRL ZIPs into R2 mount")
    ap.add_argument("days", type=int, help="days back from today JST (inclusive)")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    args = ap.parse_args(argv)
    if args.days < 1:
        print("days must be a positive integer", file=sys.stderr)
        return 2
    return run(args.days, Path(args.dest))


if __name__ == "__main__":
    raise SystemExit(main())
