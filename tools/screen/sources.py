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
