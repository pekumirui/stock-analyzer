from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = ROOT / "static" / "js"
TIP_RE = re.compile(r'(?:class|id)="[^"]*-tip"')

_NODE = r"""
const fs = require("fs");
const listeners = {};
global.document = {addEventListener(type, fn){ (listeners[type] ||= []).push(fn); }, querySelector(){ return null; }};
const chartTip = new Function(fs.readFileSync(process.argv[1], "utf8") + "; return chartTip;")();
const inside = {}, outside = {};
const host = {contains: t => t === inside};
let hidden = 0;
const ctl = chartTip(host, () => { hidden++; });
const fire = (type, ev) => (listeners[type] || []).forEach(fn => fn(ev));
const log = [];
log.push(ctl.tap(3), hidden);
fire("pointerdown", {target: inside}); log.push(ctl.at, hidden);
fire("pointerdown", {target: outside}); log.push(ctl.at, hidden);
ctl.tap(2); log.push(ctl.tap(2), ctl.at, hidden);
ctl.hover(5); fire("keydown", {key: "Escape"}); log.push(ctl.at, hidden);
fire("pointerdown", {target: outside}); log.push(hidden);
log.push(ctl.press(7, {pointerType: "mouse"}), ctl.press(7, {pointerType: "mouse"}), ctl.at);
log.push(ctl.press(7, {pointerType: "touch"}), ctl.at);
const again = chartTip(host, () => {}); log.push(again === ctl, Object.keys(listeners).map(k => listeners[k].length));
console.log(JSON.stringify(log));
"""


def test_every_chart_tip_goes_through_chart_tip():
    makers = sorted(p.name for p in JS.glob("*.js") if TIP_RE.search(p.read_text(encoding="utf-8")))
    assert makers, "値の札を作る JS が見つからない"
    missing = [n for n in makers if "chartTip(" not in (JS / n).read_text(encoding="utf-8")]
    assert not missing, f"値の札は chartTip を通す（docs/screen/SPEC.md「グラフの値の札」）: {missing}"


def test_chart_tip_hides_on_outside_tap_escape_and_second_tap():
    out = subprocess.run(["node", "-e", _NODE, str(JS / "api.js")], check=True, capture_output=True, text=True)
    assert json.loads(out.stdout) == [
        True, 0,
        3, 0,
        None, 1,
        False, None, 2,
        None, 3,
        3,
        True, True, 7,
        False, None,
        True, [1, 1],
    ]
