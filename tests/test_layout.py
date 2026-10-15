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

GONE = (
    ROOT / "scripts",
    ROOT / "app" / "issuer_aliases.json",
    ROOT / "app" / "monthly_row_order.json",
    ROOT / "static" / "monthly_row_order.json",
    # 機能別フォルダへ移した。元の場所に戻さない
    ROOT / "app" / "api.py",
    ROOT / "tests" / "testdata",
    ROOT / "tools" / "fixtures",
    *(ROOT / "app" / f"{m}.py" for m in (
        "jquants", "ingest", "analytics", "earnings_schedule", "monthly_ingest", "monthly_tree",
        "public_monthly", "ir_monthly_codes", "supply_chain", "material_stats", "estat", "meti")),
    *(ROOT / "config" / f"{n}.json" for n in (
        "ir_monthly", "issuer_aliases", "issuer_metrics", "monthly_vocab", "monthly_row_order",
        "supply_chain", "supply_companies", "material_stats")),
)
REQUIRED = (
    ROOT / "config" / "monthly" / "issuer_aliases.json",
    ROOT / "config" / "monthly" / "monthly_row_order.json",
    ROOT / "jobs" / "run_batch.py",
    ROOT / "jobs" / "check_demo.py",
    ROOT / "tools" / "market" / "check_api.py",
    ROOT / "tools" / "monthly" / "read_xls.py",
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
