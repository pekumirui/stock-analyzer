"""config/ir_monthly.json の銘柄コード。デバッグ用グループの正本。URL は出さない。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import IR_MONTHLY_PATH

_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")


def load_ir_monthly_codes(path: Path | None = None) -> list[str]:
    """issuers と html の code を 4 桁のまま、ファイル順で重複なく返す。"""
    src = IR_MONTHLY_PATH if path is None else path
    raw = json.loads(src.read_text(encoding="utf-8"))
    blocks: list = []
    if isinstance(raw, dict):
        for key in ("issuers", "html"):
            block = raw.get(key)
            if isinstance(block, list):
                blocks.extend(block)
    elif isinstance(raw, list):
        blocks = list(raw)
    out: list[str] = []
    seen: set[str] = set()
    for item in blocks:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip().upper()
        if len(code) >= 5:
            code = code[:4]
        if not _CODE_RE.fullmatch(code) or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out
