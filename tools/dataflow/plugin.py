"""pytest プラグイン。テスト中に SQLite へ流れた SQL から docs/DATA_FLOW.md と docs/dataflow/ を作る。

`--dataflow=write` で書き出し、`--dataflow=check` で今のファイルと突き合わせる（作るファイルは `render.py`）（CI）。どちらも `tests` 全体を走らせる前提。
付けなければ何もしない。

1. SQL は SQLAlchemy の `before_cursor_execute` で拾う。DB に触るのは SQLAlchemy だけという約束は
   `tests/nonfunctional/test_db_access.py` が守る（例外は SQL を出さない `backup_db` と、`app/db.py` の PRAGMA）
2. 1 文ごとに、呼び出しスタックでいちばん外側にある `app/`・`jobs/` の関数へ、その文が読み書きしたテーブルを付ける
   （テストは `jobs/` を通さず `app/` を直に呼ぶことが多いので、入口のフレームは探さない）
3. 入口（`jobs/` の `__main__`、`app/api/` のルート関数）から関数の参照グラフ（`callgraph.py`）で届く関数のテーブルを足す
4. ジョブが HTTP で呼ぶ API（コードの中の `/api/...` の URL）は、そのルートのテーブルも足す（月次の取込は loopback の API に POST する）
"""
from __future__ import annotations

import difflib
import json
import os
import sys
import threading
from collections import defaultdict
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from tools.dataflow.callgraph import CallGraph, Key, is_project_file, match_route, module_name
from tools.dataflow.render import PAGE_DIR, render
from tools.dataflow.sql import tables

ROOT = Path(__file__).resolve().parents[2]
# 銘柄ごとの月次 HTML の読み方。入口ではない
JOBS_SKIP_DIRS = {"html_issuers"}
# スキーマの付け足しと、それに付く既存行の直し。どの入口も起動時に通るので、入口の読み書きに数えない
SKIP: frozenset[Key] = frozenset({("app.db", "init_db")})
# 書くテーブルを引数で受け取る汎用の関数。テストが下ごしらえで直に呼ぶと、ここに何でも付いて
# これを呼ぶ入口すべてに広がる。いちばん外側がこれらの文は数えない（入口の関数から呼ばれた分は、その関数に付く）
GENERIC: frozenset[Key] = frozenset({
    ("app.db", "upsert"),
    ("app.db", "delete_all_chunked"),
    ("app.db", "run_sqlite_write"),
})

_lock = threading.Lock()
_seen: dict[Key, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))  # 関数 → テーブル → {"R", "W"}
_modules: dict[str, str] = {}  # 絶対パス → モジュール名（app/ と jobs/ だけ）


def pytest_addoption(parser):
    parser.addoption(
        "--dataflow",
        choices=("write", "check"),
        default=None,
        help="テスト中の SQL から docs/DATA_FLOW.md と docs/dataflow/ を書く（write）か、今のファイルと突き合わせる（check）",
    )


def _outer_function() -> Key | None:
    found = None
    f = sys._getframe(1)
    while f is not None:
        code = f.f_code
        mod = _modules.get(code.co_filename)
        if mod is not None:
            qual = getattr(code, "co_qualname", code.co_name).split(".<locals>")[0]
            if not qual.startswith("<"):
                found = (mod, qual)
        f = f.f_back
    return found


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    writes, reads = tables(statement)
    if not writes and not reads:
        return
    key = _outer_function()
    if key is None or key in GENERIC:
        return
    with _lock:
        seen = _seen[key]
        for t in writes:
            seen[t].add("W")
        for t in reads:
            seen[t].add("R")


def pytest_configure(config):
    if not config.getoption("--dataflow"):
        return
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if is_project_file(rel):
            _modules[str(path)] = module_name(rel)
    # Engine のクラスに付けると、app.db.engine も sqlite_readonly_engine も、テストが作るエンジンも通る
    event.listen(Engine, "before_cursor_execute", _before_cursor_execute)


def job_entries(graph: CallGraph) -> dict[str, str]:
    """`jobs/...py` → モジュール名。`__main__` で何かを呼ぶものだけ。"""
    out = {}
    for name, mod in graph.mods.items():
        if not name.startswith("jobs.") or JOBS_SKIP_DIRS & set(name.split(".")) or not mod.main_refs:
            continue
        out[name.replace(".", "/") + ("/__init__.py" if mod.is_pkg else ".py")] = name
    return out


def _iter_api_routes(routes, prefix: str = ""):
    """(パス, APIRoute)。FastAPI 0.16x は include したルーターを展開せず `original_router` で持つので、たどってつなぐ。"""
    from fastapi.routing import APIRoute

    for route in routes:
        if isinstance(route, APIRoute):
            yield prefix + route.path, route
        elif hasattr(route, "original_router"):
            sub = route.original_router
            yield from _iter_api_routes(sub.routes, prefix + sub.prefix)


def api_entries() -> dict[str, Key]:
    """`GET /api/...` → ルート関数。`app/api/` のものだけ（SPA の配信・公開ページは除く）。"""
    from app.main import app

    schema_paths = set(app.openapi()["paths"])
    out: dict[str, Key] = {}
    for path, route in _iter_api_routes(app.routes):
        fn = route.endpoint
        if not fn.__module__.startswith("app.api"):
            continue
        if route.include_in_schema and path not in schema_paths:
            raise RuntimeError(f"dataflow: ルートのパスを組み立て損ねた（{path} が OpenAPI に無い）")
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            out[f"{method} {path}"] = (fn.__module__, fn.__qualname__)
    return out


def _union(keys: set[Key]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for k in keys:
        for t, rw in _seen.get(k, {}).items():
            out[t] |= rw
    return dict(out)


def collect():
    from app.models import Base

    known = {t.lower() for t in Base.metadata.tables}
    unknown = {t for seen in _seen.values() for t in seen if t not in known and not t.startswith("sqlite_")}
    for seen in _seen.values():
        for t in [t for t in seen if t not in known]:
            del seen[t]

    graph = CallGraph(ROOT)
    apis_all = api_entries()
    apis = {label: _union(graph.reachable({key}, SKIP)) for label, key in apis_all.items()}
    jobs_all = job_entries(graph)
    jobs: dict[str, dict[str, set[str]]] = {}
    job_apis: dict[str, list[str]] = {}
    for rel, mod in jobs_all.items():
        reach = graph.reachable(graph.main_roots(mod), SKIP)
        seen = _union(reach)
        called = sorted({
            label for method, url in graph.api_urls(reach) for label in apis_all
            if label.split(" ", 1)[0] == method and match_route(url, label.split(" ", 1)[1])
        })
        for label in called:
            for t, rw in apis[label].items():
                seen.setdefault(t, set()).update(rw)
        jobs[rel] = seen
        if called:
            job_apis[rel] = called
    return (
        {k: v for k, v in jobs.items() if v or k in job_apis},
        job_apis,
        {k: v for k, v in apis.items() if v},
        sorted(jobs_all),
        sorted(apis_all),
        sorted(unknown),
        sorted(known),
    )


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    mode = session.config.getoption("--dataflow")
    if not mode:
        return
    event.remove(Engine, "before_cursor_execute", _before_cursor_execute)
    tw = session.config.get_terminal_writer()
    what = f"docs/DATA_FLOW.md と {PAGE_DIR}/"
    if exitstatus != 0:
        tw.line(f"dataflow: テストが通っていないので {what} を{'書かない' if mode == 'write' else '突き合わせない'}", red=True)
        return
    if os.environ.get("DATAFLOW_DUMP"):
        Path(os.environ["DATAFLOW_DUMP"]).write_text(json.dumps(
            {f"{m}:{q}": {t: "".join(sorted(rw)) for t, rw in v.items()} for (m, q), v in _seen.items()},
            ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    files = render(*collect())
    # 作らなくなったファイル（機能が消えたページなど）も差分に数える
    stale = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / PAGE_DIR).glob("*") if p.is_file())
    stale = [rel for rel in stale if rel not in files]
    if mode == "write":
        for rel, text in files.items():
            path = ROOT / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for rel in stale:
            (ROOT / rel).unlink()
        tw.line(f"dataflow: {what} を書いた", green=True)
        from tools.jobs.render import write as write_jobs

        for rel in write_jobs():
            tw.line(f"dataflow: {rel} を書いた", green=True)
        return
    bad = False
    for rel, text in files.items():
        path = ROOT / rel
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if current == text:
            continue
        bad = True
        tw.line(f"dataflow: {rel} が実装と合わない", red=True)
        for line in difflib.unified_diff(current.splitlines(), text.splitlines(), rel, "(テストから作った版)", lineterm=""):
            tw.line(line)
    for rel in stale:
        bad = True
        tw.line(f"dataflow: {rel} はもう作らないファイル", red=True)
    if not bad:
        tw.line(f"dataflow: {what} は最新", green=True)
        return
    tw.line("dataflow: `python -m pytest tests --dataflow=write` で作り直す", red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
