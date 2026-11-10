"""kabukpi MCP のあり・なしで同じプロンプトを claude -p に渡し、ツール呼び出しとトークンを集計する。

  python tools/agent_bench.py run --code 9880 --company イノテック --period "2027年3月期 第2四半期"
  python tools/agent_bench.py report <フォルダ> --date 2026-11-10

<保存先>/<コード>-<会社名>/<日付>_report.md と <日付>_mcp.jsonl などを OneDrive に上げ、手元の作業フォルダは消す。
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

KABUKPI = "mcp__claude_ai_kabukpi"
OTHER_SERVERS = [
    "mcp__github",
    "mcp__j-quants-doc-mcp",
    "mcp__claude_ai_Claude_Docs",
    "mcp__claude_ai_Linear",
    "mcp__claude_ai_conoha_vps",
]
KABUKPI_DENY = [
    "monthly_add_element", "monthly_delete_element", "monthly_ingest", "monthly_put_doc",
    "monthly_put_values", "monthly_set_industry", "app_logs", "batch_logs", "nginx_logs",
    "service_status", "fs_grep", "fs_list", "fs_read", "db_stats",
]
BUILTIN = ["WebSearch", "WebFetch"]
CONDS = ("mcp", "nomcp")
PUBLISH = "onedrive:agent-bench"
TODAY = datetime.date.today().isoformat()
PROMPT_TEMPLATE = Path(__file__).with_name("agent_bench_prompt.md")


def build_cmd(cond: str, model: str, budget: float) -> list[str]:
    deny = list(OTHER_SERVERS)
    if cond == "mcp":
        deny += [f"{KABUKPI}__{t}" for t in KABUKPI_DENY]
        allow = BUILTIN + [KABUKPI]
    else:
        deny.append(KABUKPI)
        allow = list(BUILTIN)
    return [
        "claude", "-p", "--model", model, "--output-format", "stream-json", "--verbose",
        "--tools", ",".join(BUILTIN),
        "--allowedTools", *allow,
        "--disallowedTools", *deny,
        "--max-budget-usd", str(budget),
    ]


def run_one(cond: str, prompt: str, out: Path, model: str, budget: float) -> None:
    env = dict(os.environ, CLAUDE_CODE_DISABLE_CLAUDE_MDS="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
               MCP_CONNECTION_NONBLOCKING="0")
    with tempfile.TemporaryDirectory(prefix="bench-") as cwd, out.open("w", encoding="utf-8") as f:
        subprocess.run([*build_cmd(cond, model, budget), prompt], cwd=cwd, env=env,
                       stdout=f, stderr=sys.stderr, check=False)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(c.get("text", "") for c in content if isinstance(c, dict))


def summarize(path: Path) -> dict:
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    turns: list[int] = []
    result: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("type") == "assistant":
            u = d["message"].get("usage", {})
            turns.append(u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                         + u.get("cache_read_input_tokens", 0))
            for c in d["message"].get("content", []):
                if c.get("type") == "tool_use":
                    rec = {"name": c["name"].removeprefix(f"{KABUKPI}__"), "input": c["input"], "chars": 0}
                    calls.append(rec)
                    by_id[c["id"]] = rec
        elif d.get("type") == "user":
            content = d["message"].get("content", [])
            if isinstance(content, list):
                for c in content:
                    if c.get("type") == "tool_result" and c.get("tool_use_id") in by_id:
                        by_id[c["tool_use_id"]]["chars"] = len(_text(c.get("content", "")))
        elif d.get("type") == "result":
            result = d
    per_tool: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in calls:
        per_tool[c["name"]][0] += 1
        per_tool[c["name"]][1] += c["chars"]
    return {"calls": calls, "per_tool": dict(per_tool), "result": result, "max_context": max(turns, default=0)}


def render_report(folder: Path, date: str) -> str:
    stats = {c: summarize(folder / f"{date}_{c}.jsonl") for c in CONDS if (folder / f"{date}_{c}.jsonl").exists()}
    lines = [f"# {folder.name} {date}", ""]
    prompt = folder / f"{date}_prompt.txt"
    if prompt.exists():
        lines += ["## プロンプト", "", *[f"> {x}" for x in prompt.read_text(encoding="utf-8").splitlines()], ""]

    def row(label: str, fn) -> str:
        return f"| {label} | " + " | ".join(str(fn(s)) for s in stats.values()) + " |"

    def usage(s: dict, key: str) -> int:
        return s["result"].get("usage", {}).get(key) or 0

    lines += ["## 比較", "", "| | " + " | ".join(stats) + " |", "|---" * (len(stats) + 1) + "|",
              row("費用 USD", lambda s: f"{s['result'].get('total_cost_usd', 0):.2f}"),
              row("ターン", lambda s: s["result"].get("num_turns")),
              row("ツール呼び出し", lambda s: len(s["calls"])),
              row("最後のターンの文脈", lambda s: s["max_context"]),
              row("入力（キャッシュ込み）", lambda s: usage(s, "input_tokens") + usage(s, "cache_creation_input_tokens")
                  + usage(s, "cache_read_input_tokens")),
              row("出力", lambda s: usage(s, "output_tokens")),
              row("秒", lambda s: round((s["result"].get("duration_ms") or 0) / 1000)), ""]
    for cond, s in stats.items():
        lines += [f"## {cond} のツール", "", "| ツール | 回数 | 結果の字数 |", "|---|---|---|"]
        for name, (n, chars) in sorted(s["per_tool"].items(), key=lambda kv: -kv[1][1]):
            lines.append(f"| {name} | {n} | {chars} |")
        lines += ["", "| # | ツール | 字数 | 引数 |", "|---|---|---|---|"]
        for i, c in enumerate(s["calls"], 1):
            arg = json.dumps(c["input"], ensure_ascii=False)[:160].replace("|", "\\|")
            lines.append(f"| {i} | {c['name']} | {c['chars']} | `{arg}` |")
        lines.append("")
    for cond, s in stats.items():
        lines += [f"## {cond} の回答", "", s["result"].get("result", ""), ""]
    return "\n".join(lines)


def publish(folder: Path, root: str) -> None:
    dest = f"{root.rstrip('/')}/{folder.name}"
    subprocess.run(["rclone", "copy", str(folder), dest], check=True)
    remote = set(subprocess.run(["rclone", "lsf", dest], check=True, capture_output=True, text=True).stdout.splitlines())
    missing = {p.name for p in folder.iterdir()} - remote
    if missing:
        sys.exit(f"上がっていないファイルがあるので手元を残す: {folder} {sorted(missing)}")
    print(f"published: {dest}")
    shutil.rmtree(folder)


def cmd_run(a: argparse.Namespace) -> None:
    template = Path(a.prompt_file or PROMPT_TEMPLATE).read_text(encoding="utf-8")
    prompt = template.format(code=a.code, company=a.company, period=a.period)
    folder = Path(tempfile.mkdtemp(prefix="agent-bench-")) / f"{a.code}-{a.company}"
    folder.mkdir()
    (folder / f"{a.date}_prompt.txt").write_text(prompt, encoding="utf-8")
    with ThreadPoolExecutor(len(a.conds)) as ex:
        list(ex.map(lambda c: run_one(c, prompt, folder / f"{a.date}_{c}.jsonl", a.model, a.budget), a.conds))
    (folder / f"{a.date}_report.md").write_text(render_report(folder, a.date), encoding="utf-8")
    if a.publish:
        publish(folder, a.publish)
        shutil.rmtree(folder.parent, ignore_errors=True)
    else:
        print(f"saved: {folder}")


def cmd_report(a: argparse.Namespace) -> None:
    folder = Path(a.folder)
    report = folder / f"{a.date}_report.md"
    report.write_text(render_report(folder, a.date), encoding="utf-8")
    if a.publish:
        publish(folder, a.publish)
    else:
        print(report.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)
    r = sub.add_parser("run")
    r.add_argument("--code", required=True)
    r.add_argument("--company", required=True)
    r.add_argument("--date", default=TODAY)
    r.add_argument("--period", required=True, help="予想させる決算。例: 2027年3月期 第2四半期")
    r.add_argument("--prompt-file", help="{code} {company} {period} を埋めるテンプレート。既定は agent_bench_prompt.md")
    r.add_argument("--conds", nargs="+", choices=CONDS, default=list(CONDS))
    r.add_argument("--model", default="claude-opus-5-5")
    r.add_argument("--budget", type=float, default=5.0)
    r.add_argument("--publish", default=PUBLISH, help="rclone の保存先。空文字なら手元に残す")
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("report")
    s.add_argument("folder", help="<コード>-<会社名> のフォルダ")
    s.add_argument("--date", default=TODAY)
    s.add_argument("--publish", default=PUBLISH, help="rclone の保存先。空文字なら表示だけ")
    s.set_defaults(func=cmd_report)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
