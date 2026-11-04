"""rel_path で決算説明資料を開く。local は data/briefing、onedrive は rclone で一時取得する。

一時ファイルは読み終わったら消す。data/briefing へは戻さない（KAB-345）。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from app.market.briefing_store import (
    STORAGE_LOCAL,
    STORAGE_ONEDRIVE,
    find_saved,
)
from jobs.ops.backup_db import offsite_settings_from_env
from jobs.share_paths import share_path

RCLONE_CAT_TIMEOUT_S = 300.0
RemoteGet = Callable[[str], bytes]


class BriefingFileError(Exception):
    """OneDrive から読めない。索引と手元のファイルは変えない。"""


def safe_rel(rel: str) -> str:
    text = (rel or "").replace("\\", "/").strip()
    parts = Path(text).parts
    if not text or text.startswith("/") or ".." in parts or any(part == "" for part in parts):
        raise ValueError(f"unsafe rel_path: {rel!r}")
    return text


def briefing_dir(environ: Mapping[str, str] | None = None, root: Path | None = None) -> Path:
    if root is not None:
        return root
    return share_path("briefing", environ=environ)


def local_file(rel: str, root: Path) -> Path:
    """root の中のパス。.. では外へ出ない。"""
    rel = safe_rel(rel)
    base = root.resolve()
    path = (base / rel).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"unsafe rel_path: {rel!r}")
    return path


def remote_path(rel: str, *, environ: Mapping[str, str] | None = None) -> str:
    """backup と同じ remote の data/briefing/ の下へ、索引の相対パスのまま置く。"""
    settings = offsite_settings_from_env(environ)
    return f"{settings.dest('data/briefing')}/{safe_rel(rel)}"


def rclone_cat_bytes(rel: str, *, environ: Mapping[str, str] | None = None) -> bytes:
    settings = offsite_settings_from_env(environ)
    bin_name = settings.rclone_bin
    if shutil.which(bin_name) is None and not Path(bin_name).is_file():
        raise BriefingFileError(f"rclone not installed ({bin_name})")
    src = remote_path(rel, environ=environ)
    try:
        proc = subprocess.run(  # nosec B603 - 固定の実行ファイル。shell は使わない
            [bin_name, "cat", src],
            capture_output=True,
            timeout=RCLONE_CAT_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise BriefingFileError(f"rclone cat timeout {rel}") from exc
    if proc.returncode != 0 or not proc.stdout:
        err = " ".join((proc.stderr or b"").decode("utf-8", "replace").split())[:300]
        raise BriefingFileError(f"rclone cat exit={proc.returncode} {rel} {err}")
    return proc.stdout


@contextmanager
def open_briefing(
    rel_path: str,
    *,
    storage: str | None = None,
    root: Path | None = None,
    remote_get: RemoteGet | None = None,
    environ: Mapping[str, str] | None = None,
) -> Iterator[Path]:
    """PDF のパス。onedrive のときは一時ファイルで、抜けると消える。"""
    where = storage or STORAGE_LOCAL
    rel = safe_rel(rel_path)
    if where != STORAGE_ONEDRIVE:
        path = local_file(rel, briefing_dir(environ, root))
        if not path.is_file():
            raise FileNotFoundError(rel)
        yield path
        return
    data = remote_get(rel) if remote_get is not None else rclone_cat_bytes(rel, environ=environ)
    fd, name = tempfile.mkstemp(prefix="briefing-", suffix=".pdf")
    os.close(fd)
    tmp = Path(name)
    try:
        tmp.write_bytes(data)
        yield tmp
    finally:
        tmp.unlink(missing_ok=True)


def read_briefing(
    rel_path: str,
    *,
    storage: str | None = None,
    root: Path | None = None,
    remote_get: RemoteGet | None = None,
    environ: Mapping[str, str] | None = None,
) -> bytes:
    with open_briefing(
        rel_path, storage=storage, root=root, remote_get=remote_get, environ=environ,
    ) as path:
        return path.read_bytes()


def read_indexed(
    document_id: str,
    *,
    root: Path | None = None,
    remote_get: RemoteGet | None = None,
    environ: Mapping[str, str] | None = None,
) -> bytes | None:
    """索引にあればバイトを返す。無ければ None。OneDrive の取得失敗は例外。"""
    found = find_saved(document_id)
    if found is None:
        return None
    return read_briefing(
        found.rel_path,
        storage=found.storage,
        root=root,
        remote_get=remote_get,
        environ=environ,
    )
