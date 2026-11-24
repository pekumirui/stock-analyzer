from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DURATIONS = ROOT / "tests" / "durations.json"
UNKNOWN_FILE_SEC = 1.0

_spent: dict[str, float] = defaultdict(float)
_store = [False]


def pytest_addoption(parser):
    parser.addoption(
        "--shard",
        default=None,
        help="i/n。テストのファイルを tests/durations.json の時間で n 組に均等に分け、i 組目（1 から）だけ走らせる",
    )
    parser.addoption(
        "--store-durations",
        action="store_true",
        help="ファイルごとの所要時間を tests/durations.json に書く（--shard の分け方に使う）",
    )


def parse_shard(spec: str) -> tuple[int, int]:
    try:
        index, total = (int(x) for x in spec.split("/"))
    except ValueError:
        raise pytest.UsageError(f"--shard は i/n: {spec!r}") from None
    if not 1 <= index <= total:
        raise pytest.UsageError(f"--shard は 1 <= i <= n: {spec!r}")
    return index, total


def assign(files: set[str], durations: dict[str, float], total: int) -> dict[str, int]:
    loads = [0.0] * total
    owner: dict[str, int] = {}
    for f in sorted(files, key=lambda f: (-durations.get(f, UNKNOWN_FILE_SEC), f)):
        k = min(range(total), key=lambda j: (loads[j], j))
        owner[f] = k + 1
        loads[k] += durations.get(f, UNKNOWN_FILE_SEC)
    return owner


def _file_of(nodeid: str) -> str:
    return nodeid.split("::", 1)[0]


def pytest_configure(config):
    _store[0] = bool(config.getoption("--store-durations")) and not hasattr(config, "workerinput")


def pytest_collection_modifyitems(config, items):
    spec = config.getoption("--shard")
    if not spec:
        return
    index, total = parse_shard(spec)
    durations = json.loads(DURATIONS.read_text(encoding="utf-8")) if DURATIONS.exists() else {}
    owner = assign({_file_of(item.nodeid) for item in items}, durations, total)
    keep = [item for item in items if owner[_file_of(item.nodeid)] == index]
    drop = [item for item in items if owner[_file_of(item.nodeid)] != index]
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep


def pytest_runtest_logreport(report):
    if _store[0]:
        _spent[_file_of(report.nodeid)] += report.duration


def pytest_sessionfinish(session, exitstatus):
    if not _store[0]:
        return
    DURATIONS.write_text(
        json.dumps({f: round(sec, 1) for f, sec in sorted(_spent.items())}, indent=1) + "\n",
        encoding="utf-8",
    )
