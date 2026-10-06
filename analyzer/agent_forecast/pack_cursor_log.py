"""Cursor サブエージェントの予想テキストを、score.py が読める JSONL に変換する。

claude -p の stream-json と同じイベント型（init / assistant tool_use / result）にする。
トークンは API 値が無いので、読んだファイルと出力の文字数から推定する（費用は 0）。

  python3 pack_cursor_log.py --cond A --model cursor-grok-4.6-high --text-file /tmp/out.txt
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404
from pathlib import Path

from common import load_target, render_prompt, root_of, target_args

VENDOR = "grok"


def _chars(path: Path) -> int:
    if path.suffix.lower() == ".pdf":
        r = subprocess.run(  # nosec B603
            ["pdftotext", "-layout", str(path), "-"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        return len((r.stdout or b"").decode("utf-8", "replace"))
    return len(path.read_text(encoding="utf-8", errors="replace"))


def _resolve(cond_dir: Path, rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    cand = cond_dir / rel
    if cand.exists():
        return cand
    return Path(rel)


def pack(
    *,
    cond: str,
    model: str,
    text: str,
    cond_dir: Path,
    out: Path,
    duration_s: int,
    files_read: list[str],
    monthly_calls: list[dict],
    denials: list[object],
    prompt: str,
) -> Path:
    reads = [r for r in files_read if r]
    input_chars = len(prompt) + sum(_chars(_resolve(cond_dir, r)) for r in reads)
    snap = cond_dir.parent / "monthly_snapshot"
    if not (snap / "index.json").exists():
        snap = cond_dir.parent / "mcp" / "snapshot"
    for call in monthly_calls:
        year = call.get("year")
        if call.get("tool") == "monthly_list" and (snap / "index.json").exists():
            input_chars += _chars(snap / "index.json")
        elif call.get("tool") == "monthly_get" and year is not None:
            p = snap / f"{year}.json"
            if p.exists():
                input_chars += _chars(p)
    output_chars = len(text)
    # 日本語混じり JSON の粗い換算。API の実測ではない。
    input_tokens = max(1, input_chars // 2)
    output_tokens = max(1, output_chars // 2)

    events: list[dict] = [
        {
            "type": "system",
            "subtype": "init",
            "model": model,
            "tools": ["Read", "Glob", "Grep"] + (["monthly_list", "monthly_get"] if monthly_calls else []),
        }
    ]
    for rel in reads:
        events.append(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Read",
                            "input": {"file_path": f"/cond_{cond}/{rel.lstrip('/')}"},
                        }
                    ]
                },
            }
        )
    for call in monthly_calls:
        name = "mcp__monthly__monthly_list" if call.get("tool") == "monthly_list" else "mcp__monthly__monthly_get"
        inp: dict = {"code": str(call.get("code") or "6036")}
        if call.get("year") is not None:
            inp["year"] = call["year"]
        events.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})
    events.append({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}})
    events.append(
        {
            "type": "result",
            "result": text,
            "modelUsage": {model: {}},
            "usage": {
                "input_tokens": input_tokens,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
                "output_tokens": output_tokens,
            },
            "num_turns": 1 + len(reads) + len(monthly_calls),
            "duration_ms": duration_s * 1000,
            "total_cost_usd": 0,
            "permission_denials": denials,
            "estimated_tokens": True,
        }
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events))
    print(f"{out} in≈{input_tokens} out≈{output_tokens} reads={len(reads)} mcp={len(monthly_calls)}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    ap.add_argument("--cond", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--model", default="cursor-grok-4.6-high")
    ap.add_argument("--text-file", type=Path, required=True)
    ap.add_argument("--meta-file", type=Path, default=None, help="RUN_META の JSON")
    ap.add_argument("--duration", type=int, default=0)
    ap.add_argument("--cond-dir", type=Path, default=None)
    args = ap.parse_args()
    t = load_target(args.target)
    root = root_of(t, args.root)
    cond_dir = args.cond_dir or Path("/tmp/grok-forecast") / args.cond
    meta = json.loads(args.meta_file.read_text()) if args.meta_file else {}
    pack(
        cond=args.cond,
        model=args.model,
        text=args.text_file.read_text(),
        cond_dir=cond_dir,
        out=root / "results" / VENDOR / f"{args.cond}{args.tag}.jsonl",
        duration_s=args.duration,
        files_read=list(meta.get("files_read") or []),
        monthly_calls=list(meta.get("monthly_calls") or []),
        denials=list(meta.get("denied_attempts") or []),
        prompt=render_prompt(t),
    )


if __name__ == "__main__":
    main()
