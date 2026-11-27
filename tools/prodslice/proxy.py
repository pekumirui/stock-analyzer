from __future__ import annotations

import asyncio

from starlette.responses import PlainTextResponse, Response

from app.main import app as local_app
from tools.prodslice.load import ssh_command

TIMEOUT_S = 90


def split_reply(raw: bytes) -> tuple[int, str, bytes]:
    head, _, body = raw.partition(b"\n")
    code, _, ctype = head.decode("ascii", "replace").partition(" ")
    if not code.isdigit():
        return 502, "text/plain; charset=utf-8", raw
    return int(code), ctype or "application/octet-stream", body


async def fetch(path: str) -> Response:
    proc = await asyncio.create_subprocess_exec(
        *ssh_command(["get", path], mux=True),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        return PlainTextResponse("本番への中継が時間切れ", status_code=504)
    if proc.returncode != 0:
        return PlainTextResponse(f"本番への中継に失敗: {err.decode(errors='replace')}", status_code=502)
    status, ctype, body = split_reply(out)
    return Response(body, status_code=status, headers={"content-type": ctype, "cache-control": "no-store"})


async def app(scope, receive, send):
    if scope["type"] != "http" or not scope["path"].startswith("/api/"):
        await local_app(scope, receive, send)
        return
    if scope["method"] not in ("GET", "HEAD"):
        response: Response = PlainTextResponse("本番への中継は GET だけ", status_code=405)
    else:
        path = (scope.get("raw_path") or scope["path"].encode()).decode("latin-1")
        query = scope.get("query_string", b"").decode("latin-1")
        response = await fetch(path + (f"?{query}" if query else ""))
    await response(scope, receive, send)
