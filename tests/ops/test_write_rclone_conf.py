"""rclone.conf の [r2] upsert。他 remote を消さない。キーは出さない。"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))

from write_rclone_conf import (
    apply_r2,
    main,
    r2_section_body,
    upsert_section,
    write_conf,
)

ONEDRIVE = (
    "[onedrive]\n"
    "type = onedrive\n"
    'token = {"access_token":"secret-token-do-not-leak","expiry":"2099-01-01T00:00:00Z"}\n'
    "drive_id = drive-id-1\n"
    "drive_type = personal\n"
)


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_upsert_keeps_other_remotes() -> None:
    existing = ONEDRIVE + "\n[other]\nfoo = bar\n"
    got = apply_r2(existing, "AKIAFAKE", "secret-r2-key")
    if "[onedrive]" not in got or "secret-token-do-not-leak" not in got:
        _fail("onedrive remote must remain")
    if "[other]" not in got or "foo = bar" not in got:
        _fail("other remote must remain")
    if "access_key_id = AKIAFAKE" not in got or "secret_access_key = secret-r2-key" not in got:
        _fail("r2 keys missing")
    if got.count("[r2]") != 1:
        _fail("r2 section duplicated")


def test_upsert_replaces_existing_r2_only() -> None:
    old = (
        ONEDRIVE
        + "\n[r2]\n"
        + r2_section_body("OLDKEY", "OLDSECRET")
        + "\n[keep]\nflag = 1\n"
    )
    got = apply_r2(old, "NEWKEY", "NEWSECRET")
    if "OLDKEY" in got or "OLDSECRET" in got:
        _fail("old r2 keys remained")
    if "access_key_id = NEWKEY" not in got:
        _fail("new r2 key missing")
    if "secret-token-do-not-leak" not in got or "[keep]" not in got:
        _fail("neighbors clobbered")


def test_upsert_empty_and_idempotent() -> None:
    first = apply_r2(None, "K", "S")
    if not first.startswith("[r2]\n"):
        _fail(f"empty start {first!r}")
    second = apply_r2(first, "K", "S")
    if first != second:
        _fail("idempotent apply_r2")
    extra = upsert_section("[alpha]\na = 1\n", "beta", "b = 2\n")
    if "[alpha]" not in extra or "[beta]" not in extra:
        _fail("append section")


def test_write_conf_skips_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "rclone.conf"
    if not write_conf(path, "K", "S"):
        _fail("first write must change")
    if write_conf(path, "K", "S"):
        _fail("identical write must be a no-op")
    mode = path.stat().st_mode & 0o777
    if mode != 0o600:
        _fail(f"mode {oct(mode)}")


def test_main_noop_without_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main()
    if rc != 0:
        _fail("main without keys")
    if buf.getvalue():
        _fail(f"silent no-op expected {buf.getvalue()!r}")
    conf = tmp_path / ".config" / "rclone" / "rclone.conf"
    if conf.exists():
        _fail("must not create conf without keys")


def test_main_does_not_print_keys(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "AKIA-CANARY")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "r2-secret-canary")
    conf_dir = tmp_path / ".config" / "rclone"
    conf_dir.mkdir(parents=True)
    (conf_dir / "rclone.conf").write_text(ONEDRIVE, encoding="utf-8")
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = main()
    text = buf.getvalue()
    if rc != 0:
        _fail("main rc")
    if "AKIA-CANARY" in text or "r2-secret-canary" in text or "secret-token" in text:
        _fail(f"keys leaked {text!r}")
    if "updated rclone.conf" not in text:
        _fail(f"status missing {text!r}")
    got = (conf_dir / "rclone.conf").read_text(encoding="utf-8")
    if "secret-token-do-not-leak" not in got or "AKIA-CANARY" not in got:
        _fail("conf content")


def test_update_sh_calls_helper() -> None:
    text = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")
    if 'python3 "$APP_DIR/deploy/write_rclone_conf.py"' not in text:
        _fail("update.sh must call write_rclone_conf.py")
    if "path.write_text(text" in text:
        _fail("update.sh must not overwrite rclone.conf inline")
    helper = ROOT / "deploy" / "write_rclone_conf.py"
    if not helper.is_file():
        _fail("missing deploy/write_rclone_conf.py")


def main_tests() -> None:
    test_upsert_keeps_other_remotes()
    test_upsert_replaces_existing_r2_only()
    test_upsert_empty_and_idempotent()
    test_update_sh_calls_helper()
    with TemporaryDirectory() as d:
        test_write_conf_skips_unchanged(Path(d))
    print("ok: write_rclone_conf")


if __name__ == "__main__":
    os.environ.setdefault("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    main_tests()
