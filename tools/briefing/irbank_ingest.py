#!/usr/bin/env python3
"""IR BANK が保管する過去の決算説明資料 PDF を data/briefing へ一度だけ取る（KAB-330）。

TDnet は約 30 日で開示を消す。毎日の分は jobs/market/tdnet_briefing_r2.py（cron 22:10）が取る。
このツールは cron に入れない。止め方は tools/tdnet_pdf/ingest.py と同じ。

会社ごとの開示一覧 https://irbank.net/{4桁}/ir を読む。読み終えた社は次回飛ばす（--rescan のときは読み直す）。表題は
app.market.briefing_title.is_material_title。PDF は
https://api.irbank.net/api/market-files/v1/tdnet/{18桁の末尾14桁}.pdf。
保存先とファイル名は TDnet のジョブと同じ。索引は briefing_documents（source=irbank）。
開示番号（18 桁、またはその末尾 14 桁）が索引にあれば取り直さない。TDnet 済みも同じ。

429 / 503 で止める（終了コード 0）。その銘柄は briefing_irbank_scans に書かないので、次の実行が続きから。
PDF の取得に失敗した銘柄も書かない（404 だけなら書く）。成功した PDF は索引にあるので取り直さない。
PDF を書く直前に shutil.disk_usage(--dest).free を見る。空きが MIN_FREE_BYTES 未満なら、
その社は scans に書かず stopped=disk_low、終了コード 0。一時ファイルの直前も同じ。
同じ code の既存索引と今回の一覧を合わせた最新の日付から 1 年以上前の PDF は data/briefing に置かない。
一時ファイルへ書き、その会社の分だけ jobs/market/briefing_offsite.py と同じバッチで OneDrive へ送る。
一致したものだけ索引を最初から onedrive で書く。最新そのものは手元に残す。
--limit はこの実行の社数。--dry-run は一覧だけ見て、PDF も索引も進捗も書かない。
--rescan は briefing_irbank_scans を無視して一覧を読み直す。--code とも --all とも併用できる。
索引にある PDF は取り直さない。付けないときの対象は、読み終えた社を飛ばす今までどおり。

  python tools/briefing/irbank_ingest.py --code 7203 --dry-run
  python tools/briefing/irbank_ingest.py --all --limit 80 --sleep 1
  deploy/cron-run.sh tools/briefing/irbank_ingest.py --all --limit 80 --sleep 1

最後の 1 行: irbank_briefing stopped=<done|limit|rate_limited|disk_low> companies=… listed=…
saved=… skipped=… reindexed=… missing=… failed=… remaining=…
remaining は実行後にまだ scans が無い対象社数。--rescan で読み直した社は数えない。
--dry-run は進捗を書かないので、見た社も残る。
表は jobs/migrate.py（init_db）が作る。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db, run_sqlite_write, upsert
from app.market.briefing_store import (
    SOURCE_IRBANK,
    STATUS_SAVED,
    STORAGE_LOCAL,
    STORAGE_ONEDRIVE,
    record_briefing,
    saved_document_ids,
)
from app.market.briefing_title import is_material_title
from app.models import BriefingDocument, BriefingIrbankScan, Stock
from jobs._r2common import (
    abort_if_disconnected,
    fail_if_mount_down,
    is_mount_disconnect_error,
)
from jobs.market import briefing_offsite
from jobs.monthly.tdnet_monthly_r2 import dest_path
from jobs.ops.backup_db import (
    RcloneRunFn,
    make_rclone_runner,
    offsite_settings_from_env,
)
from jobs.share_paths import share_path

LIST_URL = "https://irbank.net/{code}/ir"
PDF_URL = "https://api.irbank.net/api/market-files/v1/tdnet/{no}.pdf"
USER_AGENT = "stock-analyzer/1.0 (personal; tools/briefing)"
# df の GiB で 25G。未満なら PDF を書かず止める（KAB-355）。
MIN_FREE_BYTES = 25 * 1024 * 1024 * 1024
DEFAULT_DEST = str(share_path("briefing"))
JST = ZoneInfo("Asia/Tokyo")
# J-Quants ProdCat。KAB-325 の tanshin_covers と同じ。011 内国株券、021 外国株券、013 REIT。
_ORDINARY = frozenset({"011", "021"})
_REIT = "013"
_ETF = frozenset({"014", "023"})
_FUND_WORD = ("ファンド", "投資信託", "ＥＴＦ", "ＥＴＮ", "ETF", "ETN")
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
_ANCHOR = re.compile(r"<a\b(?P<attrs>[^>]*\bclass=\"nxq\"[^>]*)>(?P<text>.*?)</a>", re.I | re.S)
_HREF = re.compile(r'href="/([0-9A-Za-z]{4})/(\d{18})"')
_TITLE_ATTR = re.compile(r'title="([^"]*)"')
_ATTR_TITLE = re.compile(r"\|\s*(?P<title>.*?)（(?P<date>\d{4}/\d{2}/\d{2}) (?P<time>\d{2}:\d{2})提出）\s*$")
_TEXT_TIME = re.compile(r"（\d{2}:\d{2}）\s*$")
_DT = re.compile(r"<dt\b[^>]*>(\d{4}/\d{2}/\d{2})</dt>")
_LINEARIZED_LEN = re.compile(rb"/Linearized\s+[0-9.]+\s*/L\s+([0-9]+)")


class RateLimited(RuntimeError):
    pass


class Missing(RuntimeError):
    pass


@dataclass(frozen=True)
class Issuer:
    code: str
    code4: str
    name: str


@dataclass(frozen=True)
class Disclosure:
    document_id: str
    pdf_id: str
    code4: str
    title: str
    pubdate: str


@dataclass
class CompanyCounts:
    listed: int = 0
    saved: int = 0
    skipped: int = 0
    reindexed: int = 0
    missing: int = 0
    failed: int = 0


@dataclass
class CompanyOutcome:
    counts: CompanyCounts = field(default_factory=CompanyCounts)
    # ok は一覧を読み終えた。rate_limited は途中で止めたので進捗行を書かない。
    status: str = "ok"
    detail: str = ""


@dataclass
class RunStats:
    stopped: str = "done"
    companies: int = 0
    listed: int = 0
    saved: int = 0
    skipped: int = 0
    reindexed: int = 0
    missing: int = 0
    failed: int = 0
    remaining: int = 0
    exit_code: int = 0
    lines: list[str] = field(default_factory=list)

    def add(self, counts: CompanyCounts) -> None:
        self.companies += 1
        self.listed += counts.listed
        self.saved += counts.saved
        self.skipped += counts.skipped
        self.reindexed += counts.reindexed
        self.missing += counts.missing
        self.failed += counts.failed

    def line(self, *, dry_run: bool) -> str:
        head = "irbank_briefing dry_run" if dry_run else "irbank_briefing"
        return (
            f"{head} stopped={self.stopped} companies={self.companies} listed={self.listed} "
            f"saved={self.saved} skipped={self.skipped} reindexed={self.reindexed} "
            f"missing={self.missing} failed={self.failed} remaining={self.remaining}"
        )


def code5(raw: str) -> str:
    s = raw.strip().upper()
    if not _CODE_RE.fullmatch(s):
        raise ValueError(f"code は 4〜5 桁の英数字です: {raw}")
    return s + "0" if len(s) == 4 else s


def code4_of(code: str) -> str:
    text = code.strip().upper()
    return text[:4] if len(text) >= 5 else text


def _fundish(name: str | None) -> bool:
    text = unicodedata.normalize("NFKC", name or "")
    return any(word in text for word in _FUND_WORD)


def is_briefing_issuer(category: str | None, name: str | None) -> bool:
    """普通株と REIT。ETF・ETN・ファンドは除く。区分が空のときは名称で判断する。

    KAB-325（tanshin_covers）の is_cover_target と同じ商品区分。
    REIT は名称に「ファンド」または「インフラ」がある投資法人を除く。
    """
    cat = (category or "").strip()
    if cat in _ETF:
        return False
    if cat == _REIT:
        folded = unicodedata.normalize("NFKC", name or "")
        return "ファンド" not in folded and "インフラ" not in folded
    if cat in _ORDINARY:
        return True
    if cat:
        return False
    return not _fundish(name)


def exclusion_reason(category: str | None, name: str | None) -> str | None:
    if is_briefing_issuer(category, name):
        return None
    cat = (category or "").strip()
    if cat in _ETF:
        return "etf"
    if cat == _REIT:
        folded = unicodedata.normalize("NFKC", name or "")
        if "ファンド" in folded:
            return "fund"
        if "インフラ" in folded:
            return "infra"
        return "reit"
    if not cat:
        return "fund-name"
    return "category"


def _pick(group: list[Stock]) -> Stock:
    return min(group, key=lambda s: (not str(s.code).endswith("0"), str(s.code)))


def issuers(
    db: Session, codes: list[str] | None, *, rescan: bool = False,
) -> tuple[list[Issuer], list[str]]:
    """対象社。同じ 4 桁は 1 社。notes は --code で外した銘柄。

    rescan でなければ briefing_irbank_scans にある 4 桁は除く。
    読み終わったらトランザクションを閉じる。開いたまま PDF を落とすと、索引の
    UPSERT が同じプロセスの 2 本目の接続になる（KAB-332）。
    """
    db.rollback()
    try:
        return _issuers(db, codes, rescan=rescan)
    finally:
        db.rollback()


def _issuers(
    db: Session, codes: list[str] | None, *, rescan: bool = False,
) -> tuple[list[Issuer], list[str]]:
    stocks = db.query(Stock).order_by(Stock.code).all()
    by_code = {str(s.code): s for s in stocks}
    scanned4 = {code4_of(str(code)) for (code,) in db.query(BriefingIrbankScan.code)}
    notes: list[str] = []
    grouped: dict[str, list[Stock]] = {}

    def take(stock: Stock) -> None:
        key = code4_of(str(stock.code))
        grouped.setdefault(key, []).append(stock)

    if codes is None:
        for stock in stocks:
            if is_briefing_issuer(stock.product_category, stock.name):
                take(stock)
    else:
        seen4: set[str] = set()
        for code in codes:
            stock = by_code.get(code)
            if stock is None:
                notes.append(f"skip unknown {code}")
                continue
            reason = exclusion_reason(stock.product_category, stock.name)
            if reason:
                notes.append(f"skip excluded {code} {reason} {stock.name or ''}".rstrip())
                continue
            key = code4_of(code)
            if key in seen4:
                continue
            seen4.add(key)
            group = [
                s for s in stocks
                if code4_of(str(s.code)) == key and is_briefing_issuer(s.product_category, s.name)
            ]
            for item in group:
                take(item)

    out: list[Issuer] = []
    for key in sorted(grouped):
        stock = _pick(grouped[key])
        if key in scanned4 and not rescan:
            if codes is not None:
                notes.append(f"skip scanned {stock.code}")
            continue
        out.append(Issuer(code=str(stock.code), code4=key, name=str(stock.name or "")))
    return out, notes


def _title_and_pubdate(attr: str, link_text: str, before: str) -> tuple[str, str] | None:
    raw = html.unescape(attr).strip()
    matched = _ATTR_TITLE.search(raw)
    if matched:
        title = matched.group("title").strip()
        pub = f"{matched.group('date').replace('/', '-')} {matched.group('time')}:00"
        return title, pub
    text = _TEXT_TIME.sub("", re.sub(r"<[^>]+>", "", html.unescape(link_text))).strip()
    dates = _DT.findall(before)
    if not text or not dates:
        return None
    clock = re.search(r"（(\d{2}:\d{2})）\s*$", html.unescape(link_text))
    hhmm = clock.group(1) if clock else "00:00"
    return text, f"{dates[-1].replace('/', '-')} {hhmm}:00"


def parse_ir_list(page: str, code4: str) -> list[Disclosure]:
    """開示一覧の表題と 18 桁の開示番号。採否はここではしない。"""
    want = code4.upper()
    seen: set[str] = set()
    out: list[Disclosure] = []
    for match in _ANCHOR.finditer(page):
        href = _HREF.search(match.group("attrs"))
        if href is None:
            continue
        found, document_id = href.group(1).upper(), href.group(2)
        if found != want or document_id in seen:
            continue
        title_attr = _TITLE_ATTR.search(match.group("attrs"))
        parsed = _title_and_pubdate(
            title_attr.group(1) if title_attr else "",
            match.group("text"),
            page[:match.start()],
        )
        if parsed is None:
            continue
        title, pubdate = parsed
        seen.add(document_id)
        out.append(Disclosure(
            document_id=document_id,
            pdf_id=document_id[-14:],
            code4=want,
            title=title,
            pubdate=pubdate,
        ))
    return out


def select_materials(items: list[Disclosure]) -> list[Disclosure]:
    picked = [item for item in items if is_material_title(item.title)]
    picked.sort(key=lambda item: (item.pubdate, item.document_id), reverse=True)
    return picked


def _request(client: httpx.Client, url: str) -> httpx.Response:
    response = client.get(url)
    if response.status_code in (429, 503):
        raise RateLimited(f"http={response.status_code}")
    if response.status_code == 404:
        raise Missing("http=404")
    response.raise_for_status()
    return response


def fetch_list(client: httpx.Client, code4: str) -> str:
    return _request(client, LIST_URL.format(code=code4)).text


def fetch_pdf(client: httpx.Client, pdf_id: str) -> bytes:
    body = _request(client, PDF_URL.format(no=pdf_id)).content
    if not body.startswith(b"%PDF"):
        raise ValueError("not a pdf")
    linearized = _LINEARIZED_LEN.search(body[:1024])
    if linearized and len(body) < int(linearized.group(1)):
        raise ValueError(f"truncated pdf (bytes={len(body)} linearized_L={linearized.group(1)})")
    return body


def read_pdf(path: Path) -> bytes | None:
    try:
        if not path.is_file():
            return None
        data = path.read_bytes()
    except OSError as exc:
        if is_mount_disconnect_error(exc):
            raise
        return None
    if data.startswith(b"%PDF"):
        return data
    return None


def write_pdf(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def _index(
    issuer: Issuer,
    item: Disclosure,
    rel_path: str,
    data: bytes,
    *,
    storage: str = STORAGE_LOCAL,
) -> None:
    record_briefing(
        source=SOURCE_IRBANK,
        document_id=item.document_id,
        code=issuer.code,
        company_name=issuer.name or None,
        pubdate=item.pubdate,
        title=item.title,
        rel_path=rel_path,
        url=PDF_URL.format(no=item.pdf_id),
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        storage=storage,
    )


def _already_indexed(items: list[Disclosure]) -> set[str]:
    keys: list[str] = []
    for item in items:
        keys.append(item.document_id)
        keys.append(item.pdf_id)
    found = saved_document_ids(keys)
    return {item.document_id for item in items if item.document_id in found or item.pdf_id in found}


def mark_scanned(issuer: Issuer, counts: CompanyCounts) -> None:
    row = {
        "code": issuer.code,
        "scanned_at": datetime.now(JST).replace(tzinfo=None),
        "listed": counts.listed,
        "saved": counts.saved,
        "skipped": counts.skipped,
        "reindexed": counts.reindexed,
        "missing": counts.missing,
        "failed": counts.failed,
    }
    db = SessionLocal()
    try:
        run_sqlite_write(db, lambda: upsert(db, BriefingIrbankScan, [row], commit=False))
    finally:
        db.close()


def _pause(sleep: float, state: dict[str, int]) -> None:
    if state["n"] and sleep:
        time.sleep(sleep)
    state["n"] += 1


@dataclass
class _Staged:
    item: Disclosure
    rel_path: str
    data: bytes


def _indexed_days(code: str) -> list[date]:
    """同じ code の保存済み資料の日付。storage は問わない。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(BriefingDocument.rel_path, BriefingDocument.pubdate)
            .filter(BriefingDocument.code == code, BriefingDocument.status == STATUS_SAVED)
            .all()
        )
    finally:
        db.close()
    days: list[date] = []
    for rel, pub in rows:
        day = briefing_offsite.document_day(rel or "", pub or "")
        if day is not None:
            days.append(day)
    return days


def _cutoff(issuer: Issuer, materials: list[Disclosure]) -> date | None:
    """最新資料の 1 年前。既存索引と今回の一覧の両方を見る。"""
    days = _indexed_days(issuer.code)
    for item in materials:
        day = briefing_offsite.document_day("", item.pubdate)
        if day is not None:
            days.append(day)
    if not days:
        return None
    return briefing_offsite.year_before(max(days))


def _is_old(item: Disclosure, cutoff: date | None) -> bool:
    if cutoff is None:
        return False
    day = briefing_offsite.document_day("", item.pubdate)
    return day is not None and day <= cutoff


def disk_is_low(dest: Path, disk_usage: Callable[[Path], object]) -> bool:
    """--dest の空きが閾値未満、または空きを読めない。"""
    try:
        target = dest if dest.exists() else dest.parent
        free = getattr(disk_usage(target), "free", None)
    except OSError:
        return True
    if isinstance(free, bool) or not isinstance(free, int):
        return True
    return free < MIN_FREE_BYTES


def _ship_old(
    staged: list[_Staged],
    temp_root: Path,
    issuer: Issuer,
    *,
    run: RcloneRunFn,
    environ: Mapping[str, str] | None,
    log: Callable[[str], None],
) -> tuple[int, int]:
    """この会社の古い PDF だけを、offsite と同じバッチで送る。"""
    saved = failed = 0
    offset = 0
    while offset < len(staged):
        chunk = staged[offset:offset + briefing_offsite.BATCH_SIZE]
        offset += len(chunk)
        by_rel = {item.rel_path: item for item in chunk}

        def commit(pdf: briefing_offsite.LocalPdf, _by: dict[str, _Staged] = by_rel) -> None:
            item = _by[pdf.rel_path]
            _index(issuer, item.item, item.rel_path, item.data, storage=STORAGE_ONEDRIVE)

        result = briefing_offsite.deliver_matches(
            temp_root,
            [briefing_offsite.LocalPdf(item.rel_path, temp_root / item.rel_path) for item in chunk],
            run=run,
            commit=commit,
            environ=environ,
        )
        saved += len(result.ok)
        failed += len(result.failed) + len(result.missing)
        for rel in result.ok:
            log(f"offsite {rel}")
        for rel in result.missing:
            log(f"fail {rel}: missing")
        for rel, reason in result.failed:
            log(f"fail {rel}: {reason}")
    return saved, failed


def process_company(
    client: httpx.Client,
    issuer: Issuer,
    dest: Path,
    *,
    sleep: float,
    dry_run: bool,
    pause_state: dict[str, int],
    log: Callable[[str], None],
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
    rclone: RcloneRunFn | None = None,
    environ: Mapping[str, str] | None = None,
) -> CompanyOutcome:
    outcome = CompanyOutcome()
    counts = outcome.counts
    _pause(sleep, pause_state)
    try:
        page = fetch_list(client, issuer.code4)
    except RateLimited as exc:
        outcome.status = "rate_limited"
        outcome.detail = str(exc)
        return outcome
    except Missing:
        outcome.status = "list_missing"
        log(f"missing list {issuer.code}")
        if not dry_run:
            mark_scanned(issuer, counts)
        return outcome
    materials = select_materials(parse_ir_list(page, issuer.code4))
    counts.listed = len(materials)
    indexed = _already_indexed(materials)
    cutoff = None if dry_run else _cutoff(issuer, materials)
    staged: list[_Staged] = []
    temp_dir: tempfile.TemporaryDirectory | None = None
    temp_root: Path | None = None
    try:
        for item in materials:
            if item.document_id in indexed:
                counts.skipped += 1
                log(f"skip indexed {issuer.code} {item.document_id} {item.pubdate} {item.title}")
                continue
            if dry_run:
                log(f"todo {issuer.code} {item.document_id} {item.pubdate} {item.title}")
                continue
            path = dest_path(dest, item.pubdate, issuer.code4, issuer.name, item.title)
            try:
                existing = read_pdf(path)
                if existing is not None:
                    _index(issuer, item, path.relative_to(dest).as_posix(), existing)
                    counts.reindexed += 1
                    log(f"reindexed {path.relative_to(dest).as_posix()}")
                    continue
                _pause(sleep, pause_state)
                data = fetch_pdf(client, item.pdf_id)
                if disk_is_low(dest, disk_usage):
                    outcome.status = "disk_low"
                    break
                if _is_old(item, cutoff):
                    if temp_dir is None:
                        temp_dir = tempfile.TemporaryDirectory(prefix="irbank-briefing-")
                        temp_root = Path(temp_dir.name)
                    staged_path = dest_path(temp_root, item.pubdate, issuer.code4, issuer.name, item.title)
                    write_pdf(staged_path, data)
                    staged.append(_Staged(
                        item=item,
                        rel_path=staged_path.relative_to(temp_root).as_posix(),
                        data=data,
                    ))
                    continue
                write_pdf(path, data)
                _index(issuer, item, path.relative_to(dest).as_posix(), data)
                counts.saved += 1
                log(f"saved {path.relative_to(dest).as_posix()} ({len(data)} bytes)")
            except RateLimited as exc:
                outcome.status = "rate_limited"
                outcome.detail = str(exc)
                break
            except Missing as exc:
                counts.missing += 1
                log(f"missing {item.document_id} {exc}")
            except Exception as exc:
                down = abort_if_disconnected("irbank_briefing", dest, exc)
                if down is not None:
                    raise
                counts.failed += 1
                log(f"fail {item.document_id} {type(exc).__name__}: {exc}")
        if staged and temp_root is not None and not dry_run:
            runner = rclone
            if runner is None:
                runner = make_rclone_runner(offsite_settings_from_env(environ).rclone_bin)
            shipped, broken = _ship_old(
                staged, temp_root, issuer, run=runner, environ=environ, log=log,
            )
            counts.saved += shipped
            counts.failed += broken
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()
    if not dry_run and outcome.status == "ok" and counts.failed == 0:
        mark_scanned(issuer, counts)
    return outcome


def run(
    db: Session,
    dest: Path,
    *,
    codes: list[str] | None,
    limit: int | None = None,
    sleep: float = 1.0,
    dry_run: bool = False,
    rescan: bool = False,
    client: httpx.Client | None = None,
    log: Callable[[str], None] | None = None,
    disk_usage: Callable[[Path], object] = shutil.disk_usage,
    rclone: RcloneRunFn | None = None,
    environ: Mapping[str, str] | None = None,
) -> RunStats:
    write = print if log is None else log
    stats = RunStats()
    todo, notes = issuers(db, codes, rescan=rescan)
    for note in notes:
        write(note)
    own = client is None
    client = client or httpx.Client(
        timeout=60, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
    )
    pause_state = {"n": 0}
    try:
        for index, issuer in enumerate(todo):
            if limit is not None and index >= limit:
                stats.stopped = "limit"
                break
            try:
                outcome = process_company(
                    client, issuer, dest, sleep=sleep, dry_run=dry_run,
                    pause_state=pause_state, log=write, disk_usage=disk_usage,
                    rclone=rclone, environ=environ,
                )
            except Exception as exc:
                down = abort_if_disconnected("irbank_briefing", dest, exc)
                if down is not None:
                    stats.exit_code = down
                    stats.stopped = "mount"
                    break
                outcome = CompanyOutcome(counts=CompanyCounts(failed=1), status="list_failed")
                write(f"fail list {issuer.code} {type(exc).__name__}: {exc}")
            if outcome.status in ("rate_limited", "disk_low"):
                stats.stopped = outcome.status
                stats.listed += outcome.counts.listed
                stats.saved += outcome.counts.saved
                stats.skipped += outcome.counts.skipped
                stats.reindexed += outcome.counts.reindexed
                stats.missing += outcome.counts.missing
                stats.failed += outcome.counts.failed
                write(f"{outcome.status} {issuer.code} {outcome.detail}".rstrip())
                break
            stats.add(outcome.counts)
    finally:
        if own:
            client.close()
    # remaining は scans が無い社数。--rescan で読み直した社は記録が残るので数えない。
    stats.remaining = len(issuers(db, codes)[0])
    return stats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--code", action="append", default=[], help="銘柄コード（4 桁か 5 桁）。複数可")
    ap.add_argument("--all", action="store_true", help="対象の全銘柄（普通株と REIT。読み終えた社は飛ばす）")
    ap.add_argument(
        "--rescan", action="store_true",
        help="briefing_irbank_scans を無視して一覧を読み直す。索引済みの PDF は取り直さない",
    )
    ap.add_argument("--limit", type=int, help="この実行で見る最大社数")
    ap.add_argument("--sleep", type=float, default=1.0, help="IR BANK への請求間隔（秒）。既定 1")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="PDF の保存先。既定は data/briefing")
    ap.add_argument("--dry-run", action="store_true", help="一覧だけ見る。PDF も索引も進捗も書かない")
    args = ap.parse_args(argv)
    if bool(args.code) == args.all:
        ap.error("--code か --all のどちらか一方を指定してください")
    if args.limit is not None and args.limit < 1:
        print("limit must be a positive integer", file=sys.stderr)
        return 2
    if args.sleep < 0:
        print("sleep must be >= 0", file=sys.stderr)
        return 2
    try:
        codes = None if args.all else sorted({code5(c) for c in args.code})
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    dest = Path(args.dest)
    if not args.dry_run:
        rc = fail_if_mount_down("irbank_briefing", dest, require_existing=False)
        if rc is not None:
            return rc
        dest.mkdir(parents=True, exist_ok=True)
    init_db()
    db = SessionLocal()
    try:
        stats = run(
            db, dest, codes=codes, limit=args.limit, sleep=args.sleep, dry_run=args.dry_run,
            rescan=args.rescan,
        )
    finally:
        db.close()
    print(stats.line(dry_run=args.dry_run), flush=True)
    return stats.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
