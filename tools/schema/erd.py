from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TBLS = "docs/schema/tbls.json"
ERD_DIR = "schema/erd"
LIAM = "@liam-hq/cli@0.7.24"
log = logging.getLogger("mkdocs.hooks.erd")


def command(npx: str, src: Path, out: Path) -> list[str]:
    return [npx, "--yes", LIAM, "erd", "build", "--format", "tbls", "--input", str(src), "--output-dir", str(out)]


def build(dest: Path, src: Path = ROOT / TBLS) -> bool:
    npx = shutil.which("npx")
    if npx is None:
        log.warning("npx が無いので Liam の ER 図（%s）を作らない。Node.js を入れる", ERD_DIR)
        return False
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "erd"
        run = subprocess.run(command(npx, src, out), cwd=tmp, capture_output=True, text=True, check=False)
        if run.returncode != 0 or not (out / "schema.json").is_file():
            log.warning("Liam の ER 図を作れなかった: %s", (run.stdout + run.stderr).strip())
            return False
        target = dest / ERD_DIR
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(out, target, ignore=shutil.ignore_patterns(".keep", "serve.json"))
    return True
