"""/app ナビの wiki リンク。公開 HTML には URL を出さない。"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-wiki-nav-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.assets import inject_wiki_nav
from app.config import WIKI_URL
from app.main import app  # noqa: E402

WIKI = "https://stock-analyzer-wiki.pages.dev"


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _app_nav(html: str) -> str:
    start = html.find('<nav id="app-nav">')
    if start < 0:
        _fail("missing #app-nav")
    end = html.find("</nav>", start)
    if end < 0:
        _fail("missing #app-nav close")
    return html[start : end + len("</nav>")]


def test_wiki_url_constant() -> None:
    if WIKI_URL != WIKI:
        _fail(f"WIKI_URL: {WIKI_URL!r}")


def test_inject_wiki_nav_inserts_before_close() -> None:
    src = '<nav id="app-nav">\n    <button data-tab="earnings">決算速報</button>\n  </nav>'
    out = inject_wiki_nav(src, WIKI)
    nav = _app_nav(out)
    if WIKI not in nav:
        _fail("injected HTML missing wiki URL")
    if '<button data-tab="earnings">決算速報</button>' not in nav:
        _fail("injected HTML dropped a tab")
    if inject_wiki_nav("<p>no nav</p>", WIKI) != "<p>no nav</p>":
        _fail("missing #app-nav must be left as-is")
    if inject_wiki_nav(src, "") != src:
        _fail("empty url must be left as-is")


def test_app_nav_has_wiki_link() -> None:
    r = TestClient(app).get("/app")
    if r.status_code != 200:
        _fail(f"GET /app: {r.status_code}")
    nav = _app_nav(r.text)
    m = re.search(r"<a\b[^>]*\bhref=\"" + re.escape(WIKI) + r"\"[^>]*>", nav)
    if not m:
        _fail(f"GET /app #app-nav missing wiki <a>: {nav}")
    tag = m.group(0)
    if 'target="_blank"' not in tag:
        _fail(f"wiki link missing target=_blank: {tag}")
    if 'rel="noopener noreferrer"' not in tag:
        _fail(f"wiki link missing rel=noopener noreferrer: {tag}")
    if ">wiki</a>" not in nav:
        _fail("wiki link label")


def test_public_html_omits_wiki_url() -> None:
    c = TestClient(app)
    for path in ("/monthly", "/monthly/9999", "/robots.txt", "/sitemap.xml", "/static/index.html"):
        r = c.get(path)
        if WIKI in r.text:
            _fail(f"{path} must not contain wiki URL")
    disk = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    if WIKI in disk:
        _fail("static/index.html must not contain wiki URL")
    pub = (ROOT / "app" / "monthly" / "public.py").read_text(encoding="utf-8")
    if WIKI in pub:
        _fail("public monthly template must not contain wiki URL")
