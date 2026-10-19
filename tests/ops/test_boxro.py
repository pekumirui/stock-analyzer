"""boxro read-only SFTP jail: keys file, drop-in, binds, sshd reload-on-change."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BOXRO_SH = ROOT / "deploy" / "boxro.sh"
KEYS = ROOT / "deploy" / "ssh" / "boxro_authorized_keys"
PROVISION = ROOT / "deploy" / "provision.sh"
KEY_LINE = (
    "restrict ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAIAX/NTi1DbHtuQxf4Oig6cjtn2MzcHqMlsShA92t0LkI "
    "grokbot-box-vps-ro"
)
DROPIN = """\
Match User boxro
    AuthorizedKeysFile /etc/ssh/authorized_keys/boxro
    ChrootDirectory /srv/boxro
    ForceCommand internal-sftp -R
    AllowTcpForwarding no
    X11Forwarding no
    PermitTTY no
    PasswordAuthentication no
"""


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_static_contracts() -> None:
    if not KEYS.is_file():
        _fail("deploy/ssh/boxro_authorized_keys missing")
    keys = KEYS.read_text(encoding="utf-8")
    live = [
        line
        for line in keys.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if live != [KEY_LINE]:
        _fail(f"expected one restrict key, got {live!r}")
    for line in live:
        if not line.startswith("restrict "):
            _fail(f"key line must start with restrict: {line!r}")

    provision = PROVISION.read_text(encoding="utf-8")
    if "source \"$APP_DIR/deploy/boxro.sh\"" not in provision:
        _fail("provision.sh must source deploy/boxro.sh")
    if "if ! boxro_apply; then" not in provision:
        _fail("provision.sh must catch boxro_apply so a jail failure cannot abort app deploy")
    if re.search(r"^boxro_apply$", provision, re.M):
        _fail("bare boxro_apply would abort provision under set -e")
    if "systemctl restart ssh" in provision or "systemctl restart sshd" in provision:
        _fail("provision.sh must not restart ssh")

    script = BOXRO_SH.read_text(encoding="utf-8")
    if "systemctl restart ssh" in script or "systemctl restart sshd" in script:
        _fail("boxro.sh must not restart ssh")
    code = "\n".join(
        line
        for line in script.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    for forbidden in (
        "/home/stock/backups",
        "r2-share",
        "stock.db",
        ".env",
        "rclone.conf",
    ):
        if forbidden in code:
            _fail(f"boxro.sh must not bind {forbidden}")
    if "internal-sftp -R" not in script:
        _fail("boxro.sh must ForceCommand internal-sftp -R")
    if "AuthorizedKeysFile /etc/ssh/authorized_keys/boxro" not in script:
        _fail("boxro.sh must pin AuthorizedKeysFile")
    if "useradd --system --shell" not in script or "--no-create-home" not in script:
        _fail("boxro.sh must create a system user without a home")
    if "findmnt -n --target" in script or "findmnt --target" in script:
        _fail("findmnt --target matches parent /; use mountpoint or --mountpoint")
    if "mountpoint -q" not in script:
        _fail("boxro.sh must detect the exact mount with mountpoint -q")
    if "boxro_drop_logs_bind" not in script:
        _fail("boxro.sh must clean up the former logs bind")
    if "boxro_mount_ro_bind \"$BOXRO_LOGS_SRC\"" in script:
        _fail("boxro.sh must not bind logs")
    if 'boxro_ensure_fstab_line "$(boxro_fstab_line "$BOXRO_LOGS_SRC"' in script:
        _fail("boxro.sh must not add a logs fstab line")

    chk = subprocess.run(
        ["bash", "-n", str(BOXRO_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if chk.returncode != 0:
        _fail(f"bash -n boxro.sh failed: {chk.stderr or chk.stdout}")
    chk = subprocess.run(
        ["bash", "-n", str(PROVISION)],
        capture_output=True,
        text=True,
        check=False,
    )
    if chk.returncode != 0:
        _fail(f"bash -n provision.sh failed: {chk.stderr or chk.stdout}")
    sc = shutil.which("shellcheck")
    if sc:
        proc = subprocess.run(
            [sc, "-x", str(BOXRO_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            _fail(f"shellcheck boxro.sh failed:\n{proc.stdout}{proc.stderr}")


def _write_stubs(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "getent").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'if [[ "${1:-}" == passwd && "${2:-}" == "${BOXRO_USER:-boxro}" '
        '&& -f "${BOXRO_TEST_USER_FLAG}" ]]; then\n'
        '  echo "${2}:x:999:999::/nonexistent:/usr/sbin/nologin"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    (bin_dir / "useradd").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "useradd %s\\n" "$*" >> "${BOXRO_TEST_LOG}"\n'
        'touch "${BOXRO_TEST_USER_FLAG}"\n',
        encoding="utf-8",
    )
    (bin_dir / "passwd").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "passwd %s\\n" "$*" >> "${BOXRO_TEST_LOG}"\n',
        encoding="utf-8",
    )
    (bin_dir / "mountpoint").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "path=\n"
        "while [[ $# -gt 0 ]]; do\n"
        '  case "$1" in\n'
        "    -q|--quiet) shift ;;\n"
        "    --) shift ;;\n"
        "    *) path=\"$1\"; shift ;;\n"
        "  esac\n"
        "done\n"
        '[[ -n "$path" ]] || exit 1\n'
        '[[ -f "${BOXRO_TEST_MOUNTED}" ]] || exit 1\n'
        'grep -qxF "$path" "${BOXRO_TEST_MOUNTED}"\n',
        encoding="utf-8",
    )
    # --target matches the parent filesystem (the production bug). --mountpoint
    # is exact. OPTIONS is ro only for recorded mountpoints.
    (bin_dir / "findmnt").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "target=\n"
        "mp=\n"
        "want_opts=0\n"
        "while [[ $# -gt 0 ]]; do\n"
        '  case "$1" in\n'
        "    --target) target=\"$2\"; shift 2 ;;\n"
        "    --target=*) target=\"${1#--target=}\"; shift ;;\n"
        "    --mountpoint) mp=\"$2\"; shift 2 ;;\n"
        "    --mountpoint=*) mp=\"${1#--mountpoint=}\"; shift ;;\n"
        "    -o) [[ \"${2:-}\" == OPTIONS ]] && want_opts=1; shift 2 ;;\n"
        "    -o*) [[ \"$1\" == -oOPTIONS ]] && want_opts=1; shift ;;\n"
        "    -n) shift ;;\n"
        "    *) shift ;;\n"
        "  esac\n"
        "done\n"
        "listed() {\n"
        '  [[ -f "${BOXRO_TEST_MOUNTED}" ]] && grep -qxF "$1" "${BOXRO_TEST_MOUNTED}"\n'
        "}\n"
        "if [[ -n \"$mp\" ]]; then\n"
        '  listed "$mp" || exit 1\n'
        "  if [[ \"$want_opts\" == 1 ]]; then echo ro,relatime,bind; fi\n"
        "  exit 0\n"
        "fi\n"
        '[[ -n "$target" ]] || exit 1\n'
        'if listed "$target"; then\n'
        "  if [[ \"$want_opts\" == 1 ]]; then echo ro,relatime,bind; fi\n"
        "  exit 0\n"
        "fi\n"
        "# util-linux findmnt --target: any existing path on / succeeds.\n"
        'if [[ -e "$target" ]]; then\n'
        "  if [[ \"$want_opts\" == 1 ]]; then echo rw,relatime; fi\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    (bin_dir / "umount").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "umount %s\\n" "$*" >> "${BOXRO_TEST_LOG}"\n'
        "dest=\"${@: -1}\"\n"
        '[[ "$dest" == -- ]] && exit 1\n'
        'if [[ -f "${BOXRO_TEST_MOUNTED}" ]]; then\n'
        '  grep -vxF "$dest" "${BOXRO_TEST_MOUNTED}" > "${BOXRO_TEST_MOUNTED}.tmp" || true\n'
        '  mv "${BOXRO_TEST_MOUNTED}.tmp" "${BOXRO_TEST_MOUNTED}"\n'
        "fi\n",
        encoding="utf-8",
    )
    (bin_dir / "mount").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "mount %s\\n" "$*" >> "${BOXRO_TEST_LOG}"\n'
        "dest=\n"
        'if [[ "${1:-}" == --bind ]]; then dest="${3:-}"; fi\n'
        "if [[ \"$*\" == *remount* ]]; then\n"
        "  dest=\"${@: -1}\"\n"
        "fi\n"
        'if [[ -n "$dest" ]]; then\n'
        '  grep -qxF "$dest" "${BOXRO_TEST_MOUNTED}" 2>/dev/null '
        '|| printf "%s\\n" "$dest" >> "${BOXRO_TEST_MOUNTED}"\n'
        "fi\n",
        encoding="utf-8",
    )
    (bin_dir / "sshd").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "sshd %s\\n" "$*" >> "${BOXRO_TEST_LOG}"\n'
        'if [[ "${BOXRO_TEST_SSHD_FAIL:-0}" == 1 ]]; then exit 1; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    (bin_dir / "systemctl").write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'printf "systemctl %s\\n" "$*" >> "${BOXRO_TEST_LOG}"\n'
        'if [[ "$*" == *restart* ]]; then\n'
        '  echo "unexpected restart: $*" >&2\n'
        "  exit 1\n"
        "fi\n"
        'if [[ "${1:-}" == reload && ( "${2:-}" == ssh || "${2:-}" == sshd ) ]]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    for name in (
        "getent",
        "useradd",
        "passwd",
        "mountpoint",
        "findmnt",
        "mount",
        "umount",
        "sshd",
        "systemctl",
    ):
        _chmod_x(bin_dir / name)


def _env(tmp: Path, app: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{tmp / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["APP_DIR"] = str(app)
    env["BOXRO_USER"] = "boxro"
    env["BOXRO_SRV"] = str(tmp / "srv")
    env["BOXRO_CHROOT"] = str(tmp / "srv" / "boxro")
    env["BOXRO_DATA_SRC"] = str(app / "data")
    env["BOXRO_LOGS_SRC"] = str(tmp / "var" / "log" / "app")
    env["BOXRO_FSTAB"] = str(tmp / "etc" / "fstab")
    env["BOXRO_SSHD_DROPIN"] = str(tmp / "etc" / "ssh" / "sshd_config.d" / "90-boxro.conf")
    env["BOXRO_AUTH_DIR"] = str(tmp / "etc" / "ssh" / "authorized_keys")
    env["BOXRO_AUTH_DEST"] = str(tmp / "etc" / "ssh" / "authorized_keys" / "boxro")
    env["BOXRO_AUTH_SRC"] = str(app / "deploy" / "ssh" / "boxro_authorized_keys")
    env["BOXRO_SSHD"] = str(tmp / "bin" / "sshd")
    env["BOXRO_SYSTEMCTL"] = str(tmp / "bin" / "systemctl")
    env["BOXRO_TEST_LOG"] = str(tmp / "calls.log")
    env["BOXRO_TEST_USER_FLAG"] = str(tmp / "user.flag")
    env["BOXRO_TEST_MOUNTED"] = str(tmp / "mounted")
    env["BOXRO_TEST_SSHD_FAIL"] = "0"
    return env


def _seed_app(tmp: Path) -> Path:
    app = tmp / "app"
    (app / "data" / "xbrl_golden").mkdir(parents=True)
    (app / "data" / "xbrl_golden" / "README.md").write_text("golden\n", encoding="utf-8")
    keys_dir = app / "deploy" / "ssh"
    keys_dir.mkdir(parents=True)
    shutil.copy(KEYS, keys_dir / "boxro_authorized_keys")
    (tmp / "var" / "log" / "app").mkdir(parents=True)
    (tmp / "var" / "log" / "app" / "stock-analyzer.log").write_text("log\n", encoding="utf-8")
    (tmp / "etc").mkdir(parents=True)
    (tmp / "etc" / "fstab").write_text("proc /proc proc defaults 0 0\n", encoding="utf-8")
    (tmp / "calls.log").write_text("", encoding="utf-8")
    (tmp / "mounted").write_text("", encoding="utf-8")
    return app


def _run(env: dict[str, str], func: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            f"set -euo pipefail; source {BOXRO_SH.as_posix()!r}; {func}",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


def _assert_ok(proc: subprocess.CompletedProcess[str], label: str) -> None:
    if proc.returncode != 0:
        _fail(
            f"{label} rc={proc.returncode} stdout={proc.stdout!r} stderr={proc.stderr!r}"
        )


def test_apply_idempotent_and_isolated(tmp_path: Path) -> None:
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    first = _run(env, "boxro_apply")
    _assert_ok(first, "first boxro_apply")
    log = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if "useradd --system --shell /usr/sbin/nologin --no-create-home boxro" not in log:
        _fail(f"missing useradd: {log!r}")
    if "passwd -l boxro" not in log:
        _fail(f"password not locked: {log!r}")
    if "systemctl reload ssh" not in log:
        _fail(f"first apply must reload ssh: {log!r} {first.stdout!r}")
    if "restart" in log:
        _fail(f"must not restart: {log!r}")
    fstab = Path(env["BOXRO_FSTAB"]).read_text(encoding="utf-8")
    data_line = (
        f"{env['BOXRO_DATA_SRC']} {env['BOXRO_CHROOT']}/data none bind,ro 0 0"
    )
    logs_line = (
        f"{env['BOXRO_LOGS_SRC']} {env['BOXRO_CHROOT']}/logs none bind,ro 0 0"
    )
    if data_line not in fstab:
        _fail(f"fstab missing data bind: {fstab!r}")
    if logs_line in fstab or "/logs none bind,ro" in fstab:
        _fail(f"fstab must not bind logs: {fstab!r}")
    if "backups" in fstab or "r2-share" in fstab:
        _fail(f"fstab must not bind secrets: {fstab!r}")
    dest = Path(env["BOXRO_AUTH_DEST"])
    if dest.read_text(encoding="utf-8") != (app / "deploy" / "ssh" / "boxro_authorized_keys").read_text(
        encoding="utf-8"
    ):
        _fail("authorized_keys not synced")
    dropin = Path(env["BOXRO_SSHD_DROPIN"]).read_text(encoding="utf-8")
    if dropin != DROPIN:
        _fail(f"drop-in mismatch: {dropin!r}")
    Path(env["BOXRO_TEST_LOG"]).write_text("", encoding="utf-8")
    second = _run(env, "boxro_apply")
    _assert_ok(second, "second boxro_apply")
    log2 = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if "useradd" in log2:
        _fail(f"idempotent apply must not useradd again: {log2!r}")
    if "systemctl reload" in log2:
        _fail(f"unchanged drop-in must not reload ssh: {log2!r} {second.stdout!r}")
    if Path(env["BOXRO_FSTAB"]).read_text(encoding="utf-8").count(data_line) != 1:
        _fail(f"fstab duplicated: {Path(env['BOXRO_FSTAB']).read_text()!r}")
    if "boxro: sshd drop-in unchanged" not in second.stdout:
        _fail(f"second apply should skip drop-in: {second.stdout!r}")


def test_rejects_key_without_restrict(tmp_path: Path) -> None:
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    _assert_ok(_run(env, "boxro_apply"), "seed")
    src = Path(env["BOXRO_AUTH_SRC"])
    src.write_text(
        "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAX/NTi1DbHtuQxf4Oig6cjtn2MzcHqMlsShA92t0LkI x\n",
        encoding="utf-8",
    )
    before = Path(env["BOXRO_AUTH_DEST"]).read_text(encoding="utf-8")
    proc = _run(env, "boxro_sync_authorized_keys")
    if proc.returncode == 0:
        _fail("key without restrict must fail")
    if "must start with restrict" not in proc.stderr:
        _fail(f"expected restrict error: {proc.stderr!r}")
    after = Path(env["BOXRO_AUTH_DEST"]).read_text(encoding="utf-8")
    if after != before:
        _fail("failed sync must not rewrite dest")


def test_syncs_additional_restrict_key(tmp_path: Path) -> None:
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    _assert_ok(_run(env, "boxro_apply"), "seed")
    extra = (
        "restrict ssh-ed25519 "
        "AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "
        "claude-box-vps-ro"
    )
    src = Path(env["BOXRO_AUTH_SRC"])
    src.write_text(f"{KEY_LINE}\n{extra}\n", encoding="utf-8")
    proc = _run(env, "boxro_sync_authorized_keys")
    _assert_ok(proc, "sync two keys")
    dest = Path(env["BOXRO_AUTH_DEST"]).read_text(encoding="utf-8")
    if KEY_LINE not in dest or extra not in dest:
        _fail(f"dest missing keys: {dest!r}")
    Path(env["BOXRO_TEST_LOG"]).write_text("", encoding="utf-8")
    proc = _run(env, "boxro_sync_authorized_keys")
    _assert_ok(proc, "sync unchanged keys")
    if "systemctl" in Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8"):
        _fail("keys sync must not call systemctl")


def test_sshd_t_failure_restores_previous(tmp_path: Path) -> None:
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    _assert_ok(_run(env, "boxro_apply"), "seed")
    dest = Path(env["BOXRO_SSHD_DROPIN"])
    dest.write_text("Match User boxro\n    PermitTTY no\n", encoding="utf-8")
    env["BOXRO_TEST_SSHD_FAIL"] = "1"
    Path(env["BOXRO_TEST_LOG"]).write_text("", encoding="utf-8")
    proc = _run(env, "boxro_apply_sshd_dropin")
    if proc.returncode == 0:
        _fail("sshd -t failure must abort")
    if "sshd -t failed; restoring" not in proc.stderr:
        _fail(f"expected restore message: {proc.stderr!r} {proc.stdout!r}")
    restored = dest.read_text(encoding="utf-8")
    if restored != "Match User boxro\n    PermitTTY no\n":
        _fail(f"must restore previous drop-in, got {restored!r}")
    log = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if "systemctl reload" in log:
        _fail(f"failed sshd -t must not reload: {log!r}")


def test_sshd_t_failure_removes_new_dropin(tmp_path: Path) -> None:
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    env["BOXRO_TEST_SSHD_FAIL"] = "1"
    proc = _run(env, "boxro_apply_sshd_dropin")
    if proc.returncode == 0:
        _fail("first-install sshd -t failure must abort")
    dest = Path(env["BOXRO_SSHD_DROPIN"])
    if dest.exists():
        _fail("failed first write must remove drop-in")
    log = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if "systemctl reload" in log:
        _fail(f"failed first write must not reload: {log!r}")


def test_unmounted_dir_on_root_is_bind_mounted(tmp_path: Path) -> None:
    """Dir exists on / but is not a mountpoint: must bind, not remount."""
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    dest = Path(env["BOXRO_CHROOT"]) / "data"
    dest.mkdir(parents=True)
    (dest / "already-there").write_text("x\n", encoding="utf-8")
    proc = _run(
        env,
        'boxro_mount_ro_bind "$BOXRO_DATA_SRC" "${BOXRO_CHROOT}/data"',
    )
    _assert_ok(proc, "bind unmounted dest")
    log = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    bind = f"mount --bind {env['BOXRO_DATA_SRC']} {dest}"
    if bind not in log:
        _fail(f"unmounted dest must mount --bind first: {log!r}")
    first_mount = next(
        (line for line in log.splitlines() if line.startswith("mount ")),
        "",
    )
    if "remount" in first_mount:
        _fail(f"must not remount before bind: {log!r}")
    if "remount,bind,ro" not in log:
        _fail(f"must remount ro after bind: {log!r}")
    mounted = Path(env["BOXRO_TEST_MOUNTED"]).read_text(encoding="utf-8")
    if str(dest) not in mounted.splitlines():
        _fail(f"dest should be recorded as mounted: {mounted!r}")


def test_already_mounted_remounts_ro(tmp_path: Path) -> None:
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    dest = Path(env["BOXRO_CHROOT"]) / "data"
    dest.mkdir(parents=True)
    Path(env["BOXRO_TEST_MOUNTED"]).write_text(f"{dest}\n", encoding="utf-8")
    proc = _run(
        env,
        'boxro_mount_ro_bind "$BOXRO_DATA_SRC" "${BOXRO_CHROOT}/data"',
    )
    _assert_ok(proc, "remount existing")
    log = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if f"mount --bind {env['BOXRO_DATA_SRC']} {dest}" in log:
        _fail(f"already mounted dest must not bind again: {log!r}")
    if "remount,bind,ro" not in log:
        _fail(f"already mounted dest must remount ro: {log!r}")


def test_partial_vps_state_is_idempotent(tmp_path: Path) -> None:
    """User + dirs + fstab exist, nothing mounted, no drop-in/keys."""
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    Path(env["BOXRO_TEST_USER_FLAG"]).write_text("", encoding="utf-8")
    data_dest = Path(env["BOXRO_CHROOT"]) / "data"
    logs_dest = Path(env["BOXRO_CHROOT"]) / "logs"
    data_dest.mkdir(parents=True)
    logs_dest.mkdir(parents=True)
    data_line = f"{env['BOXRO_DATA_SRC']} {data_dest} none bind,ro 0 0"
    logs_line = f"{env['BOXRO_LOGS_SRC']} {logs_dest} none bind,ro 0 0"
    Path(env["BOXRO_FSTAB"]).write_text(
        f"proc /proc proc defaults 0 0\n{data_line}\n{logs_line}\n",
        encoding="utf-8",
    )
    proc = _run(env, "boxro_apply")
    _assert_ok(proc, "partial-state boxro_apply")
    log = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if "useradd" in log:
        _fail(f"existing user must not useradd: {log!r}")
    if f"mount --bind {env['BOXRO_DATA_SRC']} {data_dest}" not in log:
        _fail(f"partial state must bind data: {log!r}")
    if f"mount --bind {env['BOXRO_LOGS_SRC']} {logs_dest}" in log:
        _fail(f"must not bind logs: {log!r}")
    fstab = Path(env["BOXRO_FSTAB"]).read_text(encoding="utf-8")
    if fstab.count(data_line) != 1:
        _fail(f"data fstab duplicated on resume: {fstab!r}")
    if logs_line in fstab:
        _fail(f"logs fstab line must be removed: {fstab!r}")
    if logs_dest.exists():
        _fail("empty logs dest must be rmdir'd")
    if not Path(env["BOXRO_AUTH_DEST"]).is_file():
        _fail("partial state must sync authorized_keys")
    if Path(env["BOXRO_SSHD_DROPIN"]).read_text(encoding="utf-8") != DROPIN:
        _fail("partial state must write sshd drop-in")
    if "systemctl reload ssh" not in log:
        _fail(f"partial state must reload ssh once: {log!r}")


def test_drops_existing_logs_bind_without_touching_app_logs(tmp_path: Path) -> None:
    """Production leftover: logs mount + fstab line; /var/log/app must stay."""
    _write_stubs(tmp_path / "bin")
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    Path(env["BOXRO_TEST_USER_FLAG"]).write_text("", encoding="utf-8")
    data_dest = Path(env["BOXRO_CHROOT"]) / "data"
    logs_dest = Path(env["BOXRO_CHROOT"]) / "logs"
    data_dest.mkdir(parents=True)
    logs_dest.mkdir(parents=True)
    app_log = Path(env["BOXRO_LOGS_SRC"]) / "stock-analyzer.log"
    marker = "keep-app-log\n"
    app_log.write_text(marker, encoding="utf-8")
    data_line = f"{env['BOXRO_DATA_SRC']} {data_dest} none bind,ro 0 0"
    logs_line = f"{env['BOXRO_LOGS_SRC']} {logs_dest} none bind,ro 0 0"
    prod_line = "/var/log/app /srv/boxro/logs none bind,ro 0 0"
    Path(env["BOXRO_FSTAB"]).write_text(
        f"proc /proc proc defaults 0 0\n{data_line}\n{logs_line}\n{prod_line}\n",
        encoding="utf-8",
    )
    Path(env["BOXRO_TEST_MOUNTED"]).write_text(f"{logs_dest}\n", encoding="utf-8")
    proc = _run(env, "boxro_apply")
    _assert_ok(proc, "drop logs bind")
    log = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if f"umount -- {logs_dest}" not in log and f"umount {logs_dest}" not in log:
        _fail(f"must umount logs dest: {log!r}")
    if env["BOXRO_LOGS_SRC"] in log and "umount" in log:
        for line in log.splitlines():
            if line.startswith("umount ") and env["BOXRO_LOGS_SRC"] in line.split():
                _fail(f"must not umount /var/log/app: {log!r}")
    if f"mount --bind {env['BOXRO_LOGS_SRC']}" in log:
        _fail(f"must not bind logs: {log!r}")
    fstab = Path(env["BOXRO_FSTAB"]).read_text(encoding="utf-8")
    if logs_line in fstab or prod_line in fstab:
        _fail(f"logs fstab lines must be gone: {fstab!r}")
    if data_line not in fstab or "proc /proc proc defaults 0 0" not in fstab:
        _fail(f"must keep data and unrelated fstab lines: {fstab!r}")
    bak = Path(str(env["BOXRO_FSTAB"]) + ".boxro.bak")
    if not bak.is_file():
        _fail("must back up fstab before editing")
    if logs_line not in bak.read_text(encoding="utf-8"):
        _fail("backup must contain the removed logs line")
    if logs_dest.exists():
        _fail("empty logs dest must be removed")
    if app_log.read_text(encoding="utf-8") != marker:
        _fail("must not touch /var/log/app contents")
    Path(env["BOXRO_TEST_LOG"]).write_text("", encoding="utf-8")
    again = _run(env, "boxro_apply")
    _assert_ok(again, "second drop is idempotent")
    log2 = Path(env["BOXRO_TEST_LOG"]).read_text(encoding="utf-8")
    if "umount" in log2:
        _fail(f"already dropped logs must not umount again: {log2!r}")
    if f"mount --bind {env['BOXRO_LOGS_SRC']}" in log2:
        _fail(f"second apply must not bind logs: {log2!r}")


def test_provision_continues_after_boxro_failure(tmp_path: Path) -> None:
    """The if ! boxro_apply wrapper must not trip set -e."""
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "boxro_apply() { echo boxro-fail >&2; return 1; }\n"
        "if ! boxro_apply; then\n"
        '  echo "boxro: apply failed (sftp jail incomplete; app deploy continues)" >&2\n'
        "fi\n"
        "echo app-deploy-ok\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"boxro failure must not abort: rc={proc.returncode} {proc.stderr!r}")
    if "app-deploy-ok" not in proc.stdout:
        _fail(f"app deploy must continue: {proc.stdout!r}")
    if "sftp jail incomplete" not in proc.stderr:
        _fail(f"must report boxro failure: {proc.stderr!r}")


def test_dropin_body() -> None:
    proc = subprocess.run(
        [
            "bash",
            "-c",
            f"set -euo pipefail; source {BOXRO_SH.as_posix()!r}; boxro_sshd_dropin_body",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"dropin body rc={proc.returncode} {proc.stderr!r}")
    if proc.stdout != DROPIN:
        _fail(f"drop-in body mismatch: {proc.stdout!r}")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_static_contracts()
    test_dropin_body()
    with TemporaryDirectory() as raw:
        test_apply_idempotent_and_isolated(Path(raw))
    with TemporaryDirectory() as raw:
        test_unmounted_dir_on_root_is_bind_mounted(Path(raw))
    with TemporaryDirectory() as raw:
        test_already_mounted_remounts_ro(Path(raw))
    with TemporaryDirectory() as raw:
        test_partial_vps_state_is_idempotent(Path(raw))
    with TemporaryDirectory() as raw:
        test_drops_existing_logs_bind_without_touching_app_logs(Path(raw))
    with TemporaryDirectory() as raw:
        test_provision_continues_after_boxro_failure(Path(raw))
    with TemporaryDirectory() as raw:
        test_rejects_key_without_restrict(Path(raw))
    with TemporaryDirectory() as raw:
        test_syncs_additional_restrict_key(Path(raw))
    with TemporaryDirectory() as raw:
        test_sshd_t_failure_restores_previous(Path(raw))
    with TemporaryDirectory() as raw:
        test_sshd_t_failure_removes_new_dropin(Path(raw))
    print("ok: boxro")


if __name__ == "__main__":
    main()
