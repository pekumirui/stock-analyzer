from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENTRY_RE = re.compile(r"(?:jobs|tools|tests)[/\\][A-Za-z0-9_.-]+\.py")
CALLERS = (
    ROOT / "deploy" / "crontab",
    ROOT / "deploy" / "update.sh",
    ROOT / "run.sh",
    ROOT / "run.ps1",
    *sorted((ROOT / ".github" / "workflows").glob("*.yml")),
)

GONE = (
    ROOT / "scripts",
    ROOT / "app" / "issuer_aliases.json",
    ROOT / "app" / "monthly_row_order.json",
    ROOT / "static" / "monthly_row_order.json",
)
REQUIRED = (
    ROOT / "config" / "issuer_aliases.json",
    ROOT / "config" / "monthly_row_order.json",
    ROOT / "jobs" / "run_batch.py",
    ROOT / "jobs" / "check_demo.py",
    ROOT / "tools" / "check_api.py",
    ROOT / "tools" / "read_xls.py",
    ROOT / "tests" / "ci_smoke.py",
)


def main() -> None:
    for path in GONE:
        if path.exists():
            raise SystemExit(f"stale path must not exist: {path.relative_to(ROOT)}")
    for path in REQUIRED:
        if not path.is_file():
            raise SystemExit(f"missing {path.relative_to(ROOT)}")
    leftover = list(ROOT.rglob("seed_demo.py"))
    if leftover:
        raise SystemExit(f"seed_demo.py should have been removed: {leftover}")
    tracked_venv = subprocess.check_output(
        ["git", "ls-files", "-z", "--", ".venv"],
        cwd=ROOT,
        text=True,
    )
    if tracked_venv:
        raise SystemExit(f".venv must not be tracked: {tracked_venv!r}")
    gi_lines = {
        line.strip()
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    if ".venv" not in gi_lines:
        raise SystemExit(
            ".gitignore must list .venv without a trailing slash so a symlink is ignored"
        )
    with tempfile.TemporaryDirectory(prefix="gitignore-venv-") as raw:
        repo = Path(raw)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        (repo / ".gitignore").write_text(
            (ROOT / ".gitignore").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (repo / ".venv").symlink_to("/nonexistent-venv")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        listed = subprocess.check_output(
            ["git", "ls-files", "--", ".venv"],
            cwd=repo,
            text=True,
        )
        if listed.strip():
            raise SystemExit(
                ".gitignore must ignore a .venv symlink, not only a directory: "
                f"{listed!r}"
            )
    for src in CALLERS:
        text = src.read_text(encoding="utf-8")
        rel_src = src.relative_to(ROOT).as_posix()
        if re.search(r"scripts[/\\]", text):
            raise SystemExit(f"stale scripts/ path in {rel_src}")
        for raw in ENTRY_RE.findall(text):
            rel = raw.replace("\\", "/")
            if not (ROOT / rel).is_file():
                raise SystemExit(f"missing {rel} (from {rel_src})")
    print("ok: layout")


if __name__ == "__main__":
    main()
