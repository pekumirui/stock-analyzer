from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ENTRY_RE = re.compile(r"(?:jobs|tools|tests)(?:[/\\][A-Za-z0-9_-]+)*[/\\][A-Za-z0-9_.-]+\.py")
CALLERS = (
    ROOT / "deploy" / "crontab",
    ROOT / "deploy" / "update.sh",
    ROOT / "run.sh",
    ROOT / "run.ps1",
    *sorted((ROOT / ".github" / "workflows").glob("*.yml")),
)

REQUIRED = (
    ROOT / "config" / "monthly" / "issuer_aliases.json",
    ROOT / "config" / "monthly" / "monthly_row_order.json",
    ROOT / "jobs" / "market" / "run_batch.py",
    ROOT / "jobs" / "market" / "check_demo.py",
    ROOT / "tools" / "market" / "check_api.py",
    ROOT / "tools" / "monthly" / "read_xls.py",
    ROOT / "tests" / "ci_smoke.py",
    ROOT / "mcp" / "log_mcp.py",
)


def main() -> None:
    for path in REQUIRED:
        if not path.is_file():
            raise SystemExit(f"missing {path.relative_to(ROOT)}")
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
