from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DEPLOY = "deploy"
NGINX = "deploy/nginx-site.sh"
SERVED_BY_NGINX = "nginx が直に返す"
UNIT_SUFFIXES = (".service", ".timer")
_UPSTREAM = re.compile(r'^([A-Z_]*UPSTREAM)="\$\{\1:-([^}]+)\}"', re.M)
_PROXY_BLOCK = re.compile(r"^proxy_block=\$\(cat <<EOF\n(.*?)^EOF\n\)", re.M | re.S)
_LOCATION = re.compile(r"^    location (?:(=|\^~|~\*?) )?(\S+|\"[^\"]*\") \{$", re.M)
_PROXY_PASS = re.compile(r"proxy_pass http://\$\{(\w+)\}")
_ALIAS = re.compile(r"alias \$\{APP_DIR\}(/\S*);")
_RETURN = re.compile(r"return (\d+) (\S+);")


@dataclass(frozen=True)
class Unit:
    name: str
    kind: str
    description: str
    start: tuple[str, ...]
    after: tuple[str, ...]
    wants: tuple[str, ...]
    wanted_by: tuple[str, ...]
    restart: str
    interval: str
    triggers: str
    ports: tuple[int, ...]


@dataclass(frozen=True)
class Location:
    modifier: str
    path: str
    upstream: str
    target: str
    access: bool
    internal: bool


@dataclass
class Nginx:
    upstreams: dict[str, str] = field(default_factory=dict)
    locations: list[Location] = field(default_factory=list)


def _sections(text: str) -> dict[str, dict[str, list[str]]]:
    out: dict[str, dict[str, list[str]]] = {}
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            out.setdefault(section, {})
        elif "=" in line and section:
            key, _, value = line.partition("=")
            out[section].setdefault(key, []).append(value)
    return out


def _first(sec: dict[str, list[str]], key: str) -> str:
    return sec.get(key, [""])[0]


def _words(sec: dict[str, list[str]], key: str) -> tuple[str, ...]:
    return tuple(w for v in sec.get(key, []) for w in v.split())


def _ports(root: Path, service: dict[str, list[str]]) -> tuple[int, ...]:
    found = [int(p) for v in service.get("Environment", []) for p in re.findall(r"\w+_PORT=(\d+)", v)]
    for cmd in service.get("ExecStart", []):
        for script in re.findall(r"(deploy/[\w.-]+\.sh)", cmd):
            path = root / script
            if path.is_file():
                found += [int(p) for p in re.findall(r"--port\s+(\d+)", path.read_text(encoding="utf-8"))]
    return tuple(dict.fromkeys(found))


def _unit(root: Path, path: Path) -> Unit:
    sec = _sections(path.read_text(encoding="utf-8"))
    unit, service, timer, install = (sec.get(k, {}) for k in ("Unit", "Service", "Timer", "Install"))
    boot, every = _first(timer, "OnBootSec"), _first(timer, "OnUnitActiveSec")
    return Unit(
        name=path.name,
        kind=path.suffix.removeprefix("."),
        description=_first(unit, "Description"),
        start=tuple(service.get("ExecStart", [])),
        after=_words(unit, "After"),
        wants=_words(unit, "Wants"),
        wanted_by=_words(install, "WantedBy"),
        restart=_first(service, "Restart"),
        interval=f"起動 {boot} 後に 1 回目、以降 {every} ごと" if boot and every else every,
        triggers=_first(timer, "Unit"),
        ports=_ports(root, service),
    )


def units(root: Path) -> list[Unit]:
    paths = sorted(p for p in (root / DEPLOY).iterdir() if p.suffix in UNIT_SUFFIXES)
    return [_unit(root, p) for p in paths]


def nginx(root: Path) -> Nginx:
    text = (root / NGINX).read_text(encoding="utf-8")
    out = Nginx(upstreams={k: v for k, v in _UPSTREAM.findall(text)})
    block = _PROXY_BLOCK.search(text)
    if block is None:
        return out
    body = block.group(1)
    marks = list(_LOCATION.finditer(body))
    for i, m in enumerate(marks):
        chunk = body[m.end(): marks[i + 1].start() if i + 1 < len(marks) else len(body)]
        proxy = _PROXY_PASS.search(chunk)
        alias = _ALIAS.search(chunk)
        ret = _RETURN.search(chunk)
        if proxy:
            upstream, target = proxy.group(1), ""
        elif alias:
            upstream, target = "", f"{SERVED_BY_NGINX} `APP_DIR{alias.group(1)}`"
        elif ret:
            upstream, target = "", f"{ret.group(1)} で `{ret.group(2)}` へ"
        else:
            upstream, target = "", ""
        out.locations.append(Location(
            m.group(1) or "", m.group(2).strip('"'), upstream, target,
            "${auth_block}" in chunk, bool(re.search(r"^\s+internal;$", chunk, re.M)),
        ))
    return out
