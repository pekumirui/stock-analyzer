"""検証レポート: run.py の結果 JSON (+ 元 PDF) -> HTML 1 枚。LLM を使わない。

    .venv/bin/python analyzer/deck_reader/report.py toyota_2027q1 --root /workspace/deck-reader --model haiku
    .venv/bin/python analyzer/deck_reader/report.py /tmp/toyota_2027q1_haiku.json --pdf /tmp/toyota.pdf -o /tmp/report.html

- id を渡すと <root>/results/<id>_<model>.json を読み、<root>/pdf/<id>.pdf があれば画像を埋め込み、
  <root>/reports/<id>_<model>.html に書く（run.py と同じ置き場）。パスを渡したときは --pdf / -o で指定する
- 画像は LLM に読ませたページだけ入れる（--pages で絞った残りは「未読」として畳む）。全ページなら --all-pages
- --format pdf（または -o *.pdf）で Chromium (headless) を使って PDF にする。スマホで見るとき用。中間の HTML は残さない
- --publish onedrive:deck-reader/<フォルダ> で、PDF のレポートと結果 JSON を rclone で上げる（run.py --publish と同じ）

- スライド画像と読み取り結果 (title / summary / facts) を横に並べ、項目と数字の対応を目で確かめる
- 未照合 (verified=false) の数字、JSON エラー、period_kind の不正値などを機械的に強調する
- --pdf を省くと画像なし (要約と facts の表だけ)
"""
import argparse
import base64
import html
import json
import pathlib
import shutil
import subprocess  # nosec B404
import tempfile

from fetch import DEFAULT_ROOT, pdf_path

PERIOD_KINDS = {
    "actual": "実績",
    "forecast": "予想",
    "previous_forecast": "前回予想",
    "plan": "計画",
    "change": "増減",
}
SKIP_KINDS = ("divider", "disclaimer")


def esc(s):
    return html.escape("" if s is None else str(s))


def page_images(pdf_path, page_nos, dpi=90, quality=78):
    """{page_no: data URI}。JPEG にしてサイズを抑える (21 ページで 2〜3MB 程度)。"""
    import io

    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_path)
    out = {}
    for n in page_nos:
        if 1 <= n <= len(doc):
            buf = io.BytesIO()
            doc[n - 1].render(scale=dpi / 72).to_pil().convert("RGB").save(buf, "JPEG", quality=quality)
            out[n] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    return out


def fact_issues(f):
    """1 件の fact に対する機械チェック。[(level, message)]"""
    issues = []
    if not f.get("verified"):
        issues.append(("bad", "テキスト層に無い数字" if f.get("value_num") is not None else "数値でない値"))
    pk = f.get("period_kind")
    if pk not in PERIOD_KINDS:
        issues.append(("warn", f"period_kind が不正: {pk}"))
    v, u = str(f.get("value") or ""), str(f.get("unit") or "")
    if u and u not in ("null", "None") and v.endswith(u):
        issues.append(("info", "value に単位が重複"))
    st = f.get("sum_status") or {True: "ok", False: "mismatch"}.get(f.get("sum_ok"))
    if st == "mismatch":
        issues.append(("bad", f"内訳の合計 {f.get('sum_of'):g} と合わない"))
    elif st == "partial":
        issues.append(("info", f"内訳は一部だけ（計 {f.get('sum_of'):g}）"))
    elif st == "split":
        issues.append(("info", "別の切り口の内訳が 2 組あり、それぞれ合計と一致"))
    elif st == "paren":
        issues.append(("info", "括弧をマイナスとみなすと内訳の合計と一致"))
    elif st == "ok":
        issues.append(("info", "内訳の合計と一致"))
    if f.get("part_of"):
        issues.append(("info", f"内訳 → {f['part_of']}"))
    return issues


def page_status(p):
    if p["kind"] in SKIP_KINDS and "facts" not in p:
        return "skip"
    if "facts" not in p and not p.get("error"):
        return "unread"
    if p.get("error"):
        return "error"
    if page_problems(p):
        return "warn"
    return "ok"


def sum_mismatch(f):
    return f.get("sum_status") == "mismatch" or f.get("sum_ok") is False


def page_problems(p):
    """ページにある問題の名前（見出しに出す）。"""
    facts = p.get("facts", [])
    out = []
    if any(not f.get("verified") for f in facts):
        out.append("未照合あり")
    if any(sum_mismatch(f) for f in facts):
        out.append("検算不一致あり")
    return out


STATUS_LABEL = {"ok": "全件照合", "warn": "要確認", "error": "JSON エラー", "skip": "VLM 対象外", "unread": "未読"}


def render_metric(metric):
    parts = str(metric or "").split("/")
    depth = len(parts) - 1
    prefix = "/".join(parts[:-1])
    pre = f'<span class="path">{esc(prefix)}/</span>' if prefix else ""
    return f'<td class="metric" style="--depth:{depth}" title="{esc(metric)}">{pre}<span class="leaf">{esc(parts[-1])}</span></td>'


def render_facts(facts):
    if not facts:
        return '<p class="muted">facts なし</p>'
    rows = []
    for f in facts:
        issues = fact_issues(f)
        cls = "row-bad" if any(lv == "bad" for lv, _ in issues) else ""
        pk = f.get("period_kind")
        badge = f'<span class="pk pk-{esc(pk) if pk in PERIOD_KINDS else "bad"}">{esc(PERIOD_KINDS.get(pk, pk))}</span>'
        mark = "✓" if f.get("verified") else "✗"
        notes = "".join(f'<span class="issue issue-{lv}">{esc(m)}</span>' for lv, m in issues)
        note = f'<span class="note">{esc(f.get("note"))}</span>' if f.get("note") else ""
        seg = f.get("segment")
        rows.append(
            f'<tr class="{cls}">{render_metric(f.get("metric"))}'
            f'<td>{esc(seg) if seg not in (None, "null") else ""}</td>'
            f'<td class="period">{esc(f.get("period"))}</td><td>{badge}</td>'
            f'<td class="num">{esc(f.get("value"))}</td><td class="unit">{esc(f.get("unit"))}</td>'
            f'<td class="mark mark-{"ok" if f.get("verified") else "bad"}">{mark}</td>'
            f'<td class="issues">{notes}{note}</td></tr>'
        )
    return (
        '<div class="table-wrap"><table class="facts"><thead><tr>'
        "<th>metric</th><th>segment</th><th>period</th><th>種類</th>"
        '<th class="num">value</th><th>unit</th><th title="テキスト層に実在するか">照合</th><th>チェック</th>'
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )


def render_page(p, img):
    st = page_status(p)
    n = p["page"]
    facts = p.get("facts", [])
    bad = sum(1 for f in facts if not f.get("verified"))
    head = (
        f'<header class="page-head"><span class="pno">p{n}</span>'
        f'<span class="kind">{esc(p["kind"])}</span>'
        f'<h2>{esc(p.get("title") or "")}</h2>'
        f'<span class="status status-{st}">{"・".join(page_problems(p)) if st == "warn" else STATUS_LABEL[st]}</span>'
        + (f'<span class="count">facts {len(facts)} / 未照合 {bad}</span>' if "facts" in p else "")
        + "</header>"
    )
    text = f'<details class="text"><summary>テキスト層 (pypdfium2)</summary><pre>{esc(p.get("text"))}</pre></details>'
    if st == "skip" or (st == "unread" and not img):
        return f'<section class="page page-skip" id="p{n}">{head}{text}</section>'
    body = []
    if p.get("summary"):
        body.append(f'<p class="summary">{esc(p["summary"])}</p>')
    if p.get("error"):
        body.append(f'<div class="error"><b>{esc(p["error"])}</b>'
                    f'<details><summary>VLM の生出力 (先頭 2000 文字)</summary><pre>{esc(p.get("raw"))}</pre></details></div>')
    body.append(render_facts(facts))
    body.append(text)
    fig = f'<figure class="slide"><img src="{img}" alt="p{n} のスライド画像"></figure>' if img else ""
    return (f'<section class="page page-{st}" id="p{n}">{head}'
            f'<div class="page-body{" has-img" if img else ""}">{fig}<div class="read">{"".join(body)}</div></div></section>')


def render(res, images, source):
    s = res.get("stats", {})
    pages = res["pages"]
    ratio = s.get("verified_ratio")
    tiles = [
        ("verified 率", f"{ratio * 100:.1f}%" if ratio is not None else "-", f'{s.get("verified")}/{s.get("facts")} 件'),
        ("JSON エラー", str(s.get("errors", 0)), "ページ"),
        ("検算 不一致", str(s.get("sum_mismatch", "-")),
         f'/ {s.get("sum_checks", "-")} 件（内訳の合計）。一部だけの内訳 {s.get("sum_partial", 0)} 件'),
        ("LLM 対象", f'{s.get("llm_pages")}', f'/ {s.get("pages")} ページ'),
        ("所要時間", f'{s.get("llm_sec")}s', "並列実行の壁時計"),
        ("費用", f'${s.get("cost_usd")}', f'入力 {s.get("input_tokens", 0):,} / 出力 {s.get("output_tokens", 0):,} トークン'),
    ]
    tile_html = "".join(f'<div class="tile"><span class="t-label">{a}</span><span class="t-val">{esc(b)}</span>'
                        f'<span class="t-sub">{esc(c)}</span></div>' for a, b, c in tiles)
    nav = "".join(f'<a class="chip chip-{page_status(p)}" href="#p{p["page"]}" title="{STATUS_LABEL[page_status(p)]}">'
                  f'{p["page"]}</a>' for p in pages)
    legend = "".join(f'<span class="chip chip-{k}">{v}</span>' for k, v in STATUS_LABEL.items())
    pk_legend = "".join(f'<span class="pk pk-{k}">{v}</span><code>{k}</code>' for k, v in PERIOD_KINDS.items())
    body = "".join(render_page(p, images.get(p["page"])) for p in pages)
    meta = res.get("meta", {})
    return TEMPLATE.format(
        css=CSS, stem=esc(source.rsplit(".", 1)[0]), model=esc(res.get("model")), prompt=esc(res.get("prompt_version")), source=esc(source),
        pages_meta=esc(meta.get("pages")), tiles=tile_html, nav=nav, legend=legend, pk_legend=pk_legend, body=body,
    )


CSS = """
/* layout: 上に集計とページ索引、下にページごとのカード (左スライド / 右 読み取り結果) */
:root{
  --bg:#f6f7f8; --surface:#ffffff; --sunk:#eef0f2; --fg:#1b1f24; --muted:#5d6670; --line:#d9dde2;
  --accent:#0f6b6b; --bad:#c62828; --bad-bg:#fdecec; --warn:#a15c00; --warn-bg:#fff3df; --ok:#2e7d32; --ok-bg:#e8f4e9;
  --pk-actual:#59636e; --pk-forecast:#1f5fbf; --pk-prev:#5b7fb8; --pk-plan:#2e7d32; --pk-change:#b85a00;
  --f-head:"BIZ UDPGothic","Hiragino Sans","Noto Sans JP",sans-serif;
  --f-body:"Noto Sans JP","Hiragino Sans","Yu Gothic",sans-serif;
  --f-num:"JetBrains Mono",ui-monospace,"SFMono-Regular",Menlo,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
  --bg:#121417; --surface:#1a1d21; --sunk:#22262b; --fg:#e4e7ea; --muted:#9aa3ad; --line:#30353b;
  --accent:#4fb3b3; --bad:#ff6b6b; --bad-bg:#3a1d1f; --warn:#f0a83a; --warn-bg:#382a14; --ok:#6cc070; --ok-bg:#1d3320;
  --pk-actual:#8b96a1; --pk-forecast:#6fa3f0; --pk-prev:#8aa6d4; --pk-plan:#6cc070; --pk-change:#f0a050; color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#121417; --surface:#1a1d21; --sunk:#22262b; --fg:#e4e7ea; --muted:#9aa3ad; --line:#30353b;
  --accent:#4fb3b3; --bad:#ff6b6b; --bad-bg:#3a1d1f; --warn:#f0a83a; --warn-bg:#382a14; --ok:#6cc070; --ok-bg:#1d3320;
  --pk-actual:#8b96a1; --pk-forecast:#6fa3f0; --pk-prev:#8aa6d4; --pk-plan:#6cc070; --pk-change:#f0a050; color-scheme:dark}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);font-family:var(--f-body);font-size:14px;line-height:1.6;margin:0}
.wrap{max-width:1440px;margin:0 auto;padding-block:24px 64px;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));
  display:flex;flex-direction:column;gap:20px}
h1{font-family:var(--f-head);font-size:22px;margin:0;text-wrap:balance}
h2{font-family:var(--f-head);font-size:17px;margin:0;text-wrap:balance}
.muted{color:var(--muted)}
code{font-family:var(--f-num);font-size:12px;color:var(--muted)}
.top{display:flex;flex-direction:column;gap:6px}
.meta{color:var(--muted);font-size:13px;display:flex;flex-wrap:wrap;gap:4px 16px}
.meta b{color:var(--fg);font-family:var(--f-num);font-weight:500}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:6px;overflow:hidden}
.tile{background:var(--surface);padding:12px 14px;display:flex;flex-direction:column;gap:2px}
.t-label{font-size:12px;color:var(--muted);letter-spacing:.04em}
.t-val{font-family:var(--f-num);font-size:24px;font-weight:600;font-variant-numeric:tabular-nums}
.t-sub{font-size:12px;color:var(--muted)}
.index{display:flex;flex-direction:column;gap:8px}
.chips{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.chip{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:28px;padding:0 8px;border-radius:4px;
  font-family:var(--f-num);font-size:13px;text-decoration:none;color:var(--fg);border:1px solid var(--line);background:var(--surface)}
.legend .chip{font-family:var(--f-body);font-size:12px;height:24px}
a.chip:hover,a.chip:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
.chip-ok{background:var(--ok-bg);border-color:transparent}
.chip-warn{background:var(--warn-bg);border-color:var(--warn);color:var(--warn)}
.chip-error{background:var(--bad);border-color:var(--bad);color:var(--surface);font-weight:600}
.chip-skip,.chip-unread{color:var(--muted);border-style:dashed;background:transparent}
.controls{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;font-size:13px;color:var(--muted)}
.controls label{display:inline-flex;gap:6px;align-items:center;color:var(--fg);cursor:pointer}
.pk-legend{display:inline-flex;flex-wrap:wrap;gap:4px 6px;align-items:center}
.page{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:16px;display:flex;flex-direction:column;gap:12px;
  scroll-margin-top:12px}
.page-error{border-left:4px solid var(--bad)}
.page-warn{border-left:4px solid var(--warn)}
.page-skip{background:transparent;border-style:dashed;padding:10px 16px}
.page-skip h2{font-size:14px;color:var(--muted)}
.page-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 12px}
.pno{font-family:var(--f-num);font-weight:700;font-size:18px;color:var(--accent)}
.kind{font-family:var(--f-num);font-size:12px;color:var(--muted)}
.status{font-size:12px;padding:1px 8px;border-radius:3px}
.status-ok{background:var(--ok-bg);color:var(--ok)}
.status-warn{background:var(--warn-bg);color:var(--warn)}
.status-error{background:var(--bad-bg);color:var(--bad);font-weight:600}
.status-skip,.status-unread{color:var(--muted)}
.count{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.page-body{display:grid;gap:16px;grid-template-columns:1fr}
.page-body.has-img{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
@media (max-width:1000px){.page-body.has-img{grid-template-columns:1fr}}
.slide{margin:0;position:sticky;top:12px;align-self:start}
@media (max-width:1000px){.slide{position:static}}
.slide img{width:100%;max-width:100%;border:1px solid var(--line);border-radius:4px;display:block;background:#fff}
.read{min-width:0;display:flex;flex-direction:column;gap:10px}
.summary{margin:0;padding:10px 12px;background:var(--sunk);border-radius:4px;max-width:70ch}
.error{padding:10px 12px;background:var(--bad-bg);color:var(--bad);border-radius:4px;display:flex;flex-direction:column;gap:6px}
.error pre{color:var(--fg)}
.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:4px}
table.facts{border-collapse:collapse;width:100%;font-size:13px}
.facts th{position:sticky;top:0;background:var(--sunk);text-align:left;font-weight:600;font-size:12px;color:var(--muted);
  padding:6px 8px;white-space:nowrap;border-bottom:1px solid var(--line)}
.facts td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:top}
.facts tr:last-child td{border-bottom:0}
.facts td.metric{padding-left:calc(8px + var(--depth) * 12px);min-width:180px}
.path{color:var(--muted);font-size:11px}
.leaf{font-weight:600}
.period{white-space:nowrap;font-family:var(--f-num);font-size:12px}
.num{text-align:right;font-family:var(--f-num);font-variant-numeric:tabular-nums;white-space:nowrap}
.unit{white-space:nowrap;color:var(--muted)}
.mark{text-align:center;font-weight:700}
.mark-ok{color:var(--ok)} .mark-bad{color:var(--bad)}
tr.row-bad td{background:var(--bad-bg)}
.issues{font-size:12px}
.issue{display:inline-block;margin:0 4px 2px 0;padding:0 6px;border-radius:3px;white-space:nowrap}
.issue-bad{color:var(--bad);border:1px solid var(--bad)}
.issue-warn{color:var(--warn);border:1px solid var(--warn)}
.issue-info{color:var(--muted);border:1px solid var(--line)}
.note{color:var(--muted)}
.pk{display:inline-block;font-size:11px;padding:0 6px;border-radius:3px;border:1px solid currentColor;white-space:nowrap}
.pk-actual{color:var(--pk-actual)} .pk-forecast{color:var(--pk-forecast)} .pk-previous_forecast{color:var(--pk-prev);border-style:dashed}
.pk-plan{color:var(--pk-plan)} .pk-change{color:var(--pk-change)} .pk-bad{color:var(--bad)}
details summary{cursor:pointer;color:var(--muted);font-size:12px}
details summary:focus-visible{outline:2px solid var(--accent)}
pre{white-space:pre-wrap;word-break:break-all;font-family:var(--f-num);font-size:11.5px;line-height:1.5;margin:6px 0 0;
  padding:8px;background:var(--sunk);border-radius:4px;max-height:360px;overflow:auto}
body.only-bad .facts tbody tr:not(.row-bad){display:none}
body.only-bad .page-ok, body.only-bad .page-skip{display:none}
/* PDF（スマホ）: 読ませたページは PDF の新しいページから。スライドを上、読み取り結果を下に 1 列で。表はページをまたがせる */
@media print{
  body{background:#fff;font-size:12px}
  .wrap{max-width:none;padding:0;gap:12px}
  .controls{display:none}
  .page{break-inside:auto}
  .page:not(.page-skip){break-before:page}
  .page-body.has-img{grid-template-columns:1fr}
  .slide{position:static;break-inside:avoid}
  .table-wrap{overflow:visible}
  .facts th{position:static}
  .facts tr{break-inside:avoid}
}
"""

TEMPLATE = """<title>{stem} 読み取り検証</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=BIZ+UDPGothic:wght@700&family=JetBrains+Mono:wght@400;500;600;700&family=Noto+Sans+JP:wght@400;600&display=swap">
<style>{css}</style>
<div class="wrap">
<div class="top">
  <h1>決算説明資料 読み取り検証</h1>
  <div class="meta"><span>資料 <b>{source}</b></span><span>モデル <b>{model}</b></span>
  <span>プロンプト <b>{prompt}</b></span><span>ページ数 <b>{pages_meta}</b></span></div>
</div>
<div class="tiles">{tiles}</div>
<div class="index">
  <div class="chips">{nav}</div>
  <div class="controls">
    <label for="only-bad"><input type="checkbox" id="only-bad"> 問題のあるページ・行だけ表示</label>
    <span class="chips legend">{legend}</span>
    <span class="pk-legend">{pk_legend}</span>
  </div>
</div>
{body}
</div>
<script>
document.getElementById('only-bad').addEventListener('change', e => document.body.classList.toggle('only-bad', e.target.checked));
</script>
"""


CHROME_NAMES = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
CHROME_GLOBS = ("~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome", "/opt/pw-browsers/chromium*/chrome-linux*/chrome")


def find_chrome():
    for name in CHROME_NAMES:
        if path := shutil.which(name):
            return path
    for pattern in CHROME_GLOBS:
        p = pathlib.Path(pattern).expanduser()
        hits = sorted(pathlib.Path(p.anchor).glob(str(p.relative_to(p.anchor))))
        if hits:
            return str(hits[-1])
    raise SystemExit("PDF にするには Chromium が要る（PATH か ~/.cache/ms-playwright/）")


def write_pdf(html_text, out):
    """HTML を一時フォルダに書き、Chromium の --print-to-pdf で PDF にする。一時ファイルは消える。"""
    chrome = find_chrome()
    with tempfile.TemporaryDirectory() as tmp:
        src = pathlib.Path(tmp) / "report.html"
        src.write_text(html_text)
        cmd = [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
               f"--user-data-dir={tmp}/profile", f"--print-to-pdf={out.resolve()}", src.as_uri()]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)  # nosec B603
    if not out.exists():
        raise SystemExit(f"PDF にできなかった: {proc.stderr[-500:]}")


def build(path, pdf, out, fmt="pdf", all_pages=False):
    """結果 JSON（path）と元の PDF（pdf、無ければ画像なし）からレポートを out に書く。"""
    res = json.loads(pathlib.Path(path).read_text())
    want = ("ok", "warn", "error", "unread") if all_pages else ("ok", "warn", "error")
    images = page_images(str(pdf), [p["page"] for p in res["pages"] if page_status(p) in want]) if pdf else {}
    source = pathlib.Path(pdf).name if pdf else pathlib.Path(path).name
    out = pathlib.Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "pdf":
        write_pdf(render(res, images, source), out)
    else:
        out.write_text(render(res, images, source))
    return out


def publish(files, remote):
    """files を rclone で remote（例: onedrive:deck-reader/10decks）に上げる。失敗したら例外。"""
    rclone = shutil.which("rclone")
    if not rclone:
        raise SystemExit("rclone が無い（bash .cursor/install.sh で入る）")
    for f in files:
        dst = f"{remote.rstrip('/')}/{pathlib.Path(f).name}"
        subprocess.run([rclone, "copyto", str(f), dst], check=True, timeout=600)  # nosec B603
        print(f"  -> {dst}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("result", help="decks の id（<root>/results/<id>_<model>.json）か、run.py の結果 JSON のパス")
    ap.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    ap.add_argument("--model", default="haiku", help="id を渡したときの結果の名前（haiku、haiku_v2_elow など。run.py の表示どおり）")
    ap.add_argument("--pdf", help="元の PDF (スライド画像を埋め込む)。id のときは <root>/pdf/<id>.pdf")
    ap.add_argument("-o", "--out", help="出力。id のときは <root>/reports/<id>_<model>.<format>、パスのときは report.<format>")
    ap.add_argument("--format", choices=("html", "pdf"), help="省くと -o の拡張子、それも無ければ html")
    ap.add_argument("--all-pages", action="store_true", help="LLM に読ませていないページの画像も入れる")
    ap.add_argument("--publish", metavar="REMOTE", help="PDF のレポートと結果 JSON を rclone で上げる（例: onedrive:deck-reader/10decks）")
    a = ap.parse_args()
    fmt = a.format or ("pdf" if a.publish or (a.out and a.out.lower().endswith(".pdf")) else "html")
    path = pathlib.Path(a.result)
    if path.suffix == ".json":
        pdf, out = a.pdf, pathlib.Path(a.out or f"report.{fmt}")
    else:
        path = a.root / "results" / f"{a.result}_{a.model}.json"
        default_pdf = pdf_path(a.root, a.result)
        pdf = a.pdf or (str(default_pdf) if default_pdf.exists() else None)
        out = pathlib.Path(a.out) if a.out else a.root / "reports" / f"{a.result}_{a.model}.{fmt}"
    if not path.exists():
        raise SystemExit(f"{path} が無い。run.py を先に")
    out = build(path, pdf, out, fmt, a.all_pages)
    print(f"{out} ({out.stat().st_size / 1e6:.1f} MB)")
    if a.publish:
        publish([out, path], a.publish)


if __name__ == "__main__":
    main()
