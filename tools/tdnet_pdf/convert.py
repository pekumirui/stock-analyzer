"""TDnet の開示 PDF → ページごとの SVG を並べた HTML（KAB-164 / KAB-169。docs/jobs/xbrl.md「過去の短信を PDF から HTML にする」）。

pdfium（pypdfium2）で文字と図形を読み、同じ位置に描く。文字は <text>（検索・コピーできる）、
罫線・網掛け・枠・曲線は <path>。語の幅は textLength で PDF に合わせるので、ブラウザのフォントが
違っても表の桁がずれない。画像（ロゴ・グラフの画像）は出さない。

- 文字: textpage の 1 文字ずつの原点（ベースライン）・ゆるい字の枠・大きさ。pdfium は UTF-16 の 1 単位ずつ返すので、
  サロゲートの組（𠮷 など）は 1 字に戻し、組にならないものは U+FFFD にする。返す HTML は utf8_safe で
  残りを U+FFFD にする（encode の errors='replace' は ASCII '?' になる。KAB-210）。フォント名・太さ・色は
  文字が属するテキストのオブジェクトごとに 1 回だけ取る。pdfium が足した空白・改行（generated）は捨てる。
  ベースラインが 1pt 以内の文字を 1 行にまとめ、左から並べ、隙間が 1.2pt を超えるか書式が変われば語を切る
- 図形: ページのオブジェクトの path。Form XObject の入れ子を辿り、行列を掛け合わせてページの座標にする
"""
from __future__ import annotations

import ctypes
import html
import re
from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from pypdfium2 import raw

CONVERTER = "svg2"


def utf8_safe(s: str) -> str:
    """UTF-8 に書けないサロゲートを U+FFFD 1 字に置き換える。それ以外の文字は触らない。

    ``encode('utf-8')``（strict）は ``UnicodeEncodeError: surrogates not allowed``。
    ``errors='replace'`` は ASCII ``?`` になるので使わない。組の復元は ``_chars``。ここは漏れの止め。
    """
    try:
        s.encode("utf-8")
    except UnicodeEncodeError:
        return "".join("\ufffd" if 0xD800 <= ord(c) <= 0xDFFF else c for c in s)
    return s


_BOLD = re.compile(r"bold|heavy|black|demi|w[6-9](?![0-9])|[-,]b$", re.I)
_SANS = re.compile(r"gothic|goth|sans|arial|helvetica|meiryo|yu ?gothic|ud ?kaku", re.I)
_TITLE = re.compile(r"決算短信|業績予想|配当予想|予想の修正|修正に関する|お知らせ|Financial Results", re.I)

STYLE = (
    "body{margin:0;background:#fff}"
    "svg{display:block;margin:0 auto;border-bottom:1px solid #ccc}"
    "text{white-space:pre}"
)

LINE_TOL = 1.0   # 同じ行とみなすベースラインの差（pt）
GAP_TOL = 1.2    # 語を切る字間（pt）
_MAX_DEPTH = 8   # Form XObject の入れ子
_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


@dataclass
class Converted:
    html: str
    pages: int
    title: str | None


@dataclass(frozen=True)
class _Style:
    family: str
    bold: bool
    fill: str | None   # 黒なら None


@dataclass
class _Char:
    ch: str
    x0: float
    x1: float
    base: float        # SVG 座標（上から）のベースライン
    size: float
    style: _Style


def _rgb(r: int, g: int, b: int) -> str:
    return f"rgb({r},{g},{b})"


def _chars(page, height: float) -> list[_Char]:
    textpage = page.get_textpage()
    tp = textpage.raw
    try:
        ox, oy = ctypes.c_double(), ctypes.c_double()
        box = raw.FS_RECTF()
        r, g, b, a = (ctypes.c_uint() for _ in range(4))
        buf = ctypes.create_string_buffer(256)
        flags = ctypes.c_int()
        styles: dict[int, _Style] = {}
        out: list[_Char] = []
        n_chars = raw.FPDFText_CountChars(tp)
        low = False
        for i in range(n_chars):
            if low:
                low = False
                continue
            u = raw.FPDFText_GetUnicode(tp, i)
            if u < 32 or u in (0xFFFE, 0xFFFF) or raw.FPDFText_IsGenerated(tp, i) == 1:
                continue
            key = ctypes.cast(raw.FPDFText_GetTextObject(tp, i), ctypes.c_void_p).value or 0
            if 0xD800 <= u <= 0xDFFF:
                v = raw.FPDFText_GetUnicode(tp, i + 1) if u <= 0xDBFF and i + 1 < n_chars else 0
                if 0xDC00 <= v <= 0xDFFF and key == (
                        ctypes.cast(raw.FPDFText_GetTextObject(tp, i + 1), ctypes.c_void_p).value or 0):
                    u = 0x10000 + ((u - 0xD800) << 10) + (v - 0xDC00)
                    low = True
                else:
                    u = 0xFFFD   # 組にならないサロゲートは UTF-8 に書けない
            st = styles.get(key)
            if st is None:
                n = raw.FPDFText_GetFontInfo(tp, i, buf, len(buf), flags)
                name = buf.raw[: max(n - 1, 0)].decode("latin-1", "replace").split("+")[-1] if n else ""
                fill = None
                if raw.FPDFText_GetFillColor(tp, i, r, g, b, a) and (r.value, g.value, b.value) != (0, 0, 0):
                    fill = _rgb(r.value, g.value, b.value)
                st = styles[key] = _Style(
                    "sans-serif" if _SANS.search(name) else "serif",
                    raw.FPDFText_GetFontWeight(tp, i) >= 600 or bool(_BOLD.search(name)),
                    fill,
                )
            raw.FPDFText_GetCharOrigin(tp, i, ox, oy)
            raw.FPDFText_GetLooseCharBox(tp, i, box)
            out.append(_Char(chr(u), box.left, box.right, height - oy.value, raw.FPDFText_GetFontSize(tp, i), st))
        return out
    finally:
        textpage.close()


def _lines(chars: list[_Char]) -> list[list[_Char]]:
    rows: list[list[_Char]] = []
    for c in sorted(chars, key=lambda c: (c.base, c.x0)):
        if rows and abs(rows[-1][0].base - c.base) <= LINE_TOL:
            rows[-1].append(c)
        else:
            rows.append([c])
    for row in rows:
        row.sort(key=lambda c: c.x0)
    return rows


def _words(row: list[_Char]) -> list[list[_Char]]:
    words: list[list[_Char]] = []
    for c in row:
        if words:
            p = words[-1][-1]
            if (p.style == c.style and abs(p.size - c.size) < 0.05
                    and c.x0 - p.x1 <= GAP_TOL and c.x0 >= p.x0):
                words[-1].append(c)
                continue
        words.append([c])
    return words


def _text_svg(word: list[_Char]) -> str:
    first, last = word[0], word[-1]
    st = first.style
    attrs = f'font-family="{st.family}"' + (' font-weight="bold"' if st.bold else "") + (
        f' fill="{st.fill}"' if st.fill else "")
    w = max(last.x1 - first.x0, 0.1)
    text = html.escape("".join(c.ch for c in word))
    return (
        f'<text x="{first.x0:.1f}" y="{first.base:.1f}" font-size="{first.size:.1f}" {attrs} '
        f'textLength="{w:.1f}" lengthAdjust="spacingAndGlyphs">{text}</text>'
    )


def _mul(m, n):
    """子の行列 m のあとに親の行列 n。(a, b, c, d, e, f)。"""
    a, b, c, d, e, f = m
    pa, pb, pc, pd, pe, pf = n
    return (a * pa + b * pc, a * pb + b * pd, c * pa + d * pc, c * pb + d * pd,
            e * pa + f * pc + pe, e * pb + f * pd + pf)


def _matrix(obj):
    fm = raw.FS_MATRIX()
    if not raw.FPDFPageObj_GetMatrix(obj, fm):
        return _IDENTITY
    return (fm.a, fm.b, fm.c, fm.d, fm.e, fm.f)


def _path_svg(obj, m, height: float) -> str | None:
    fillmode, stroke = ctypes.c_int(), ctypes.c_int()
    if not raw.FPDFPath_GetDrawMode(obj, fillmode, stroke):
        return None
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    fill = "none"
    if fillmode.value and raw.FPDFPageObj_GetFillColor(obj, r, g, b, a) and a.value:
        fill = _rgb(r.value, g.value, b.value)
    stroke_c = "none"
    sw = ctypes.c_float(1.0)
    if stroke.value and raw.FPDFPageObj_GetStrokeColor(obj, r, g, b, a) and a.value:
        stroke_c = _rgb(r.value, g.value, b.value)
        raw.FPDFPageObj_GetStrokeWidth(obj, sw)
    if fill in ("none", "rgb(255,255,255)") and stroke_c == "none":
        return None
    am, bm, cm, dm, em, fm_ = m
    px, py = ctypes.c_float(), ctypes.c_float()
    d: list[str] = []
    bez: list[str] = []
    for k in range(raw.FPDFPath_CountSegments(obj)):
        seg = raw.FPDFPath_GetPathSegment(obj, k)
        raw.FPDFPathSegment_GetPoint(seg, px, py)
        x = px.value * am + py.value * cm + em
        y = height - (px.value * bm + py.value * dm + fm_)
        kind = raw.FPDFPathSegment_GetType(seg)
        pt = f"{x:.1f} {y:.1f}"
        if kind == raw.FPDF_SEGMENT_MOVETO:
            d.append("M" + pt)
        elif kind == raw.FPDF_SEGMENT_BEZIERTO:
            bez.append(pt)
            if len(bez) == 3:
                d.append("C" + " ".join(bez))
                bez = []
        else:
            d.append("L" + pt)
        if raw.FPDFPathSegment_GetClose(seg):
            d.append("Z")
    if not d:
        return None
    scale = abs(am * dm - bm * cm) ** 0.5 or 1.0
    width = max(sw.value * scale, 0.1) if stroke_c != "none" else 0
    rule = ' fill-rule="evenodd"' if fillmode.value == raw.FPDF_FILLMODE_ALTERNATE else ""
    return f'<path d="{"".join(d)}" fill="{fill}"{rule} stroke="{stroke_c}" stroke-width="{width:.2f}"/>'


def _paths(objs, parent, height: float, out: list[str], depth: int = 0) -> None:
    for obj in objs:
        kind = raw.FPDFPageObj_GetType(obj)
        if kind == raw.FPDF_PAGEOBJ_PATH:
            s = _path_svg(obj, _mul(_matrix(obj), parent), height)
            if s:
                out.append(s)
        elif kind == raw.FPDF_PAGEOBJ_FORM and depth < _MAX_DEPTH:
            m = _mul(_matrix(obj), parent)
            n = raw.FPDFFormObj_CountObjects(obj)
            _paths((raw.FPDFFormObj_GetObject(obj, k) for k in range(n)), m, height, out, depth + 1)


def _page_svg(page) -> tuple[str, list[list[_Char]]]:
    w, h = page.get_size()
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w:.0f}pt" height="{h:.0f}pt" viewBox="0 0 {w:.1f} {h:.1f}">']
    n = raw.FPDFPage_CountObjects(page.raw)
    _paths((raw.FPDFPage_GetObject(page.raw, k) for k in range(n)), _IDENTITY, h, parts)
    lines = _lines(_chars(page, h))
    for row in lines:
        for word in _words(row):
            parts.append(_text_svg(word))
    parts.append("</svg>")
    return "".join(parts), lines


def _title(lines: list[list[_Char]]) -> str | None:
    for row in lines:
        s = re.sub(r"\s+", " ", "".join(c.ch for c in row)).strip()
        if _TITLE.search(s):
            return s[:200]
    return None


def convert_pdf(src: Path | str | bytes) -> Converted:
    """src は PDF のパスかバイト列。"""
    doc = pdfium.PdfDocument(src if isinstance(src, bytes) else str(src))
    try:
        pages: list[str] = []
        title = None
        for i in range(len(doc)):
            page = doc[i]
            try:
                svg, lines = _page_svg(page)
            finally:
                page.close()
            pages.append(svg)
            if i == 0:
                title = _title(lines)
    finally:
        doc.close()
    body = "\n".join(pages)
    out = (
        f'<!DOCTYPE html><html><head><meta charset="utf-8"><style>{STYLE}</style></head>'
        f"<body>\n{body}\n</body></html>"
    )
    return Converted(
        html=utf8_safe(out),
        pages=len(pages),
        title=utf8_safe(title) if title else None,
    )
