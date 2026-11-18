"""スマホ端末だけページ末尾に PC版 / スマホ版の切り替え。PC 幅では出さない。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-view-mode-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient

from app.main import app

JS = ROOT / "static" / "js" / "view-mode.js"
INDEX = ROOT / "static" / "index.html"
PUBLIC = ROOT / "app" / "monthly" / "public.py"


def _fail(msg: str) -> None:
    raise AssertionError(msg)


def _markup_ok(src: str, label: str) -> None:
    if 'id="view-mode"' not in src or 'id="view-mode-btn"' not in src:
        _fail(f"{label} missing #view-mode")
    if 'hidden' not in src.split('id="view-mode"', 1)[1][:40]:
        _fail(f"{label} #view-mode must start hidden")
    if "/static/js/view-mode.js" not in src:
        _fail(f"{label} missing view-mode.js")
    vp = src.find('name="viewport"')
    js = src.find("/static/js/view-mode.js")
    if vp < 0 or js < 0 or js < vp:
        _fail(f"{label} view-mode.js must follow the viewport meta")


def test_view_mode_markup_on_app_and_public() -> None:
    _markup_ok(INDEX.read_text(encoding="utf-8"), "SPA")
    _markup_ok(PUBLIC.read_text(encoding="utf-8"), "public template")
    c = TestClient(app)
    app_html = c.get("/app")
    pub_html = c.get("/monthly")
    if app_html.status_code != 200:
        _fail(f"GET /app: {app_html.status_code}")
    if pub_html.status_code != 200:
        _fail(f"GET /monthly: {pub_html.status_code}")
    if 'id="view-mode"' not in app_html.text or "/static/js/view-mode.js?v=" not in app_html.text:
        _fail("GET /app must version view-mode.js")
    if 'id="view-mode"' not in pub_html.text or "/static/js/view-mode.js?v=" not in pub_html.text:
        _fail("GET /monthly must version view-mode.js")
    if "stock-analyzer-wiki.pages.dev" in pub_html.text:
        _fail("GET /monthly must still omit wiki URL")


def test_view_mode_js_phone_only_and_persists() -> None:
    proc = subprocess.run(
        ["node", "-e", r"""
const vm = require(process.argv[1]);
function doc(content){
  const meta = { content, setAttribute(k, v){ if(k === 'content') this.content = v; } };
  const host = { hidden: true };
  const btn = { textContent: '', onclick: null };
  return {
    querySelector: s => s === 'meta[name="viewport"]' ? meta : null,
    getElementById: id => id === 'view-mode' ? host : id === 'view-mode-btn' ? btn : null,
    meta, host, btn,
  };
}
function store(init){
  const m = Object.assign({}, init);
  return { getItem: k => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, data: m };
}
if (vm.VIEW_MODE_KEY !== 'ui.pcView') process.exit(2);
if (vm.VIEW_MODE_PC !== 'width=1280' || vm.VIEW_MODE_MAX !== 720) process.exit(3);
if (vm.viewModeNativePhone(390) !== true || vm.viewModeNativePhone(720) !== true) process.exit(4);
if (vm.viewModeNativePhone(721) !== false || vm.viewModeNativePhone(1280) !== false) process.exit(5);
if (vm.viewModeLabel(false) !== 'PC版を表示' || vm.viewModeLabel(true) !== 'スマホ版に戻す') process.exit(6);

const wide = doc('width=device-width, initial-scale=1.0');
if (vm.mountViewMode(wide, 1280, store({})) !== false || wide.host.hidden !== true) process.exit(7);

const phone = doc('width=device-width, initial-scale=1.0');
const st = store({});
let reloads = 0;
if (vm.mountViewMode(phone, 390, st, () => { reloads += 1; }) !== true) process.exit(8);
if (phone.host.hidden !== false || phone.btn.textContent !== 'PC版を表示') process.exit(9);
phone.btn.onclick({ preventDefault(){} });
if (st.getItem('ui.pcView') !== '1' || phone.meta.content !== 'width=1280') process.exit(10);
if (phone.btn.textContent !== 'スマホ版に戻す' || reloads !== 1) process.exit(11);
phone.btn.onclick({ preventDefault(){} });
if (st.getItem('ui.pcView') !== '0' || phone.meta.content !== 'width=device-width, initial-scale=1.0') process.exit(12);
if (phone.btn.textContent !== 'PC版を表示' || reloads !== 2) process.exit(13);

const saved = doc('width=device-width, initial-scale=1.0');
if (!vm.applyViewMode(saved, 390, store({ 'ui.pcView': '1' }))) process.exit(14);
if (saved.meta.content !== 'width=1280') process.exit(15);
if (vm.applyViewMode(saved, 1280, store({ 'ui.pcView': '1' }))) process.exit(16);
console.log(JSON.stringify({ ok: true }));
""", str(JS)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"node view-mode: {proc.returncode} {proc.stderr or proc.stdout}")
    got = json.loads(proc.stdout)
    if got.get("ok") is not True:
        _fail(f"node view-mode payload: {got!r}")
