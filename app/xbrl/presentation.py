"""表示リンク（*pre.xml）の区分ごとの要素の並びと、区分名から財務3表への振り分け。"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import unquote

from app.xbrl.parser import namespace_is_standard

LINK_NS = "{http://www.xbrl.org/2003/linkbase}"
XLINK_NS = "{http://www.w3.org/1999/xlink}"
XSD_ELEMENT = "{http://www.w3.org/2001/XMLSchema}element"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
PARENT_CHILD = "http://www.xbrl.org/2003/arcrole/parent-child"
CONCEPT_LABEL = "http://www.xbrl.org/2003/arcrole/concept-label"
LABEL_LINKBASE_REF = "http://www.xbrl.org/2003/role/labelLinkbaseRef"
STANDARD_LABEL = "http://www.xbrl.org/2003/role/label"
SKIPPED_DIRS = frozenset({"auditdoc", "audit"})

ROLE_PREFIX = "rol_"
NOT_STATEMENT_RE = re.compile(r"^rol_Notes|ChangesInEquity|ChangesInNetAssets|UnitholdersEquity")
STATEMENT_RES = (
    ("CF", re.compile(r"Statements?OfCashFlows")),
    ("BS", re.compile(r"BalanceSheet|StatementOfFinancialPosition")),
    ("PL", re.compile(r"StatementOfIncome|StatementOfProfitOrLoss|StatementOfComprehensiveIncome")),
)
CONSOLIDATED_RE = re.compile(r"Consolidated")
IFRS_RE = re.compile(r"IFRS")
STRUCTURE_RE = re.compile(r"(Heading|Table|Axis|Member|LineItems)$")
DATE_TAIL_RE = re.compile(r"[_-]\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class PresentationItem:
    namespace_key: str
    local_name: str
    is_custom: bool
    preferred_label: str | None = None
    depth: int | None = None
    label: str | None = None
    preferred_role: str | None = field(default=None, compare=False)

    def as_json(self) -> list:
        return [self.namespace_key, self.local_name, int(self.is_custom), self.preferred_label, self.depth, self.label]

    @property
    def is_heading(self) -> bool:
        return "Abstract" in self.local_name


@dataclass
class PresentationRole:
    role: str
    items: list[PresentationItem] = field(default_factory=list)


@dataclass(frozen=True)
class StatementRole:
    statement: str
    is_consolidated: bool
    is_ifrs: bool


def role_name(role_uri: str) -> str:
    return role_uri.rstrip("/").rsplit("/", 1)[-1]


def classify_role(name: str) -> StatementRole | None:
    if not name.startswith(ROLE_PREFIX) or NOT_STATEMENT_RE.search(name):
        return None
    for statement, pattern in STATEMENT_RES:
        if pattern.search(name):
            return StatementRole(
                statement=statement,
                is_consolidated=bool(CONSOLIDATED_RE.search(name)),
                is_ifrs=bool(IFRS_RE.search(name)),
            )
    return None


def find_presentation_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or not path.name.lower().endswith("pre.xml"):
            continue
        if {p.lower() for p in path.relative_to(root).parts} & SKIPPED_DIRS:
            continue
        out.append(path)
    return out


class _Schemas:
    """要素 id → (名前空間の接頭辞, local_name)。書類の中の xsd は name 属性で引く。"""

    def __init__(self) -> None:
        self._local: dict[Path, dict[str, str]] = {}

    def _names(self, path: Path) -> dict[str, str]:
        if path not in self._local:
            names: dict[str, str] = {}
            try:
                for el in ET.parse(path).getroot().iter(XSD_ELEMENT):
                    el_id, name = el.get("id"), el.get("name")
                    if el_id and name:
                        names[el_id] = name
            except (ET.ParseError, OSError):
                pass
            self._local[path] = names
        return self._local[path]

    def resolve(self, base: Path, href: str) -> PresentationItem | None:
        doc, sep, frag = href.partition("#")
        if not sep or not frag:
            return None
        remote = doc.startswith(("http://", "https://"))
        name = None
        if not remote:
            name = self._names((base.parent / unquote(doc)).resolve()).get(frag)
        if name and frag.endswith("_" + name):
            prefix = frag[: -len(name) - 1]
        else:
            stem = DATE_TAIL_RE.sub("", unquote(doc).rsplit("/", 1)[-1].removesuffix(".xsd"))
            if stem and frag.startswith(stem + "_"):
                prefix, name = stem, frag[len(stem) + 1:]
            elif "_" in frag:
                prefix, name = frag.rsplit("_", 1)
            else:
                return None
        return PresentationItem(
            namespace_key=prefix,
            local_name=name,
            is_custom=not (remote and namespace_is_standard(prefix, doc)),
        )


def _order(arc: ET.Element) -> float:
    try:
        return float(arc.get("order") or 0)
    except ValueError:
        return 0.0


def _walk(link: ET.Element, base: Path, schemas: _Schemas) -> list[PresentationItem]:
    concept_of: dict[str, tuple[str, str]] = {}
    items: dict[tuple[str, str], PresentationItem] = {}
    for loc in link.iter(LINK_NS + "loc"):
        label, href = loc.get(XLINK_NS + "label"), loc.get(XLINK_NS + "href")
        if not label or not href or label in concept_of:
            continue
        item = schemas.resolve(base, href)
        if item is not None:
            key = (item.namespace_key, item.local_name)
            concept_of[label] = key
            items.setdefault(key, item)
    arcs: dict[tuple, tuple[float, int]] = {}
    for n, arc in enumerate(link.iter(LINK_NS + "presentationArc")):
        if (arc.get(XLINK_NS + "arcrole") or PARENT_CHILD) != PARENT_CHILD:
            continue
        src = concept_of.get(arc.get(XLINK_NS + "from") or "")
        dst = concept_of.get(arc.get(XLINK_NS + "to") or "")
        if src is None or dst is None:
            continue
        try:
            priority = float(arc.get("priority") or 0)
        except ValueError:
            priority = 0.0
        pref = arc.get("preferredLabel")
        key = (src, dst, _order(arc), pref or None)
        held = arcs.get(key)
        if held is not None and (held[0] > priority or (held[0] == priority and held[1] < 0)):
            continue
        arcs[key] = (priority, -1 if arc.get("use") == "prohibited" else n)
    children: dict[tuple[str, str], list] = {}
    targets: set[tuple[str, str]] = set()
    for (src, dst, order, pref), (_priority, n) in arcs.items():
        if n < 0:
            continue
        children.setdefault(src, []).append((order, n, dst, pref))
        targets.add(dst)
    for kids in children.values():
        kids.sort()
    seq = {key: i for i, key in enumerate(items)}
    roots = sorted((key for key in children if key not in targets), key=seq.__getitem__)
    out: list[PresentationItem] = []

    def visit(key: tuple[str, str], pref: str | None, path: frozenset, depth: int) -> None:
        item = items[key]
        if not STRUCTURE_RE.search(item.local_name):
            out.append(PresentationItem(item.namespace_key, item.local_name, item.is_custom,
                                        role_name(pref) if pref else None, depth, preferred_role=pref))
            depth += 1
        for _, _, dst, dst_pref in children.get(key, ()):
            if dst not in path:
                visit(dst, dst_pref, path | {dst}, depth)

    for root in roots:
        visit(root, None, frozenset({root}), 0)
    return out


Labels = dict[tuple[str, str], dict[str, str]]
_REMOTE_LABELS: dict[Path, Labels] = {}


def _read_labels(path: Path, schemas: _Schemas) -> Labels:
    """ラベルリンク 1 つ → (namespace_key, local_name) → ラベルの役割（role の最後）→ 日本語のラベル。"""
    out: Labels = {}
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return out
    for link in tree.getroot().iter(LINK_NS + "labelLink"):
        concept: dict[str, tuple[str, str]] = {}
        for loc in link.iter(LINK_NS + "loc"):
            item = schemas.resolve(path, loc.get(XLINK_NS + "href") or "")
            if item is not None:
                concept[loc.get(XLINK_NS + "label") or ""] = (item.namespace_key, item.local_name)
        texts: dict[str, list[tuple[str, str]]] = {}
        for res in link.iter(LINK_NS + "label"):
            if not (res.get(XML_LANG) or "").startswith("ja"):
                continue
            role = res.get(XLINK_NS + "role") or STANDARD_LABEL
            texts.setdefault(res.get(XLINK_NS + "label") or "", []).append((role, (res.text or "").strip()))
        for arc in link.iter(LINK_NS + "labelArc"):
            if (arc.get(XLINK_NS + "arcrole") or CONCEPT_LABEL) != CONCEPT_LABEL:
                continue
            key = concept.get(arc.get(XLINK_NS + "from") or "")
            if key is None:
                continue
            slot = out.setdefault(key, {})
            for role, text in texts.get(arc.get(XLINK_NS + "to") or "", ()):
                if text:
                    slot.setdefault(role, text)
    return out


def _label_files(root: Path, cache_dir: Path | None) -> list[tuple[Path, bool]]:
    """書類の xsd が参照するラベルリンク（会社のものが先、タクソノミのものが後ろ）。英語のラベルリンクは読まない。"""
    from app.xbrl.parser import CachedTimeoutFileLoader

    loader = CachedTimeoutFileLoader(cache_dir=cache_dir) if cache_dir is not None else None
    local: list[tuple[Path, bool]] = []
    remote: list[tuple[Path, bool]] = []
    for xsd in sorted(root.rglob("*.xsd")):
        if {p.lower() for p in xsd.relative_to(root).parts} & SKIPPED_DIRS:
            continue
        try:
            refs = list(ET.parse(xsd).getroot().iter(LINK_NS + "linkbaseRef"))
        except (ET.ParseError, OSError):
            continue
        for ref in refs:
            href = ref.get(XLINK_NS + "href") or ""
            if ref.get(XLINK_NS + "role") != LABEL_LINKBASE_REF or href.endswith("-en.xml"):
                continue
            if href.startswith(("http://", "https://")):
                if loader is not None:
                    remote.append((loader._get_cache_path(href), True))
            else:
                local.append(((xsd.parent / unquote(href)).resolve(), False))
    seen: set[Path] = set()
    return [(p, r) for p, r in local + remote if not (p in seen or seen.add(p))]


def _labels(root: Path, cache_dir: Path | None, schemas: _Schemas) -> list[Labels]:
    out = []
    for path, remote in _label_files(root, cache_dir):
        if remote:
            if path not in _REMOTE_LABELS:
                _REMOTE_LABELS[path] = _read_labels(path, _Schemas()) if path.is_file() else {}
            out.append(_REMOTE_LABELS[path])
        elif path.is_file():
            out.append(_read_labels(path, schemas))
    return out


def _heading_label(item: PresentationItem, found: list[Labels]) -> str | None:
    roles = [item.preferred_role] if item.preferred_role else []
    for role in [*roles, STANDARD_LABEL]:
        for labels in found:
            text = labels.get((item.namespace_key, item.local_name), {}).get(role)
            if text:
                return text
    return None


def read_presentation(root: Path, cache_dir: Path | None = None) -> list[PresentationRole]:
    """区分は出てきた順、要素は親子と order で辿った順と深さ。AuditDoc と読めないファイルは飛ばす。

    見出しの要素（名前に Abstract を含む）には表示名を付ける。preferredLabel の役割のラベル、無ければ標準のラベル。
    会社のラベルリンク（書類の中）を先に、タクソノミのラベルリンク（cache_dir の下）を後に見る。
    """
    from app.config import XBRL_CACHE_DIR

    schemas = _Schemas()
    roles: dict[str, PresentationRole] = {}
    for path in find_presentation_files(root):
        try:
            tree = ET.parse(path)
        except (ET.ParseError, OSError):
            continue
        for link in tree.getroot().iter(LINK_NS + "presentationLink"):
            uri = link.get(XLINK_NS + "role")
            if not uri:
                continue
            name = role_name(uri)
            items = _walk(link, path, schemas)
            roles.setdefault(name, PresentationRole(role=name)).items.extend(items)
    if any(item.is_heading for role in roles.values() if classify_role(role.role) for item in role.items):
        found = _labels(root, Path(cache_dir or XBRL_CACHE_DIR), schemas)
        for role in roles.values():
            if classify_role(role.role):
                role.items = [replace(i, label=_heading_label(i, found)) if i.is_heading else i for i in role.items]
    return list(roles.values())


def statement_roles(roles: list[PresentationRole]) -> list[tuple[PresentationRole, StatementRole]]:
    """財務3表に当たる区分だけ（出てきた順）。"""
    out = []
    for role in roles:
        kind = classify_role(role.role)
        if kind is not None and role.items:
            out.append((role, kind))
    return out
