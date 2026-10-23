"""EDINET の type=1 ZIP（XBRL/PublicDoc/*.xbrl）を合成する。テキストブロックは実物の HTML の形に合わせる。"""
from __future__ import annotations

import io
import zipfile
from html import escape

NS = {
    "jpdei_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor",
    "jpcrp-sbr_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp-sbr/2026-03-31/jpcrp-sbr_cor",
    "jptoo-ton_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jptoo-ton/2026-03-31/jptoo-ton_cor",
    "jptoo-tor_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jptoo-tor/2026-03-31/jptoo-tor_cor",
    "jptoo-pst_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jptoo-pst/2026-03-31/jptoo-pst_cor",
    "jptoi_cor": "http://disclosure.edinet-fsa.go.jp/taxonomy/jptoi/2026-03-31/jptoi_cor",
}


def table(*rows: list[str]) -> str:
    body = "".join("<tr>" + "".join(f"<td><p>{escape(c)}</p></td>" for c in r) + "</tr>" for r in rows)
    return f"<table>{body}</table>"


def para(*texts: str) -> str:
    return "".join(f"<p>{escape(t)}</p>" for t in texts)


def xbrl_zip(facts: list[tuple[str, str]], name: str = "jpcrp170000-sbr-001_E99999-000_2026-10-14_01_2026-10-14",
             honbun: str | None = None) -> bytes:
    """facts は (prefix:LocalName, 値)。TextBlock は HTML をエスケープして入れる（実物と同じ）。"""
    attrs = " ".join(f'xmlns:{p}="{u}"' for p, u in NS.items())
    els = []
    for qname, value in facts:
        els.append(f'<{qname} contextRef="FilingDateInstant">{escape(value)}</{qname}>')
    xml = (f'<?xml version="1.0" encoding="UTF-8"?>'
           f'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" {attrs}>'
           '<xbrli:context id="FilingDateInstant"><xbrli:entity><xbrli:identifier scheme="x">E99999</xbrli:identifier>'
           '</xbrli:entity><xbrli:period><xbrli:instant>2026-10-14</xbrli:instant></xbrli:period></xbrli:context>'
           + "".join(els) + "</xbrli:xbrl>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"XBRL/PublicDoc/{name}.xbrl", xml.encode("utf-8"))
        zf.writestr("XBRL/PublicDoc/manifest_PublicDoc.xml", "<manifest/>")
        if honbun is not None:
            zf.writestr(f"XBRL/PublicDoc/0100000_honbun_{name}_ixbrl.htm", honbun.encode("utf-8"))
    return buf.getvalue()
