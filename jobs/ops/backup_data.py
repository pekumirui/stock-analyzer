"""共有ファイル（data/ の取り直せないもの）を OneDrive へ写す。KAB-280。

使い方:
  python jobs/ops/backup_data.py
  python jobs/ops/backup_data.py --only xbrl

data/xbrl（TDnet の ZIP は約 1 か月で消える）と data/monthly を
onedrive:stock-analyzer-backups/data/{xbrl,monthly} へ rclone copy し、
rclone check --one-way で確かめる。一覧取得の失敗だけ最大 3 回やり直す。
成功は check の exit 0 だけ（0 differences found では成功にしない）。
sync は使わない（VPS で消すと
OneDrive からも消える）。VPS 側も消さない。
data/edinet（EDINET の ZIP。KAB-281）だけは rclone move で、送って確かめたものから
VPS 側を消す。1 回の量は --max-duration で区切り、打ち切りは失敗にしない
（残りは翌日。KAB-304）。remote・パス・BACKUP_OFFSITE は backup_db と同じ。
rclone が無い / remote が無いときは警告してスキップ（exit 0）。
送信・確認の失敗は非ゼロ終了（cron-run.sh の fail cron が page する）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.offsite_settings import BackupError, OffsiteSettings, offsite_settings_from_env
from jobs.ops.backup_db import (
    RCLONE_CHECK_TIMEOUT_S,
    RCLONE_COPY_TIMEOUT_S,
    CmdResult,
    OffsiteSkip,
    RcloneRunFn,
    WhichFn,
    clip_cmd_output,
    make_rclone_runner,
    probe_offsite,
    rclone_flags,
)
from app.share_paths import share_path

KINDS = ("xbrl", "monthly", "edinet")
# VPS 側を消す（送って確かめたものだけ）。EDINET の ZIP は EDINET から取り直せる間は
# 取り直し、閲覧期間を過ぎたら OneDrive から読む（app/edinet/archive.py）。KAB-281
MOVE_KINDS = frozenset({"edinet"})
# 書きかけ（archive.save の一時ファイル）と、書いた直後のものは送らない
MOVE_MIN_AGE = "10m"
# Python の 3600s kill より先に rclone が自分で止まる。SOFT は送り中のファイルを完了してから止める
RCLONE_MOVE_MAX_DURATION = "50m"
RCLONE_LIMIT_TRANSFER_EXIT = 8
RCLONE_LIMIT_DURATION_EXIT = 10
CHECK_LIST_ATTEMPTS = 3
CHECK_LIST_RETRY_SLEEP_S = 30.0
_LIST_ERROR_MARKERS = (
    "couldn't list files",
    "error reading destination directory:",
    "error reading destination root directory:",
    "error reading source directory:",
    "error reading source root directory:",
)


def remote_kind(kind: str) -> str:
    return f"data/{kind}"


def _zip_count(local: Path) -> int:
    return sum(1 for p in local.rglob("*.zip") if p.is_file())


def move_limit_reason(result: CmdResult) -> str | None:
    """rclone の計画した打ち切りなら理由。本当の失敗なら None。

    v1.75 は --max-duration が 10、--max-transfer が 8。古い版は fatal (7) の
    ことがあるので、文言でも見る。account suspended などの fatal は残す。
    """
    blob = f"{result.stdout}\n{result.stderr}".lower()
    if result.returncode == RCLONE_LIMIT_DURATION_EXIT or "max transfer duration reached" in blob:
        return "max-duration"
    if result.returncode == RCLONE_LIMIT_TRANSFER_EXIT or "max transfer limit reached" in blob:
        return "max-transfer"
    return None


def rclone_list_error(result: CmdResult) -> bool:
    blob = f"{result.stdout}\n{result.stderr}".lower()
    return any(marker in blob for marker in _LIST_ERROR_MARKERS)


def copy_dir(
    local: Path,
    kind: str,
    settings: OffsiteSettings,
    run: RcloneRunFn,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    if not local.is_dir():
        raise BackupError(f"missing {local}")
    dest = settings.dest(remote_kind(kind))
    flags = rclone_flags()
    copied = run(["copy", str(local), dest, *flags], RCLONE_COPY_TIMEOUT_S)
    if copied.returncode != 0:
        raise BackupError(
            f"rclone copy {kind} exit={copied.returncode} {clip_cmd_output(copied.stderr)}"
        )
    argv = ["check", str(local), dest, "--one-way", *flags]
    for attempt in range(1, CHECK_LIST_ATTEMPTS + 1):
        checked = run(argv, RCLONE_CHECK_TIMEOUT_S)
        if checked.returncode == 0:
            break
        if attempt == CHECK_LIST_ATTEMPTS or not rclone_list_error(checked):
            raise BackupError(
                f"rclone check {kind} exit={checked.returncode} "
                f"attempts={attempt} {clip_cmd_output(checked.stderr)}"
            )
        print(
            f"check {kind} list-error attempt={attempt}/{CHECK_LIST_ATTEMPTS} "
            f"exit={checked.returncode} {clip_cmd_output(checked.stderr)}",
            flush=True,
        )
        sleep(CHECK_LIST_RETRY_SLEEP_S)
    files = sum(1 for p in local.rglob("*") if p.is_file())
    print(f"offsite ok {kind} files={files} dest={dest}", flush=True)


def move_dir(local: Path, kind: str, settings: OffsiteSettings, run: RcloneRunFn) -> None:
    """rclone move は 1 ファイルずつ送って確かめてから VPS 側を消す。sync はしない。"""
    if not local.is_dir():
        print(f"offsite ok {kind} files=0 (no {local})", flush=True)
        return
    files = _zip_count(local)
    dest = settings.dest(remote_kind(kind))
    moved = run(
        [
            "move",
            str(local),
            dest,
            "--include",
            "*.zip",
            "--min-age",
            MOVE_MIN_AGE,
            "--delete-empty-src-dirs",
            "--max-duration",
            RCLONE_MOVE_MAX_DURATION,
            "--cutoff-mode",
            "SOFT",
            *rclone_flags(),
        ],
        RCLONE_COPY_TIMEOUT_S,
    )
    capped = move_limit_reason(moved)
    if moved.returncode != 0 and capped is None:
        raise BackupError(
            f"rclone move {kind} exit={moved.returncode} {clip_cmd_output(moved.stderr)}"
        )
    left = _zip_count(local)
    sent = files - left
    if capped and sent == 0 and left > 0:
        raise BackupError(
            f"rclone move {kind} {capped} moved=0 left={left} {clip_cmd_output(moved.stderr)}"
        )
    extra = f" capped={capped}" if capped else ""
    print(f"offsite ok {kind} moved={sent} left={left} dest={dest}{extra}", flush=True)


def run(
    kinds: Sequence[str] = KINDS,
    *,
    environ: Mapping[str, str] | None = None,
    which: WhichFn = shutil.which,
    rclone: RcloneRunFn | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    settings = offsite_settings_from_env(environ)
    runner = rclone or make_rclone_runner(settings.rclone_bin)
    try:
        probe_offsite(settings, which=which, run=runner)
    except OffsiteSkip as exc:
        print(f"offsite skipped: {exc}", flush=True)
        return 0
    for kind in kinds:
        local = share_path(kind, environ=environ)
        if kind in MOVE_KINDS:
            move_dir(local, kind, settings, runner)
        else:
            copy_dir(local, kind, settings, runner, sleep=sleep)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy data/xbrl and data/monthly, move data/edinet to OneDrive")
    ap.add_argument("--only", choices=KINDS, action="append", help="copy only this kind")
    args = ap.parse_args(argv)
    try:
        return run(args.only or KINDS)
    except Exception as exc:
        print(f"fail backup_data: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
