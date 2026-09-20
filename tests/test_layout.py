from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

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
    print("ok: layout")


if __name__ == "__main__":
    main()
