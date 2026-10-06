"""条件ごとに claude -p を 1 回起動し、stream-json の全イベントを <root>/results/<X>.jsonl に保存する。

読めるのは cond_<X>/ の中だけ（Read の許可ルール）。道具は Read / Glob / Grep と、条件によって月次 MCP。
全許可モードは使わない。-p では許可していない操作は自動で拒否される。

  .venv/bin/python analyzer/agent_forecast/run.py --target targets/6036_FY2026Q4.json A B C
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
import time
from pathlib import Path

from build_conditions import CONDITIONS
from common import load_target, render_prompt, root_of, target_args


def run(root: Path, name: str, prompt: str, model: str | None, timeout: int, tag: str) -> Path:
    cond = root / f"cond_{name}"
    if not cond.exists():
        raise SystemExit(f"{cond} が無い。build_conditions.py を先に")
    out = root / "results" / f"{name}{tag}.jsonl"
    out.parent.mkdir(exist_ok=True)
    allowed = [f"Read(/{cond}/**)"]
    if CONDITIONS[name]["monthly_api"]:
        allowed += ["mcp__monthly__monthly_list", "mcp__monthly__monthly_get"]
    cmd = [
        "claude",
        "-p",
        prompt,
        "--tools",
        "Read,Glob,Grep",
        "--allowedTools",
        *allowed,
        "--mcp-config",
        str(root / f"mcp_{name}.json"),
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    if model:
        cmd += ["--model", model]
    t0 = time.time()
    with out.open("w") as f:
        subprocess.run(cmd, cwd=cond, stdout=f, stderr=subprocess.STDOUT, timeout=timeout, check=False)  # nosec B603
    result = next(
        (e for e in map(json.loads, filter(str.strip, out.read_text().splitlines())) if e.get("type") == "result"),
        {},
    )
    print(
        f"cond_{name}{tag}: {time.time() - t0:.0f}s turns={result.get('num_turns')} "
        f"cost=${result.get('total_cost_usd')} denials={len(result.get('permission_denials') or [])}"
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    ap.add_argument("--model", default=None, help="省略時は claude の既定")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--tag", default="", help="同じ条件を複数回回すときの接尾辞（例 _2）")
    ap.add_argument("conds", nargs="+", choices=sorted(CONDITIONS))
    args = ap.parse_args()
    t = load_target(args.target)
    root = root_of(t, args.root)
    prompt = render_prompt(t)
    (root / "results").mkdir(parents=True, exist_ok=True)
    (root / "results" / "prompt.md").write_text(prompt)
    for name in args.conds:
        run(root, name, prompt, args.model, args.timeout, args.tag)


if __name__ == "__main__":
    main()
