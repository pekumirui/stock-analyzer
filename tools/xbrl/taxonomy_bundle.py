"""Scan TDnet filing ZIPs for taxonomy URLs and sync/check the local bundle.

Parse never downloads. The bundle (XBRL_CACHE_DIR, default data/xbrl_taxonomy)
must contain every http(s) file any filing references, including past years.

Typical maintainer flow on the box where R2 is mounted:

  python tools/xbrl/taxonomy_bundle.py scan \\
      --zips /home/stock/r2-share/xbrl --out /tmp/taxonomy-urls.txt

  python tools/xbrl/taxonomy_bundle.py sync \\
      --zips /home/stock/r2-share/xbrl \\
      --from-cache /home/stock/r2-share/xbrl/_cache \\
      --to data/xbrl_taxonomy

  python tools/xbrl/taxonomy_bundle.py check \\
      --zips /home/stock/r2-share/xbrl --bundle data/xbrl_taxonomy

`sync` copies only files referenced by at least one ZIP (plus their imports /
linkbases). `--all-from-cache` copies the whole cache instead (larger).
`--prune` deletes bundle files that no filing referenced.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import XBRL_CACHE_DIR, XBRL_DEST  # noqa: E402
from app.xbrlp.file_loader import FileLoader  # noqa: E402
from app.xbrlp.parser import resolve_url  # noqa: E402

XML_SUFFIXES = {".xml", ".xsd", ".htm", ".html", ".xhtml", ".ixbrl"}
SKIP_ZIP_PARTS = {"auditdoc", "audit"}
# Harvest taxonomy file URLs from messy iXBRL even when XML parse fails.
URL_RE = re.compile(
    r"https?://[^\s\"'<>]+?\.(?:xsd|xml)(?:\?[^\s\"'<>]*)?",
    re.IGNORECASE,
)
HREF_LOCAL_NAMES = {"href", "schemalocation"}


def cache_path_for(cache_dir: Path, url: str) -> Path:
    return FileLoader(cache_dir=cache_dir)._get_cache_path(url)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[-1].lower() if "}" in tag else tag.lower()


def iter_xml_refs(data: bytes, base_url: str) -> set[str]:
    """Collect resolved schemaLocation / xlink:href / http(s) .xsd|.xml URLs."""
    found: set[str] = set()
    for match in URL_RE.findall(data.decode("utf-8", errors="ignore")):
        found.add(match.split("#", 1)[0])
    try:
        root = ET.parse(io.BytesIO(data)).getroot()
    except ET.ParseError:
        return found
    for elem in root.iter():
        for key, raw in elem.attrib.items():
            local = _local_name(key)
            if local == "schemalocation" and " " in raw.strip() and not raw.strip().endswith((".xsd", ".xml")):
                parts = raw.split()
                # xsi:schemaLocation is (namespace, location) pairs.
                locations = parts[1::2] if len(parts) >= 2 else []
                for loc in locations:
                    loc = loc.split("#", 1)[0]
                    if loc:
                        found.add(resolve_url(base_url, loc))
                continue
            if local not in HREF_LOCAL_NAMES:
                continue
            href = raw.split("#", 1)[0].strip()
            if not href:
                continue
            found.add(resolve_url(base_url, href))
    return found


def _zip_xml_names(zf: zipfile.ZipFile) -> Iterable[str]:
    for name in zf.namelist():
        path = Path(name)
        if path.suffix.lower() not in XML_SUFFIXES:
            continue
        parts = {p.lower() for p in path.parts}
        if parts & SKIP_ZIP_PARTS:
            continue
        yield name


def refs_from_zip(zip_path: Path) -> set[str]:
    found: set[str] = set()
    try:
        zf = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile):
        return found
    with zf:
        for name in _zip_xml_names(zf):
            try:
                data = zf.read(name)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                continue
            found |= iter_xml_refs(data, name)
    return found


def remote_urls(urls: Iterable[str]) -> set[str]:
    return {u for u in urls if u.startswith(("http://", "https://"))}


def expand_from_cache(urls: Iterable[str], cache_dir: Path) -> tuple[set[str], set[str]]:
    """Follow imports / linkbases stored in the cache. Returns (all, missing)."""
    pending = list(remote_urls(urls))
    seen: set[str] = set()
    missing: set[str] = set()
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        path = cache_path_for(cache_dir, url)
        if not path.is_file():
            missing.add(url)
            continue
        try:
            data = path.read_bytes()
        except OSError:
            missing.add(url)
            continue
        for nxt in remote_urls(iter_xml_refs(data, url)):
            if nxt not in seen:
                pending.append(nxt)
    return seen, missing


def iter_zip_paths(root: Path) -> Iterable[Path]:
    if root.is_file() and root.suffix.lower() == ".zip":
        yield root
        return
    for path in root.rglob("*.zip"):
        rel = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
        if rel.startswith("_cache/") or "/_cache/" in f"/{rel}":
            continue
        yield path


def collect_from_zips(zips_root: Path, *, cache_dir: Path | None = None) -> tuple[set[str], set[str]]:
    refs: set[str] = set()
    for zp in iter_zip_paths(zips_root):
        refs |= remote_urls(refs_from_zip(zp))
    if cache_dir is None:
        return refs, set()
    return expand_from_cache(refs, cache_dir)


def copy_url(url: str, src_cache: Path, dest: Path) -> bool:
    src = cache_path_for(src_cache, url)
    if not src.is_file():
        return False
    dest_path = cache_path_for(dest, url)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(src.read_bytes())
    return True


def cmd_scan(args: argparse.Namespace) -> int:
    cache = Path(args.cache) if args.cache else None
    urls, missing_in_cache = collect_from_zips(Path(args.zips), cache_dir=cache)
    lines = sorted(urls)
    text = "\n".join(lines) + ("\n" if lines else "")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        print(f"wrote {len(lines)} urls -> {out}", flush=True)
    else:
        sys.stdout.write(text)
    if missing_in_cache:
        print(f"missing-in-cache {len(missing_in_cache)}", flush=True)
        for url in sorted(missing_in_cache):
            print(f"  MISSING {url}", flush=True)
        return 1
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    src = Path(args.from_cache)
    dest = Path(args.to)
    dest.mkdir(parents=True, exist_ok=True)
    if args.all_from_cache:
        n = 0
        for path in src.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(src)
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
            n += 1
        print(f"copied {n} files {src} -> {dest}", flush=True)
        return 0
    zips_root = Path(args.zips)
    urls, missing = collect_from_zips(zips_root, cache_dir=src)
    copied = 0
    skipped_missing: list[str] = []
    for url in sorted(urls):
        if url in missing:
            skipped_missing.append(url)
            continue
        if copy_url(url, src, dest):
            copied += 1
        else:
            skipped_missing.append(url)
    print(f"copied {copied} taxonomy files -> {dest}", flush=True)
    if args.prune:
        keep = {cache_path_for(dest, u).resolve() for u in urls if u not in missing}
        removed = 0
        for path in dest.rglob("*"):
            if not path.is_file() or path.name in {"README.md", ".gitkeep"}:
                continue
            if path.resolve() not in keep:
                path.unlink()
                removed += 1
        print(f"pruned {removed} unreferenced files", flush=True)
    if skipped_missing:
        print(f"missing-in-cache {len(skipped_missing)}", flush=True)
        for url in skipped_missing:
            print(f"  MISSING {url}", flush=True)
        return 1
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    bundle = Path(args.bundle)
    zips_root = Path(args.zips)
    urls, _ = collect_from_zips(zips_root, cache_dir=bundle if bundle.is_dir() else None)
    missing: list[str] = []
    present = 0
    for url in sorted(urls):
        path = cache_path_for(bundle, url)
        if path.is_file():
            present += 1
        else:
            missing.append(url)
    print(f"referenced={len(urls)} present={present} missing={len(missing)} bundle={bundle}", flush=True)
    for url in missing:
        print(f"  MISSING {url}", flush=True)
    return 1 if missing else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sc = sub.add_parser("scan", help="List http(s) taxonomy URLs referenced by filing ZIPs")
    sc.add_argument("--zips", default=XBRL_DEST, help="Filing ZIP root (or a single .zip)")
    sc.add_argument("--cache", help="If set, expand imports/linkbases from this cache")
    sc.add_argument("--out", help="Write URL list (default: stdout)")
    sc.set_defaults(func=cmd_scan)

    sy = sub.add_parser("sync", help="Copy referenced taxonomy files into the bundle")
    sy.add_argument("--zips", default=XBRL_DEST, help="Filing ZIP root (ignored with --all-from-cache)")
    sy.add_argument("--from-cache", required=True, help="Existing FileLoader cache (e.g. R2 xbrl/_cache)")
    sy.add_argument("--to", default=XBRL_CACHE_DIR, help="Bundle directory")
    sy.add_argument("--all-from-cache", action="store_true", help="Copy every file in --from-cache")
    sy.add_argument("--prune", action="store_true", help="Delete bundle files no ZIP referenced")
    sy.set_defaults(func=cmd_sync)

    ck = sub.add_parser("check", help="Report referenced taxonomy files missing from the bundle")
    ck.add_argument("--zips", default=XBRL_DEST, help="Filing ZIP root")
    ck.add_argument("--bundle", default=XBRL_CACHE_DIR, help="Bundle directory")
    ck.set_defaults(func=cmd_check)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
