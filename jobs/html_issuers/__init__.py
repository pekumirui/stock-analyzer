"""銘柄固定の HTML 月次パーサ。parse: true はここだけ。"""
from __future__ import annotations

from collections.abc import Callable

from html_issuers.i141A import parse as parse_141A
from html_issuers.i1911 import parse as parse_1911
from html_issuers.i1928 import parse as parse_1928
from html_issuers.i2664 import parse as parse_2664
from html_issuers.i2685 import parse as parse_2685
from html_issuers.i2698 import parse as parse_2698
from html_issuers.i2702 import parse as parse_2702
from html_issuers.i2742 import parse as parse_2742
from html_issuers.i2769 import parse as parse_2769
from html_issuers.i2790 import parse as parse_2790
from html_issuers.i2791 import parse as parse_2791
from html_issuers.i2910 import parse as parse_2910
from html_issuers.i3028 import parse as parse_3028
from html_issuers.i3038 import parse as parse_3038
from html_issuers.i3082 import parse as parse_3082
from html_issuers.i3148 import parse as parse_3148
from html_issuers.i3222 import parse as parse_3222
from html_issuers.i3391 import parse as parse_3391
from html_issuers.i3542 import parse as parse_3542
from html_issuers.i3549 import parse as parse_3549
from html_issuers.i3733 import parse as parse_3733
from html_issuers.i6045 import parse as parse_6045
from html_issuers.i6537 import parse as parse_6537
from html_issuers.i7416 import parse as parse_7416
from html_issuers.i7421 import parse as parse_7421
from html_issuers.i7494 import parse as parse_7494
from html_issuers.i7512 import parse as parse_7512
from html_issuers.i7514 import parse as parse_7514
from html_issuers.i7522 import parse as parse_7522
from html_issuers.i7561 import parse as parse_7561
from html_issuers.i7564 import parse as parse_7564
from html_issuers.i7616 import parse as parse_7616
from html_issuers.i7679 import parse as parse_7679
from html_issuers.i8011 import parse as parse_8011
from html_issuers.i8016 import parse as parse_8016
from html_issuers.i8153 import parse as parse_8153
from html_issuers.i8163 import parse as parse_8163
from html_issuers.i8179 import parse as parse_8179
from html_issuers.i8194 import parse as parse_8194
from html_issuers.i8217 import parse as parse_8217
from html_issuers.i8255 import parse as parse_8255
from html_issuers.i8278 import parse as parse_8278
from html_issuers.i8281 import parse as parse_8281
from html_issuers.i9759 import parse as parse_9759
from html_issuers.i9843 import parse as parse_9843
from html_issuers.i9900 import parse as parse_9900
from html_issuers.i9946 import parse as parse_9946
from html_issuers.i9948 import parse as parse_9948
from html_issuers.i9989 import parse as parse_9989
from html_issuers.i9993 import parse as parse_9993
from html_issuers.i9994 import parse as parse_9994

PARSERS: dict[str, Callable] = {
    "141A": parse_141A,
    "1911": parse_1911,
    "1928": parse_1928,
    "2664": parse_2664,
    "2685": parse_2685,
    "2698": parse_2698,
    "2702": parse_2702,
    "2742": parse_2742,
    "2769": parse_2769,
    "2790": parse_2790,
    "2791": parse_2791,
    "2910": parse_2910,
    "3028": parse_3028,
    "3038": parse_3038,
    "3082": parse_3082,
    "3148": parse_3148,
    "3222": parse_3222,
    "3391": parse_3391,
    "3542": parse_3542,
    "3549": parse_3549,
    "3733": parse_3733,
    "6045": parse_6045,
    "6537": parse_6537,
    "7416": parse_7416,
    "7421": parse_7421,
    "7494": parse_7494,
    "7512": parse_7512,
    "7514": parse_7514,
    "7522": parse_7522,
    "7561": parse_7561,
    "7564": parse_7564,
    "7616": parse_7616,
    "7679": parse_7679,
    "8011": parse_8011,
    "8016": parse_8016,
    "8153": parse_8153,
    "8163": parse_8163,
    "8179": parse_8179,
    "8194": parse_8194,
    "8217": parse_8217,
    "8255": parse_8255,
    "8278": parse_8278,
    "8281": parse_8281,
    "9759": parse_9759,
    "9843": parse_9843,
    "9900": parse_9900,
    "9946": parse_9946,
    "9948": parse_9948,
    "9989": parse_9989,
    "9993": parse_9993,
    "9994": parse_9994,
}


def parse_issuer(html: str, code: str, **_kwargs: object) -> dict:
    """表示コードでも引ける。table と unit は各モジュールの宣言。"""
    from tdnet_monthly_r2 import display_code

    key = display_code(code)
    parser = PARSERS.get(key)
    if parser is None:
        raise ValueError(f"{key} page does not match declaration")
    return parser(html)
