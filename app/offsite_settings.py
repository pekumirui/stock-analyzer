from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_OFFSITE_REMOTE = "onedrive"
DEFAULT_OFFSITE_PATH = "stock-analyzer-backups"


class BackupError(Exception):
    """想定内の失敗（容量・破損・欠ファイル・オフサイト）。traceback ではなく fail 行にする。"""


@dataclass(frozen=True)
class OffsiteSettings:
    remote: str = DEFAULT_OFFSITE_REMOTE
    root: str = DEFAULT_OFFSITE_PATH
    rclone_bin: str = "rclone"
    enabled: bool = True

    def dest(self, kind: str) -> str:
        return f"{self.remote}:{self.root}/{kind}"


def env_flag(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = (environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in ("0", "false", "no", "off"):
        return False
    if raw in ("1", "true", "yes", "on"):
        return True
    raise BackupError(f"{name}={raw!r} must be 0/1")


def _norm_remote(raw: str) -> str:
    return raw.strip().rstrip(":")


def offsite_settings_from_env(environ: Mapping[str, str] | None = None) -> OffsiteSettings:
    env = os.environ if environ is None else environ
    remote = _norm_remote(env.get("BACKUP_OFFSITE_REMOTE") or DEFAULT_OFFSITE_REMOTE)
    root = (env.get("BACKUP_OFFSITE_PATH") or DEFAULT_OFFSITE_PATH).strip().strip("/")
    bin_name = (env.get("BACKUP_RCLONE_BIN") or "rclone").strip() or "rclone"
    if not remote or not root:
        raise BackupError("BACKUP_OFFSITE_REMOTE and BACKUP_OFFSITE_PATH must be non-empty")
    return OffsiteSettings(
        remote=remote,
        root=root,
        rclone_bin=bin_name,
        enabled=env_flag(env, "BACKUP_OFFSITE", True),
    )
