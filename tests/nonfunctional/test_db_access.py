"""DB には SQLAlchemy を通して触る。`app/` と `jobs/` で sqlite3 を直に開かず、SQLAlchemy の生の接続も取り出さない。

docs/DATA_FLOW.md（tools/dataflow/）は SQLAlchemy の `before_cursor_execute` で SQL を拾うので、通さない読み書きは表から黙って抜ける。
読み取り専用で開くなら `app.db.sqlite_readonly_engine`。
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VENDOR = ("app/xbrlp/", "app/edinet_wrapper/")

# ファイル → 許す理由
SQLITE3_ALLOWED = {
    "jobs/ops/backup_db.py": "SQLite のバックアップ API（Connection.backup）と PRAGMA quick_check。テーブルを読み書きする SQL は出さない",
}
RAW_ALLOWED = {
    "app/db.py": "接続ごとの PRAGMA（apply_sqlite_pragmas）。テーブルを読み書きする SQL は出さない",
}
RAW_NAMES = {"raw_connection", "driver_connection", "dbapi_connection"}


def _files():
    for d in ("app", "jobs"):
        for path in sorted((ROOT / d).rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if not rel.startswith(VENDOR):
                yield rel, ast.parse(path.read_text(encoding="utf-8"))


def _imports_sqlite3(tree: ast.AST) -> bool:
    for n in ast.walk(tree):
        if isinstance(n, ast.Import) and any(a.name.split(".")[0] == "sqlite3" for a in n.names):
            return True
        if isinstance(n, ast.ImportFrom) and n.level == 0 and (n.module or "").split(".")[0] == "sqlite3":
            return True
    return False


def _raw_names(tree: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(tree):
        name = n.attr if isinstance(n, ast.Attribute) else n.id if isinstance(n, ast.Name) else n.arg if isinstance(n, ast.arg) else None
        if name in RAW_NAMES:
            out.add(name)
    return out


def test_no_direct_sqlite3():
    bad = [rel for rel, tree in _files() if _imports_sqlite3(tree) and rel not in SQLITE3_ALLOWED]
    assert not bad, f"sqlite3 を直に使わず SQLAlchemy（app.db）を通す: {bad}"


def test_no_raw_dbapi_connection():
    bad = {rel: sorted(names) for rel, tree in _files() if (names := _raw_names(tree)) and rel not in RAW_ALLOWED}
    assert not bad, f"SQLAlchemy の生の接続を取り出さない: {bad}"


def test_allowlists_are_current():
    """許しているファイルがもう使っていなければ、許すのをやめる。"""
    trees = dict(_files())
    stale = [rel for rel in SQLITE3_ALLOWED if rel not in trees or not _imports_sqlite3(trees[rel])]
    stale += [rel for rel in RAW_ALLOWED if rel not in trees or not _raw_names(trees[rel])]
    assert not stale, f"許可の一覧から外す: {stale}"
