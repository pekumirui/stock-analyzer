from __future__ import annotations

import json
import posixpath
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from functools import lru_cache
from pathlib import Path

from app.config import XBRL_ZIP_MANIFEST_ONLY_PATH

XBRL_SUFFIXES = ("_ixbrl.htm", "_ixbrl.xhtml", ".xsd", ".xml", ".xbrl")


@lru_cache(maxsize=1)
def manifest_only_doc_ids() -> frozenset[str]:
    raw = json.loads(XBRL_ZIP_MANIFEST_ONLY_PATH.read_text(encoding="utf-8"))
    return frozenset(str(item["doc_id"]) for item in raw.get("filings") or [])


def _is_manifest(name: str) -> bool:
    base = posixpath.basename(name).lower()
    return "manifest" in base and base.endswith(".xml")


def _instance_stems(raw: bytes) -> set[str]:
    stems = set()
    for el in ET.fromstring(raw).iter():
        if isinstance(el.tag, str) and el.tag.rsplit("}", 1)[-1] == "instance":
            name = (el.get("preferredFilename") or "").strip()
            if name:
                stems.add(name.rsplit(".", 1)[0])
    return stems


def foreign_names(names: Iterable[str], read: Callable[[str], bytes]) -> set[str]:
    names = list(names)
    stems_by_dir: dict[str, set[str]] = {}
    for name in names:
        if not _is_manifest(name):
            continue
        try:
            stems = _instance_stems(read(name))
        except ET.ParseError:
            continue
        if stems:
            stems_by_dir.setdefault(posixpath.dirname(name), set()).update(stems)
    out = set()
    for name in names:
        stems = stems_by_dir.get(posixpath.dirname(name))
        if not stems or _is_manifest(name) or not name.lower().endswith(XBRL_SUFFIXES):
            continue
        if not any(stem in posixpath.basename(name) for stem in stems):
            out.add(name)
    return out


def kept_names(doc_id: str | None, names: Iterable[str], read: Callable[[str], bytes]) -> list[str]:
    names = list(names)
    if doc_id not in manifest_only_doc_ids():
        return names
    dropped = foreign_names(names, read)
    return [name for name in names if name not in dropped]


def drop_foreign_files(root: Path, doc_id: str | None) -> list[Path]:
    if doc_id not in manifest_only_doc_ids():
        return []
    files = {p.relative_to(root).as_posix(): p for p in root.rglob("*") if p.is_file()}
    dropped = sorted(foreign_names(files, lambda name: files[name].read_bytes()))
    for name in dropped:
        files[name].unlink()
    return [files[name] for name in dropped]
