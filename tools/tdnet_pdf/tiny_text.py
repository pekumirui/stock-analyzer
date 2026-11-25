#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.xbrl.pdf.convert import CONVERTER

_TEXT = re.compile(r'font-size="([\d.]+)"[^>]*textLength="([\d.]+)"[^>]*>([^<]*)</text>')
STRETCH = 3.0
MIN_SHARE = 0.01


def stretched_share(html: str) -> float:
    words = stretched = 0
    for size, length, text in _TEXT.findall(html):
        n = len(text.strip())
        if n < 2 or float(size) <= 0:
            continue
        words += 1
        if float(length) / (n * float(size)) > STRETCH:
            stretched += 1
    return stretched / words if words else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="字が潰れて横線になる短信 PDF の変換（KAB-572）の開示番号を 1 行 1 つ出す。読むだけ")
    ap.add_argument("--db", required=True)
    args = ap.parse_args(argv)
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    seen = hits = 0
    for no, html_gz in conn.execute(
        "SELECT disclosure_no, html_gz FROM tdnet_pdf_documents"
        " WHERE status = 'ok' AND html_gz IS NOT NULL AND (converter IS NULL OR converter != ?)",
        (CONVERTER,),
    ):
        seen += 1
        if stretched_share(gzip.decompress(html_gz).decode("utf-8", "replace")) >= MIN_SHARE:
            hits += 1
            print(no, flush=True)
    print(f"tiny_text scanned={seen} hits={hits}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
