"""実行ログ（<root>/results/<vendor>/*.jsonl）から、リポジトリに残す軽量版を作る。

残すのは、エージェントの文章・ツール呼び出し（名前と引数）・ツール結果の大きさ・トークンと費用・拒否。
ツール結果の本体（読んだ PDF や JSON の中身）は入れない。モデル名も入れない（採点表と元のログにある）。
パスは作業フォルダからの相対にする。

  .venv/bin/python analyzer/agent_forecast/slim_logs.py --target targets/6036_FY2026Q4.json --vendor claude
  # → results/<vendor>/<id>_logs/<条件>.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from common import HERE, load_target, root_of, target_args


def slim(path: Path, root: Path) -> dict:
    steps: list[dict] = []
    result: dict = {}
    init: dict = {}
    for line in path.read_text().splitlines():
        if not line.strip().startswith("{"):
            continue
        e = json.loads(line)
        msg = e.get("message") or {}
        content = msg.get("content") if isinstance(msg.get("content"), list) else []
        if e.get("type") == "system" and e.get("subtype") == "init":
            init = e
        elif e.get("type") == "assistant":
            for c in content:
                if c.get("type") == "text":
                    steps.append({"kind": "text", "text": c["text"]})
                elif c.get("type") == "thinking" and c.get("thinking"):
                    steps.append({"kind": "thinking", "text": c["thinking"]})
                elif c.get("type") == "tool_use":
                    steps.append({"kind": "tool_use", "name": c["name"], "input": c["input"]})
        elif e.get("type") == "user":
            for c in content:
                if c.get("type") == "tool_result":
                    body = c.get("content")
                    step = {"kind": "tool_result", "chars": len(json.dumps(body, ensure_ascii=False))}
                    if c.get("is_error"):
                        step["error"] = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)[:500]
                    steps.append(step)
        elif e.get("type") == "result":
            result = e
    u = result.get("usage") or {}
    out = {
        "condition": path.stem,
        "claude_code_version": init.get("claude_code_version"),
        "tools": init.get("tools"),
        "num_turns": result.get("num_turns"),
        "duration_s": round((result.get("duration_ms") or 0) / 1000),
        "cost_usd": result.get("total_cost_usd"),
        "usage": {k: u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")},
        "permission_denials": result.get("permission_denials") or [],
        "steps": steps,
    }
    # 作業フォルダの場所は回によって違う（root を変える前の実行もある）ので、cond_<X>/ までを落とす
    text = re.sub(r"/[^\"\s]*?/cond_[A-Z]/", "", json.dumps(out, ensure_ascii=False))
    return json.loads(text.replace(str(root), "<root>"))


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    ap.add_argument("--vendor", default="claude")
    ap.add_argument("--out", type=Path, default=None, help="既定 results/<vendor>/<id>_logs")
    args = ap.parse_args()
    t = load_target(args.target)
    root = root_of(t, args.root)
    out = args.out or HERE / "results" / args.vendor / f"{t['id']}_logs"
    out.mkdir(parents=True, exist_ok=True)
    for path in sorted((root / "results" / args.vendor).glob("*.jsonl")):
        data = slim(path, root)
        (out / f"{path.stem}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n")
        print(f"{path.stem}: {len(data['steps'])} steps")


if __name__ == "__main__":
    main()
