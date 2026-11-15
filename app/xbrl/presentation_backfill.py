"""取り込み済みの書類の表示リンクを ZIP から埋める（xbrl_presentation_roles。docs/DESIGN_xbrl.md §4.5）。

fact・taxonomy には触らない。ZIP から *pre.xml と *.xsd だけ展開して読む。1 件ごとに commit する。
"""
from __future__ import annotations

import io
import time
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import run_sqlite_write
from app.models import EdinetArchive, EdinetDocument, XbrlFiling
from app.xbrl.presentation import SKIPPED_DIRS, read_presentation
from app.xbrl.store import (
    PRESENTATION_FAILED,
    PRESENTATION_GONE,
    store_presentation,
)

if TYPE_CHECKING:
    from app.edinet.archive import RemoteGet
    from app.edinet_wrapper import EdinetClient

SOURCES = ("edinet", "tdnet")
COUNT_KEYS = ("done", "ok", "none", "gone", "failed", "errors")
ORIGINS = ("local", "edinet", "offsite")
EDINET_MAIN = 1


class ZipGone(Exception):
    """ZIP が手に入らない（ファイルが無い・EDINET が 404 で OneDrive にも無い）。次の回も同じ。"""


class ZipUnavailable(Exception):
    """今は取りに行かない・行けない（--local-only で手元に無い）。印を付けずに飛ばす。"""


@dataclass(frozen=True)
class Got:
    content: bytes
    remote: bool
    origin: str = "local"


Fetch = Callable[[XbrlFiling], Got]


def targets(db: Session, *, sources: Iterable[str] = SOURCES, codes: Iterable[str] | None = None,
            archived_only: bool = False) -> list[int]:
    """まだ埋めていない parsed の書類の id。新しい提出から。archived_only は退避の記録がある EDINET の書類だけ。"""
    q = select(XbrlFiling.id).where(
        XbrlFiling.source.in_(list(sources)),
        XbrlFiling.parse_status == "parsed",
        XbrlFiling.presentation_status.is_(None),
    )
    if archived_only:
        q = q.where(XbrlFiling.source == "edinet", XbrlFiling.doc_id.in_(
            select(EdinetArchive.doc_id).where(EdinetArchive.file_type == EDINET_MAIN, EdinetArchive.status == "saved")
        ))
    wanted = list(codes or [])
    if wanted:
        q = q.where(XbrlFiling.code.in_(wanted))
    return list(db.scalars(q.order_by(XbrlFiling.submitted_at.desc(), XbrlFiling.id.desc())))


def tdnet_fetcher(dest: Path) -> Fetch:
    def fetch(filing: XbrlFiling) -> Got:
        path = dest / filing.raw_path
        if not path.is_file():
            raise ZipGone(f"missing-zip path={filing.raw_path}")
        return Got(path.read_bytes(), remote=False)

    return fetch


def _edinet_doc(db: Session, doc_id: str) -> EdinetDocument | None:
    return db.scalars(
        select(EdinetDocument).where(EdinetDocument.doc_id == doc_id)
        .order_by(EdinetDocument.file_date, EdinetDocument.seq_number).limit(1)
    ).first()


def stage_offsite(
    db: Session, ids: Iterable[int], staged: Path, *, root: Path | None = None,
    copy: Callable[[list[str], Path], None] | None = None,
) -> int:
    """ids のうち、退避の記録があって手元に無い EDINET の ZIP を OneDrive から staged へまとめて写す。写した件数。"""
    from app.edinet.archive import archive_dir, rclone_copy

    wanted = list(ids)
    rels: list[str] = []
    for start in range(0, len(wanted), 500):
        rows = db.execute(
            select(EdinetArchive.rel_path)
            .join(XbrlFiling, XbrlFiling.doc_id == EdinetArchive.doc_id)
            .where(XbrlFiling.id.in_(wanted[start:start + 500]), XbrlFiling.source == "edinet",
                   XbrlFiling.presentation_status.is_(None),
                   EdinetArchive.file_type == EDINET_MAIN, EdinetArchive.status == "saved")
        )
        rels.extend(rel for (rel,) in rows if not ((root or archive_dir()) / rel).is_file())
    if not rels:
        return 0
    (copy or rclone_copy)(rels, staged)
    return sum(1 for rel in rels if (staged / rel).is_file())


def edinet_fetcher(
    db: Session,
    client: EdinetClient | None,
    *,
    root: Path | None = None,
    remote_get: RemoteGet | None = None,
    today: date | None = None,
    staged: Path | None = None,
) -> Fetch:
    """client が None なら手元（data/edinet）だけを見る。staged は stage_offsite が写した ZIP の置き場。"""
    from app.edinet.archive import _expires, archive_dir, archived, download, rel_path
    from app.edinet.client import is_not_found, reraise

    def fetch(filing: XbrlFiling) -> Got:
        doc = _edinet_doc(db, filing.doc_id)
        if doc is None:
            raise ZipGone("no edinet_documents row")
        if staged is not None:
            path = staged / rel_path(doc.doc_type_code, doc.doc_id, EDINET_MAIN)
            if path.is_file():
                return Got(path.read_bytes(), remote=False, origin="offsite")
        if client is None:
            path = (root or archive_dir()) / rel_path(doc.doc_type_code, doc.doc_id, EDINET_MAIN)
            if not path.is_file():
                raise ZipUnavailable("not in data/edinet")
            return Got(path.read_bytes(), remote=False)
        try:
            skip_edinet = archived(db, doc.doc_id, EDINET_MAIN) or _expires(doc) < (today or date.today())
            got = download(db, client, doc, EDINET_MAIN, expired=skip_edinet, root=root, remote_get=remote_get)
        except Exception as exc:
            reraise(exc)
            if is_not_found(exc):
                raise ZipGone(f"edinet 404 err={exc}") from exc
            raise
        return Got(got.content, remote=got.source != "local", origin=got.source)

    return fetch


def read_zip(content: bytes) -> list:
    """ZIP から表示リンクを読む。ZIP として読めなければ zipfile.BadZipFile。"""
    with zipfile.ZipFile(io.BytesIO(content)) as zf, TemporaryDirectory(prefix="xbrl-pre-") as tmp:
        for name in zf.namelist():
            low = name.lower()
            if not (low.endswith("pre.xml") or low.endswith(".xsd")):
                continue
            if {p.lower() for p in Path(name).parts} & SKIPPED_DIRS:
                continue
            zf.extract(name, tmp)
        return read_presentation(Path(tmp))


def _mark(db: Session, filing_id: int, status: str) -> None:
    def write() -> None:
        store_presentation(db, filing_id, [])
        db.get(XbrlFiling, filing_id).presentation_status = status

    run_sqlite_write(db, write)


def fill_one(db: Session, filing_id: int, fetch: Fetch, seen: Counter | None = None) -> tuple[str, bool]:
    """1 書類を埋めて commit する。戻り値は (ok / none / gone / failed / errors / skipped, 遠くから取ったか)。

    一時的な失敗（取得の 5xx・タイムアウト・OneDrive の失敗・書き込みのロック）は印を付けず errors。
    EDINET のメンテは呼び側へ投げる。
    """
    from app.edinet.client import is_maintenance

    filing = db.get(XbrlFiling, filing_id)
    if filing is None or filing.presentation_status is not None:
        return "skipped", False
    began = time.monotonic()
    try:
        got = fetch(filing)
    except ZipGone as exc:
        print(f"presentation gone id={filing_id} doc_id={filing.doc_id} {exc}", flush=True)
        _mark(db, filing_id, PRESENTATION_GONE)
        return "gone", False
    except ZipUnavailable:
        return "skipped", False
    except Exception as exc:
        if is_maintenance(exc):
            raise
        db.rollback()
        print(f"presentation fetch-error id={filing_id} doc_id={filing.doc_id} err={exc}", flush=True)
        return "errors", False
    if seen is not None:
        seen[got.origin] += 1
        seen[f"{got.origin}_ms"] += int((time.monotonic() - began) * 1000)
    try:
        roles = read_zip(got.content)
    except Exception as exc:
        print(f"presentation failed id={filing_id} doc_id={filing.doc_id} err={exc!r}"[:500], flush=True)
        _mark(db, filing_id, PRESENTATION_FAILED)
        return "failed", got.remote

    def write() -> str:
        status = store_presentation(db, filing_id, roles)
        db.get(XbrlFiling, filing_id).presentation_status = status
        return status

    try:
        return run_sqlite_write(db, write), got.remote
    except Exception as exc:
        print(f"presentation write-error id={filing_id} doc_id={filing.doc_id} err={exc}", flush=True)
        return "errors", got.remote


def line(counts: Counter) -> str:
    return " ".join(f"{key}={counts[key]}" for key in COUNT_KEYS)


def origins(counts: Counter) -> str:
    return " ".join(f"{key}={counts[key]}/{counts[key + '_ms'] / 1000:.0f}s" for key in ORIGINS)


def run(
    db: Session,
    ids: list[int],
    fetchers: dict[str, Fetch],
    *,
    batch: int | None = None,
    sleep: float = 0.0,
    sleeper: Callable[[float], None] = time.sleep,
) -> Counter:
    counts: Counter = Counter()
    started = time.monotonic()
    for n, filing_id in enumerate(ids, 1):
        source = db.scalar(select(XbrlFiling.source).where(XbrlFiling.id == filing_id))
        fetch = fetchers.get(source or "")
        if fetch is None:
            counts["skipped"] += 1
        else:
            status, remote = fill_one(db, filing_id, fetch, counts)
            counts[status] += 1
            if status not in ("skipped", "errors"):
                counts["done"] += 1
            if remote and sleep > 0:
                sleeper(sleep)
        if batch and (n % batch == 0 or n == len(ids)):
            print(f"presentation batch {n}/{len(ids)} {line(counts)} "
                  f"skipped={counts['skipped']} sec={time.monotonic() - started:.0f} {origins(counts)}", flush=True)
    return counts
