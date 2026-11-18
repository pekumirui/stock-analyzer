from __future__ import annotations

import subprocess
from pathlib import Path

from tools.schema import erd


def test_build_skips_without_npx(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(erd.shutil, "which", lambda _name: None)
    assert erd.build(tmp_path) is False
    assert not (tmp_path / erd.ERD_DIR).exists()


def test_build_copies_liam_output(tmp_path: Path, monkeypatch) -> None:
    seen = []

    def run(cmd, **_kw):
        seen.append(cmd)
        out = Path(cmd[cmd.index("--output-dir") + 1])
        (out / "assets").mkdir(parents=True)
        for name in ("index.html", "schema.json", "serve.json", ".keep", "assets/app.js"):
            (out / name).write_text("x", encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(erd.shutil, "which", lambda _name: "/bin/npx")
    monkeypatch.setattr(erd.subprocess, "run", run)
    (tmp_path / erd.ERD_DIR).mkdir(parents=True)
    (tmp_path / erd.ERD_DIR / "old.js").write_text("x", encoding="utf-8")
    assert erd.build(tmp_path) is True
    got = sorted(p.relative_to(tmp_path / erd.ERD_DIR).as_posix() for p in (tmp_path / erd.ERD_DIR).rglob("*") if p.is_file())
    assert got == ["assets/app.js", "index.html", "schema.json"]
    assert seen[0][:3] == ["/bin/npx", "--yes", erd.LIAM]
    assert seen[0][seen[0].index("--format") + 1] == "tbls"


def test_build_fails_soft_when_liam_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(erd.shutil, "which", lambda _name: "/bin/npx")
    monkeypatch.setattr(erd.subprocess, "run", lambda cmd, **_kw: subprocess.CompletedProcess(cmd, 0, "WARN: Invalid schema format", ""))
    assert erd.build(tmp_path) is False
    assert not (tmp_path / erd.ERD_DIR).exists()


def test_liam_version_is_pinned() -> None:
    name, _, version = erd.LIAM.rpartition("@")
    assert name == "@liam-hq/cli"
    assert version.count(".") == 2
