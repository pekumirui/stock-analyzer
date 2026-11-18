"""EDINET の書類取得 API の ZIP を残し、閲覧期間を過ぎたら OneDrive から読む（KAB-281）。

取得はここを通す: 手元（data/edinet）→ EDINET → EDINET が 404 で残してあれば OneDrive。
EDINET から取った ZIP は data/edinet/{書類種別}/{docID}_type{n}.zip に書き、edinet_archives に
記録する。OneDrive への退避は jobs/ops/backup_data.py（毎朝 rclone move。VPS 側は消える）。
OneDrive の場所は backup_data と同じ BACKUP_OFFSITE_REMOTE / BACKUP_OFFSITE_PATH の下の data/edinet。
設計は docs/DESIGN_edinet.md §10。
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import run_sqlite_write, upsert
from app.edinet.client import is_not_found
from app.edinet.documents import now_jst
from app.models import EdinetArchive
from jobs.share_paths import share_path

if TYPE_CHECKING:
    from app.edinet_wrapper import EdinetClient
    from app.models import EdinetDocument

ARCHIVE_DIR_ENV = "EDINET_ARCHIVE_DIR"
SAVED = "saved"
GONE = "gone"
RCLONE_CAT_TIMEOUT_S = 300.0

RemoteGet = Callable[[str], bytes]


class ArchiveError(Exception):
    """OneDrive から読めない。一時的な失敗として扱う（呼び側は error）。"""


@dataclass(frozen=True)
class Fetched:
    content: bytes
    source: str  # local / edinet / offsite


def archive_dir(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    raw = (env.get(ARCHIVE_DIR_ENV) or "").strip()
    return Path(raw) if raw else share_path("edinet", environ=env)


def rel_path(doc_type_code: str | None, doc_id: str, file_type: int) -> str:
    return f"{doc_type_code or 'unknown'}/{doc_id}_type{int(file_type)}.zip"


def offsite_root(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    remote = (env.get("BACKUP_OFFSITE_REMOTE") or "onedrive").strip().rstrip(":")
    root = (env.get("BACKUP_OFFSITE_PATH") or "stock-analyzer-backups").strip().strip("/")
    return f"{remote}:{root}/data/edinet"


def rclone_get(rel: str, environ: Mapping[str, str] | None = None) -> bytes:
    env = os.environ if environ is None else environ
    bin_name = (env.get("BACKUP_RCLONE_BIN") or "rclone").strip() or "rclone"
    if shutil.which(bin_name) is None and not Path(bin_name).is_file():
        raise ArchiveError(f"rclone not installed ({bin_name})")
    src = f"{offsite_root(env)}/{rel}"
    try:
        proc = subprocess.run(
            [bin_name, "cat", src], capture_output=True, timeout=RCLONE_CAT_TIMEOUT_S, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError(f"rclone cat timeout {src}") from exc
    if proc.returncode != 0 or not proc.stdout:
        err = " ".join(proc.stderr.decode("utf-8", "replace").split())[:300]
        raise ArchiveError(f"rclone cat exit={proc.returncode} {src} {err}")
    return proc.stdout


def rclone_copy(rels: list[str], dest: Path, *, transfers: int = 8, timeout: float = 3600.0,
                environ: Mapping[str, str] | None = None) -> None:
    """OneDrive の ZIP をまとめて dest へ写す（dest の下は rel_path と同じ並び）。1 件ずつの rclone cat より速い。"""
    env = os.environ if environ is None else environ
    bin_name = (env.get("BACKUP_RCLONE_BIN") or "rclone").strip() or "rclone"
    if shutil.which(bin_name) is None and not Path(bin_name).is_file():
        raise ArchiveError(f"rclone not installed ({bin_name})")
    dest.mkdir(parents=True, exist_ok=True)
    listing = dest / ".files-from"
    listing.write_text("\n".join(rels) + "\n", encoding="utf-8")
    cmd = [bin_name, "copy", offsite_root(env), str(dest), "--files-from", str(listing), "--no-traverse",
           "--transfers", str(int(transfers))]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise ArchiveError(f"rclone copy timeout files={len(rels)}") from exc
    finally:
        listing.unlink(missing_ok=True)
    if proc.returncode != 0:
        err = " ".join(proc.stderr.decode("utf-8", "replace").split())[:300]
        raise ArchiveError(f"rclone copy exit={proc.returncode} files={len(rels)} {err}")


def is_valid_zip(content: bytes) -> bool:
    """壊れた・途中で切れた ZIP は残さない（残すと手元・OneDrive の写しが取り直しを妨げる）。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return zf.testzip() is None
    except (zipfile.BadZipFile, OSError, EOFError, ValueError):
        return False


def archived(db: Session, doc_id: str, file_type: int) -> bool:
    row = db.get(EdinetArchive, (doc_id, int(file_type)))
    return row is not None and row.status == SAVED


def save(db: Session, doc: EdinetDocument, file_type: int, content: bytes,
         *, root: Path | None = None) -> Path:
    """data/edinet に書いて（一時ファイル → rename）記録する。commit まで行う。"""
    rel = rel_path(doc.doc_type_code, doc.doc_id, file_type)
    path = (root or archive_dir()) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(content)
    os.replace(tmp, path)
    _record(db, {
        "doc_id": doc.doc_id, "file_type": int(file_type), "doc_type_code": doc.doc_type_code,
        "rel_path": rel, "status": SAVED, "bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(), "saved_at": now_jst(),
    })
    return path


def mark_gone(db: Session, doc: EdinetDocument, file_type: int) -> None:
    _record(db, {
        "doc_id": doc.doc_id, "file_type": int(file_type), "doc_type_code": doc.doc_type_code,
        "rel_path": rel_path(doc.doc_type_code, doc.doc_id, file_type), "status": GONE,
        "bytes": None, "sha256": None, "saved_at": now_jst(),
    })


def _record(db: Session, row: dict) -> None:
    run_sqlite_write(db, lambda: upsert(db, EdinetArchive, [row], commit=False))


def download(db: Session, client: EdinetClient, doc: EdinetDocument, file_type: int, *,
             expired: bool = False, root: Path | None = None,
             remote_get: RemoteGet | None = None) -> Fetched:
    """手元 → EDINET → OneDrive の順に ZIP を返す。

    ZIP として読めないもの（CRC 不一致・途中切れ）は残さず、そのまま返す（パースが失敗にする）。
    expired（閲覧期間外と分かっている）なら EDINET を飛ばす。EDINET が 404 で残していなければ
    EDINET の例外をそのまま投げる（呼び側の missing 判定はこれまでどおり）。
    """
    rel = rel_path(doc.doc_type_code, doc.doc_id, file_type)
    path = (root or archive_dir()) / rel
    if path.is_file():
        return Fetched(path.read_bytes(), "local")
    get = remote_get or rclone_get
    if expired and archived(db, doc.doc_id, file_type):
        return Fetched(get(rel), "offsite")
    try:
        got = client.download(doc.doc_id, file_type)
    except Exception as exc:
        if is_not_found(exc) and archived(db, doc.doc_id, file_type):
            return Fetched(get(rel), "offsite")
        raise
    if is_valid_zip(got.content):
        save(db, doc, file_type, got.content, root=root)
    return Fetched(got.content, "edinet")


# 閲覧期間（年）。書類種別 → 年。DESIGN_edinet.md の早見表と各モジュールの KEEP_YEARS
_KEEP_YEARS = {
    **dict.fromkeys(("120", "130", "140", "150", "160", "170"), 10),
    **dict.fromkeys(("220", "230"), 1),
    **dict.fromkeys(("240", "250", "270", "280", "290", "300"), 5),
    **dict.fromkeys(("350", "360"), 5),
}


@dataclass(frozen=True)
class Target:
    doc: EdinetDocument
    file_type: int
    expires: date


def _expires(doc: EdinetDocument) -> date:
    years = _KEEP_YEARS.get(doc.doc_type_code or "", 10)
    day = doc.file_date
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(month=2, day=28, year=day.year + years)


def backfill_targets(db: Session) -> list[Target]:
    """取込済みで edinet_archives に無い書類。閲覧期間の終わりが近い順。

    有報系・自己株買い・公開買付け（パースした種類）は type=1、大量保有は type=5。
    """
    from app.models import (
        EdinetBuyback,
        EdinetDocument,
        EdinetTenderOfferDoc,
        LargeHoldingReport,
        XbrlFiling,
    )

    want: dict[str, int] = {}
    for doc_id in db.scalars(select(XbrlFiling.doc_id).where(XbrlFiling.source == "edinet")):
        want[doc_id] = 1
    for doc_id in db.scalars(select(EdinetBuyback.doc_id)):
        want[doc_id] = 1
    for doc_id in db.scalars(select(EdinetTenderOfferDoc.doc_id)
                             .where(EdinetTenderOfferDoc.parse_status != "meta")):
        want[doc_id] = 1
    for doc_id in db.scalars(select(LargeHoldingReport.doc_id)):
        want[doc_id] = 5
    for doc_id, file_type in db.execute(select(EdinetArchive.doc_id, EdinetArchive.file_type)):
        if want.get(doc_id) == file_type:
            del want[doc_id]
    if not want:
        return []
    T = EdinetDocument
    docs: dict[str, EdinetDocument] = {}
    ids = list(want)
    for i in range(0, len(ids), 500):
        rows = db.scalars(select(T).where(
            T.doc_id.in_(ids[i:i + 500]), T.withdrawal_status != "1", T.doc_info_edit_status != "1",
        ).order_by(T.file_date, T.seq_number))
        for doc in rows:
            docs.setdefault(doc.doc_id, doc)
    out = [Target(doc, want[doc_id], _expires(doc)) for doc_id, doc in docs.items()]
    out.sort(key=lambda t: (t.expires, t.doc.doc_id))
    return out


def backfill(db: Session, client: EdinetClient, targets: list[Target]) -> dict[str, int]:
    """1 件ずつ EDINET から取って残す。404 は gone。メンテは呼び側へ投げる。"""
    from app.edinet.client import reraise

    out = {"saved": 0, "gone": 0, "errors": 0}
    for t in targets:
        try:
            got = client.download(t.doc.doc_id, t.file_type)
        except Exception as exc:
            reraise(exc)
            if is_not_found(exc):
                mark_gone(db, t.doc, t.file_type)
                out["gone"] += 1
                continue
            print(f"edinet archive download-error doc_id={t.doc.doc_id} err={exc}", flush=True)
            out["errors"] += 1
            continue
        if not is_valid_zip(got.content):
            print(f"edinet archive bad-zip doc_id={t.doc.doc_id} bytes={len(got.content)}", flush=True)
            out["errors"] += 1
            continue
        save(db, t.doc, t.file_type, got.content)
        out["saved"] += 1
    return out
