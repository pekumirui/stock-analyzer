"""ZIP → facts/contexts/labels using vendored xbrlp plus context walk."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from collections.abc import Iterable
from pathlib import Path

from app.xbrl.context import (
    ParsedContext,
    assign_relative_periods,
    classify_title,
    parse_contexts_from_xml,
    resolve_fiscal_year_end,
)
from app.xbrlp import Fact, FileLoader, Label, Parser
from app.xbrlp.parser import LabelResourceParser, resolve_url


# 標準タクソノミの名前空間 URI はこの配下（KAB-81）。会社独自は
# EDINET が disclosure.edinet-fsa.go.jp/jpcrp030000/asr/…、TDnet が www.xbrl.tdnet.info/jp/tse/… で外れる
STANDARD_NS_URI_ROOTS = (
    "disclosure.edinet-fsa.go.jp/taxonomy/",
    "www.xbrl.tdnet.info/taxonomy/",
)
# URI が無いときだけ使う標準の namespace_key（完全一致。jpcrp030000-asr_E… は外れる）
STANDARD_NS_KEYS = frozenset({
    "jppfs_cor",
    "jpcrp_cor",
    "jpdei_cor",
    "jpigp_cor",
    "jpctl_cor",
    "jplvh_cor",
    "jpsps_cor",
    "tse-ed-t",
    "tse-t-ed",
})


def namespace_is_standard(key: str | None, uri: str | None = None) -> bool:
    if uri:
        tail = uri.lower().split("://", 1)[-1]
        return tail.startswith(STANDARD_NS_URI_ROOTS)
    return (key or "") in STANDARD_NS_KEYS


def namespace_key_of(fact: Fact) -> str:
    if fact.qname.prefix:
        return fact.qname.prefix
    uri = fact.qname.namespace_uri or ""
    if uri:
        tail = uri.rstrip("/").rsplit("/", 1)[-1]
        if tail:
            return tail
    return "custom"


@dataclass
class ParsedFact:
    namespace_uri: str
    namespace_key: str
    local_name: str
    context_ref: str
    unit: str | None
    scale: int | None
    value_num: Decimal | None
    value_text: str | None
    is_nil: bool
    is_numeric: bool


@dataclass
class ParsedPackage:
    contexts: list[ParsedContext]
    facts: list[ParsedFact]
    labels: dict[tuple[str, str], dict[str, str]]
    ixbrl_files: list[Path]
    fiscal_year_end: date | None = None


def looks_like_ixbrl(path: Path) -> bool:
    if path.suffix.lower() not in {".htm", ".html", ".xhtml", ".ixbrl", ".xml"}:
        return False
    try:
        sample = path.read_text(encoding="utf-8", errors="ignore")[:80_000]
    except OSError:
        return False
    return "ix:nonFraction" in sample or "ix:nonNumeric" in sample or "inlineXBRL" in sample


class TaxonomyMissingError(FileNotFoundError):
    """Referenced taxonomy URL is not in the local bundle. Never downloads."""

    def __init__(self, url: str, cache_path: Path | None = None):
        self.url = url
        self.cache_path = cache_path
        extra = f" (looked in {cache_path})" if cache_path is not None else ""
        super().__init__(f"taxonomy not in bundle: {url}{extra}")

    def __reduce__(self):
        # Rebuild from url/path so the message survives the trip back from a parse worker.
        return (type(self), (self.url, self.cache_path))


class CachedTimeoutFileLoader(FileLoader):
    """Local taxonomy bundle only. The timeout kwarg is ignored (KAB-60, no download)."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        timeout: float | None = None,
        ignore_failure: bool = False,
    ):
        super().__init__(cache_dir=cache_dir, ignore_failure=ignore_failure)
        self.timeout = timeout

    def _fetch_url(self, url: str) -> Path:
        cache_path = self._get_cache_path(url)
        if cache_path.is_file():
            return cache_path
        raise TaxonomyMissingError(url, cache_path)

    def _download_file(self, url: str, dest_path: Path) -> None:
        raise TaxonomyMissingError(url, dest_path)


class RecordingFileLoader(CachedTimeoutFileLoader):
    """Bundle loader that records every remote URL the parser tries to fetch."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        *,
        timeout: float | None = None,
        ignore_failure: bool = True,
        fetched: set[str] | None = None,
        missing: set[str] | None = None,
    ):
        super().__init__(cache_dir=cache_dir, timeout=timeout, ignore_failure=ignore_failure)
        self.fetched: set[str] = fetched if fetched is not None else set()
        self.missing: set[str] = missing if missing is not None else set()
        self.scanned_label_urls: set[str] = set()

    def _fetch_url(self, url: str) -> Path:
        cache_path = self._get_cache_path(url)
        if cache_path.is_file():
            self.fetched.add(url)
            return cache_path
        self.missing.add(url)
        raise TaxonomyMissingError(url, cache_path)


def _remote_taxonomy_url(url: str) -> str | None:
    text = str(url).split("#", 1)[0].strip()
    if text.startswith(("http://", "https://")):
        return text
    return None


def _scan_label_linkbase_urls(label_url: str, loader: RecordingFileLoader, parser: Parser) -> None:
    """Record remote files load_labels() would fetch from one labelLinkbaseRef."""
    if label_url in loader.scanned_label_urls:
        return
    loader.scanned_label_urls.add(label_url)
    label_path = loader.fetch(label_url)
    if label_path is None:
        return
    loc_parser = LabelResourceParser(file_loader=loader, base_url=label_url)
    loc_tag = None
    href_attr = None
    type_attr = None
    role_attr = None
    try:
        for event, elem in ET.iterparse(str(label_path), events=["start-ns", "end"]):
            loc_parser.on_xml_event(event, elem)
            if event == "start-ns":
                prefix, uri = elem
                if prefix == "link":
                    loc_tag = f"{{{uri}}}loc"
                elif prefix == "xlink":
                    href_attr = f"{{{uri}}}href"
                    type_attr = f"{{{uri}}}type"
                    role_attr = f"{{{uri}}}role"
                continue
            if event != "end" or loc_tag is None or elem.tag != loc_tag:
                continue
            if (elem.get(type_attr) if type_attr else None) != "locator":
                continue
            # Locators with xlink:role are remote resources (LabelResourceParser).
            if role_attr and elem.get(role_attr):
                continue
            loc_href = elem.get(href_attr) if href_attr else None
            if not loc_href:
                continue
            schema_url = _remote_taxonomy_url(resolve_url(label_url, loc_href.split("#", 1)[0]))
            if schema_url is None:
                continue
            parser._get_or_parse_xsd_schema(schema_url)
    except ET.ParseError:
        return


def collect_parser_taxonomy_urls(
    unpacked: Path,
    *,
    cache_dir: Path | None,
    shared_xsd_cache: dict | None = None,
    file_loader: RecordingFileLoader | None = None,
) -> tuple[set[str], set[str]]:
    """Remote URLs parse_package() would fetch from this unpacked filing.

    Closure is schemaRef → xsd:import (recursive) plus load_labels() label
    linkbases. Presentation / definition / generic-label linkbases are omitted.
    """
    if file_loader is None:
        cache = cache_dir if cache_dir is not None else unpacked / ".taxonomy-bundle-empty"
        file_loader = RecordingFileLoader(cache_dir=cache, ignore_failure=True)
    parser = Parser(
        file_loader=file_loader,
        shared_xsd_cache=shared_xsd_cache if shared_xsd_cache is not None else {},
    )
    discovered = find_ixbrl_files(unpacked)
    manifest = find_manifest(unpacked)
    manifest_files: list[Path] = []
    if manifest is not None:
        try:
            parser.prepare_ixbrl(manifest)
            manifest_files = list(parser.ixbrl_files)
        except (FileNotFoundError, ValueError):
            manifest_files = []
    parser.ixbrl_files = union_ixbrl_files(discovered, manifest_files)
    if not parser.ixbrl_files:
        needed = set(file_loader.fetched) | set(file_loader.missing)
        return needed, set(file_loader.missing) if cache_dir is not None else set()

    try:
        parser.find_xsd_files()
    except ValueError:
        needed = set(file_loader.fetched) | set(file_loader.missing)
        return needed, set(file_loader.missing) if cache_dir is not None else set()

    parser._ensure_linkbase_refs()
    needed: set[str] = set()
    for url in parser._collect_all_schemas_with_imports([str(path) for path in parser.root_xsd_files]):
        remote = _remote_taxonomy_url(url)
        if remote:
            needed.add(remote)
    needed |= file_loader.fetched | file_loader.missing

    for href, _ in parser.linkbase_refs.get("labelLinkbaseRef", []):
        label_url = _remote_taxonomy_url(str(href)) or str(href).split("#", 1)[0]
        remote = _remote_taxonomy_url(label_url)
        if remote:
            needed.add(remote)
        _scan_label_linkbase_urls(label_url, file_loader, parser)

    needed |= file_loader.fetched | file_loader.missing
    for url in parser.xsd_schemas_by_url:
        remote = _remote_taxonomy_url(url)
        if remote:
            needed.add(remote)
    if cache_dir is None:
        return needed, set()
    missing = {url for url in needed if not file_loader._get_cache_path(url).is_file()}
    return needed, missing


# Process-level caches reused across parse_package() calls (one Parser per filing).
_SHARED_XSD_CACHE: dict = {}
LabelSlots = dict[tuple[str, str], dict[str, str]]
_SHARED_LABEL_CACHE: dict[str, LabelSlots] = {}


def reset_taxonomy_caches() -> None:
    """Drop in-process XSD / label-linkbase caches (tests)."""
    _SHARED_XSD_CACHE.clear()
    _SHARED_LABEL_CACHE.clear()


def _label_slots(labels: Iterable[Label]) -> LabelSlots:
    """(prefix, local) → first ja / first en label, in linkbase order."""
    slots: LabelSlots = {}
    for lab in labels:
        slot = slots.setdefault((lab.qname.prefix or "", lab.qname.local_name), {})
        if lab.lang.startswith("ja"):
            slot.setdefault("label_ja", lab.text)
        elif lab.lang.startswith("en"):
            slot.setdefault("label_en", lab.text)
    return slots


def _collect_labels(parser: Parser, wanted: set[tuple[str, str]]) -> LabelSlots:
    """Labels for ``wanted`` keys only. Remote label linkbases are reduced once per process.

    Same result as walking every label of every linkbase with first-wins per slot.
    A non-missing-taxonomy error keeps the labels found before it.
    """
    labels: LabelSlots = {}
    saved = None
    try:
        parser._ensure_linkbase_refs()
        saved = parser.linkbase_refs
        for ref in list(saved.get("labelLinkbaseRef", [])):
            href = ref[0]
            remote = str(href).startswith(("http://", "https://"))
            slots = _SHARED_LABEL_CACHE.get(href) if remote else None
            if slots is None:
                parser.linkbase_refs = {"labelLinkbaseRef": [ref]}
                slots = _label_slots(parser.load_labels())
                if remote:
                    _SHARED_LABEL_CACHE[href] = slots
            for key in wanted:
                src = slots.get(key)
                if src is None:
                    continue
                slot = labels.setdefault(key, {})
                for name, text in src.items():
                    slot.setdefault(name, text)
    except (TaxonomyMissingError, FileNotFoundError):
        raise
    except Exception:
        pass
    finally:
        if saved is not None:
            parser.linkbase_refs = saved
    return labels


def find_manifest(root: Path) -> Path | None:
    hits = list(root.rglob("*manifest*.xml")) + list(root.rglob("*Manifest*.xml"))
    return hits[0] if hits else None


def union_ixbrl_files(*groups: list[Path]) -> list[Path]:
    """Preserve first-seen order (Summary first from find_ixbrl_files)."""
    seen: set[Path] = set()
    out: list[Path] = []
    for group in groups:
        for path in group:
            key = path.resolve()
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out


def find_ixbrl_files(root: Path) -> list[Path]:
    skipped_parts = {"auditdoc", "audit"}
    found: list[Path] = []
    summary_first: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        parts = {p.lower() for p in path.parts}
        if parts & skipped_parts:
            continue
        if not looks_like_ixbrl(path):
            continue
        if "summary" in parts:
            summary_first.append(path)
        else:
            found.append(path)
    return summary_first + found


def extract_zip(zip_path: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dest)
    return dest


def _scale_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _fact_to_row(fact: Fact, text_max: int) -> ParsedFact | None:
    key = namespace_key_of(fact)
    uri = fact.qname.namespace_uri or f"urn:unknown/{key}"
    local = fact.qname.local_name
    unit = fact.unit_ref
    if fact.is_nil:
        return ParsedFact(
            namespace_uri=uri,
            namespace_key=key,
            local_name=local,
            context_ref=fact.context_ref,
            unit=unit,
            scale=_scale_int(fact.scale),
            value_num=None,
            value_text=None,
            is_nil=True,
            is_numeric=fact.is_numeric,
        )
    value = fact.value
    if fact.is_numeric:
        if not isinstance(value, Decimal):
            return None
        return ParsedFact(
            namespace_uri=uri,
            namespace_key=key,
            local_name=local,
            context_ref=fact.context_ref,
            unit=unit,
            scale=_scale_int(fact.scale),
            value_num=value,
            value_text=None,
            is_nil=False,
            is_numeric=True,
        )
    text = "" if value is None else str(value)
    if len(text) > text_max:
        return None
    return ParsedFact(
        namespace_uri=uri,
        namespace_key=key,
        local_name=local,
        context_ref=fact.context_ref,
        unit=unit,
        scale=None,
        value_num=None,
        value_text=text or None,
        is_nil=False,
        is_numeric=False,
    )


def parse_package(
    unpacked: Path,
    *,
    cache_dir: Path,
    text_max: int,
    fiscal_year_end: date | None = None,
    title: str | None = None,
    exclude_dates: set[date] | None = None,
) -> ParsedPackage:
    discovered = find_ixbrl_files(unpacked)
    manifest = find_manifest(unpacked)
    parser = Parser(
        file_loader=CachedTimeoutFileLoader(cache_dir=cache_dir, ignore_failure=False),
        shared_xsd_cache=_SHARED_XSD_CACHE,
    )
    manifest_files: list[Path] = []
    if manifest is not None:
        try:
            parser.prepare_ixbrl(manifest)
            manifest_files = list(parser.ixbrl_files)
        except (FileNotFoundError, ValueError):
            manifest_files = []
    # Attachment/manifest.xml は本表だけ列挙する。短信サマリー（tse-ed-t）は必ず足す。
    parser.ixbrl_files = union_ixbrl_files(discovered, manifest_files)
    if not parser.ixbrl_files:
        raise ValueError("no-ixbrl")

    facts: list[ParsedFact] = []
    seen_facts: set[tuple[str, str, str, str]] = set()
    for fact in parser.load_facts():
        row = _fact_to_row(fact, text_max)
        if row is None:
            continue
        key = (row.namespace_uri, row.local_name, row.context_ref, row.unit or "")
        if key in seen_facts:
            continue
        seen_facts.add(key)
        facts.append(row)

    contexts: list[ParsedContext] = []
    seen_ctx: set[str] = set()
    for path in parser.ixbrl_files:
        for ctx in parse_contexts_from_xml(path):
            if ctx.context_ref in seen_ctx:
                continue
            seen_ctx.add(ctx.context_ref)
            contexts.append(ctx)

    wanted_labels = {(f.namespace_key, f.local_name) for f in facts}
    # セグメント等の次元メンバー。会社独自のメンバーは名前をここでしか取れない（KAB-77）
    wanted_labels |= {tuple(m.split(":", 1)) for c in contexts for m in c.dimensions.values() if ":" in m}
    labels = _collect_labels(parser, wanted_labels)
    fy_end = fiscal_year_end
    if fy_end is None:
        fy_end = resolve_fiscal_year_end(
            contexts,
            facts,
            title=title,
            exclude_dates=exclude_dates,
        )
    assign_relative_periods(contexts, fy_end)
    return ParsedPackage(
        contexts=contexts,
        facts=facts,
        labels=labels,
        ixbrl_files=list(parser.ixbrl_files),
        fiscal_year_end=fy_end,
    )


def package_meta(title: str) -> dict:
    return classify_title(title)
