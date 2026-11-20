from __future__ import annotations

import re

_CODE4_HEAD_RE = re.compile(r"^[0-9][0-9A-Z]{3}$")


def to_code4(company_code: str) -> str:
    """TDnet/J-Quants の発行体コードを表示4桁にする。末尾0を無条件には落とさない。

    - 空白除去。空なら ""（呼び出し側が _unmatched/_nocode）
    - 既に4文字ならそのまま（7203, 262A）
    - 5文字以上で先頭4が [0-9][0-9A-Z]{3} なら先頭4
      （92790→9279, 72030→7203, 269A0→269A, 386A0→386A）
    - それ以外は除去後の文字列をそのまま返す
    """
    code = re.sub(r"\s+", "", company_code or "")
    if not code:
        return ""
    if len(code) == 4:
        return code
    if len(code) >= 5:
        head = code[:4]
        if _CODE4_HEAD_RE.match(head):
            return head
    return code


def jq_lookup_code(company_code: str) -> str:
    """stocks 照合専用。to_code4 に末尾 0 を付けて J-Quants 5桁にする。"""
    code4 = to_code4(company_code)
    if not code4:
        return ""
    return code4 + "0"
