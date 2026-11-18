"""表に出す市場・業種は共通の書き方を通す（KAB-199。docs/SCREEN_SPEC.md 共通見た目 5）。

市場は fmtMarket（東証P / 東証S / 東証G）、業種は fmtSector（末尾の「業」を外して先頭 5 文字）。
画面に生の値（`プライム`、`東Ｇ`、`情報･通信業`）を出していたら落とす。`title` 属性の正式名はよい。
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JS = sorted((ROOT / "static/js").glob("*.js"))
FIELD = re.compile(r"\.(market|sector33|sector)\b")
TEMPLATE = re.compile(r"\$\{([^{}]*)\}")


def _raw_uses(path: Path) -> list[str]:
    out = []
    for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for m in TEMPLATE.finditer(line):
            expr = m.group(1)
            if not FIELD.search(expr) or "fmtMarket" in expr or "fmtSector" in expr:
                continue
            if line[:m.start()].endswith('title="'):
                continue
            out.append(f"{path.name}:{no}: {line.strip()}")
        if "fillText(" in line and FIELD.search(line) and "fmtMarket" not in line and "fmtSector" not in line:
            out.append(f"{path.name}:{no}: {line.strip()}")
    return out


def test_tables_use_shared_market_and_sector_labels() -> None:
    bad = [x for p in JS if p.name != "api.js" for x in _raw_uses(p)]
    assert not bad, "市場は fmtMarket、業種は fmtSector を通す（docs/SCREEN_SPEC.md）:\n" + "\n".join(bad)


def test_shared_label_helpers_live_in_api_js() -> None:
    api = (ROOT / "static/js/api.js").read_text(encoding="utf-8")
    for name in ("function fmtMarket(", "function fmtSector(", "const shortName =", "const under ="):
        assert name in api
    others = [p.name for p in JS if p.name != "api.js"
              and re.search(r"function fmt(Market|Sector)\(|fmtMonthly(Market|Sector)",
                            p.read_text(encoding="utf-8"))]
    assert not others, f"書き方を別の場所で作り直している: {others}"


def test_detector_catches_raw_values(tmp_path: Path) -> None:
    js = tmp_path / "x.js"
    lines = [
        'h += `<td>${esc(r.market)}</td>`;',                       # 生の市場
        'h += `<td>${esc(r.sector||"")}</td>`;',                   # 生の業種
        'h += `<td title="${esc(r.sector33)}">${esc(fmtSector(r.sector33))}</td>`;',  # よい
        'g.fillText(r.sector33, 0, 0);',                           # 画像に生の業種
    ]
    js.write_text("\n".join(lines), encoding="utf-8")
    assert [x.split(":")[1] for x in _raw_uses(js)] == ["1", "2", "4"]
