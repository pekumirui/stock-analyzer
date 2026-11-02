"""HTML の /static URL に中身のハッシュを付ける（`?v=`）。

iOS Safari はメモリに残った CSS を、no-cache でも再読込でもサーバーに確かめずに使い続ける。
URL を中身ごとに変えれば、古いものは使われない。
"""
from __future__ import annotations

import hashlib
import re
from html import escape
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
_ASSET_URL = re.compile(r'((?:src|href)=")(/static/[^"?#]+)(")')
_versions: dict[str, tuple[float, str]] = {}


def asset_version(url_path: str) -> str | None:
    """/static/... の中身の短いハッシュ。static の外や無いファイルは None。"""
    path = (STATIC / url_path.removeprefix("/static/")).resolve()
    if STATIC not in path.parents or not path.is_file():
        return None
    mtime = path.stat().st_mtime
    hit = _versions.get(url_path)
    if hit and hit[0] == mtime:
        return hit[1]
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    _versions[url_path] = (mtime, version)
    return version


def version_static_urls(html: str) -> str:
    def repl(m: re.Match[str]) -> str:
        version = asset_version(m.group(2))
        return m.group(0) if version is None else f"{m.group(1)}{m.group(2)}?v={version}{m.group(3)}"

    return _ASSET_URL.sub(repl, html)


def html_etag(html: str) -> str:
    return '"' + hashlib.sha256(html.encode("utf-8")).hexdigest()[:32] + '"'


def inject_wiki_nav(html: str, url: str) -> str:
    """#app-nav の末尾に wiki リンクを足す。テンプレートは触らない。"""
    if not url:
        return html
    start = html.find('<nav id="app-nav">')
    if start < 0:
        return html
    end = html.find("</nav>", start)
    if end < 0:
        return html
    href = escape(url, quote=True)
    link = f'<a id="wiki-link" href="{href}" target="_blank" rel="noopener noreferrer">wiki</a>'
    return html[:end].rstrip() + "\n    " + link + "\n  " + html[end:]
