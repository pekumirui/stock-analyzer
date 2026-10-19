"""Scan TDnet filing ZIPs for taxonomy URLs and sync/check the local bundle.

Parse never downloads. The bundle (XBRL_CACHE_DIR, default data/xbrl_taxonomy)
must contain every http(s) file the parser actually reads, including past years.

`check` / `sync` count the same closure as parse: schemaRef → xsd:import, plus
the label linkbases `load_labels` fetches. Generic labels, definition linkbases,
and comment-only URLs are ignored.

Typical maintainer flow on the box where R2 is mounted:

  python tools/xbrl/taxonomy_bundle.py scan \\
      --zips /home/stock/r2-share/xbrl --out /tmp/taxonomy-urls.txt

  python tools/xbrl/taxonomy_bundle.py sync \\
      --zips /home/stock/r2-share/xbrl \\
      --from-cache /home/stock/r2-share/xbrl/_cache \\
      --to data/xbrl_taxonomy

  python tools/xbrl/taxonomy_bundle.py check \\
      --zips /home/stock/r2-share/xbrl --bundle data/xbrl_taxonomy

`sync` copies only files the parser would read. `--all-from-cache` copies the
whole cache instead (larger). `--prune` deletes bundle files the parser would
not read.
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import XBRL_CACHE_DIR, XBRL_DEST  # noqa: E402
from app.xbrl.parser import (  # noqa: E402
    RecordingFileLoader,
    collect_parser_taxonomy_urls,
    extract_zip,
)
from app.xbrlp.file_loader import FileLoader  # noqa: E402


def cache_path_for(cache_dir: Path, url: str) -> Path:
    return FileLoader(cache_dir=cache_dir)._get_cache_path(url)


def remote_urls(urls: Iterable[str]) -> set[str]:
    return {u for u in urls if u.startswith(("http://", "https://"))}


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
    """Parser-needed remote taxonomy URLs across filing ZIPs.

    When cache_dir is set, follow xsd:import and label linkbases stored there.
    Missing is empty when cache_dir is None (scan without expansion).
    """
    urls: set[str] = set()
    missing: set[str] = set()
    shared_xsd: dict = {}
    loader = RecordingFileLoader(cache_dir=cache_dir, ignore_failure=True) if cache_dir is not None else None
    for zp in iter_zip_paths(zips_root):
        try:
            with TemporaryDirectory(prefix="xbrl-bundle-") as tmp:
                unpacked = extract_zip(zp, Path(tmp) / "unpacked")
                found, miss = collect_parser_taxonomy_urls(
                    unpacked,
                    cache_dir=cache_dir,
                    shared_xsd_cache=shared_xsd,
                    file_loader=loader,
                )
        except (OSError, zipfile.BadZipFile, RuntimeError):
            continue
        urls |= found
        missing |= miss
    if cache_dir is None:
        return urls, set()
    return urls, missing


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

    sc = sub.add_parser("scan", help="List http(s) taxonomy URLs the parser would fetch")
    sc.add_argument("--zips", default=XBRL_DEST, help="Filing ZIP root (or a single .zip)")
    sc.add_argument("--cache", help="If set, expand imports/label linkbases from this cache")
    sc.add_argument("--out", help="Write URL list (default: stdout)")
    sc.set_defaults(func=cmd_scan)

    sy = sub.add_parser("sync", help="Copy parser-needed taxonomy files into the bundle")
    sy.add_argument("--zips", default=XBRL_DEST, help="Filing ZIP root (ignored with --all-from-cache)")
    sy.add_argument("--from-cache", required=True, help="Existing FileLoader cache (e.g. R2 xbrl/_cache)")
    sy.add_argument("--to", default=XBRL_CACHE_DIR, help="Bundle directory")
    sy.add_argument("--all-from-cache", action="store_true", help="Copy every file in --from-cache")
    sy.add_argument("--prune", action="store_true", help="Delete bundle files the parser would not read")
    sy.set_defaults(func=cmd_sync)

    ck = sub.add_parser("check", help="Report parser-needed taxonomy files missing from the bundle")
    ck.add_argument("--zips", default=XBRL_DEST, help="Filing ZIP root")
    ck.add_argument("--bundle", default=XBRL_CACHE_DIR, help="Bundle directory")
    ck.set_defaults(func=cmd_check)

    args = ap.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
