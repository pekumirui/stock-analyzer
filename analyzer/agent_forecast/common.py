"""対象（銘柄 × 決算期）の設定と、決算期の日付計算。

設定は targets/<id>.json。作業フォルダは既定で /workspace/forecast-exp/<id>/。
"""

from __future__ import annotations

import calendar
import json
from datetime import date
from pathlib import Path
from string import Template

HERE = Path(__file__).resolve().parent
DEFAULT_BASE = Path("/workspace/forecast-exp")
WEEKDAYS = "月火水木金土日"
PERIOD_Q = {"Q1": 1, "Q2": 2, "Q3": 3, "FY": 4}


def load_target(path: Path) -> dict:
    t = json.loads(path.read_text())
    t["id"] = path.stem
    for kind in ("tanshin", "monthly", "briefing"):
        for item in t.get(kind, []):
            assert item["date"] <= t["cutoff"], (kind, item)
    return t


def target_args(ap) -> None:  # noqa: ANN001
    ap.add_argument("--target", type=Path, required=True, help="targets/<id>.json")
    ap.add_argument("--root", type=Path, default=None, help="作業フォルダ（既定 /workspace/forecast-exp/<id>）")


def root_of(t: dict, root: Path | None) -> Path:
    return root or DEFAULT_BASE / t["id"]


def _add_months(y: int, m: int, n: int) -> tuple[int, int]:
    k = y * 12 + (m - 1) + n
    return k // 12, k % 12 + 1


def _fy_start(fye: str) -> tuple[int, int]:
    y, m = map(int, fye.split("-"))
    return _add_months(y, m, -11)


def prev_fye(fye: str) -> str:
    y, m = map(int, fye.split("-"))
    return f"{y - 1:04d}-{m:02d}"


def next_fye(fye: str) -> str:
    y, m = map(int, fye.split("-"))
    return f"{y + 1:04d}-{m:02d}"


def fy_label(fye: str) -> str:
    y, m = map(int, fye.split("-"))
    return f"{y}年{m}月期"


def fy_start_date(fye: str) -> str:
    y, m = _fy_start(fye)
    return f"{y:04d}-{m:02d}-01"


def quarter_start(fye: str, q: int) -> str:
    y, m = _add_months(*_fy_start(fye), 3 * (q - 1))
    return f"{y:04d}-{m:02d}-01"


def quarter_end(fye: str, q: int) -> str:
    y, m = _add_months(*_fy_start(fye), 3 * q - 1)
    return f"{y:04d}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def jp_date(iso: str, *, weekday: bool = False) -> str:
    d = date.fromisoformat(iso)
    s = f"{d.year} 年 {d.month} 月 {d.day} 日"
    return f"{s}（{WEEKDAYS[d.weekday()]}）" if weekday else s


def target_label(t: dict) -> str:
    fye, q = t["target"]["fiscal_year_end"], t["target"]["quarter"]
    s, e = date.fromisoformat(quarter_start(fye, q)), date.fromisoformat(quarter_end(fye, q))
    return f"{fy_label(fye)} {q}Q単独（{s.year}/{s.month}/{s.day}〜{e.month}/{e.day}）"


def render_prompt(t: dict) -> str:
    fye, q = t["target"]["fiscal_year_end"], t["target"]["quarter"]
    kind = "決算短信" if q == 4 else f"第 {q} 四半期の決算短信"
    notes = "売上総利益 − 販管費 = 営業利益 になるようにしてください。"
    if t.get("cost_reclass"):
        notes += t["cost_reclass"]["note"]
    return Template((HERE / "prompt.md").read_text()).substitute(
        today=jp_date(t["cutoff"], weekday=True),
        name=t["name"],
        profile=t["profile"],
        announce=jp_date(t["announce"]),
        fy=fy_label(fye).replace("年", " 年 ").replace("月期", " 月期"),
        kind=kind,
        q=q,
        q_start=jp_date(quarter_start(fye, q)),
        q_end=jp_date(quarter_end(fye, q)),
        notes=notes,
        target_label=target_label(t),
    )
