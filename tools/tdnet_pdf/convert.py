"""TDnet の開示 PDF → ページごとの SVG を並べた HTML（KAB-164。docs/DESIGN_xbrl.md §16）。

pdfplumber で文字の位置・大きさと罫線・網掛けを読み、同じ位置に描く。文字は <text>（検索・コピーできる）、
罫線と網掛けは <line> / <rect>。語の幅は textLength で PDF に合わせるので、ブラウザのフォントが違っても
表の桁がずれない。画像（ロゴ・グラフ）は出さない。重ね打ちの太字は dedupe_chars で 1 つにする。
"""
from __future__ import annotations

import html
import io
import re
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

CONVERTER = "svg1"

_BOLD = re.compile(r"bold|heavy|black|demi|w[6-9](?![0-9])|[-,]b$", re.I)
_SANS = re.compile(r"gothic|goth|sans|arial|helvetica|meiryo|yu ?gothic|ud ?kaku", re.I)
_TITLE = re.compile(r"決算短信|業績予想|配当予想|予想の修正|修正に関する|お知らせ|Financial Results", re.I)

STYLE = (
    "body{margin:0;background:#fff}"
    "svg{display:block;margin:0 auto;border-bottom:1px solid #ccc}"
    "text{white-space:pre}"
)


@dataclass
class Converted:
    html: str
    pages: int
    title: str | None


def _color(c, default: str = "none") -> str:
    if c is None:
        return default
    if isinstance(c, (int, float)):
        c = (c,)
    try:
        v = [float(x) for x in c]
    except (TypeError, ValueError):
        return default  # パターン色など
    if len(v) == 1:
        g = round(v[0] * 255)
        return f"rgb({g},{g},{g})"
    if len(v) == 3:
        r, g, b = (round(x * 255) for x in v)
        return f"rgb({r},{g},{b})"
    if len(v) == 4:  # CMYK
        cy, m, y, k = v
        r, g, b = (round(255 * (1 - x) * (1 - k)) for x in (cy, m, y))
        return f"rgb({r},{g},{b})"
    return default


def _font_attrs(fontname: str | None) -> str:
    name = (fontname or "").split("+")[-1]  # サブセットの接頭辞 ABCDEF+ を外す
    family = "sans-serif" if _SANS.search(name) else "serif"
    weight = ' font-weight="bold"' if _BOLD.search(name) else ""
    return f'font-family="{family}"{weight}'


def _page_svg(page) -> str:
    w, h = float(page.width), float(page.height)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}pt" height="{h:.0f}pt" viewBox="0 0 {w:.1f} {h:.1f}">']
    for r in page.rects:
        fill = _color(r.get("non_stroking_color")) if r.get("fill") else "none"
        stroke = _color(r.get("stroking_color"), "#000") if r.get("stroke") else "none"
        if fill in ("none", "rgb(255,255,255)") and stroke == "none":
            continue
        lw = r.get("linewidth") or 0.5
        parts.append(
            f'<rect x="{r["x0"]:.1f}" y="{r["top"]:.1f}" width="{r["width"]:.1f}" height="{r["height"]:.1f}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{lw:.2f}"/>'
        )
    for ln in page.lines:
        parts.append(
            f'<line x1="{ln["x0"]:.1f}" y1="{ln["top"]:.1f}" x2="{ln["x1"]:.1f}" y2="{ln["bottom"]:.1f}" '
            f'stroke="{_color(ln.get("stroking_color"), "#000")}" stroke-width="{(ln.get("linewidth") or 0.5):.2f}"/>'
        )
    words = page.dedupe_chars(tolerance=1).extract_words(
        x_tolerance=1.2, y_tolerance=1, keep_blank_chars=True, extra_attrs=["fontname", "size"],
    )
    for wd in words:
        size = float(wd["size"])
        base = float(wd["bottom"]) - size * 0.12
        parts.append(
            f'<text x="{wd["x0"]:.1f}" y="{base:.1f}" font-size="{size:.1f}" {_font_attrs(wd["fontname"])} '
            f'textLength="{wd["x1"] - wd["x0"]:.1f}" lengthAdjust="spacingAndGlyphs">{html.escape(wd["text"])}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _title(page) -> str | None:
    for line in (page.extract_text() or "").splitlines():
        s = re.sub(r"\s+", " ", line).strip()
        if _TITLE.search(s):
            return s[:200]
    return None


def convert_pdf(src: Path | str | bytes) -> Converted:
    """src は PDF のパスかバイト列。"""
    with pdfplumber.open(io.BytesIO(src) if isinstance(src, bytes) else str(src)) as pdf:
        pages = [_page_svg(p) for p in pdf.pages]
        title = _title(pdf.pages[0]) if pdf.pages else None
    body = "\n".join(pages)
    doc = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{STYLE}</style></head>'
        f"<body>\n{body}\n</body></html>"
    )
    return Converted(html=doc, pages=len(pages), title=title)
