"""銘柄固定の HTML 月次パーサ。parse: true はここだけ。"""
from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable

_MODULE_RE = re.compile(r"^i([0-9][0-9A-Z]{3})$")


def _load_parsers() -> dict[str, Callable]:
    """i<code>.py の parse を表示コードで引けるようにする。足すのはファイルだけ。"""
    out: dict[str, Callable] = {}
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        m = _MODULE_RE.fullmatch(info.name)
        if m is None:
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        out[m.group(1)] = module.parse
    return out


PARSERS: dict[str, Callable] = _load_parsers()


def parse_issuer(html: str, code: str, **_kwargs: object) -> dict:
    """表示コードでも引ける。table と unit は各モジュールの宣言。"""
    from jobs.monthly.tdnet_monthly_r2 import display_code

    key = display_code(code)
    parser = PARSERS.get(key)
    if parser is None:
        raise ValueError(f"{key} page does not match declaration")
    return parser(html)
