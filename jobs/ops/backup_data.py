"""共有ファイル（data/ の取り直せないもの）を OneDrive へ写す。KAB-280。

使い方:
  python jobs/ops/backup_data.py
  python jobs/ops/backup_data.py --only xbrl

data/xbrl（TDnet の ZIP は約 1 か月で消える）と data/monthly を
onedrive:stock-analyzer-backups/data/{xbrl,monthly} へ rclone copy し、
rclone check --one-way で確かめる。sync は使わない（VPS で消すと
OneDrive からも消える）。VPS 側も消さない。remote・パス・BACKUP_OFFSITE は
backup_db と同じ。rclone が無い / remote が無いときは警告してスキップ（exit 0）。
送信・確認の失敗は非ゼロ終了（cron-run.sh の fail cron が page する）。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobs.ops.backup_db import (  # noqa: E402
    RCLONE_CHECK_TIMEOUT_S,
    RCLONE_COPY_TIMEOUT_S,
    BackupError,
    OffsiteSettings,
    OffsiteSkip,
    RcloneRunFn,
    WhichFn,
    clip_cmd_output,
    make_rclone_runner,
    offsite_settings_from_env,
    probe_offsite,
    rclone_flags,
)
from jobs.share_paths import share_path  # noqa: E402

KINDS = ("xbrl", "monthly")


def remote_kind(kind: str) -> str:
    return f"data/{kind}"


def copy_dir(local: Path, kind: str, settings: OffsiteSettings, run: RcloneRunFn) -> None:
    if not local.is_dir():
        raise BackupError(f"missing {local}")
    dest = settings.dest(remote_kind(kind))
    flags = rclone_flags()
    copied = run(["copy", str(local), dest, *flags], RCLONE_COPY_TIMEOUT_S)
    if copied.returncode != 0:
        raise BackupError(
            f"rclone copy {kind} exit={copied.returncode} {clip_cmd_output(copied.stderr)}"
        )
    checked = run(["check", str(local), dest, "--one-way", *flags], RCLONE_CHECK_TIMEOUT_S)
    if checked.returncode != 0:
        raise BackupError(
            f"rclone check {kind} exit={checked.returncode} {clip_cmd_output(checked.stderr)}"
        )
    files = sum(1 for p in local.rglob("*") if p.is_file())
    print(f"offsite ok {kind} files={files} dest={dest}", flush=True)


def run(
    kinds: Sequence[str] = KINDS,
    *,
    environ: Mapping[str, str] | None = None,
    which: WhichFn = shutil.which,
    rclone: RcloneRunFn | None = None,
) -> int:
    settings = offsite_settings_from_env(environ)
    runner = rclone or make_rclone_runner(settings.rclone_bin)
    try:
        probe_offsite(settings, which=which, run=runner)
    except OffsiteSkip as exc:
        print(f"offsite skipped: {exc}", flush=True)
        return 0
    for kind in kinds:
        copy_dir(share_path(kind, environ=environ), kind, settings, runner)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Copy data/xbrl and data/monthly to OneDrive")
    ap.add_argument("--only", choices=KINDS, action="append", help="copy only this kind")
    args = ap.parse_args(argv)
    try:
        return run(args.only or KINDS)
    except Exception as exc:
        print(f"fail backup_data: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
