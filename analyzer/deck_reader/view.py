"""復元ビュー: run.py の結果 JSON（v2 以降の tables）から、ページごとにタイトル・要約・グラフ・表を組み直す。LLM を使わない。

    .venv/bin/python analyzer/deck_reader/view.py <結果 JSON> -o /tmp/view.pdf
    .venv/bin/python analyzer/deck_reader/view.py <結果 JSON> -o /tmp/view.pdf --publish onedrive:deck-reader/<フォルダ>

原本の PDF を画面に出さずに、読み取った数値だけで人が読める形にできるかを確かめる試し（KAB-344）。
- グラフは実績・予想・計画の列が 3 つ以上ある表だけ。内訳（of）のある行は積み上げ棒、内訳の無い金額の行（3 本まで）は棒、
  率（%・pts）の行は別の折れ線（軸は 1 本だけ。金額と率を 1 つのグラフに重ねない）。予想・計画の棒は斜線
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


def fmt(v, unit=""):
    if v is None:
        return ""
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


def bar_chart(cols, series, unit, title, total=None):
    """series: [(name, [値…])]。2 本以上で total（合計の行）があれば積み上げ、無ければ並べた棒。"""
    stacked = total is not None
    colors = [OTHER if n == "その他" else SERIES[i % len(SERIES)] for i, (n, _) in enumerate(series)]
    n = len(cols)
    band = (W - ML - MR) / n
    vals = [v for _, vs in series for v in vs if v is not None]
    if stacked:
        pos = [sum(max(vs[i] or 0, 0) for _, vs in series) for i in range(n)]
        neg = [sum(min(vs[i] or 0, 0) for _, vs in series) for i in range(n)]
        tot = [v for v in total if v is not None]
        hi, lo = max(pos + tot + [0]), min(neg + tot + [0])
    else:
        hi, lo = max(vals + [0]), min(vals + [0])
    ticks = nice_ticks(lo, hi)
    lo, hi = min(ticks), max(ticks)
    y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)  # noqa: E731
    marks = []
    if stacked:
        bw = min(24, band * 0.55)
        for i, c in enumerate(cols):
            x = ML + band * (i + 0.5) - bw / 2
            up = down = 0.0
            segs = []
            for k, (name, vs) in enumerate(series):
                v = vs[i]
                if not v:
                    continue
                base = up if v > 0 else down
                top = base + v
                segs.append((k, name, v, base, top))
                if v > 0:
                    up = top
                else:
                    down = top
            for j, (k, name, v, base, top) in enumerate(segs):
                outer = (v > 0 and top == up) or (v < 0 and top == down)
                y0, y1 = y(base), y(top)
                gap = 1 if abs(y1 - y0) > 3 else 0  # 2px のすき間（上下 1px ずつ）
                y0 += -gap if v > 0 else gap
                y1 += gap if v > 0 else -gap
                path = bar_path(x, bw, y0, y1, 4 if outer else 0)
                marks.append(f'<path d="{path}" fill="{fill_for(k, colors[k], c["kind"])}">'
                             f'<title>{esc(c["period"])} {esc(name)} {esc(fmt(v, unit))}</title></path>')
            t = total[i] if total[i] is not None else up + down
            if i == n - 1 or t == max(tv for tv in total if tv is not None):
                ty = y(max(t, up)) - 6
                marks.append(f'<text x="{x + bw / 2:.1f}" y="{ty:.1f}" class="val" text-anchor="middle">{esc(fmt(t, unit))}</text>')
    else:
        k_n = len(series)
        bw = min(24, band * 0.7 / k_n)
        for i, c in enumerate(cols):
            x0 = ML + band * (i + 0.5) - bw * k_n / 2 - (k_n - 1)
            for k, (name, vs) in enumerate(series):
                v = vs[i]
                if v is None:
                    continue
                x = x0 + k * (bw + 2)
                marks.append(f'<path d="{bar_path(x, bw, y(0), y(v))}" fill="{fill_for(k, colors[k], c["kind"])}">'
                             f'<title>{esc(c["period"])} {esc(name)} {esc(fmt(v, unit))}</title></path>')
                if i == n - 1 and k_n == 1:
                    ty = y(v) - 6 if v >= 0 else y(v) + 14
                    marks.append(f'<text x="{x + bw / 2:.1f}" y="{ty:.1f}" class="val" text-anchor="middle">{esc(fmt(v, unit))}</text>')
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}"><defs>{patterns(colors)}</defs>'
           f'{frame(ticks, y, unit)}{"".join(marks)}{x_labels(cols, band)}</svg>')
    names = [nm for nm, _ in series]
    fc = any(c["kind"] in ("forecast", "plan") for c in cols)
    note = '<span class="note">斜線は予想・計画</span>' if fc else ""
    return (f'<figure class="chart"><figcaption>{esc(title)}<span class="unit">（{esc(unit)}）</span>{note}</figcaption>'
            f'{legend(names, colors)}{svg}</figure>')


def line_chart(cols, series, unit, title):
    colors = [SERIES[i % len(SERIES)] for i in range(len(series))]
    n = len(cols)
    band = (W - ML - MR) / n
    vals = [v for _, vs in series for v in vs if v is not None]
    ticks = nice_ticks(min(vals + [0]), max(vals))
    lo, hi = min(ticks), max(ticks)
    y = lambda v: MT + (hi - v) / (hi - lo) * (H - MT - MB)  # noqa: E731
    marks = []
    for k, (name, vs) in enumerate(series):
        pts = [(ML + band * (i + 0.5), y(v), v, cols[i]) for i, v in enumerate(vs) if v is not None]
        if len(pts) >= 2:
            dpath = "M" + "L".join(f"{px:.1f},{py:.1f}" for px, py, _, _ in pts)
            marks.append(f'<path d="{dpath}" fill="none" stroke="{colors[k]}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>')
        for px, py, v, c in pts:
            hollow = c["kind"] in ("forecast", "plan")
            marks.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="{"#fcfcfb" if hollow else colors[k]}" '
                         f'stroke="{colors[k] if hollow else "#fcfcfb"}" stroke-width="2">'
                         f'<title>{esc(c["period"])} {esc(name)} {esc(fmt(v, unit))}{esc(unit)}</title></circle>')
        if pts:
            px, py, v, _ = pts[-1]
            marks.append(f'<text x="{px:.1f}" y="{py - 9:.1f}" class="val" text-anchor="middle">{esc(fmt(v, unit))}</text>')
    svg = (f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">'
           f'{frame(ticks, y, unit)}{"".join(marks)}{x_labels(cols, band)}</svg>')
    fc = any(c["kind"] in ("forecast", "plan") for c in cols)
    note = '<span class="note">白抜きは予想・計画</span>' if fc else ""
    return (f'<figure class="chart"><figcaption>{esc(title)}<span class="unit">（{esc(unit)}）</span>{note}</figcaption>'
            f'{legend([nm for nm, _ in series], colors, "line")}{svg}</figure>')


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
        out.append(bar_chart(scols, kids, unit, f"{row_label(r)}の内訳", total=vals))
        used.add(key)
        used.update(row_key(c) for c, _ in data if c.get("of") and (c["of"] if "|" in c["of"] else c["of"] + "|null") == key)
        if len(out) >= 3:
            return out
    # 内訳に使っていない行を、単位と指標の根（/ の前）ごとに棒（金額）・折れ線（率）。
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
        if unit in RATE_UNITS:
            out.append(line_chart(scols, series, unit, title))
        elif len(items) <= 3:
            out.append(bar_chart(scols, series, unit, title))
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
            v = num(vals[i]) if i < len(vals) else None
            s = fmt(v, unit)
            if v is not None and c.get("kind") == "change" and v > 0:
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
.val{fill:var(--ink);font-size:11px;font-weight:600}
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
