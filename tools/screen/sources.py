from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

JS_DIR = "static/js"
SCREENS_JS = f"{JS_DIR}/screens.js"
TABS_JS = f"{JS_DIR}/tabs.js"
_JSON_CONST = r"const {name} = JSON\.parse\(String\.raw`(.*?)`\);"
_TABS = re.compile(r"const TABS = (\[.*?\]);")
_API_TEXT = re.compile(r"""["'`](/api/[^"'`\s]*)""")
_METHOD = re.compile(r"""method:\s*["'](\w+)["']""")
_SLOT = "\x00"
SHARED = "common"


@dataclass(frozen=True)
class Screen:
    id: str
    tab: str
    label: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Call:
    file: str
    line: int
    method: str
    url: str


def _const(text: str, name: str):
    found = re.search(_JSON_CONST.format(name=name), text, re.S)
    if not found:
        raise ValueError(f"{SCREENS_JS} に const {name} = JSON.parse(String.raw`…`) が無い")
    return json.loads(found.group(1))


def tabs(root: Path) -> list[str]:
    found = _TABS.search((root / TABS_JS).read_text(encoding="utf-8"))
    if not found:
        raise ValueError(f"{TABS_JS} に const TABS = […] が無い")
    return json.loads(found.group(1))


def screens(root: Path) -> list[Screen]:
    text = (root / SCREENS_JS).read_text(encoding="utf-8")
    labels = _const(text, "SCREEN_LABELS")
    paths: dict[str, list[str]] = {}
    tab_of: dict[str, str] = {}
    for route in _const(text, "ROUTES"):
        paths.setdefault(route["id"], []).append(route["path"])
        tab_of[route["id"]] = route["tab"]
    if set(paths) != set(labels):
        raise ValueError(f"{SCREENS_JS}: ROUTES と SCREEN_LABELS の画面 id が合わない: {sorted(set(paths) ^ set(labels))}")
    return [Screen(sid, tab_of[sid], labels[sid], tuple(paths[sid])) for sid in paths]


def sub_tabs(all_screens: list[Screen]) -> dict[str, str]:
    return {part: s.tab for s in all_screens for part in s.id.split("-")[1:]}


def file_tab(name: str, tab_names: list[str], subs: dict[str, str]) -> str:
    head = Path(name).stem.split("-")[0]
    return head if head in tab_names else subs.get(head, SHARED)


def calls(root: Path) -> list[Call]:
    out = []
    for path in sorted((root / JS_DIR).glob("*.js")):
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for raw in _API_TEXT.findall(line):
                url = re.sub(r"\$\{[^}]*\}?", _SLOT, raw.split("?", 1)[0])
                method = _METHOD.search(line)
                out.append(Call(path.name, no, method.group(1).upper() if method else "GET", url))
    return out


def match(call: Call, routes: list[str]) -> str | None:
    best: tuple[int, str] | None = None
    a = call.url.rstrip("/").split("/")
    for label in routes:
        method, template = label.split(" ", 1)
        b = template.rstrip("/").split("/")
        if method != call.method or len(a) != len(b):
            continue
        if not all(x == y or (y.startswith("{") and y.endswith("}")) for x, y in zip(a, b)):
            continue
        score = sum(x == y for x, y in zip(a, b))
        if best is None or score > best[0]:
            best = (score, label)
    return best[1] if best else None


NAV_TARGETS = {
    "gotoChart": "stock-price",
    "gotoHolder": "holders-detail",
    "gotoHolderFromStock": "holders-detail",
    "openHolder": "holders-detail",
    "showMonthlyDetail": "monthly-detail",
    "openStockNewsDeck": "stock-deck-detail",
}
_NAV_CALL = re.compile(r"(?<![\w.])(" + "|".join(NAV_TARGETS) + r")\(")
_HREF = re.compile(r"""href=["'](#[a-z]+[^"'\s]*|/monthly/[^"'\s]+)["']""")


@dataclass(frozen=True)
class Link:
    file: str
    line: int
    source: str
    target: str


def _body_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for found in re.finditer(r"function (" + "|".join(NAV_TARGETS) + r")\(", text):
        start = text.index("{", found.end())
        depth = 0
        for i in range(start, len(text)):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                spans.append((found.start(), i))
                break
    return spans


def file_screen(name: str, all_screens: list[Screen], tab_names: list[str]) -> str | None:
    ids = {s.id for s in all_screens}
    stem = Path(name).stem
    if stem in ids:
        return stem
    tab = file_tab(name, tab_names, sub_tabs(all_screens))
    if f"{tab}-{stem}" in ids:
        return f"{tab}-{stem}"
    return tab if tab in ids else None


def _href_screen(href: str, all_screens: list[Screen]) -> str | None:
    if href.startswith("/monthly/"):
        return "monthly-detail"
    tab, _, rest = re.sub(r"\$\{[^}]*\}", "x", href[1:].split("?", 1)[0]).partition("/")
    parts = rest.split("/") if rest else []
    best = None
    for s in all_screens:
        for path in s.paths if s.tab == tab else ():
            pattern = path.rstrip("?").split("/") if path.rstrip("?") else []
            if len(pattern) == len(parts) and all(p.startswith(":") or p == r for p, r in zip(pattern, parts)):
                score = sum(not p.startswith(":") for p in pattern)
                if best is None or score > best[0]:
                    best = (score, s.id)
    return best[1] if best else None


def links(root: Path, all_screens: list[Screen], tab_names: list[str]) -> list[Link]:
    out = []
    for path in sorted((root / JS_DIR).glob("*.js")):
        source = file_screen(path.name, all_screens, tab_names)
        if source is None:
            continue
        text = path.read_text(encoding="utf-8")
        spans = _body_spans(text)
        found = [(m.start(), NAV_TARGETS[m.group(1)]) for m in _NAV_CALL.finditer(text)]
        found += [(m.start(), _href_screen(m.group(1), all_screens)) for m in _HREF.finditer(text)]
        for pos, target in sorted(found):
            if target is None or any(a <= pos <= b for a, b in spans):
                continue
            out.append(Link(path.name, text.count("\n", 0, pos) + 1, source, target))
    return out
