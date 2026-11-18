"""Read-only MCP file tools: allowlist, denylist, traversal, truncation."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mcp"))

os.environ.setdefault("MCP_TOKEN", "test-token-for-ci-only")
os.environ["MCP_APP_DIR"] = str(ROOT)

from starlette.testclient import TestClient

import fs_tools as fs
import log_mcp as m


def _rpc(client: TestClient, method: str, params: dict | None = None, token: str = "test-token-for-ci-only", req_id: int = 1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"})


class _Env:
    keys = ("MCP_DATA_DIR", "MCP_LOG_DIR", "MCP_FS_MAX_BYTES", "MCP_FS_TIMEOUT")

    def __init__(self, data: Path, logs: Path, **extra: str) -> None:
        self.prev = {k: os.environ.get(k) for k in self.keys}
        os.environ["MCP_DATA_DIR"] = str(data)
        os.environ["MCP_LOG_DIR"] = str(logs)
        for key, val in extra.items():
            os.environ[key] = val

    def close(self) -> None:
        for key, val in self.prev.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    (data / "nested").mkdir(parents=True)
    logs.mkdir(parents=True)
    (data / "hello.txt").write_text("hello world\n", encoding="utf-8")
    (data / "nested" / "inner.txt").write_text("inner\n", encoding="utf-8")
    (logs / "app.log").write_text(
        "ok line\nERROR boom\nERROR again\n",
        encoding="utf-8",
    )
    return data, logs


def _call(name: str, **args):
    return m.call_tool(name, args)


def _body(text: str) -> dict:
    return json.loads(text)


def _must_err(name: str, needle: str, **args) -> str:
    text, is_error = _call(name, **args)
    if not is_error:
        raise SystemExit(f"{name} {args} should isError: {text!r}")
    if needle not in text:
        raise SystemExit(f"{name} {args} missing {needle!r}: {text!r}")
    return text


def test_fs_list_and_read_allowed(tmp_path: Path) -> None:
    data, logs = _layout(tmp_path)
    env = _Env(data, logs)
    try:
        text, is_error = _call("fs_list", root="data")
        if is_error:
            raise SystemExit(f"list data: {text}")
        body = _body(text)
        names = {e["name"] for e in body["entries"]}
        if names != {"hello.txt", "nested"}:
            raise SystemExit(f"list names: {names}")
        if body["truncated"] or body["root"] != "data":
            raise SystemExit(f"list meta: {body}")
        hello = next(e for e in body["entries"] if e["name"] == "hello.txt")
        if hello["type"] != "file" or hello["size"] != 12 or "mtime" not in hello:
            raise SystemExit(f"hello entry: {hello}")
        nested = next(e for e in body["entries"] if e["name"] == "nested")
        if nested["type"] != "dir":
            raise SystemExit(f"nested type: {nested}")

        rec, rec_err = _call("fs_list", root="data", recursive=True)
        if rec_err:
            raise SystemExit(f"recursive: {rec}")
        rec_paths = {e["path"] for e in _body(rec)["entries"]}
        if "hello.txt" not in rec_paths or "nested/inner.txt" not in rec_paths:
            raise SystemExit(f"recursive paths: {rec_paths}")

        logged, log_err = _call("fs_list", root="logs")
        if log_err or "app.log" not in {e["name"] for e in _body(logged)["entries"]}:
            raise SystemExit(f"list logs: {logged}")

        read, read_err = _call("fs_read", root="data", path="hello.txt")
        if read_err:
            raise SystemExit(f"read: {read}")
        rbody = _body(read)
        if rbody["content"] != "hello world\n" or rbody["truncated"] or rbody["size"] != 12:
            raise SystemExit(f"read body: {rbody}")
        if rbody["encoding"] != "utf-8" or rbody["offset"] != 0 or rbody["length"] != 12:
            raise SystemExit(f"read meta: {rbody}")

        part, part_err = _call("fs_read", root="data", path="hello.txt", offset=6, length=5)
        if part_err or _body(part)["content"] != "world":
            raise SystemExit(f"partial: {part}")

        b64, b64_err = _call("fs_read", root="data", path="hello.txt", encoding="base64")
        if b64_err:
            raise SystemExit(f"base64: {b64}")
        import base64

        if base64.b64decode(_body(b64)["content"]) != b"hello world\n":
            raise SystemExit(f"base64 payload: {b64}")

        grepped, grep_err = _call("fs_grep", root="logs", path="app.log", pattern="ERROR")
        if grep_err:
            raise SystemExit(f"grep: {grepped}")
        gbody = _body(grepped)
        if gbody["count"] != 2 or gbody["truncated"]:
            raise SystemExit(f"grep count: {gbody}")
        if [row["text"] for row in gbody["matches"]] != ["ERROR boom", "ERROR again"]:
            raise SystemExit(f"grep lines: {gbody}")
    finally:
        env.close()


def test_fs_list_and_read_monthly_sample(tmp_path: Path) -> None:
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    logs.mkdir()
    sample = data / "monthly" / "sample.txt"
    sample.parent.mkdir(parents=True)
    payload = "monthly sample\n"
    sample.write_text(payload, encoding="utf-8")
    env = _Env(data, logs)
    try:
        listed, list_err = _call("fs_list", root="data", path="monthly")
        if list_err:
            raise SystemExit(f"list monthly: {listed}")
        names = {e["name"] for e in _body(listed)["entries"]}
        if names != {"sample.txt"}:
            raise SystemExit(f"monthly names: {names}")
        read, read_err = _call("fs_read", root="data", path="monthly/sample.txt")
        if read_err:
            raise SystemExit(f"read monthly: {read}")
        body = _body(read)
        if body["content"] != payload or body["truncated"] or body["size"] != len(payload.encode()):
            raise SystemExit(f"monthly read: {body}")
    finally:
        env.close()


def test_fs_allows_monthly_pdf_and_xbrl_zip(tmp_path: Path) -> None:
    import base64

    data = tmp_path / "data"
    logs = tmp_path / "logs"
    logs.mkdir()
    pdf_bytes = b"%PDF-1.4 sample"
    zip_bytes = b"PK\x03\x04sample"
    pdf = data / "monthly" / "7203" / "x.pdf"
    zipped = data / "xbrl" / "7203" / "y.zip"
    pdf.parent.mkdir(parents=True)
    zipped.parent.mkdir(parents=True)
    pdf.write_bytes(pdf_bytes)
    zipped.write_bytes(zip_bytes)
    (data / "monthly" / "7203" / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (data / "xbrl" / "7203" / "notes.db").write_text("sqlite\n", encoding="utf-8")
    env = _Env(data, logs)
    try:
        listed, list_err = _call("fs_list", root="data", path="monthly/7203")
        if list_err:
            raise SystemExit(f"list monthly/7203: {listed}")
        names = {e["name"] for e in _body(listed)["entries"]}
        if "x.pdf" not in names:
            raise SystemExit(f"monthly pdf missing: {names}")
        if ".env" in names:
            raise SystemExit(f"monthly .env leaked: {names}")

        pdf_text, pdf_err = _call(
            "fs_read", root="data", path="monthly/7203/x.pdf", encoding="base64",
        )
        if pdf_err:
            raise SystemExit(f"read pdf: {pdf_text}")
        if base64.b64decode(_body(pdf_text)["content"]) != pdf_bytes:
            raise SystemExit(f"pdf payload: {pdf_text}")

        zip_listed, zip_list_err = _call("fs_list", root="data", path="xbrl/7203")
        if zip_list_err:
            raise SystemExit(f"list xbrl/7203: {zip_listed}")
        zip_names = {e["name"] for e in _body(zip_listed)["entries"]}
        if "y.zip" not in zip_names:
            raise SystemExit(f"xbrl zip missing: {zip_names}")
        if "notes.db" in zip_names:
            raise SystemExit(f"xbrl db leaked: {zip_names}")

        zip_text, zip_err = _call(
            "fs_read", root="data", path="xbrl/7203/y.zip", encoding="base64",
        )
        if zip_err:
            raise SystemExit(f"read zip: {zip_text}")
        if base64.b64decode(_body(zip_text)["content"]) != zip_bytes:
            raise SystemExit(f"zip payload: {zip_text}")

        _must_err("fs_read", "denied", root="data", path="monthly/7203/.env")
        _must_err("fs_read", "denied", root="data", path="xbrl/7203/notes.db")
    finally:
        env.close()


def test_fs_unknown_root_and_absolute_and_traversal(tmp_path: Path) -> None:
    data, logs = _layout(tmp_path)
    env = _Env(data, logs)
    try:
        _must_err("fs_list", "root must be data or logs", root="etc")
        _must_err("fs_list", "root must be data or logs", root="home")
        _must_err("fs_read", "root must be data or logs", root="", path="hello.txt")
        _must_err("fs_read", "path must be relative", root="data", path="/etc/passwd")
        _must_err("fs_read", "path must be relative", root="logs", path="/var/log/app/app.log")
        _must_err("fs_read", "outside allowed root", root="data", path="../hello.txt")
        _must_err("fs_read", "outside allowed root", root="data", path="nested/../../hello.txt")
        _must_err("fs_list", "outside allowed root", root="logs", path="..")
        _must_err("fs_read", "path is required", root="data", path="")
        _must_err("fs_read", "path not found", root="data", path="missing.txt")
        _must_err("fs_read", "path is a directory", root="data", path="nested")
    finally:
        env.close()


def test_fs_symlink_escape_rejected(tmp_path: Path) -> None:
    data, logs = _layout(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("should not leak\n", encoding="utf-8")
    (data / "escape").symlink_to(secret)
    (data / "alias").symlink_to(data / "hello.txt")
    env = _Env(data, logs)
    try:
        listed, list_err = _call("fs_list", root="data")
        if list_err:
            raise SystemExit(f"list: {listed}")
        names = {e["name"] for e in _body(listed)["entries"]}
        if "escape" in names:
            raise SystemExit(f"list advertised escape symlink: {names}")
        if "alias" not in names or "hello.txt" not in names:
            raise SystemExit(f"list missing internal: {names}")

        _must_err("fs_read", "outside allowed root", root="data", path="escape")
        _must_err("fs_grep", "outside allowed root", root="data", path="escape", pattern="secret")

        ok, ok_err = _call("fs_read", root="data", path="alias")
        if ok_err or "hello world" not in _body(ok)["content"]:
            raise SystemExit(f"internal symlink should read: {ok}")
        if "should not leak" in ok or "should not leak" in listed:
            raise SystemExit("secret leaked")
    finally:
        env.close()


def test_fs_denylist_and_deny_prefixes(tmp_path: Path) -> None:
    data, logs = _layout(tmp_path)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "dump.txt").write_text("backup dump\n", encoding="utf-8")
    (data / "ok.txt").write_text("visible\n", encoding="utf-8")
    denied = {
        ".env": "SECRET=1\n",
        ".env.local": "SECRET=2\n",
        "stock.db": "sqlite\n",
        "notes.sqlite": "sqlite\n",
        "notes.sqlite3": "sqlite\n",
        "cert.pem": "-----BEGIN\n",
        "id_rsa": "ssh-key\n",
        "id_ed25519": "ssh-key\n",
        "tls.key": "key\n",
        "rclone.conf": "[r2]\n",
    }
    for name, body in denied.items():
        (data / name).write_text(body, encoding="utf-8")
    (data / "blocked_link").symlink_to(blocked / "dump.txt")
    (data / "nested" / ".env").write_text("NESTED=1\n", encoding="utf-8")
    env = _Env(data, logs)
    orig_prefixes = fs.DENY_PREFIXES
    fs.DENY_PREFIXES = orig_prefixes + (blocked,)
    try:
        listed, list_err = _call("fs_list", root="data")
        if list_err:
            raise SystemExit(f"list: {listed}")
        names = {e["name"] for e in _body(listed)["entries"]}
        for name in denied:
            if name in names:
                raise SystemExit(f"list leaked denylisted {name}: {names}")
        if "blocked_link" in names:
            raise SystemExit(f"list leaked deny-prefix symlink: {names}")
        if "ok.txt" not in names or "hello.txt" not in names:
            raise SystemExit(f"list missing allowed: {names}")

        rec, rec_err = _call("fs_list", root="data", recursive=True)
        if rec_err:
            raise SystemExit(f"recursive: {rec}")
        rec_paths = {e["path"] for e in _body(rec)["entries"]}
        if "nested/.env" in rec_paths:
            raise SystemExit(f"recursive leaked nested .env: {rec_paths}")

        for name in denied:
            _must_err("fs_read", "denied", root="data", path=name)
        _must_err("fs_read", "denied", root="data", path="nested/.env")

        leaked, leak_err = _call("fs_read", root="data", path="blocked_link")
        if not leak_err:
            raise SystemExit(f"deny-prefix symlink should isError: {leaked}")
        if "backup dump" in leaked:
            raise SystemExit(f"deny-prefix symlink leaked: {leaked}")

        const_text = Path(fs.__file__).read_text(encoding="utf-8")
        if "/home/stock/backups" not in const_text or "/home/stock/r2-share" not in const_text:
            raise SystemExit("DENY_PREFIXES must include backups and r2-share")
        if not any(Path(p) == Path("/home/stock/backups") for p in orig_prefixes):
            raise SystemExit(f"backups prefix missing: {orig_prefixes}")
        if not any(Path(p) == Path("/home/stock/r2-share") for p in orig_prefixes):
            raise SystemExit(f"r2-share prefix missing: {orig_prefixes}")

        env.close()
        env2 = _Env(blocked, logs)
        try:
            _must_err("fs_list", "denied", root="data")
            _must_err("fs_read", "denied", root="data", path="dump.txt")
        finally:
            env2.close()
    finally:
        fs.DENY_PREFIXES = orig_prefixes
        env.close()


def test_fs_truncation_returns_total_size(tmp_path: Path) -> None:
    data, logs = _layout(tmp_path)
    payload = ("abcdefghij" * 20 + "\n") * 10  # 2100 bytes
    (data / "big.txt").write_text(payload, encoding="utf-8")
    env = _Env(data, logs, MCP_FS_MAX_BYTES="64")
    try:
        if fs.max_read_bytes() != 64:
            raise SystemExit(f"max_read_bytes: {fs.max_read_bytes()}")
        text, is_error = _call("fs_read", root="data", path="big.txt")
        if is_error:
            raise SystemExit(f"read big: {text}")
        body = _body(text)
        if body["size"] != len(payload.encode("utf-8")):
            raise SystemExit(f"size: {body['size']} != {len(payload)}")
        if not body["truncated"]:
            raise SystemExit(f"should truncate: {body}")
        if body["length"] != 64 or len(body["content"].encode("utf-8")) != 64:
            raise SystemExit(f"length: {body['length']} content={body['content']!r}")
        if body["content"] != payload[:64]:
            raise SystemExit(f"prefix: {body['content']!r}")

        rest, rest_err = _call("fs_read", root="data", path="big.txt", offset=64, length=64)
        if rest_err or not _body(rest)["truncated"]:
            raise SystemExit(f"second window: {rest}")
        if _body(rest)["size"] != body["size"]:
            raise SystemExit("size must stay the full file size")

        listed, list_err = _call("fs_list", root="data", limit=1)
        if list_err:
            raise SystemExit(f"list limit: {listed}")
        lbody = _body(listed)
        if lbody["count"] != 1 or not lbody["truncated"]:
            raise SystemExit(f"list truncation: {lbody}")

        grepped, grep_err = _call(
            "fs_grep", root="logs", path="app.log", pattern="ERROR", max_matches=1,
        )
        if grep_err:
            raise SystemExit(f"grep cap: {grepped}")
        gbody = _body(grepped)
        if gbody["count"] != 1 or not gbody["truncated"]:
            raise SystemExit(f"grep truncation: {gbody}")
    finally:
        env.close()


def test_fs_same_auth_as_other_mcp_tools(tmp_path: Path) -> None:
    data, logs = _layout(tmp_path)
    env = _Env(data, logs)
    try:
        client = TestClient(m.create_app("test-token-for-ci-only"))
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "fs_list", "arguments": {"root": "data"}}},
        )
        if r.status_code != 401:
            raise SystemExit(f"missing token should 401, got {r.status_code}")
        r = _rpc(client, "tools/call", {"name": "fs_list", "arguments": {"root": "data"}}, req_id=2)
        if r.status_code != 200 or r.json()["result"]["isError"]:
            raise SystemExit(f"fs_list rpc: {r.text}")
        names = {e["name"] for e in json.loads(r.json()["result"]["content"][0]["text"])["entries"]}
        if "hello.txt" not in names:
            raise SystemExit(f"rpc list: {names}")
        r = _rpc(
            client,
            "tools/call",
            {"name": "fs_read", "arguments": {"root": "data", "path": "../.env"}},
            req_id=3,
        )
        if r.status_code != 200 or not r.json()["result"]["isError"]:
            raise SystemExit(f"rpc traversal should isError: {r.text}")
    finally:
        env.close()


def test_fs_tools_registered_readonly() -> None:
    for name in ("fs_list", "fs_read", "fs_grep"):
        if name not in m.TOOLS:
            raise SystemExit(f"missing tool {name}")
        desc, schema, fn = m.TOOLS[name]
        if "読み取り専用" not in desc:
            raise SystemExit(f"{name} description must say read-only")
        if schema["properties"]["root"]["enum"] != ["data", "logs"]:
            raise SystemExit(f"{name} root enum")
        if fn is m.tool_service_status:
            raise SystemExit(f"{name} bound to wrong function")
    src = Path(fs.__file__).read_text(encoding="utf-8")
    for bad in ("os.remove", "unlink(", "write_text", "open(path, \"w\"", "chmod", "subprocess"):
        if bad in src:
            raise SystemExit(f"fs_tools contains write/exec path: {bad}")
