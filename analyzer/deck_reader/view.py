"""復元ビュー: run.py の結果 JSON（v2 以降の tables）から、ページごとにタイトル・要約・グラフ・表を組み直す。LLM を使わない。

    .venv/bin/python analyzer/deck_reader/view.py <結果 JSON> -o /tmp/view.pdf
    .venv/bin/python analyzer/deck_reader/view.py <結果 JSON> -o /tmp/view.pdf --publish onedrive:deck-reader/<フォルダ>

原本の PDF を画面に出さずに、読み取った数値だけで人が読める形にできるかを確かめる試し（KAB-344）。
- グラフは実績・予想・計画の列が 3 つ以上ある表だけ。値はすべての棒・点に付ける。内訳（of）のある行は積み上げ棒（マイナスが混ざれば並べた棒）、内訳の無い金額の行（3 本まで）は棒、
  率（%・pts）の行は別の折れ線（軸は 1 本だけ。金額と率を 1 つのグラフに重ねない）。為替の行（指標名に「為替」。単位が円でも）は通貨ごとに折れ線。
  予想・計画の棒は斜線、率の折れ線は白抜き、為替の折れ線は破線と白抜き
- 表は必ず出す（グラフに出ない列・行もすべて表にある）。数字の無いページは要約だけ
- SVG を直接書く（外部のライブラリも通信も無いので PDF にそのまま入る）。--format html なら <title> でホバー表示
"""
import argparse
import html
import json
import math
import pathlib

import extract as d
from report import publish, write_pdf

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
OTHER = "#a3a29c"
RATE_UNITS = ("%", "pts", "pt", "ポイント")
SERIES_KINDS = ("actual", "forecast", "plan")
KIND_LABEL = {"actual": "実績", "forecast": "予想", "previous_forecast": "前回予想", "plan": "計画", "change": "増減"}
W, H, ML, MR, MT, MB = 720, 250, 60, 16, 16, 46


def esc(s):
    return html.escape("" if s is None else str(s))


def num(v):
    return None if v in (None, "", "null") else d.parse_number(v)


def row_key(r):
    seg = r.get("segment")
    return f"{r.get('metric')}|{seg if seg not in (None, 'null') else 'null'}"


def row_label(r):
    seg = r.get("segment")
    metric = str(r.get("metric") or "")
    return f"{metric}（{seg}）" if seg not in (None, "null", "") else metric


def kid_label(c, parent):
    """積み上げの凡例の名前。親と違う区分ならその区分、同じなら指標名の最後の段。"""
    seg, pseg = c.get("segment"), parent.get("segment")
    if seg not in (None, "null", "") and seg != pseg:
        return str(seg)
    return str(c.get("metric") or "").split("/")[-1]


def fmt(v, unit="", dec=None):
    """dec があればその桁で（元の表の書き方に合わせる。17.0 を 17 にしない）。"""
    if v is None:
        return ""
    if dec is not None:
        return f"{v:,.{dec}f}".replace("-", "−")
    if unit in RATE_UNITS or abs(v) < 100:
        s = f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"
    else:
        s = f"{v:,.0f}" if v == int(v) or abs(v) >= 1000 else f"{v:,.1f}"
    return s.replace("-", "−")


def nice_ticks(lo, hi, n=4):
    if hi == lo:
        hi = lo + 1
    step = 10 ** math.floor(math.log10((hi - lo) / n))
    for m in (1, 2, 2.5, 5, 10):
        if (hi - lo) / (step * m) <= n:
            step *= m
            break
    start = math.floor(lo / step) * step
    ticks, t = [], start
    while not ticks or ticks[-1] < hi:  # 一番上の目盛りは必ず最大値以上（棒が枠をはみ出さない）
        ticks.append(round(t, 10))
        t += step
    return ticks


def short_period(p):
    s = str(p or "").replace("（", "(").replace("）", ")")
    return s if len(s) <= 12 else s[:12] + "…"


def x_labels(cols, band):
    out = []
    for i, c in enumerate(cols):
        x = ML + band * (i + 0.5)
        lines = [short_period(c["period"])]
        if c.get("kind") != "actual":
            lines.append(KIND_LABEL.get(c.get("kind"), ""))
        for j, t in enumerate(lines):
            out.append(f'<text x="{x:.1f}" y="{H - MB + 16 + j * 13}" class="ax" text-anchor="middle">{esc(t)}</text>')
    return "".join(out)


def frame(ticks, y, unit):
    g = []
    for t in ticks:
        yy = y(t)
        g.append(f'<line x1="{ML}" x2="{W - MR}" y1="{yy:.1f}" y2="{yy:.1f}" class="grid{" zero" if t == 0 else ""}"/>')
        g.append(f'<text x="{ML - 6}" y="{yy + 4:.1f}" class="ax" text-anchor="end">{esc(fmt(t, unit))}</text>')
    return "".join(g)


def bar_path(x, w, y0, y1, r=4):
    """y0 が基準線、y1 が先端。先端だけ角を丸める。"""
    h = abs(y1 - y0)
    r = min(r, h / 2, w / 2)
    if y1 <= y0:  # 上向き
        return (f"M{x:.1f},{y0:.1f}V{y1 + r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}"
                f"H{x + w - r:.1f}Q{x + w:.1f},{y1:.1f} {x + w:.1f},{y1 + r:.1f}V{y0:.1f}Z")
    return (f"M{x:.1f},{y0:.1f}V{y1 - r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}"
            f"H{x + w - r:.1f}Q{x + w:.1f},{y1:.1f} {x + w:.1f},{y1 - r:.1f}V{y0:.1f}Z")


def patterns(colors):
    return "".join(
        f'<pattern id="h{i}" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">'
        f'<rect width="6" height="6" fill="{c}" opacity="0.18"/><rect width="2.5" height="6" fill="{c}"/></pattern>'
        for i, c in enumerate(colors))


def fill_for(i, color, kind):
    return f"url(#h{i})" if kind in ("forecast", "plan") else color


def legend(names, colors, shape="box"):
    if len(names) < 2:
        return ""
    items = "".join(
        f'<span class="lg"><i class="sw {shape}" style="background:{c}"></i>{esc(n)}</span>' for n, c in zip(names, colors))
    return f'<div class="legend">{items}</div>'


def text_w(s, size):
    """数字の文字の幅のおおよそ（px）。"""
    return sum(0.3 if ch in ",.:" else 0.58 for ch in s) * size


def ink_on(color):
    """塗りの上に置く文字の色（明るい塗りには黒、暗い塗りには白）。"""
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return "#0b0b0b" if 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.55 else "#ffffff"


def bar_label(cx, slot, y_tip, v, unit, up, dec=None):
    """棒の先の値。横に収まらなければ縦書き。"""
    s = fmt(v, unit, dec)
    if text_w(s, 10) <= slot:
        ty = y_tip - 5 if up else y_tip + 13
        return f'<text x="{cx:.1f}" y="{ty:.1f}" class="val" text-anchor="middle">{esc(s)}</text>'
    ty = y_tip - 4 if up else y_tip + 4
    anchor = "start" if up else "end"
    return (f'<text x="{cx + 3.5:.1f}" y="{ty:.1f}" class="val" text-anchor="{anchor}" '
            f'transform="rotate(-90 {cx + 3.5:.1f} {ty:.1f})">{esc(s)}</text>')


def scale(lo, hi, head):
    """値の範囲に、値ラベルの分の余白を足して目盛りを作る。"""
    span = (hi - lo) or abs(hi) or 1
    ticks = nice_ticks(lo - (span * head if lo < 0 else 0), hi + (span * head if hi > 0 else 0))
    lo, hi = min(ticks), max(ticks)
    return ticks, (lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB))


def bar_chart(cols, series, unit, title, total=None, total_name="合計", dec=None):
    """series: [(name, [値…])]。total（合計の行）があり、内訳がすべてプラスなら積み上げ。
    マイナスが混ざる内訳（キャッシュ・フローなど）は積み上げず、内訳と合計を並べた棒にする。値はすべての棒に付ける。"""
    if total is not None and any(v is not None and v < 0 for _, vs in series for v in vs):
        series, total = series + [(total_name, total)], None
    stacked = total is not None
    colors = [OTHER if nm == "その他" else SERIES[i % len(SERIES)] for i, (nm, _) in enumerate(series)]
    n = len(cols)
    band = (W - ML - MR) / n
    marks = []
    if stacked:
        pos = [sum(max(vs[i] or 0, 0) for _, vs in series) for i in range(n)]
        tot = [v for v in total if v is not None]
        ticks, y = scale(0, max(pos + tot + [0]), 0.1)
        bw = min(48 if n <= 5 else 28, band * 0.5)  # 列が少なければ太くして、値を棒の中に書ける区分を増やす
        for i, c in enumerate(cols):
            x = ML + band * (i + 0.5) - bw / 2
            up = 0.0
            segs = []
            for k, (name, vs) in enumerate(series):
                v = vs[i]
                if v:
                    segs.append((k, name, v, up, up + v))
                    up += v
            side = []
            for k, name, v, base, top in segs:
                y0, y1 = y(base), y(top)
                gap = 1 if y0 - y1 > 3 else 0  # 2px のすき間（上下 1px ずつ）
                fill = fill_for(k, colors[k], c["kind"])
                marks.append(f'<path d="{bar_path(x, bw, y0 - gap, y1 + gap, 4 if top == up else 0)}" fill="{fill}">'
                             f'<title>{esc(c["period"])} {esc(name)} {esc(fmt(v, unit))}</title></path>')
                s = fmt(v, unit, dec)
                ym = (y0 + y1) / 2 + 3.2
                if y0 - y1 >= 13 and text_w(s, 9) <= bw - 4 and c["kind"] == "actual":
                    marks.append(f'<text x="{x + bw / 2:.1f}" y="{ym:.1f}" class="seg" fill="{ink_on(colors[k])}" '
                                 f'text-anchor="middle">{esc(s)}</text>')
                else:  # 棒の中に収まらない区分は右横に（隣の列の棒にかからない幅だけ）
                    side.append((ym, s, x + bw + 2, (band - bw) / 2 - 2))
            t = total[i] if total[i] is not None else (up if segs else None)
            top_label = y(max(t if t is not None else up, up)) - 5  # 合計のラベルの位置（右横の値はこれより下に置く）
            last = None
            for ym, s, sx, room in side:  # 下から順。重なる分は 1 段ずらし、ずらせなければ省く（表にある）
                ty = ym if last is None else max(min(ym, last - 10), MT + 8)
                if text_w(s, 9) <= room and (last is None or last - ty >= 10) and ty - top_label >= 10:
                    marks.append(f'<text x="{sx:.1f}" y="{ty:.1f}" class="seg side" text-anchor="start">{esc(s)}</text>')
                    last = ty
            if t is not None:
                marks.append(bar_label(x + bw / 2, band - 4, y(max(t, up)), t, unit, True, dec))
    else:
        vals = [v for _, vs in series for v in vs if v is not None]
        ticks, y = scale(min(vals + [0]), max(vals + [0]), 0.14)
        k_n = len(series)
        bw = min(24, band * 0.7 / k_n)
        for i, c in enumerate(cols):
            x0 = ML + band * (i + 0.5) - (bw * k_n + 2 * (k_n - 1)) / 2
            for k, (name, vs) in enumerate(series):
                v = vs[i]
                if v is None:
                    continue
                x = x0 + k * (bw + 2)
                marks.append(f'<path d="{bar_path(x, bw, y(0), y(v))}" fill="{fill_for(k, colors[k], c["kind"])}">'
                             f'<title>{esc(c["period"])} {esc(name)} {esc(fmt(v, unit))}</title></path>')
                marks.append(bar_label(x + bw / 2, bw + 2 if k_n > 1 else band - 4, y(v), v, unit, v >= 0, dec))
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}"><defs>{patterns(colors)}</defs>'
           f'{frame(ticks, y, unit)}{"".join(marks)}{x_labels(cols, band)}</svg>')
    names = [nm for nm, _ in series]
    fc = any(c["kind"] in ("forecast", "plan") for c in cols)
    note = '<span class="note">斜線は予想・計画</span>' if fc else ""
    return (f'<figure class="chart"><figcaption>{esc(title)}<span class="unit">（{esc(unit)}）</span>{note}</figcaption>'
            f'{legend(names, colors)}{svg}</figure>')


def _line_paths(pts, color, fx):
    """折れ線の path。fx のとき、右の列が予想・計画の区間だけ破線。"""
    if len(pts) < 2:
        return []
    if not fx:
        dpath = "M" + "L".join(f"{px:.1f},{py:.1f}" for px, py, _, _ in pts)
        return [f'<path d="{dpath}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>']
    parts, run, dash = [], "", None

    def flush():
        nonlocal run
        if not run:
            return
        cap = "butt" if dash else "round"
        extra = ' stroke-dasharray="6 4"' if dash else ""
        parts.append(f'<path d="{run}" fill="none" stroke="{color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="{cap}"{extra}/>')
        run = ""

    for i in range(1, len(pts)):
        seg = pts[i][3].get("kind") in ("forecast", "plan")
        bit = f"{pts[i][0]:.1f},{pts[i][1]:.1f}"
        if run and dash == seg:
            run += "L" + bit
        else:
            flush()
            dash = seg
            run = f"M{pts[i - 1][0]:.1f},{pts[i - 1][1]:.1f}L{bit}"
    flush()
    return parts


def line_chart(cols, series, unit, title, dec=None, fx=False):
    """折れ線。値はすべての点に付ける。同じ期で重なるときは上下に振り分け、置けなければ省く（表にある）。
    fx のとき予想・計画へ入る区間は破線で点は白抜き。縦軸は 0 を起点にしない。"""
    colors = [SERIES[i % len(SERIES)] for i in range(len(series))]
    n = len(cols)
    band = (W - ML - MR) / n
    vals = [v for _, vs in series for v in vs if v is not None]
    if fx:
        span = (max(vals) - min(vals)) or abs(max(vals)) or 1
        ticks, y = scale(min(vals) - span * 0.18, max(vals) + span * 0.22, 0)
    else:
        ticks, y = scale(min(vals + [0]), max(vals), 0.12)
    marks, labels = [], []
    for k, (name, vs) in enumerate(series):
        pts = [(ML + band * (i + 0.5), y(v), v, cols[i]) for i, v in enumerate(vs) if v is not None]
        marks.extend(_line_paths(pts, colors[k], fx))
        for px, py, v, c in pts:
            hollow = c["kind"] in ("forecast", "plan")
            marks.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="{"#fcfcfb" if hollow else colors[k]}" '
                         f'stroke="{colors[k] if hollow else "#fcfcfb"}" stroke-width="2">'
                         f'<title>{esc(c["period"])} {esc(name)} {esc(fmt(v, unit))}{esc(unit)}</title></circle>')
    for i in range(n):
        pts = sorted((y(vs[i]), vs[i]) for _, vs in series if vs[i] is not None)
        taken = []  # 置いた値ラベルの y の範囲
        for j, (py, v) in enumerate(pts):
            for ty in ((py - 9, py + 17) if j == 0 else (py + 17, py - 9)):
                if MT <= ty - 10 and ty <= H - MB and all(abs(ty - t) >= 12 for t in taken) \
                        and all(abs(ty - 3 - q) >= 8 for q, _ in pts):
                    taken.append(ty)
                    labels.append(f'<text x="{ML + band * (i + 0.5):.1f}" y="{ty:.1f}" class="val" text-anchor="middle">'
                                  f'{esc(fmt(v, unit, dec))}</text>')
                    break
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">'
           f'{frame(ticks, y, unit)}{"".join(marks)}{"".join(labels)}{x_labels(cols, band)}</svg>')
    fc = any(c["kind"] in ("forecast", "plan") for c in cols)
    if not fc:
        note = ""
    elif fx:
        note = '<span class="note">破線・白抜きは予想・計画</span>'
    else:
        note = '<span class="note">白抜きは予想・計画</span>'
    return (f'<figure class="chart"><figcaption>{esc(title)}<span class="unit">（{esc(unit)}）</span>{note}</figcaption>'
            f'{legend([nm for nm, _ in series], colors, "line")}{svg}</figure>')


def decimals(rows):
    """行の値の表記の小数の桁（いちばん多いもの）。"""
    return max((d._decimals(str(v)) for r in rows for v in (r.get("values") or []) if v not in (None, "")), default=0)


def charts_for(table):
    """表からグラフを組み立てる（無ければ空）。"""
    cols = table.get("cols") or []
    rows = table.get("rows") or []
    idx = [i for i, c in enumerate(cols) if c.get("kind") in SERIES_KINDS]
    if len(idx) < 3:
        return []
    scols = [cols[i] for i in idx]
    data = []
    for r in rows:
        vals = [num((r.get("values") or [None] * len(cols))[i]) if i < len(r.get("values") or []) else None for i in idx]
        if sum(v is not None for v in vals) >= 2:
            data.append((r, vals))
    out, used = [], set()
    # 内訳（of）のある親 → 積み上げ棒
    by_key = {row_key(r): (r, v) for r, v in data}
    for key, (r, vals) in by_key.items():
        kids = [(kid_label(c, r), v) for c, v in data if c.get("of") and (c["of"] if "|" in c["of"] else c["of"] + "|null") == key]
        unit = str(r.get("unit") or "")
        if len(kids) < 2 or unit in RATE_UNITS:
            continue
        if len(kids) > 8:
            latest = lambda kv: abs(next((x for x in reversed(kv[1]) if x is not None), 0))  # noqa: E731
            kids.sort(key=latest, reverse=True)
            rest = kids[7:]
            other = [sum(v[i] or 0 for _, v in rest) for i in range(len(scols))]
            kids = kids[:7] + [("その他", other)]
        out.append(bar_chart(scols, kids, unit, f"{row_label(r)}の内訳", total=vals, total_name=row_label(r),
                             dec=decimals([r] + [c for c, _ in data if c.get("of") and (c["of"] if "|" in c["of"] else c["of"] + "|null") == key])))
        used.add(key)
        used.update(row_key(c) for c, _ in data if c.get("of") and (c["of"] if "|" in c["of"] else c["of"] + "|null") == key)
        if len(out) >= 3:
            return out
    # 内訳に使っていない行を、単位と指標の根（/ の前）ごとに棒（金額）・折れ線（率・為替）。
    # 根が 1 行ずつのものは単位ごとに 3 本までまとめる（売上高と営業利益、各利益率など）。円（為替・1 株当たり）はまとめない
    rest = [(r, v) for r, v in data if row_key(r) not in used and not r.get("of")]
    groups = {}
    for r, v in rest:
        groups.setdefault((str(r.get("unit") or ""), str(r.get("metric") or "").split("/")[0]), []).append((r, v))
    merged = {}
    for (unit, root), items in groups.items():
        if len(items) == 1 and unit != "円":
            merged.setdefault((unit, "*"), []).extend(items)
        else:
            merged[(unit, root)] = items
    for (unit, _), items in merged.items():
        if len(items) > 4:
            continue
        series = [(row_label(r), v) for r, v in items]
        title = "・".join(row_label(r) for r, _ in items)
        dec = decimals([r for r, _ in items])
        fx = all("為替" in str(r.get("metric") or "") for r, _ in items)
        if unit in RATE_UNITS or fx:
            out.append(line_chart(scols, series, unit, title, dec, fx=fx))
        elif len(items) <= 3:
            out.append(bar_chart(scols, series, unit, title, dec=dec))
        if len(out) >= 3:
            break
    return out


def table_html(table):
    cols = table.get("cols") or []
    head = "".join(f'<th>{esc(c.get("period"))}<span class="k k-{esc(c.get("kind"))}">{esc(KIND_LABEL.get(c.get("kind"), c.get("kind")))}</span></th>'
                   for c in cols)
    body = []
    for r in table.get("rows") or []:
        vals = r.get("values") or []
        unit = str(r.get("unit") or "")
        cells = []
        for i, c in enumerate(cols):
            raw = vals[i] if i < len(vals) else None
            v = num(raw)
            s = "" if v is None else d.norm_text(str(raw)).strip().replace("-", "−")  # 表は資料の表記のまま（丸めない）
            if v is not None and c.get("kind") == "change" and v > 0 and not s.startswith("+"):
                s = "+" + s
            cells.append(f"<td class=\"n\">{esc(s)}</td>")
        depth = 1 if r.get("of") else 0
        body.append(f'<tr><th class="r d{depth}">{esc(row_label(r))}</th><td class="u">{esc(unit)}</td>{"".join(cells)}</tr>')
    return (f'<div class="tw"><table><thead><tr><th></th><th class="u">単位</th>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def render(res, source):
    deck = res.get("deck") or {}
    secs = []
    for p in res["pages"]:
        if "facts" not in p and not p.get("summary"):
            continue
        tables = p.get("tables") or []
        parts = []
        for t in tables:
            parts.extend(charts_for(t))
            parts.append(table_html(t))
        summary = f'<p class="sum">{esc(p.get("summary"))}</p>' if p.get("summary") else ""
        secs.append(f'<section class="pg"><header><span class="pno">p{p["page"]}</span><h2>{esc(p.get("title"))}</h2></header>'
                    f'{summary}{"".join(parts)}</section>')
    return TEMPLATE.format(css=CSS, title=esc(deck.get("name") or deck.get("id") or source), source=esc(source),
                           model=esc(res.get("model")), prompt=esc(res.get("prompt_version")), body="".join(secs))


CSS = """
:root{--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--line:#e4e3df;--sunk:#f3f2ef}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);font-family:"Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;font-size:13px;line-height:1.6}
.wrap{max-width:760px;margin:0 auto;padding:16px}
h1{font-size:18px;margin:0 0 4px}
.meta{color:var(--ink2);font-size:12px;margin-bottom:12px}
.pg{border-top:1px solid var(--line);padding:14px 0 18px;break-before:page}
.pg:first-of-type{break-before:auto}
.pg header{display:flex;gap:10px;align-items:baseline}
.pno{color:var(--muted);font-variant-numeric:tabular-nums;font-size:12px}
h2{font-size:16px;margin:0}
.sum{margin:6px 0 10px;padding:8px 10px;background:var(--sunk);border-radius:4px}
.chart{margin:10px 0 4px;break-inside:avoid}
figcaption{font-weight:600;font-size:13px}
.unit{color:var(--ink2);font-weight:400;font-size:12px}
.note{color:var(--muted);font-weight:400;font-size:11px;margin-left:8px}
.legend{display:flex;flex-wrap:wrap;gap:4px 12px;margin:4px 0 0;color:var(--ink2);font-size:12px}
.lg{display:inline-flex;align-items:center;gap:5px}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px}
.sw.line{height:3px;width:14px;border-radius:2px}
svg{width:100%;height:auto;display:block}
.grid{stroke:var(--line);stroke-width:1}
.grid.zero{stroke:#c9c8c2}
.ax{fill:var(--ink2);font-size:11px}
.val{fill:var(--ink);font-size:10px;font-weight:600}
.seg{font-size:9px;font-weight:600}
.seg.side{fill:var(--ink2);font-weight:400}
.tw{overflow-x:auto;margin:6px 0 10px}
table{border-collapse:collapse;font-size:11px;font-variant-numeric:tabular-nums;width:100%}
th,td{border-bottom:1px solid var(--line);padding:3px 6px;vertical-align:bottom}
thead th{color:var(--ink2);font-weight:600;text-align:right;white-space:normal;min-width:52px}
.k{display:block;color:var(--muted);font-weight:400;font-size:10px}
th.r{text-align:left;font-weight:600;min-width:120px}
th.r.d1{font-weight:400;padding-left:16px}
td.u{color:var(--muted);white-space:nowrap}
td.n{text-align:right;white-space:nowrap}
@media print{.wrap{max-width:none;padding:0}.tw{overflow:visible}}
"""

TEMPLATE = """<!doctype html><meta charset="utf-8"><title>{title} 復元ビュー</title><style>{css}</style>
<div class="wrap"><h1>{title}</h1><div class="meta">{source} ・ 読み取った数値と要約から組み直したもの（原本ではない） ・ {model} / {prompt}</div>
{body}</div>"""


def build(path, out, fmt="pdf"):
    res = json.loads(pathlib.Path(path).read_text())
    if not any(p.get("tables") for p in res["pages"]):
        raise SystemExit(f"{path} に tables が無い（v2 以降で、KAB-344 より後に読んだ結果が要る）")
    src = (res.get("deck") or {}).get("source") or pathlib.Path(path).name
    text = render(res, pathlib.Path(src).name)
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "pdf":
        write_pdf(text, out)
    else:
        out.write_text(text)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="run.py の結果 JSON")
    ap.add_argument("-o", "--out", required=True, help="出力（.pdf か .html）")
    ap.add_argument("--publish", metavar="REMOTE", help="出力を rclone で上げる（例: onedrive:deck-reader/view）")
    a = ap.parse_args()
    out = build(a.result, a.out, "pdf" if a.out.lower().endswith(".pdf") else "html")
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")
    if a.publish:
        publish([out], a.publish)


if __name__ == "__main__":
    main()
