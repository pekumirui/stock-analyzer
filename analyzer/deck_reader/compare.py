"""2 つの読み取り結果をページごとに並べて比べる HTML を作る。LLM も GPU も使わない。

    R=/workspace/deck-reader
    .venv/bin/python analyzer/deck_reader/compare.py --root $R \
        --pages lasertec_2026fy:7,9 toyota_2027q1:11,19 \
        --a "Haiku=$R/results/{deck}_haiku.json" --b "Sonnet=$R/results/{deck}_sonnet.json" \
        --notes notes.json -o /tmp/compare.html

- 結果ファイルは run.py の出力 (1 冊分, "pages" を持つ) でも、1 ページ分の生 JSON (title/summary/facts) でもよい。
  1 ページ分の場合はここで PDF のテキスト層と照合 (verified) し直す
- 元 PDF は <root>/pdf/{deck}.pdf を使う
- --notes で人が画像と照合した結果 {"deck:page": {"a": "...", "b": "..."}} を添えられる
"""
import argparse
import json
import pathlib

import extract as d
from fetch import DEFAULT_ROOT, pdf_path
from report import CSS, esc, page_images, render_facts


def load_side(template, deck, page, text):
    """1 ページ分の結果 {title, summary, facts, error?} を返す。ファイルが無ければ None。"""
    path = pathlib.Path(template.format(deck=deck, page=page))
    if not path.exists():
        return None
    obj = json.loads(path.read_text())
    if "pages" in obj:  # app.py の 1 冊分の出力
        return next((p for p in obj["pages"] if p["page"] == page), None)
    try:  # 1 ページ分の生 JSON
        return d.verify({"text": text}, obj)
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def side_stats(r):
    if r is None:
        return {"state": "none", "facts": 0, "bad": 0}
    if r.get("error"):
        return {"state": "error", "facts": 0, "bad": 0}
    fs = r.get("facts", [])
    bad = sum(1 for f in fs if not f.get("verified"))
    return {"state": "warn" if bad else "ok", "facts": len(fs), "bad": bad}


def render_side(label, r, note):
    st = side_stats(r)
    head = f'<div class="side-head"><b>{esc(label)}</b>'
    if st["state"] == "error":
        head += '<span class="status status-error">JSON エラー</span>'
    elif st["state"] == "none":
        head += '<span class="status status-skip">結果なし</span>'
    else:
        head += (f'<span class="status status-{st["state"]}">facts {st["facts"]} / 未照合 {st["bad"]}</span>')
    head += "</div>"
    body = []
    if note:
        body.append(f'<p class="verdict">{esc(note)}</p>')
    if r and r.get("title"):
        body.append(f'<p class="side-title">{esc(r["title"])}</p>')
    if r and r.get("summary"):
        body.append(f'<p class="summary">{esc(r["summary"])}</p>')
    if r and r.get("error"):
        body.append(f'<div class="error"><b>{esc(r["error"])}</b>'
                    + (f'<details><summary>生出力 (先頭 2000 文字)</summary><pre>{esc(r.get("raw"))}</pre></details>'
                       if r.get("raw") else "") + "</div>")
    elif r:
        body.append(render_facts(r.get("facts", [])))
    return f'<div class="side">{head}{"".join(body)}</div>'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pages", nargs="+", required=True, help="deck:1,2,3 の形で複数")
    ap.add_argument("--a", required=True, help="ラベル=パス。{deck} {page} を置換")
    ap.add_argument("--b", required=True)
    ap.add_argument("--notes", help="画像照合のメモ JSON")
    ap.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    ap.add_argument("-o", "--out", default="compare.html")
    a = ap.parse_args()
    la, ta = a.a.split("=", 1)
    lb, tb = a.b.split("=", 1)
    notes = json.loads(pathlib.Path(a.notes).read_text()) if a.notes else {}

    rows, sections, tot = [], [], {"a": [0, 0, 0], "b": [0, 0, 0]}
    for spec in a.pages:
        deck, nums = spec.split(":")
        pdf = pdf_path(a.root, deck)
        pages, _ = d.load_pages(pdf.read_bytes(), render=False)
        imgs = page_images(str(pdf), [int(n) for n in nums.split(",")])
        for n in map(int, nums.split(",")):
            p = next(x for x in pages if x["page"] == n)
            ra, rb = load_side(ta, deck, n, p["text"]), load_side(tb, deck, n, p["text"])
            sa, sb = side_stats(ra), side_stats(rb)
            for k, s in (("a", sa), ("b", sb)):
                tot[k][0] += s["facts"]; tot[k][1] += s["bad"]; tot[k][2] += s["state"] == "error"
            key = f"{deck}:{n}"
            nt = notes.get(key, {})
            anchor = f"{deck}-p{n}".replace("_", "-")
            title = (rb or {}).get("title") or (ra or {}).get("title") or ""

            def cell(s):
                if s["state"] == "error":
                    return '<span class="status status-error">JSON エラー</span>'
                if s["state"] == "none":
                    return "-"
                return f'<span class="status status-{s["state"]}">{s["facts"]} / {s["bad"]}</span>'
            rows.append(f'<tr><td><a href="#{anchor}">{esc(deck)} p{n}</a></td><td>{esc(title)}</td>'
                        f'<td>{cell(sa)}</td><td>{cell(sb)}</td><td class="vnote">{esc(nt.get("summary", ""))}</td></tr>')
            sections.append(
                f'<section class="page" id="{anchor}"><header class="page-head"><span class="pno">p{n}</span>'
                f'<span class="kind">{esc(deck)}</span><h2>{esc(title)}</h2></header>'
                f'<figure class="slide-wide"><img src="{imgs.get(n, "")}" alt="{esc(deck)} p{n}" loading="lazy"></figure>'
                f'<div class="sides">{render_side(la, ra, nt.get("a"))}{render_side(lb, rb, nt.get("b"))}</div></section>'
            )

    def tile(label, t):
        return (f'<div class="tile"><span class="t-label">{esc(label)}</span>'
                f'<span class="t-val">{t[2]}</span><span class="t-sub">JSON エラーのページ / facts {t[0]}・未照合 {t[1]}</span></div>')
    html = TEMPLATE.format(
        css=CSS + EXTRA_CSS, la=esc(la), lb=esc(lb), tiles=tile(la, tot["a"]) + tile(lb, tot["b"]),
        rows="".join(rows), sections="".join(sections), lead=esc(notes.get("_lead", "")),
    )
    out = pathlib.Path(a.out)
    out.write_text(html)
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")


EXTRA_CSS = """
.lead{margin:0;max-width:75ch;color:var(--fg)}
.overview{overflow-x:auto;border:1px solid var(--line);border-radius:6px;background:var(--surface)}
.overview table{border-collapse:collapse;width:100%;font-size:13px}
.overview th{text-align:left;font-size:12px;color:var(--muted);background:var(--sunk);padding:6px 10px;white-space:nowrap}
.overview td{padding:6px 10px;border-top:1px solid var(--line);vertical-align:top}
.overview td a{color:var(--accent);font-family:var(--f-num);white-space:nowrap}
.vnote{color:var(--fg);min-width:220px}
.slide-wide{margin:0}
.slide-wide img{width:100%;max-width:820px;border:1px solid var(--line);border-radius:4px;display:block;background:#fff}
.sides{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}
@media (max-width:1000px){.sides{grid-template-columns:1fr}}
.side{min-width:0;display:flex;flex-direction:column;gap:8px}
.side-head{display:flex;flex-wrap:wrap;gap:8px;align-items:baseline;padding-bottom:6px;border-bottom:2px solid var(--line)}
.side-head b{font-family:var(--f-head);font-size:15px}
.side-title{margin:0;font-weight:600}
.verdict{margin:0;padding:8px 12px;border-radius:4px;background:var(--warn-bg);color:var(--fg);font-size:13px;max-width:70ch}
"""

TEMPLATE = """<title>{la} vs {lb} 比較</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@700&family=JetBrains+Mono:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;600&display=swap">
<style>{css}</style>
<div class="wrap">
<div class="top"><h1>決算説明資料 読み取り比較：{la} と {lb}</h1><p class="lead">{lead}</p></div>
<div class="tiles">{tiles}</div>
<div class="overview"><table><thead><tr><th>ページ</th><th>タイトル</th><th>{la}<br>facts / 未照合</th><th>{lb}<br>facts / 未照合</th><th>画像との照合結果</th></tr></thead>
<tbody>{rows}</tbody></table></div>
{sections}
</div>
"""

if __name__ == "__main__":
    main()
