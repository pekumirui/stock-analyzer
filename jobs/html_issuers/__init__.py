"""銘柄固定の HTML 月次パーサ。PARSERS にある code は html_to_ingest に回さない。"""
from __future__ import annotations

from collections.abc import Callable

from html_issuers.i3148 import parse as parse_3148
from html_issuers.i7522 import parse as parse_7522
from html_issuers.i7561 import parse as parse_7561
from html_issuers.i8179 import parse as parse_8179
from html_issuers.i8278 import parse as parse_8278
from html_issuers.i9946 import parse as parse_9946
from html_issuers.i9989 import parse as parse_9989
from html_issuers.i9994 import parse as parse_9994

PARSERS: dict[str, Callable] = {
    "3148": parse_3148,
    "7522": parse_7522,
    "7561": parse_7561,
    "8179": parse_8179,
    "8278": parse_8278,
    "9946": parse_9946,
    "9989": parse_9989,
    "9994": parse_9994,
}
