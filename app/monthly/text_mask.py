"""数値・日付を伏せて構造だけ残す。pdfplumber に依存しない。"""
from __future__ import annotations

import re
import unicodedata

RE_DATE = re.compile(r"\d+\s*年\s*\d+\s*月(\s*\d+\s*日)?|\d{4}[/-]\d{1,2}([/-]\d{1,2})?")
RE_QUARTER = re.compile(r"第\s*\d+\s*(四半期|Q)")
RE_NUM = re.compile(r"[△▲\-−+]?\d[\d,]*(\.\d+)?")


def mask(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = RE_DATE.sub("<D>", s)
    s = RE_QUARTER.sub("<Q>", s)
    s = RE_NUM.sub("<N>", s)
    return re.sub(r"\s+", "", s)
