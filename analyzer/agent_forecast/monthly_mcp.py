"""月次 API のスナップショットを返すだけの MCP サーバー（stdio、標準ライブラリのみ）。

本番の月次 MCP（monthly_list / monthly_get）と同じ形で、締切までに切ったファイルを読む。

  python3 monthly_mcp.py <snapshot_dir>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SNAPSHOT = Path(sys.argv[1])

TOOLS = [
    {
        "name": "monthly_list",
        "description": "銘柄の月次表がある暦年の一覧。GET /api/v1/tables/{code}",
        "inputSchema": {
            "type": "object",
            "properties": {"code": {"type": "string", "description": "4-5 character stock code (6036, 60360)"}},
            "required": ["code"],
        },
    },
    {
        "name": "monthly_get",
        "description": "銘柄×暦年の月次表。GET /api/v1/tables/{code}/{year}。values は 1〜12 月。無い月は null。0 は実ゼロ。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "4-5 character stock code (6036, 60360)"},
                "year": {"type": "integer", "description": "4-digit calendar year"},
            },
            "required": ["code", "year"],
        },
    },
]


def _code(raw: str) -> str:
    raw = str(raw).strip().upper()
    return raw + "0" if len(raw) == 4 else raw


def call(name: str, args: dict) -> dict:
    index = json.loads((SNAPSHOT / "index.json").read_text())
    if _code(args.get("code", "")) != index["code"]:
        return {"error": "NOT_FOUND", "code": args.get("code")}
    if name == "monthly_list":
        return index
    if name == "monthly_get":
        year = int(args.get("year", 0))
        if year not in index["years"]:
            return {"error": "NOT_FOUND", "code": index["code"], "year": year}
        return json.loads((SNAPSHOT / f"{year}.json").read_text())
    raise ValueError(name)


def handle(msg: dict) -> dict | None:
    method, mid = msg.get("method"), msg.get("id")
    if mid is None:
        return None
    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "monthly", "version": "1"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        p = msg.get("params", {})
        try:
            body = call(p.get("name"), p.get("arguments") or {})
            result = {"content": [{"type": "text", "text": json.dumps(body, ensure_ascii=False)}], "isError": "error" in body}
        except (ValueError, OSError) as e:
            result = {"content": [{"type": "text", "text": str(e)}], "isError": True}
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        reply = handle(json.loads(line))
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
