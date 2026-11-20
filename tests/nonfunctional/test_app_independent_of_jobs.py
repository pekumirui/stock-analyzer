from __future__ import annotations

from pathlib import Path

from tools.overview.modules import dependencies

ROOT = Path(__file__).resolve().parents[2]


def test_app_does_not_import_jobs() -> None:
    bad = {
        src: sorted(t for t in targets if t == "jobs" or t.startswith("jobs/"))
        for src, targets in dependencies(ROOT).items()
        if src == "app" or src.startswith("app/")
    }
    bad = {src: targets for src, targets in bad.items() if targets}
    assert not bad, f"app/ は jobs/ を import しない: {bad}"
