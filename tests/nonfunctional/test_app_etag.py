"""/app の ETag と HTML の ?v= は /static の中身が変わると変わる。boot.js はこの 2 つを見て読み直す。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app import assets

ROOT = Path(__file__).resolve().parents[2]
API_JS = ROOT / "static" / "js" / "api.js"
HTML = '<link rel="stylesheet" href="/static/a.css"><script src="/static/a.js"></script>'


def _etag() -> str:
    return assets.html_etag(assets.version_static_urls(HTML))


def test_page_etag_follows_static_content(tmp_path, monkeypatch):
    monkeypatch.setattr(assets, "STATIC", tmp_path)
    monkeypatch.setattr(assets, "_versions", {})
    (tmp_path / "a.css").write_text("a{}", encoding="utf-8")
    js = tmp_path / "a.js"
    js.write_text("1", encoding="utf-8")
    before = _etag()
    assert _etag() == before
    js.write_text("2", encoding="utf-8")
    os.utime(js, (js.stat().st_atime, js.stat().st_mtime + 5))
    assert _etag() != before


def test_fmt_matches_to_locale_string():
    node = r"""
const src = require("fs").readFileSync(process.argv[1], "utf8");
const fmt = new Function("document", "fetch", src + "; return fmt;")({}, () => {});
const old = (v, d = 0) => v == null ? "–" : Number(v).toLocaleString("ja-JP", {maximumFractionDigits: d, minimumFractionDigits: d});
const bad = [];
for(const v of [null, undefined, 0, -0.04, 1234.5, -9876543.21, 1e12, 0.005, "12.34", NaN])
  for(const d of [undefined, 0, 1, 2, 4])
    if(fmt(v, d) !== old(v, d)) bad.push([v, d, fmt(v, d), old(v, d)]);
console.log(JSON.stringify(bad));
"""
    out = subprocess.run(["node", "-e", node, str(API_JS)], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"
