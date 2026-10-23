"""monthly_push write-only rrsync key: restrict, dest jail, append-only stock keys."""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MONTHLY_PUSH_SH = ROOT / "deploy" / "monthly_push.sh"
KEYS = ROOT / "deploy" / "ssh" / "monthly_push_authorized_keys"
BOXRO_KEYS = ROOT / "deploy" / "ssh" / "boxro_authorized_keys"
ADMIN_KEYS = ROOT / "deploy" / "admin_keys"
PROVISION = ROOT / "deploy" / "provision.sh"
ENABLE = ROOT / "deploy" / "enable-cicd.sh"
PUBKEY = (
    "ssh-ed25519 "
    "AAAAC3NzaC1lZDI1NTE5AAAAICdBzMpXkspI+L/2YcNNB645yoPqL1fOF+eR7II7vcR/ "
    "box-monthly-push"
)
KEY_LINE = (
    "restrict,command="
    '"/usr/bin/rrsync -wo /home/stock/stock-analyzer/data/monthly" '
    + PUBKEY
)
DEPLOY_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA "
    "github-actions-deploy"
)
ADMIN_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILDMGyw0aJsb7Kr8YoF6Qs8lLnAaVRZQPT/bljdSkq1E "
    "grok-stock-analyzer"
)


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_static_contracts() -> None:
    if not KEYS.is_file():
        _fail("deploy/ssh/monthly_push_authorized_keys missing")
    keys = KEYS.read_text(encoding="utf-8")
    if "BEGIN " in keys and "PRIVATE" in keys:
        _fail("monthly_push keys file must not contain a private key")
    live = [
        line
        for line in keys.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if live != [KEY_LINE]:
        _fail(f"expected restrict rrsync key {KEY_LINE!r}, got {live!r}")
    if "-wo" not in KEY_LINE or "/data/monthly" not in KEY_LINE:
        _fail("key command must be rrsync -wo …/data/monthly")
    if "from=" in KEY_LINE:
        _fail("monthly_push must not IP-restrict (box IP is not fixed)")

    admin = ADMIN_KEYS.read_text(encoding="utf-8")
    if "box-monthly-push" in admin or "rrsync" in admin:
        _fail("monthly_push key must not live in deploy/admin_keys (that file goes to root)")
    boxro = BOXRO_KEYS.read_text(encoding="utf-8")
    if "box-monthly-push" in boxro or "rrsync" in boxro:
        _fail("monthly_push key must not live in boxro_authorized_keys")

    provision = PROVISION.read_text(encoding="utf-8")
    if "source \"$APP_DIR/deploy/monthly_push.sh\"" not in provision:
        _fail("provision.sh must source deploy/monthly_push.sh")
    if "if ! monthly_push_apply; then" not in provision:
        _fail("provision.sh must catch monthly_push_apply so a failure cannot abort app deploy")
    if re.search(r"^monthly_push_apply$", provision, re.M):
        _fail("bare monthly_push_apply would abort provision under set -e")
    if "systemctl restart ssh" in provision or "systemctl restart sshd" in provision:
        _fail("provision.sh must not restart ssh")
    apt_block = provision.split("apt_retry \\", 1)[-1].split("timedatectl", 1)[0]
    if re.search(r"\brsync\b", apt_block) is None:
        _fail("provision.sh apt_retry must install rsync for /usr/bin/rrsync")

    enable = ENABLE.read_text(encoding="utf-8")
    if "monthly_push" in enable or "rrsync" in enable:
        _fail("enable-cicd.sh must not install monthly_push (boxro is provision-only too)")

    script = MONTHLY_PUSH_SH.read_text(encoding="utf-8")
    if "systemctl restart ssh" in script or "systemctl restart sshd" in script:
        _fail("monthly_push.sh must not restart ssh")
    if "systemctl reload" in script:
        _fail("monthly_push.sh must not reload ssh (authorized_keys only)")
    if "ChrootDirectory" in script or "ForceCommand" in script:
        _fail("monthly_push must use key command=rrsync, not an sshd Match jail")
    if "/root/.ssh/authorized_keys" in script:
        _fail("monthly_push.sh must not write root authorized_keys")
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
            _fail(f"monthly_push.sh must not mention {forbidden}")
    if "rrsync" not in script or "-wo" not in script:
        _fail("monthly_push.sh must pin rrsync -wo")
    if "data/monthly" not in script:
        _fail("monthly_push.sh must pin dest data/monthly")

    chk = subprocess.run(
        ["bash", "-n", str(MONTHLY_PUSH_SH)],
        capture_output=True,
        text=True,
        check=False,
    )
    if chk.returncode != 0:
        _fail(f"bash -n monthly_push.sh failed: {chk.stderr or chk.stdout}")
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
            [sc, "-x", str(MONTHLY_PUSH_SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            _fail(f"shellcheck monthly_push.sh failed:\n{proc.stdout}{proc.stderr}")


def _env(tmp: Path, app: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PATH"] = f"{tmp / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    env["APP_DIR"] = str(app)
    env["MONTHLY_PUSH_USER"] = "stock"
    env["MONTHLY_PUSH_DATA_SRC"] = str(app / "data")
    env["MONTHLY_PUSH_DEST"] = str(app / "data" / "monthly")
    env["MONTHLY_PUSH_RRSYNC"] = str(tmp / "bin" / "rrsync")
    env["MONTHLY_PUSH_AUTH_SRC"] = str(app / "deploy" / "ssh" / "monthly_push_authorized_keys")
    env["MONTHLY_PUSH_AUTH_DEST"] = str(tmp / "home" / "stock" / ".ssh" / "authorized_keys")
    return env


def _seed_app(tmp: Path) -> Path:
    app = tmp / "app"
    (app / "data").mkdir(parents=True)
    keys_dir = app / "deploy" / "ssh"
    keys_dir.mkdir(parents=True)
    shutil.copy(KEYS, keys_dir / "monthly_push_authorized_keys")
    bin_dir = tmp / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "rrsync").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    _chmod_x(bin_dir / "rrsync")
    auth_dir = tmp / "home" / "stock" / ".ssh"
    auth_dir.mkdir(parents=True)
    (auth_dir / "authorized_keys").write_text(
        f"{DEPLOY_KEY}\n{ADMIN_KEY}\n", encoding="utf-8"
    )
    (tmp / "root" / ".ssh").mkdir(parents=True)
    (tmp / "root" / ".ssh" / "authorized_keys").write_text(
        f"{ADMIN_KEY}\n", encoding="utf-8"
    )
    return app


def _run(env: dict[str, str], func: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            f"set -euo pipefail; source {MONTHLY_PUSH_SH.as_posix()!r}; {func}",
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


def test_apply_appends_without_touching_existing_keys(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    first = _run(env, "monthly_push_apply")
    _assert_ok(first, "first monthly_push_apply")
    dest = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    if not dest.startswith(f"{DEPLOY_KEY}\n{ADMIN_KEY}\n"):
        _fail(f"existing stock keys must stay at the front: {dest!r}")
    if KEY_LINE not in dest:
        _fail(f"restricted rrsync key missing: {dest!r}")
    if dest.count(KEY_LINE) != 1:
        _fail(f"key duplicated: {dest!r}")
    if not Path(env["MONTHLY_PUSH_DEST"]).is_dir():
        _fail("data/monthly must be created")
    root_auth = (tmp_path / "root" / ".ssh" / "authorized_keys").read_text(
        encoding="utf-8"
    )
    if KEY_LINE in root_auth or "rrsync" in root_auth:
        _fail(f"must not write root authorized_keys: {root_auth!r}")
    second = _run(env, "monthly_push_apply")
    _assert_ok(second, "second monthly_push_apply")
    if "authorized_keys unchanged" not in second.stdout:
        _fail(f"second apply should skip: {second.stdout!r}")
    dest2 = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    if dest2 != dest:
        _fail("idempotent apply must not rewrite authorized_keys")
    if dest2.count(KEY_LINE) != 1:
        _fail(f"second apply duplicated the key: {dest2!r}")


def test_rejects_key_without_restrict(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    src = Path(env["MONTHLY_PUSH_AUTH_SRC"])
    src.write_text(f"{PUBKEY}\n", encoding="utf-8")
    before = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    proc = _run(env, "monthly_push_sync_authorized_keys")
    if proc.returncode == 0:
        _fail("key without restrict must fail")
    if "must start with restrict" not in proc.stderr:
        _fail(f"expected restrict error: {proc.stderr!r}")
    after = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    if after != before:
        _fail("failed sync must not rewrite dest")


def test_rejects_key_without_write_only_rrsync(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    src = Path(env["MONTHLY_PUSH_AUTH_SRC"])
    src.write_text(
        'restrict,command="/usr/bin/rrsync /home/stock/stock-analyzer/data/monthly" '
        f"{PUBKEY}\n",
        encoding="utf-8",
    )
    proc = _run(env, "monthly_push_validate_authorized_keys \"$MONTHLY_PUSH_AUTH_SRC\"")
    if proc.returncode == 0:
        _fail("rrsync without -wo must fail")
    if "must include -wo" not in proc.stderr:
        _fail(f"expected -wo error: {proc.stderr!r}")


def test_rejects_dest_outside_data_monthly(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    src = Path(env["MONTHLY_PUSH_AUTH_SRC"])
    bad = (
        'restrict,command="/usr/bin/rrsync -wo /home/stock/stock-analyzer/data" '
        f"{PUBKEY}\n"
    )
    src.write_text(bad, encoding="utf-8")
    proc = _run(env, "monthly_push_validate_authorized_keys \"$MONTHLY_PUSH_AUTH_SRC\"")
    if proc.returncode == 0:
        _fail("dest data/ (not data/monthly) must fail")
    if "must end with /data/monthly" not in proc.stderr:
        _fail(f"expected dest error: {proc.stderr!r}")
    src.write_text(
        'restrict,command="/usr/bin/rrsync -wo /home/stock/stock-analyzer/data/monthly/../xbrl" '
        f"{PUBKEY}\n",
        encoding="utf-8",
    )
    proc = _run(env, "monthly_push_validate_authorized_keys \"$MONTHLY_PUSH_AUTH_SRC\"")
    if proc.returncode == 0:
        _fail("dest with .. must fail")


def test_rejects_same_blob_with_different_options(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    dest = Path(env["MONTHLY_PUSH_AUTH_DEST"])
    dest.write_text(f"{DEPLOY_KEY}\n{PUBKEY}\n", encoding="utf-8")
    proc = _run(env, "monthly_push_sync_authorized_keys")
    if proc.returncode == 0:
        _fail("unrestricted copy of the same key must fail")
    if "different options" not in proc.stderr:
        _fail(f"expected blob-conflict error: {proc.stderr!r}")
    after = dest.read_text(encoding="utf-8")
    if KEY_LINE in after:
        _fail("must not append restricted line when unrestricted blob exists")
    if DEPLOY_KEY not in after:
        _fail("must not drop the deploy key on blob conflict")


def test_if_not_apply_propagates_rrsync_failure(tmp_path: Path) -> None:
    """provision.sh uses `if ! monthly_push_apply`, which disables set -e inside."""
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    env["MONTHLY_PUSH_RRSYNC"] = str(tmp_path / "bin" / "no-such-rrsync")
    before = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    script = (
        "set -euo pipefail; "
        f"source {MONTHLY_PUSH_SH.as_posix()!r}; "
        "if ! monthly_push_apply; then echo caught; exit 7; fi; echo continued"
    )
    proc = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    if proc.returncode != 7 or "caught" not in proc.stdout:
        _fail(
            f"if ! apply must see rrsync failure: rc={proc.returncode} "
            f"out={proc.stdout!r} err={proc.stderr!r}"
        )
    if "continued" in proc.stdout:
        _fail("apply must not succeed when rrsync is missing")
    after = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    if after != before:
        _fail("failed apply must not write authorized_keys")


def test_missing_rrsync_fails_without_writing_keys(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    env["MONTHLY_PUSH_RRSYNC"] = str(tmp_path / "bin" / "no-such-rrsync")
    before = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    proc = _run(env, "monthly_push_apply")
    if proc.returncode == 0:
        _fail("missing rrsync must fail")
    if "missing" not in proc.stderr or "rrsync" not in proc.stderr:
        _fail(f"expected missing rrsync error: {proc.stderr!r}")
    after = Path(env["MONTHLY_PUSH_AUTH_DEST"]).read_text(encoding="utf-8")
    if after != before:
        _fail("missing rrsync must not write authorized_keys")


def test_missing_data_dir_fails(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    shutil.rmtree(app / "data")
    proc = _run(env, "monthly_push_apply")
    if proc.returncode == 0:
        _fail("missing data/ must fail")
    if "missing" not in proc.stderr:
        _fail(f"expected missing data error: {proc.stderr!r}")


def test_rejects_private_key_material(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    src = Path(env["MONTHLY_PUSH_AUTH_SRC"])
    src.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    proc = _run(env, "monthly_push_validate_authorized_keys \"$MONTHLY_PUSH_AUTH_SRC\"")
    if proc.returncode == 0:
        _fail("private key material must fail")
    if "secret key material" not in proc.stderr:
        _fail(f"expected secret error: {proc.stderr!r}")


def test_rejects_command_metacharacters(tmp_path: Path) -> None:
    app = _seed_app(tmp_path)
    env = _env(tmp_path, app)
    src = Path(env["MONTHLY_PUSH_AUTH_SRC"])
    src.write_text(
        'restrict,command="/usr/bin/rrsync -wo /home/stock/stock-analyzer/data/monthly; id" '
        f"{PUBKEY}\n",
        encoding="utf-8",
    )
    proc = _run(env, "monthly_push_validate_authorized_keys \"$MONTHLY_PUSH_AUTH_SRC\"")
    if proc.returncode == 0:
        _fail("command with ; must fail")
    if "shell metacharacters" not in proc.stderr:
        _fail(f"expected metacharacter error: {proc.stderr!r}")


def test_provision_continues_after_monthly_push_failure(tmp_path: Path) -> None:
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "monthly_push_apply() { echo monthly-push-fail >&2; return 1; }\n"
        "if ! monthly_push_apply; then\n"
        '  echo "monthly_push: apply failed (rrsync key not installed; app deploy continues)" >&2\n'
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
        _fail(f"monthly_push failure must not abort: rc={proc.returncode} {proc.stderr!r}")
    if "app-deploy-ok" not in proc.stdout:
        _fail(f"app deploy must continue: {proc.stdout!r}")
    if "rrsync key not installed" not in proc.stderr:
        _fail(f"must report monthly_push failure: {proc.stderr!r}")


def test_rrsync_write_only_jail(tmp_path: Path) -> None:
    """If rrsync is installed, it must refuse paths outside the dest and -wo listing."""
    rrsync = shutil.which("rrsync") or (
        "/usr/bin/rrsync" if Path("/usr/bin/rrsync").is_file() else None
    )
    if not rrsync:
        return
    monthly = tmp_path / "monthly"
    (monthly / "9279").mkdir(parents=True)
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "stock.db").write_text("secret\n", encoding="utf-8")
    (monthly / "9279" / "keep.pdf").write_text("pdf\n", encoding="utf-8")

    def _rrsync(original: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["SSH_ORIGINAL_COMMAND"] = original
        return subprocess.run(
            [rrsync, "-wo", str(monthly)],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    listed = _rrsync("rsync --server --sender -g -l -o -p -D -t -r . 9279")
    if listed.returncode == 0:
        _fail(f"rrsync -wo must not allow listing/download: {listed.stderr!r}")
    escaped = _rrsync("rsync --server -g -l -o -p -D -t -r . ../outside")
    if escaped.returncode == 0:
        _fail(f"rrsync must not allow ../ outside dest: {escaped.stderr!r}")


def main() -> None:
    from tempfile import TemporaryDirectory

    test_static_contracts()
    with TemporaryDirectory() as raw:
        test_apply_appends_without_touching_existing_keys(Path(raw))
    with TemporaryDirectory() as raw:
        test_rejects_key_without_restrict(Path(raw))
    with TemporaryDirectory() as raw:
        test_rejects_key_without_write_only_rrsync(Path(raw))
    with TemporaryDirectory() as raw:
        test_rejects_dest_outside_data_monthly(Path(raw))
    with TemporaryDirectory() as raw:
        test_rejects_same_blob_with_different_options(Path(raw))
    with TemporaryDirectory() as raw:
        test_if_not_apply_propagates_rrsync_failure(Path(raw))
    with TemporaryDirectory() as raw:
        test_missing_rrsync_fails_without_writing_keys(Path(raw))
    with TemporaryDirectory() as raw:
        test_missing_data_dir_fails(Path(raw))
    with TemporaryDirectory() as raw:
        test_rejects_private_key_material(Path(raw))
    with TemporaryDirectory() as raw:
        test_rejects_command_metacharacters(Path(raw))
    with TemporaryDirectory() as raw:
        test_provision_continues_after_monthly_push_failure(Path(raw))
    with TemporaryDirectory() as raw:
        test_rrsync_write_only_jail(Path(raw))
    print("ok: monthly_push")


if __name__ == "__main__":
    main()
