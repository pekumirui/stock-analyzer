"""pytest の共通設定。DB はプロセス（xdist のワーカー）ごとに 1 つで、ファイルごとに空から始める。

各テストファイルは読み込み時に DATABASE_URL を setdefault する。pytest ではここで先に
一時 DB へ向けるので、同じプロセスのファイルは同じパスを使い、ファイルが変わるたびに消して作り直す。
`python tests/<file>.py` で直接走らせたときは、各ファイルが自分の一時 DB を使う。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# `--dataflow=write|check` で docs/DATA_FLOW.md を作る・突き合わせる。付けなければ何もしない
pytest_plugins = ["tools.dataflow.plugin", "tools.ci.shard"]

DB_PATH = Path(tempfile.mkdtemp(prefix="stock-analyzer-pytest-")) / "stock.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH}"


@pytest.fixture(autouse=True, scope="module")
def _fresh_db_per_file():
    """ファイルの頭で DB を消して表を作り直す（直接走らせたときの main() の init_db と同じ）。"""
    from app.db import engine, init_db
    from app.xbrl.parser import reset_taxonomy_caches

    engine.dispose()
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(f"{DB_PATH}{suffix}").unlink(missing_ok=True)
    reset_taxonomy_caches()
    init_db()
    yield


@pytest.fixture(autouse=True)
def _edinet_archive_dir(tmp_path_factory, monkeypatch):
    """EDINET の ZIP（app/edinet/archive.py）は テストごとの一時ディレクトリへ。repo の data/edinet に書かない。"""
    monkeypatch.setenv("EDINET_ARCHIVE_DIR", str(tmp_path_factory.mktemp("edinet-archive")))


@pytest.fixture(autouse=True, scope="session")
def _no_standard_labels():
    from app.xbrl import three_statements

    patch = pytest.MonkeyPatch()
    patch.setattr(three_statements, "_standard_book", three_statements.StandardBook)
    yield
    patch.undo()


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """直接走らせたときに main() が渡していた一時ディレクトリ。"""
    return tmp_path


@pytest.fixture
def c():
    """API を叩く TestClient。"""
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)
