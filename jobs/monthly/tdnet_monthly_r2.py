"""TDnet の月次PDFを data/monthly へ保存する。

使い方:
  python jobs/monthly/tdnet_monthly_r2.py 1
  python jobs/monthly/tdnet_monthly_r2.py 30 --dest /tmp/monthly

N は今日(JST)から遡る日数（当日を含む）。日次 cron は 7（取りこぼしの拾い直し）。
一覧は TDnet の開示一覧（jobs/_tdnet.py。KAB-294）。表題が LIST_KEYWORDS を含み、is_monthly_title が通すものを取る。
ネットワークはテストから差し替える。
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR.parents[1]) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR.parents[1]))

from jobs import _tdnet as tdnet_list
from jobs._r2common import (
    abort_if_disconnected,
    date_window,
    fail_if_mount_down,
    http_get as _http_get,
    is_mount_disconnect_error,
    load_stock_product_map,
    sanitize_filename_part,
    yyyymmdd_from_pubdate,
)
from app.share_paths import share_path
from app.xbrl.codes import jq_lookup_code
from jobs.monthly.publication_claim import write_pdf_meta

DEFAULT_DEST = str(share_path("monthly"))
PDF_BASE = tdnet_list.TDNET_BASE
USER_AGENT = "stock-analyzer-tdnet-monthly/1.0"
SLEEP_SEC = 0.4
HTTP_TIMEOUT = 30.0

LIST_KEYWORDS = (
    "月次",
    "売上速報",
    "前年比",
    "営業概況",
    "Monthly Report",
    "店舗売上",
    "月度",
    "売上推移",
    "売上高推移",
    "業績速報",
    "前年同月比",
    "売上の状況",
)
INCLUDE_EXTRA = (
    "売上速報",
    "売上高速報",
    "前年比",
    "前年同月比",
    "営業概況",
    "店舗売上",
    "販売状況",
    "売上推移",
    "売上高推移",
    "受注速報",
    "業績速報",
    "売上の状況",
    "売上高についてのお知らせ",
    "売上高のお知らせ",
    "月次IRニュース",
)
# 月度 単体は人事・新株予約権まで拾う。売上/受注などの月次らしい語が要る。
_MONTHLY_HINTS = (
    "売上",
    "受注",
    "販売",
    "営業",
    "業績",
    "速報",
    "概況",
    "推移",
    "IRレポート",
    "IRニュース",
    "前年",
    "店舗",
)
EXCLUDE_UNLESS_MONTHLY = (
    "決算短信",
    "有報",
    "人事",
    "自己株式",
    "新株予約権",
    "月間行使",
    "四半期短信",
    "業績予想",
)
_INBS_PDF_RE = re.compile(r"/inbs/([0-9A-Za-z]+)\.pdf", re.I)

# Infra funds share ProdCat 013/022 with REITs; J-Quants has no dedicated category.
INFRA_FUND_PRODUCT_CATEGORIES = ("013", "022")
INFRA_FUND_NAME_NEEDLE = "インフラ"
INFRA_FUND_FALLBACK_NEEDLE = "インフラ投資法人"
# 8798 アドバンスクリエイト: 月次推移がグラフ画像のみでテキスト不可
# 8739 スパークス: 売上ではなく運用資産残高のストック表
MONTHLY_SKIP_CODES = frozenset({"8739", "8798", "8985"})


def is_monthly_title(title: str) -> bool:
    t = title or ""
    if "月次" in t:
        return True
    extra = any(k in t for k in INCLUDE_EXTRA) or ("monthly report" in t.lower())
    if not extra and "月度" in t:
        extra = any(h in t for h in _MONTHLY_HINTS)
    if not extra:
        return False
    if any(k in t for k in EXCLUDE_UNLESS_MONTHLY):
        return False
    return True


def tdnet_pdf_id(document_url: str) -> str | None:
    """TDnet の inbs ファイル ID。"""
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
    except OSError as e:
        if is_mount_disconnect_error(e):
            raise
        return False


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    """このジョブの識別 UA。403 と AIA は _r2common.http_get。"""
    return _http_get(url, timeout=timeout, user_agent=USER_AGENT)


def has_list_keyword(title: str) -> bool:
    """LIST_KEYWORDS のどれかを含むか（大文字小文字は見ない）。is_monthly_title の前の絞り込み。"""
    t = (title or "").casefold()
    return any(k.casefold() in t for k in LIST_KEYWORDS)


def union_monthly_records(
    start: date,
    end: date,
    get: Callable[[str], bytes] | None = None,
) -> list[dict]:
    """TDnet の開示一覧（jobs/_tdnet.py）から月次の表題の行を取る。PDF ID で重複を落とす。"""
    getter = http_get if get is None else get
    by_pdf: dict[str, dict] = {}
    for rec in tdnet_list.fetch_days(start, end, getter, SLEEP_SEC):
        title = str(rec.get("title") or "")
        if not (has_list_keyword(title) and is_monthly_title(title)):
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
    rc = fail_if_mount_down("tdnet_monthly_r2", dest, require_existing=False)
    if rc is not None:
        return rc
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
        pubdate = str(rec.get("pubdate") or "")
        if already_have(path):
            if pubdate:
                write_pdf_meta(path, pubdate, pdf_id)
            skipped += 1
            print(f"skip exists {rel}", flush=True)
            continue
        try:
            n = download_pdf(pdf_id, path)
            if pubdate:
                write_pdf_meta(path, pubdate, pdf_id)
            saved += 1
            print(f"saved {rel} ({n} bytes)", flush=True)
        except Exception as e:
            down = abort_if_disconnected("tdnet_monthly_r2", dest, e)
            if down is not None:
                return down
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
    ap = argparse.ArgumentParser(description="Fetch TDnet monthly PDFs into data/monthly")
    ap.add_argument("days", type=int, help="days back from today JST (inclusive)")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    args = ap.parse_args(argv)
    if args.days < 1:
        print("days must be a positive integer", file=sys.stderr)
        return 2
    return run(args.days, Path(args.dest))


if __name__ == "__main__":
    raise SystemExit(main())
