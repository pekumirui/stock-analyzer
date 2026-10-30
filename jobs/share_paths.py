"""共有ファイルの置き場。FUSE の `/home/stock/r2-share` とは別ルート。

バケットと同じ接頭辞を `{repo}/data` の下に置く。`SHARE_ROOT` があるときだけルートを差し替える。
FUSE 判定（`path_on_r2_share` / `check_r2_mount`）はこちらを見ない。
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARE_ROOT_ENV = "SHARE_ROOT"
PREFIXES = {
    "monthly": "monthly",
    "xbrl": "xbrl",
    "inventory": "inventory",
    "stats": "stats",
    "intraday": "intraday",
    "edinet": "edinet",
}


def share_root(
    *,
    root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> Path:
    if root is not None:
        return Path(root)
    env = os.environ if environ is None else environ
    raw = (env.get(SHARE_ROOT_ENV) or "").strip()
    if raw:
        return Path(raw)
    base = REPO_ROOT if repo_root is None else Path(repo_root)
    return base / "data"


def share_path(
    kind: str,
    *,
    root: Path | str | None = None,
    environ: Mapping[str, str] | None = None,
    repo_root: Path | None = None,
) -> Path:
    try:
        prefix = PREFIXES[kind]
    except KeyError:
        raise ValueError(f"unknown share kind: {kind}") from None
    return share_root(root=root, environ=environ, repo_root=repo_root) / prefix
