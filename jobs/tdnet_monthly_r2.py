"""TDnet の月次PDFを Cloudflare R2 mount へ保存する。

使い方:
  python jobs/tdnet_monthly_r2.py 1
  python jobs/tdnet_monthly_r2.py 30 --dest /tmp/monthly

N は今日(JST)から遡る日数（当日を含む）。ネットワークと app/ はテストから分離する。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import unicodedata
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_DEST = "/home/stock/r2-share/monthly"
LIST_BASE = "https://webapi.yanoshin.jp/webapi/tdnet/list"
PDF_BASE = "https://www.release.tdnet.info/inbs"
USER_AGENT = "stock-analyzer-tdnet-monthly/1.0"
LIST_LIMIT = 300
SLEEP_SEC = 0.4
HTTP_TIMEOUT = 30.0

# 月次を取りこぼさないよう、タイトルに「月次」が無い売上速報なども列挙して union する
LIST_KEYWORDS = (
    "月次",
    "売上速報",
    "前年比",
    "営業概況",
    "Monthly Report",
    "店舗売上",
)
INCLUDE_EXTRA = ("売上速報", "前年比", "営業概況", "店舗売上")
EXCLUDE_UNLESS_MONTHLY = ("決算短信", "有報", "人事", "自己株式")
_INBS_PDF_RE = re.compile(r"/inbs/([0-9A-Za-z]+)\.pdf", re.I)
_BAD_FILENAME_RE = re.compile(r'[\x00-\x1f\\/:*?"<>|]')


REPO_ROOT = Path(__file__).resolve().parent.parent

# Infra funds share ProdCat 013/022 with REITs; J-Quants has no dedicated category.
# Monthly skips only 013/022 whose name contains インフラ (not all REITs).
INFRA_FUND_PRODUCT_CATEGORIES = ("013", "022")
INFRA_FUND_NAME_NEEDLE = "インフラ"
INFRA_FUND_FALLBACK_NEEDLE = "インフラ投資法人"
# display_code（4桁）。月次PDFを保存しない発行体。
MONTHLY_SKIP_CODES = frozenset({"8985"})


def is_monthly_title(title: str) -> bool:
    t = title or ""
    if "月次" in t:
        return True
    extra = any(k in t for k in INCLUDE_EXTRA) or ("monthly report" in t.lower())
    if not extra:
        return False
    if any(k in t for k in EXCLUDE_UNLESS_MONTHLY):
        return False
    return True


def tdnet_pdf_id(document_url: str) -> str | None:
    """TDnet の inbs ファイル ID。やのしん numeric id は使わない。"""
    if not document_url:
        return None
    m = _INBS_PDF_RE.search(document_url)
    if not m:
        return None
    name = m.group(1)
    if name.lower().endswith(".pdf"):
        name = name[:-4]
    return name or None


def display_code(company_code: str) -> str:
    code = re.sub(r"\s+", "", company_code or "")
    if len(code) >= 5:
        return code[:4]
    return code or "0000"


def jq_lookup_code(company_code: str) -> str:
    """stocks 照合専用。display_code に末尾 0 を付けて J-Quants 5桁にする。"""
    return display_code(company_code) + "0"


def should_skip_infra_fund(
    product_category: str | None,
    stock_name: str | None,
    company_name: str | None = None,
) -> bool:
    """インフラファンドの月次PDFを落とす。REIT 全除外や 011 は対象外。"""
    cat = (product_category or "").strip()
    names = (stock_name or "", company_name or "")
    has_infra = any(INFRA_FUND_NAME_NEEDLE in n for n in names)
    if cat in INFRA_FUND_PRODUCT_CATEGORIES:
        return has_infra
    if not cat:
        return INFRA_FUND_FALLBACK_NEEDLE in (company_name or "")
    return False


def download_skip_reason(
    company_code: str,
    company_name: str | None,
    stock_map: Mapping[str, tuple[str | None, str | None]],
) -> str | None:
    """Download 前の skip 理由。`skip {return}` でログする。None なら取得してよい。

    stock_map が空（DB 欠落・非 sqlite・読取失敗）なら unknown-stock 判定はしない。
    インフラ判定は従来どおり（空 map では company_name フォールバック）。
    MONTHLY_SKIP_CODES は map の有無にかかわらず落とす。
    """
    jq = jq_lookup_code(company_code)
    disp = display_code(company_code)
    if stock_map and jq not in stock_map:
        label = company_name or ""
        return f"unknown-stock {disp} {label}"
    cat, stock_name = stock_map.get(jq, (None, None))
    if should_skip_infra_fund(cat, stock_name, company_name):
        label = stock_name or company_name or ""
        return f"infra-fund {disp} {label}"
    if disp in MONTHLY_SKIP_CODES:
        label = stock_name or company_name or ""
        return f"excluded-code {disp} {label}"
    return None


def _dotenv_setdefault(text: str, environ: dict[str, str]) -> None:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[7:].strip()
        if "=" not in s:
            continue
        key, _, val = s.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
            val = val[1:-1]
        environ.setdefault(key, val)


def resolve_database_url(
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
    repo_root: Path | None = None,
) -> str:
    """DATABASE_URL env -> repo .env (setdefault) -> <repo>/stock.db. env is not overwritten."""
    env = os.environ if environ is None else environ
    root = REPO_ROOT if repo_root is None else repo_root
    url = (env.get("DATABASE_URL") or "").strip()
    if url:
        return url
    merged = dict(env)
    src = root / ".env" if dotenv_path is None else dotenv_path
    try:
        _dotenv_setdefault(src.read_text(encoding="utf-8"), merged)
    except OSError:
        pass
    url = (merged.get("DATABASE_URL") or "").strip()
    if url:
        return url
    return f"sqlite:///{root / 'stock.db'}"


def sqlite_path_from_url(url: str, repo_root: Path | None = None) -> Path | None:
    raw = (url or "").strip()
    if not raw.startswith("sqlite:///"):
        return None
    rest = raw[len("sqlite:///"):].split("?", 1)[0]
    if not rest or rest == ":memory:":
        return None
    pth = Path(rest)
    if not pth.is_absolute():
        pth = (repo_root or REPO_ROOT) / pth
    return pth


def load_stock_product_map(
    environ: Mapping[str, str] | None = None,
    dotenv_path: Path | None = None,
    repo_root: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, tuple[str | None, str | None]]:
    """code -> (product_category, name). empty dict on missing/non-sqlite/failure."""
    try:
        root = REPO_ROOT if repo_root is None else repo_root
        if db_path is None:
            url = resolve_database_url(
                environ=environ, dotenv_path=dotenv_path, repo_root=root
            )
            db_path = sqlite_path_from_url(url, repo_root=root)
            if db_path is None:
                print("warn stocks db not sqlite; infra skip uses company_name only", flush=True)
                return {}
        if not db_path.is_file():
            print(f"warn stocks db missing {db_path}", flush=True)
            return {}
        conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=8)
        try:
            rows = conn.execute(
                "SELECT code, product_category, name FROM stocks"
            ).fetchall()
        finally:
            conn.close()
        out: dict[str, tuple[str | None, str | None]] = {}
        for code, cat, name in rows:
            if not code:
                continue
            out[str(code)] = (
                None if cat is None else str(cat),
                None if name is None else str(name),
            )
        return out
    except Exception as e:
        print(f"warn stocks db unreadable: {e}", flush=True)
        return {}


def sanitize_filename_part(s: str) -> str:
    # NFKC: 全角数字・英字を半角に（既存 R2 オブジェクト名と揃える）
    s = unicodedata.normalize("NFKC", s or "")
    s = _BAD_FILENAME_RE.sub("_", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s or "unknown"


def yyyymmdd_from_pubdate(pubdate: str) -> str:
    s = (pubdate or "").strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return f"{s[0:4]}{s[5:7]}{s[8:10]}"
    digits = re.sub(r"\D", "", s)[:8]
    if len(digits) == 8:
        return digits
    raise ValueError(f"unusable pubdate: {pubdate!r}")


def dest_filename(pubdate: str, company_code: str, company_name: str, title: str) -> str:
    ymd = yyyymmdd_from_pubdate(pubdate)
    code = display_code(company_code)
    name = sanitize_filename_part(company_name)
    t = sanitize_filename_part(title)
    stem = f"{ymd}_{code}_{name}_{t}"
    if len(stem) > 180:
        stem = stem[:180].rstrip(" ._")
    return f"{stem}.pdf"


def dest_path(
    dest: Path, pubdate: str, company_code: str, company_name: str, title: str
) -> Path:
    """monthly/{code}/YYYYMMDD_CODE_….pdf。code は display_code（TDnet as-is）。"""
    code = display_code(company_code)
    return dest / code / dest_filename(pubdate, company_code, company_name, title)


def already_have(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def jst_today(now: datetime | None = None) -> date:
    if now is None:
        now = datetime.now(JST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=JST)
    else:
        now = now.astimezone(JST)
    return now.date()


def date_window(days: int, today: date | None = None) -> tuple[date, date]:
    if days < 1:
        raise ValueError("days must be a positive integer")
    end = today or jst_today()
    start = end - timedelta(days=days - 1)
    return start, end


def parse_list_body(text: str) -> list[dict]:
    text = (text or "").strip()
    if not text:
        return []
    if text[0] not in "{[":
        left = text.find("(")
        right = text.rfind(")")
        if left != -1 and right > left:
            text = text[left + 1 : right]
    data = json.loads(text)
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        inner = None
        for key in ("Tdnet", "TDnet", "tdnet"):
            cand = item.get(key)
            if isinstance(cand, dict):
                inner = cand
                break
        if inner is None and ("title" in item or "document_url" in item):
            inner = item
        if inner:
            out.append(inner)
    return out


def list_url(start: date, end: date, keyword: str, by_id: int | None = None) -> str:
    span = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"
    q = {"keyword": keyword, "limit": str(LIST_LIMIT)}
    if by_id is not None:
        q["by_id"] = str(by_id)
    return f"{LIST_BASE}/{span}.json?{urlencode(q)}"


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    scheme = urlparse(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"unsupported URL scheme: {scheme or 'missing'}")
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
    last: Exception | None = None
    for attempt in range(2):
        try:
            with urlopen(req, timeout=timeout) as resp:  # nosec B310
                return resp.read()
        except HTTPError as e:
            last = e
            if e.code < 500 and e.code not in (408, 429):
                raise
        except (URLError, TimeoutError, OSError) as e:
            last = e
        if attempt == 0:
            time.sleep(1.5)
    assert last is not None
    raise last


def _min_yanoshin_id(records: list[dict]) -> int | None:
    ids: list[int] = []
    for rec in records:
        raw = rec.get("id")
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    return min(ids) if ids else None


def fetch_keyword_items(start: date, end: date, keyword: str) -> list[dict]:
    out: list[dict] = []
    by_id: int | None = None
    seen_by: set[int] = set()
    while True:
        url = list_url(start, end, keyword, by_id=by_id)
        body = http_get(url).decode("utf-8", errors="replace")
        time.sleep(SLEEP_SEC)
        batch = parse_list_body(body)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < LIST_LIMIT:
            break
        nxt = _min_yanoshin_id(batch)
        if nxt is None or nxt in seen_by:
            break
        seen_by.add(nxt)
        by_id = nxt
    return out


def union_monthly_records(start: date, end: date) -> list[dict]:
    by_pdf: dict[str, dict] = {}
    for kw in LIST_KEYWORDS:
        for rec in fetch_keyword_items(start, end, kw):
            title = str(rec.get("title") or "")
            if not is_monthly_title(title):
                continue
            pdf_id = tdnet_pdf_id(str(rec.get("document_url") or ""))
            if not pdf_id:
                print(f"skip no-pdf-id {rec.get('company_code')} {title}", flush=True)
                continue
            by_pdf.setdefault(pdf_id, rec)
    rows = list(by_pdf.items())
    rows.sort(key=lambda kv: str(kv[1].get("pubdate") or ""))
    return [{"_pdf_id": pdf_id, **rec} for pdf_id, rec in rows]


def download_pdf(pdf_id: str, dest: Path) -> int:
    url = f"{PDF_BASE}/{pdf_id}.pdf"
    data = http_get(url)
    if not data or not data.startswith(b"%PDF"):
        raise ValueError(f"not a PDF ({len(data)} bytes)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return len(data)


def run(days: int, dest: Path) -> int:
    start, end = date_window(days)
    dest.mkdir(parents=True, exist_ok=True)
    print(
        f"tdnet_monthly_r2 start days={days} dest={dest} "
        f"range={start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}",
        flush=True,
    )
    records = union_monthly_records(start, end)
    stock_map = load_stock_product_map()
    if not stock_map:
        print("warn stocks map empty; unknown-stock skip disabled", flush=True)
    saved = skipped = failed = 0
    for rec in records:
        pdf_id = rec["_pdf_id"]
        company_code = str(rec.get("company_code") or "")
        company_name = str(rec.get("company_name") or "")
        reason = download_skip_reason(company_code, company_name, stock_map)
        if reason:
            skipped += 1
            print(f"skip {reason}", flush=True)
            continue
        path = dest_path(
            dest,
            str(rec.get("pubdate") or ""),
            company_code,
            company_name,
            str(rec.get("title") or ""),
        )
        rel = path.relative_to(dest)
        if already_have(path):
            skipped += 1
            print(f"skip exists {rel}", flush=True)
            continue
        try:
            n = download_pdf(pdf_id, path)
            saved += 1
            print(f"saved {rel} ({n} bytes)", flush=True)
        except Exception as e:
            failed += 1
            print(f"fail {rel} id={pdf_id}: {e}", flush=True)
        time.sleep(SLEEP_SEC)
    print(
        f"tdnet_monthly_r2 done listed={len(records)} "
        f"saved={saved} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch TDnet monthly PDFs into R2 mount")
    ap.add_argument("days", type=int, help="days back from today JST (inclusive)")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    args = ap.parse_args(argv)
    if args.days < 1:
        print("days must be a positive integer", file=sys.stderr)
        return 2
    return run(args.days, Path(args.dest))


if __name__ == "__main__":
    raise SystemExit(main())
