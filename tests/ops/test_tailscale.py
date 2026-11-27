from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SH = ROOT / "deploy" / "tailscale.sh"
PROVISION = ROOT / "deploy" / "provision.sh"
SECRET = "tskey-auth-KAB609-do-not-log"
JOINED = ("Running", "Starting", "Stopped", "NeedsMachineAuth")
FORBIDDEN = (
    "--ssh",
    "--advertise-exit-node",
    "--advertise-routes",
    "--exit-node",
)

_TAILSCALE = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${TAILSCALE_LOG}"
cmd="${1:-}"
if [[ "$cmd" == version ]]; then
  echo "tailscale version test"
  exit 0
fi
if [[ "$cmd" == status ]]; then
  state=NeedsLogin
  if [[ -f "${TAILSCALE_STATE}" ]]; then
    state="$(tr -d '\n' < "${TAILSCALE_STATE}")"
  fi
  host=stock-vps
  if [[ -f "${TAILSCALE_HOSTNAME}" ]]; then
    host="$(tr -d '\n' < "${TAILSCALE_HOSTNAME}")"
  fi
  printf '{"BackendState":"%s","Self":{"HostName":"%s"}}\n' \
    "$state" "$host"
  exit 0
fi
if [[ "$cmd" == up ]]; then
  for arg in "$@"; do
    case "$arg" in
      --auth-key=file:*)
        path="${arg#--auth-key=file:}"
        printf '%s\n' "$path" > "${TAILSCALE_KEY_PATH}"
        if [[ -f "$path" ]]; then
          cp "$path" "${TAILSCALE_KEY_COPY}"
          stat -c %a "$path" > "${TAILSCALE_KEY_MODE}"
        fi
        ;;
    esac
  done
  printf '%s\n' "$*" >&2
  if [[ "${TAILSCALE_UP_FAIL:-}" == 1 ]]; then
    exit 1
  fi
  printf '%s\n' Running > "${TAILSCALE_STATE}"
  exit 0
fi
echo "unexpected tailscale $*" >&2
exit 1
"""

_CURL = r"""#!/usr/bin/env bash
set -euo pipefail
url=
out=
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)
      out="$2"
      shift 2
      ;;
    -o*)
      out="${1#-o}"
      shift
      ;;
    -*)
      shift
      ;;
    *)
      url="$1"
      shift
      ;;
  esac
done
printf '%s\n' "$url" >> "${CURL_LOG}"
if [[ "${CURL_FAIL:-}" == 1 ]]; then
  exit 1
fi
if [[ -z "$out" ]]; then
  echo "curl missing -o" >&2
  exit 1
fi
printf 'gpg' > "$out"
"""

_APT = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${APT_LOG}"
if [[ "$*" == *install* && "$*" == *tailscale* ]]; then
  if [[ ! -e "${TAILSCALE_STUB_DEST}" ]]; then
    cp "${TAILSCALE_STUB_SRC}" "${TAILSCALE_STUB_DEST}"
    chmod 755 "${TAILSCALE_STUB_DEST}"
  fi
fi
exit 0
"""

_SYSTEMCTL = r"""#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "${SYSTEMCTL_LOG}"
if [[ "${SYSTEMCTL_FAIL:-}" == 1 ]]; then
  exit 1
fi
exit 0
"""


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _ups(path: Path) -> list[str]:
    return [line for line in _lines(path) if line.startswith("up ")]


def _path_without_tailscale(shadow: Path) -> list[str]:
    out: list[str] = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        if (Path(entry) / "tailscale").exists():
            shadow.mkdir(exist_ok=True)
            for tool in Path(entry).iterdir():
                link = shadow / tool.name
                if tool.name != "tailscale" and not os.path.lexists(link):
                    link.symlink_to(tool)
            entry = str(shadow)
        if entry not in out:
            out.append(entry)
    return out


def _world(
    tmp_path: Path,
    *,
    installed: bool,
    key: str | None,
) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub_src = tmp_path / "tailscale-stub"
    _write(stub_src, _TAILSCALE)
    dest = bin_dir / "tailscale"
    if installed:
        _write(dest, _TAILSCALE)
    _write(bin_dir / "curl", _CURL)
    _write(bin_dir / "apt-get", _APT)
    _write(bin_dir / "systemctl", _SYSTEMCTL)
    (tmp_path / "os-release").write_text(
        "VERSION_CODENAME=noble\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = os.pathsep.join([str(bin_dir), *_path_without_tailscale(tmp_path / "system")])
    env["TAILSCALE_STATE"] = str(tmp_path / "state")
    env["TAILSCALE_HOSTNAME"] = str(tmp_path / "hostname")
    env["TAILSCALE_LOG"] = str(tmp_path / "tailscale.log")
    env["TAILSCALE_KEY_COPY"] = str(tmp_path / "key.copy")
    env["TAILSCALE_KEY_PATH"] = str(tmp_path / "key.path")
    env["TAILSCALE_KEY_MODE"] = str(tmp_path / "key.mode")
    env["CURL_LOG"] = str(tmp_path / "curl.log")
    env["APT_LOG"] = str(tmp_path / "apt.log")
    env["SYSTEMCTL_LOG"] = str(tmp_path / "systemctl.log")
    env["TAILSCALE_STUB_SRC"] = str(stub_src)
    env["TAILSCALE_STUB_DEST"] = str(dest)
    env["TAILSCALE_OS_RELEASE"] = str(tmp_path / "os-release")
    env["TAILSCALE_KEYRING"] = str(tmp_path / "keyring" / "tailscale.gpg")
    env["TAILSCALE_LIST"] = str(tmp_path / "apt" / "tailscale.list")
    env.pop("TAILSCALE_AUTHKEY", None)
    env.pop("CURL_FAIL", None)
    env.pop("SYSTEMCTL_FAIL", None)
    env.pop("TAILSCALE_UP_FAIL", None)
    if key is not None:
        env["TAILSCALE_AUTHKEY"] = key
    return env


def _apply(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'set -euo pipefail; source "$1"; tailscale_apply',
            "bash",
            str(SH),
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _combined(proc: subprocess.CompletedProcess[str]) -> str:
    return f"{proc.stdout}\n{proc.stderr}"


def _assert_no_secret(text: str, where: str) -> None:
    if SECRET in text:
        _fail(f"{where} contains the auth key")


def _assert_skipped(proc: subprocess.CompletedProcess[str], log: Path) -> None:
    if proc.returncode != 0:
        _fail(f"skip path failed: rc={proc.returncode} {_combined(proc)}")
    if _ups(log):
        _fail(f"skip path ran tailscale up: {_ups(log)}")
    _assert_no_secret(_combined(proc), "skip output")


def test_shell_syntax() -> None:
    for path in (SH, PROVISION):
        chk = subprocess.run(
            ["bash", "-n", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if chk.returncode != 0:
            _fail(f"bash -n {path.name} failed: {chk.stderr or chk.stdout}")
    sc = shutil.which("shellcheck")
    if sc:
        proc = subprocess.run(
            [sc, "-x", str(SH)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            _fail(f"shellcheck failed:\n{proc.stdout}{proc.stderr}")


def test_provision_calls_tailscale_apply() -> None:
    text = PROVISION.read_text(encoding="utf-8")
    source_at = text.find('source "$APP_DIR/deploy/tailscale.sh"')
    call_at = text.find("\ntailscale_apply\n")
    sudoers_at = text.find("--sudoers-only")
    reboot_only_at = text.find("--kernel-reboot-only")
    if source_at < 0 or call_at < 0:
        _fail("provision.sh must source tailscale.sh and call tailscale_apply")
    if not sudoers_at < source_at < call_at:
        _fail("tailscale runs after the sudoers-only early exit")
    if not reboot_only_at < call_at:
        _fail("tailscale runs after the kernel-reboot-only early exit")
    if "if ! tailscale_apply" in text:
        _fail("a caught tailscale_apply would hide a failed join")


def test_joined_states_do_not_run_up(tmp_path: Path) -> None:
    for state in JOINED:
        work = tmp_path / state
        env = _world(work, installed=True, key=SECRET)
        Path(env["TAILSCALE_STATE"]).write_text(state + "\n", encoding="utf-8")
        env["SYSTEMCTL_FAIL"] = "1"
        proc = _apply(env)
        log = Path(env["TAILSCALE_LOG"])
        _assert_skipped(proc, log)
        if "skip up" not in proc.stdout:
            _fail(f"{state} did not skip up: {proc.stdout!r}")
        if _lines(Path(env["SYSTEMCTL_LOG"])):
            _fail(f"{state} changed systemd: {_lines(Path(env['SYSTEMCTL_LOG']))}")
        if _lines(Path(env["APT_LOG"])) or _lines(Path(env["CURL_LOG"])):
            _fail(f"{state} touched apt or the package repo")


def test_other_hostname_stays_joined(tmp_path: Path) -> None:
    env = _world(tmp_path, installed=True, key=SECRET)
    Path(env["TAILSCALE_STATE"]).write_text("Running\n", encoding="utf-8")
    Path(env["TAILSCALE_HOSTNAME"]).write_text("other-box\n", encoding="utf-8")
    proc = _apply(env)
    _assert_skipped(proc, Path(env["TAILSCALE_LOG"]))
    if "already joined as other-box" not in proc.stdout:
        _fail(f"hostname was hidden: {proc.stdout!r}")


def test_join_once_then_skip(tmp_path: Path) -> None:
    env = _world(tmp_path, installed=True, key=f"  {SECRET}\n")
    Path(env["TAILSCALE_STATE"]).write_text("NeedsLogin\n", encoding="utf-8")
    first = _apply(env)
    if first.returncode != 0:
        _fail(f"join failed: {_combined(first)}")
    ups = _ups(Path(env["TAILSCALE_LOG"]))
    if len(ups) != 1:
        _fail(f"expected one up, got {ups}")
    up = ups[0]
    for flag in FORBIDDEN:
        if flag in up:
            _fail(f"up used {flag}: {up}")
    for required in (
        "--hostname=stock-vps",
        "--accept-dns=false",
        "--accept-routes=false",
        "--timeout=90s",
        "--auth-key=file:",
    ):
        if required not in up:
            _fail(f"up missing {required}: {up}")
    if SECRET in up:
        _fail("up argv contains the auth key")
    copy = Path(env["TAILSCALE_KEY_COPY"]).read_text(encoding="utf-8")
    if copy != SECRET:
        _fail("auth key file was not the trimmed key")
    mode = Path(env["TAILSCALE_KEY_MODE"]).read_text(encoding="utf-8").strip()
    if mode != "600":
        _fail(f"auth key file mode is {mode}")
    key_path = Path(env["TAILSCALE_KEY_PATH"]).read_text(encoding="utf-8").strip()
    if Path(key_path).exists():
        _fail("auth key file was left on disk")
    if "joined hostname=stock-vps" not in first.stdout:
        _fail(f"join did not report hostname: {first.stdout!r}")
    _assert_no_secret(_combined(first), "join output")
    second = _apply(env)
    if second.returncode != 0:
        _fail(f"second apply failed: {_combined(second)}")
    if "skip up" not in second.stdout:
        _fail(f"second apply did not skip: {second.stdout!r}")
    if len(_ups(Path(env["TAILSCALE_LOG"]))) != 1:
        _fail("second apply ran tailscale up")
    _assert_no_secret(_combined(second), "second apply")


def test_blank_authkey_skips_join(tmp_path: Path) -> None:
    for label, key in (("empty", ""), ("blank", " \n\t "), ("unset", None)):
        env = _world(tmp_path / label, installed=True, key=key)
        Path(env["TAILSCALE_STATE"]).write_text("NeedsLogin\n", encoding="utf-8")
        proc = _apply(env)
        _assert_skipped(proc, Path(env["TAILSCALE_LOG"]))
        if "auth key unset; skip join" not in proc.stdout:
            _fail(f"{label} did not skip join: {proc.stdout!r}")


def test_xtrace_and_failed_up_omit_authkey(tmp_path: Path) -> None:
    env = _world(tmp_path, installed=True, key=SECRET)
    Path(env["TAILSCALE_STATE"]).write_text("NeedsLogin\n", encoding="utf-8")
    script = tmp_path / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\nset -euo pipefail\nset -x\nsource \"$1\"\ntailscale_apply\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script), str(SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"xtrace join failed: {_combined(proc)}")
    _assert_no_secret(_combined(proc), "xtrace")
    if len(_ups(Path(env["TAILSCALE_LOG"]))) != 1:
        _fail("xtrace join did not run up once")
    env["TAILSCALE_UP_FAIL"] = "1"
    Path(env["TAILSCALE_STATE"]).write_text("NeedsLogin\n", encoding="utf-8")
    Path(env["TAILSCALE_LOG"]).write_text("", encoding="utf-8")
    failed = _apply(env)
    if failed.returncode == 0:
        _fail("failed up must fail the apply")
    if _ups(Path(env["TAILSCALE_LOG"])) == []:
        _fail("failed up was not attempted")
    _assert_no_secret(_combined(failed), "failed up output")
    key_path = Path(env["TAILSCALE_KEY_PATH"]).read_text(encoding="utf-8").strip()
    if Path(key_path).exists():
        _fail("auth key file remained after a failed up")


def test_install_then_second_apply_does_not_reinstall(tmp_path: Path) -> None:
    env = _world(tmp_path, installed=False, key=SECRET)
    first = _apply(env)
    if first.returncode != 0:
        _fail(f"install join failed: {_combined(first)}")
    curls = _lines(Path(env["CURL_LOG"]))
    if len(curls) != 1 or "noble.noarmor.gpg" not in curls[0]:
        _fail(f"keyring was not fetched for noble: {curls}")
    if "pkgs.tailscale.com" not in curls[0] or SECRET in curls[0]:
        _fail(f"keyring URL is wrong: {curls}")
    apts = _lines(Path(env["APT_LOG"]))
    installs = [line for line in apts if line.startswith("install ")]
    if installs != ["install -y -qq tailscale"]:
        _fail(f"apt install args: {apts}")
    for word in ("nginx", "ufw", "openssh", "ssh"):
        if any(word in line for line in apts):
            _fail(f"apt touched {word}: {apts}")
    services = _lines(Path(env["SYSTEMCTL_LOG"]))
    if services != ["enable --now tailscaled"]:
        _fail(f"systemd calls: {services}")
    listed = Path(env["TAILSCALE_LIST"]).read_text(encoding="utf-8")
    if "pkgs.tailscale.com" not in listed or "noble" not in listed:
        _fail(f"apt source does not track noble: {listed!r}")
    if SECRET in listed:
        _fail("apt source contains the auth key")
    if not Path(env["TAILSCALE_KEYRING"]).is_file():
        _fail("keyring was not installed")
    if len(_ups(Path(env["TAILSCALE_LOG"]))) != 1:
        _fail("install path did not join")
    _assert_no_secret(_combined(first), "install output")
    second = _apply(env)
    if second.returncode != 0:
        _fail(f"second apply failed: {_combined(second)}")
    if "skip up" not in second.stdout:
        _fail(f"second apply did not skip: {second.stdout!r}")
    if _lines(Path(env["CURL_LOG"])) != curls:
        _fail("second apply fetched the keyring again")
    if _lines(Path(env["APT_LOG"])) != apts:
        _fail("second apply ran apt again")
    if len(_ups(Path(env["TAILSCALE_LOG"]))) != 1:
        _fail("second apply ran tailscale up")


def test_install_failure_follows_authkey(tmp_path: Path) -> None:
    missing = _world(tmp_path / "missing", installed=False, key=SECRET)
    missing["CURL_FAIL"] = "1"
    failed = _apply(missing)
    if failed.returncode == 0:
        _fail("install failure with an auth key must fail the apply")
    if _ups(Path(missing["TAILSCALE_LOG"])):
        _fail("install failure still ran up")
    _assert_no_secret(_combined(failed), "install failure")
    if Path(missing["TAILSCALE_LIST"]).exists():
        _fail("failed keyring download still wrote an apt source")
    empty = _world(tmp_path / "empty", installed=False, key="")
    empty["CURL_FAIL"] = "1"
    skipped = _apply(empty)
    if skipped.returncode != 0:
        _fail(f"empty key must survive install failure: {_combined(skipped)}")
    if "skip join" not in skipped.stderr and "skip join" not in skipped.stdout:
        _fail(f"empty key did not skip: {_combined(skipped)}")
    if _ups(Path(empty["TAILSCALE_LOG"])):
        _fail("empty key ran up after install failure")


def test_install_uses_provision_apt_retry(tmp_path: Path) -> None:
    env = _world(tmp_path, installed=False, key=None)
    env["APT_RETRY_LOG"] = str(tmp_path / "retry.log")
    script = tmp_path / "run.sh"
    script.write_text(
        """\
#!/usr/bin/env bash
set -euo pipefail
source "$1"
apt_retry() {
  printf '%s\\n' "$*" >> "${APT_RETRY_LOG}"
  if [[ ! -e "${TAILSCALE_STUB_DEST}" ]]; then
    cp "${TAILSCALE_STUB_SRC}" "${TAILSCALE_STUB_DEST}"
    chmod 755 "${TAILSCALE_STUB_DEST}"
  fi
}
tailscale_ensure_installed
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        ["bash", str(script), str(SH)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"apt_retry install failed: {_combined(proc)}")
    if _lines(Path(env["APT_RETRY_LOG"])) != ["tailscale"]:
        _fail(f"apt_retry args: {_lines(Path(env['APT_RETRY_LOG']))}")
    if any(line.startswith("install ") for line in _lines(Path(env["APT_LOG"]))):
        _fail(f"direct apt-get install ran: {_lines(Path(env['APT_LOG']))}")


def test_daemon_failure_follows_authkey(tmp_path: Path) -> None:
    present = _world(tmp_path / "present", installed=True, key=SECRET)
    Path(present["TAILSCALE_STATE"]).write_text("NeedsLogin\n", encoding="utf-8")
    present["SYSTEMCTL_FAIL"] = "1"
    failed = _apply(present)
    if failed.returncode == 0:
        _fail("daemon failure with an auth key must fail the apply")
    if _ups(Path(present["TAILSCALE_LOG"])):
        _fail("daemon failure still ran up")
    _assert_no_secret(_combined(failed), "daemon failure")
    blank = _world(tmp_path / "blank", installed=True, key="")
    Path(blank["TAILSCALE_STATE"]).write_text("NeedsLogin\n", encoding="utf-8")
    blank["SYSTEMCTL_FAIL"] = "1"
    skipped = _apply(blank)
    if skipped.returncode != 0:
        _fail(f"empty key must survive daemon failure: {_combined(skipped)}")
    if _ups(Path(blank["TAILSCALE_LOG"])):
        _fail("empty key ran up after daemon failure")
