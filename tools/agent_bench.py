"""kabukpi MCP のあり・なしで同じプロンプトを claude -p に渡し、ツール呼び出しとトークンを集計する。

  python tools/agent_bench.py run --cond mcp --out /tmp/kab411/mcp.jsonl --prompt-file prompt.txt
  python tools/agent_bench.py summary /tmp/kab411/mcp.jsonl /tmp/kab411/nomcp.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
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


def cmd_run(a: argparse.Namespace) -> None:
    prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    env = dict(os.environ, CLAUDE_CODE_DISABLE_CLAUDE_MDS="1", CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
               MCP_CONNECTION_NONBLOCKING="0")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bench-") as cwd, out.open("w", encoding="utf-8") as f:
        cmd = build_cmd(a.cond, a.model, a.budget)
        if a.dry:
            prompt = "hi"
        subprocess.run([*cmd, prompt], cwd=cwd, env=env, stdout=f, stderr=sys.stderr, check=False)
    if a.dry:
        for line in out.read_text(encoding="utf-8").splitlines():
            d = json.loads(line)
            if d.get("type") == "system" and d.get("subtype") == "init":
                print("\n".join(d["tools"]))


def _result_len(content) -> int:
    if isinstance(content, str):
        return len(content)
    return sum(len(c.get("text", "")) for c in content if isinstance(c, dict))


def summarize(path: Path) -> dict:
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    result: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if d.get("type") == "assistant":
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
                        by_id[c["tool_use_id"]]["chars"] = _result_len(c.get("content", ""))
        elif d.get("type") == "result":
            result = d
    per_tool: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for c in calls:
        per_tool[c["name"]][0] += 1
        per_tool[c["name"]][1] += c["chars"]
    return {"calls": calls, "per_tool": dict(per_tool), "result": result}


def cmd_summary(a: argparse.Namespace) -> None:
    for p in a.files:
        s = summarize(Path(p))
        r = s["result"]
        u = r.get("usage", {})
        print(f"## {p}")
        print(f"turns={r.get('num_turns')} cost_usd={r.get('total_cost_usd')} duration_s={(r.get('duration_ms') or 0) / 1000:.0f}")
        print(f"input={u.get('input_tokens')} cache_create={u.get('cache_creation_input_tokens')} "
              f"cache_read={u.get('cache_read_input_tokens')} output={u.get('output_tokens')}")
        print("tool\tcalls\tresult_chars")
        for name, (n, chars) in sorted(s["per_tool"].items(), key=lambda kv: -kv[1][1]):
            print(f"{name}\t{n}\t{chars}")
        if a.calls:
            for c in s["calls"]:
                print(f"  {c['name']}\t{c['chars']}\t{json.dumps(c['input'], ensure_ascii=False)[:200]}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(required=True)
    r = sub.add_parser("run")
    r.add_argument("--cond", choices=["mcp", "nomcp"], required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--prompt-file", required=True)
    r.add_argument("--model", default="claude-opus-5-5")
    r.add_argument("--budget", type=float, default=5.0)
    r.add_argument("--dry", action="store_true")
    r.set_defaults(func=cmd_run)
    s = sub.add_parser("summary")
    s.add_argument("files", nargs="+")
    s.add_argument("--calls", action="store_true")
    s.set_defaults(func=cmd_summary)
    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
