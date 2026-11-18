"""MkDocs のビルド前に docs/ と AGENTS.md を docs_dir へ集める。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.schema.erd import build
from tools.wiki.collect import collect_into


def on_config(config):
    dest = Path(config["docs_dir"])
    dest.mkdir(parents=True, exist_ok=True)
    result = collect_into(REPO, dest, build)
    config["nav"] = result.nav
    return config


def on_files(files, config):
    """収集したファイルをすべてサイトに含める。テーマ由来のファイルは残す。"""
    from mkdocs.structure.files import File, Files, InclusionLevel

    docs_dir = Path(config["docs_dir"]).resolve()
    site_dir = config["site_dir"]
    use_directory_urls = config["use_directory_urls"]
    kept = []
    for item in files:
        src_dir = Path(item.src_dir).resolve() if item.src_dir else None
        if src_dir != docs_dir:
            kept.append(item)
    collected = []
    for path in docs_dir.rglob("*"):
        if not path.is_file() or path.name == ".gitkeep":
            continue
        rel = path.relative_to(docs_dir).as_posix()
        collected.append(
            File(
                rel,
                str(docs_dir),
                site_dir,
                use_directory_urls,
                inclusion=InclusionLevel.INCLUDED,
            )
        )
    return Files(collected + kept)
