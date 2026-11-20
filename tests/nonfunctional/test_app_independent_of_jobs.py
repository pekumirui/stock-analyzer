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


def test_modules_the_backup_jobs_share_with_app_import_only_the_standard_library() -> None:
    import ast
    import sys

    shared = ("app/offsite_settings.py", "app/share_paths.py")
    bad = {}
    for rel in shared:
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                names.add("." if node.level else (node.module or "").split(".")[0])
        extra = sorted(names - set(sys.stdlib_module_names) - {"__future__"})
        if extra:
            bad[rel] = extra
    assert not bad, f"backup_db / migrate_r2_share が読む app のモジュールは標準ライブラリだけを使う: {bad}"
    assert (ROOT / "app" / "__init__.py").read_text(encoding="utf-8").strip() == ""
