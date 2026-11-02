"""説明資料 1 冊を LLM に読ませ、ページ情報 + facts の JSON を作る。PoC では `claude -p`（claude.ai の利用枠）を使う。

1 ページにつき 1 回 `claude -p` を起動する。作業フォルダ <root>/work/<id>/<model>/p<N>/ に slide.png と text.txt を置き、
読めるのはそのフォルダだけ（Read の許可ルール）。道具は Read だけで、Bash・Web・書き込み・MCP は無い。
`--setting-sources ""` でリポジトリの指示・メモリを読ませない。全許可モードは使わない。

結果は <root>/results/<id>_<model>.json（report.py / compare.py が読む形）。各ページの claude の出力は
work の下の result.json に残る（トークン・費用・所要時間）。

  .venv/bin/python analyzer/deck_reader/run.py toyota_2027q1 --model haiku              # まず 1 冊
  .venv/bin/python analyzer/deck_reader/run.py toyota_2027q1 --model haiku --pages 7,13 # ページを絞る
  .venv/bin/python analyzer/deck_reader/run.py --all --model haiku --jobs 4
  .venv/bin/python analyzer/deck_reader/run.py toyota_2027q1 --dry-run                  # 起動コマンドだけ表示
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess  # nosec B404
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import extract as d
from fetch import DEFAULT_ROOT, load_decks, pdf_path

TASK = (
    "\n\n作業フォルダの slide.png（スライド画像）と text.txt（同じスライドの文字データ）を Read で読み、"
    "上の形式の JSON オブジェクトだけを返してください。"
)


def claude_cmd(page_dir: Path, model: str) -> list[str]:
    cmd = [
        "claude",
        "-p",
        d.SYSTEM + TASK,
        "--tools",
        "Read",
        "--allowedTools",
        f"Read(/{page_dir}/**)",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--output-format",
        "json",
    ]
    if model:
        cmd += ["--model", model]
    return cmd


def run_page(page: dict, page_dir: Path, model: str, timeout: int) -> dict:
    """1 ページを読ませて {title, summary, facts, usage...} を返す。失敗は error にして続ける。"""
    page_dir.mkdir(parents=True, exist_ok=True)
    (page_dir / "slide.png").write_bytes(page["png"])
    (page_dir / "text.txt").write_text(d.page_text(page))
    out = page_dir / "result.json"
    t0 = time.time()
    try:
        proc = subprocess.run(  # nosec B603
            claude_cmd(page_dir, model), cwd=page_dir, capture_output=True, text=True, timeout=timeout, check=False
        )
        out.write_text(proc.stdout or proc.stderr)
        res = json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {timeout}s", "sec": round(time.time() - t0, 1)}
    except json.JSONDecodeError:
        return {"error": "claude の出力が JSON でない", "raw": out.read_text()[:2000], "sec": round(time.time() - t0, 1)}
    meta = {
        "sec": round(time.time() - t0, 1),
        "cost_usd": res.get("total_cost_usd"),
        "usage": res.get("usage"),
        "models": sorted(res.get("modelUsage") or {}),
        "denials": len(res.get("permission_denials") or []),
    }
    if res.get("is_error"):
        return dict(meta, error=f"claude error: {res.get('subtype')}", raw=str(res.get("result"))[:2000])
    text = res.get("result") or ""
    try:
        return dict(meta, **d.verify(page, d.parse_json(text)))
    except Exception as e:  # JSON 崩れはページ単位でエラーにして続ける
        return dict(meta, error=f"{type(e).__name__}: {e}", raw=text[:2000])


def run_deck(deck: dict, root: Path, model: str, pages_filter: set[int] | None, jobs: int, timeout: int, dry: bool):
    pdf = pdf_path(root, deck["id"])
    if not pdf.exists():
        raise SystemExit(f"{pdf} が無い。fetch.py を先に（doc_id が無い資料は decks.json の note に従って置く）")
    pages, meta = d.load_pages(pdf.read_bytes())
    todo = [p for p in pages if d.needs_llm(p["kind"]) and (not pages_filter or p["page"] in pages_filter)]
    work = root / "work" / deck["id"] / model
    if dry:
        for p in todo:
            pdir = work / f"p{p['page']}"
            cmd = claude_cmd(pdir, model)
            cmd[2] = "<プロンプト>"
            print(f"p{p['page']} ({p['kind']}): cd {pdir} && {shlex.join(cmd)}")
        return None
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = {p["page"]: ex.submit(run_page, p, work / f"p{p['page']}", model, timeout) for p in todo}
        by_page = {n: f.result() for n, f in futs.items()}
    out_pages = []
    for p in pages:
        r = {"page": p["page"], "kind": p["kind"], "text": p["text"]}
        r.update(by_page.get(p["page"], {}))
        out_pages.append(r)
    done = list(by_page.values())
    usage = [x.get("usage") or {} for x in done]
    res = {
        "deck": deck,
        "meta": meta,
        "model": model,
        "prompt_version": d.PROMPT_VERSION,
        "pages": out_pages,
        "stats": dict(
            d.stats(out_pages),
            llm_sec=round(time.time() - t0, 1),
            cost_usd=round(sum(x.get("cost_usd") or 0 for x in done), 4),
            input_tokens=sum(u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                             + u.get("cache_creation_input_tokens", 0) for u in usage),
            output_tokens=sum(u.get("output_tokens", 0) for u in usage),
            denials=sum(x.get("denials", 0) for x in done),
        ),
    }
    dst = root / "results" / f"{deck['id']}_{model}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    s = res["stats"]
    print(f"{deck['id']} ({model}): {s['llm_sec']}s facts={s['facts']} verified={s['verified_ratio']} "
          f"errors={s['errors']} cost=${s['cost_usd']} in={s['input_tokens']:,} out={s['output_tokens']:,} -> {dst}")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ids", nargs="*", help="decks.json の id")
    ap.add_argument("--all", action="store_true", help="decks.json の全部")
    ap.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    ap.add_argument("--model", default="haiku", help="claude -p の --model（haiku / sonnet / opus）")
    ap.add_argument("--pages", help="7,13 のようにページを絞る（1 冊のとき）")
    ap.add_argument("--jobs", type=int, default=2, help="同時に起動する claude -p の数")
    ap.add_argument("--timeout", type=int, default=600, help="1 ページの上限秒")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.ids and not args.all:
        ap.error("id か --all を指定する")
    pages_filter = {int(x) for x in args.pages.split(",")} if args.pages else None
    for deck in load_decks(None if args.all else args.ids):
        run_deck(deck, args.root, args.model, pages_filter, args.jobs, args.timeout, args.dry_run)


if __name__ == "__main__":
    main()
