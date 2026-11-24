from __future__ import annotations

import json

import pytest

from tools.ci.shard import DURATIONS, UNKNOWN_FILE_SEC, assign, parse_shard


def test_every_file_goes_to_exactly_one_shard() -> None:
    files = {f"tests/test_{i}.py" for i in range(20)}
    owner = assign(files, {}, 3)
    assert set(owner) == files
    assert set(owner.values()) == {1, 2, 3}


def test_long_files_are_spread_out() -> None:
    durations = {"tests/a.py": 30.0, "tests/b.py": 29.0, "tests/c.py": 1.0, "tests/d.py": 1.0}
    owner = assign(set(durations), durations, 2)
    assert owner["tests/a.py"] != owner["tests/b.py"]


def test_unknown_files_count_as_the_default() -> None:
    owner = assign({"tests/new.py", "tests/old.py"}, {"tests/old.py": UNKNOWN_FILE_SEC * 10}, 2)
    assert owner["tests/new.py"] != owner["tests/old.py"]


@pytest.mark.parametrize("spec", ["0/3", "4/3", "1", "a/b"])
def test_bad_shard_spec_is_rejected(spec: str) -> None:
    with pytest.raises(pytest.UsageError):
        parse_shard(spec)


def test_recorded_durations_cover_test_files() -> None:
    recorded = json.loads(DURATIONS.read_text(encoding="utf-8"))
    missing = sorted(
        p.relative_to(DURATIONS.parent.parent).as_posix()
        for p in DURATIONS.parent.rglob("test_*.py")
        if p.relative_to(DURATIONS.parent.parent).as_posix() not in recorded
    )
    assert len(missing) <= len(recorded) // 10, f"tests/durations.json が古い。`python -m pytest tests -n auto --dist loadfile --store-durations` で作り直す: {missing}"
