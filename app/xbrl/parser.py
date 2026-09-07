"""ZIP → facts/contexts/labels using vendored xbrlp plus context walk."""
from __future__ import annotations

import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.xbrl.context import (
    ParsedContext,
    assign_relative_periods,
    classify_title,
    parse_contexts_from_xml,
)
from app.xbrlp import Fact, FileLoader, Label, Parser


STANDARD_NS_PREFIXES = (
    "jppfs",
    "jpcrp",
    "jpdei",
    "jpigp",
    "tse-ed",
    "tse-t-ed",
    "jpctl",
    "jplvh",
)


def namespace_is_standard(key: str | None, uri: str | None = None) -> bool:
    blob = f"{key or ''} {uri or ''}".lower()
    return any(p in blob for p in STANDARD_NS_PREFIXES)


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


def looks_like_ixbrl(path: Path) -> bool:
    if path.suffix.lower() not in {".htm", ".html", ".xhtml", ".ixbrl", ".xml"}:
        return False
    try:
        sample = path.read_text(encoding="utf-8", errors="ignore")[:80_000]
    except OSError:
        return False
    return "ix:nonFraction" in sample or "ix:nonNumeric" in sample or "inlineXBRL" in sample


def find_manifest(root: Path) -> Path | None:
    hits = list(root.rglob("*manifest*.xml")) + list(root.rglob("*Manifest*.xml"))
    return hits[0] if hits else None


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
) -> ParsedPackage:
    files = find_ixbrl_files(unpacked)
    manifest = find_manifest(unpacked)
    parser = Parser(file_loader=FileLoader(cache_dir=cache_dir))
    if manifest is not None:
        try:
            parser.prepare_ixbrl(manifest)
        except (FileNotFoundError, ValueError):
            parser.ixbrl_files = files
    else:
        parser.ixbrl_files = files
    if not parser.ixbrl_files:
        raise ValueError("no-ixbrl")

    labels: dict[tuple[str, str], dict[str, str]] = {}
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

    try:
        for lab in parser.load_labels():
            ns = lab.qname.prefix or ""
            local = lab.qname.local_name
            slot = labels.setdefault((ns, local), {})
            if lab.lang.startswith("ja") and "label_ja" not in slot:
                slot["label_ja"] = lab.text
            elif lab.lang.startswith("en") and "label_en" not in slot:
                slot["label_en"] = lab.text
    except Exception:
        pass

    contexts: list[ParsedContext] = []
    seen_ctx: set[str] = set()
    for path in parser.ixbrl_files:
        for ctx in parse_contexts_from_xml(path):
            if ctx.context_ref in seen_ctx:
                continue
            seen_ctx.add(ctx.context_ref)
            contexts.append(ctx)
    assign_relative_periods(contexts, fiscal_year_end)
    return ParsedPackage(
        contexts=contexts,
        facts=facts,
        labels=labels,
        ixbrl_files=list(parser.ixbrl_files),
    )


def package_meta(title: str) -> dict:
    return classify_title(title)
