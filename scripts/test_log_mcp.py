"""MCP サーバの認証・JSON-RPC・入力バリデーション。systemd は不要。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "deploy"))

os.environ.setdefault("MCP_TOKEN", "test-token-for-ci-only")
os.environ["MCP_APP_DIR"] = str(ROOT)

from starlette.testclient import TestClient  # noqa: E402

import log_mcp as m  # noqa: E402


def _rpc(client: TestClient, method: str, params: dict | None = None, token: str = "test-token-for-ci-only", req_id: int = 1):
    body = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers={"Authorization": f"Bearer {token}"})


def test_since_and_grep() -> None:
    assert m.parse_since("1h") == "1 hours ago"
    assert m.parse_since("30m") == "30 minutes ago"
    assert m.parse_since("today") == "today"
    assert m.parse_since("2026-08-24") == "2026-08-24"
    try:
        m.parse_since("--user=root")
    except ValueError:
        pass
    else:
        raise SystemExit("since must reject option-like strings")
    try:
        m.parse_grep("-n; rm -rf /")
    except ValueError:
        pass
    else:
        raise SystemExit("grep must reject dash-prefix")
    try:
        m.parse_grep("a" * 200)
    except ValueError:
        pass
    else:
        raise SystemExit("grep must cap length")


def test_tail_grep(tmp_path: Path) -> None:
    p = tmp_path / "batch.log"
    p.write_text("ok line\nERROR boom\nJQUANTS_API_KEY=secret-value\n", encoding="utf-8")
    out = m.tail_grep(p, 10, "ERROR")
    if "ERROR boom" not in out or "ok line" in out:
        raise SystemExit(f"tail_grep filter failed: {out!r}")


def test_pick_log(tmp_path: Path) -> None:
    app = tmp_path / "app"
    logs = tmp_path / "logs"
    app.mkdir(parents=True)
    logs.mkdir(parents=True)
    (app / "batch.log").write_text("old\n", encoding="utf-8")
    prev_app = os.environ.get("MCP_APP_DIR")
    prev_log = os.environ.get("MCP_LOG_DIR")
    os.environ["MCP_APP_DIR"] = str(app)
    os.environ.pop("MCP_LOG_DIR", None)
    try:
        if m.pick_log("batch.log") != app / "batch.log":
            raise SystemExit(f"fallback app_dir failed: {m.pick_log('batch.log')}")
        (logs / "batch.log").write_text("new\n", encoding="utf-8")
        os.environ["MCP_LOG_DIR"] = str(logs)
        if m.pick_log("batch.log") != logs / "batch.log":
            raise SystemExit(f"prefer log_dir failed: {m.pick_log('batch.log')}")
    finally:
        if prev_app is None:
            os.environ.pop("MCP_APP_DIR", None)
        else:
            os.environ["MCP_APP_DIR"] = prev_app
        if prev_log is None:
            os.environ.pop("MCP_LOG_DIR", None)
        else:
            os.environ["MCP_LOG_DIR"] = prev_log


def test_http_auth_and_tools() -> None:
    token = "test-token-for-ci-only"
    client = TestClient(m.create_app(token))

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}})
    if r.status_code != 401:
        raise SystemExit(f"missing token should 401, got {r.status_code}")

    r = _rpc(client, "initialize", {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
    if r.status_code != 200:
        raise SystemExit(f"initialize: {r.status_code} {r.text}")
    result = r.json()["result"]
    if result["serverInfo"]["name"] != "stock-analyzer-logs":
        raise SystemExit(f"bad initialize: {result}")

    r = _rpc(client, "tools/list", req_id=2)
    names = {t["name"] for t in r.json()["result"]["tools"]}
    expect = {
        "service_status",
        "app_logs",
        "batch_logs",
        "nginx_logs",
        "db_stats",
        "db_lookup",
        "monthly_list",
        "monthly_get",
        "monthly_add_element",
        "monthly_put_values",
        "monthly_ingest",
    }
    if names != expect:
        raise SystemExit(f"tools {names} != {expect}")

    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        Path(d, "batch.log").write_text("hello batch\nERROR failed ingest\n", encoding="utf-8")
        r = _rpc(client, "tools/call", {"name": "batch_logs", "arguments": {"grep": "ERROR", "lines": 50}}, req_id=3)
        text = r.json()["result"]["content"][0]["text"]
        if "ERROR failed ingest" not in text or "hello batch" in text:
            raise SystemExit(f"batch_logs: {text!r}")
        if r.json()["result"]["isError"]:
            raise SystemExit("batch_logs should not be isError")

    r = _rpc(client, "tools/call", {"name": "app_logs", "arguments": {"since": "; reboot"}}, req_id=4)
    if not r.json()["result"]["isError"]:
        raise SystemExit("bad since should set isError")

    r = client.get("/mcp", params={"token": token})
    if r.status_code != 200 or "stock-analyzer-logs" not in r.text:
        raise SystemExit(f"GET /mcp: {r.status_code} {r.text}")

    r = _rpc(client, "initialize", token="wrong-token")
    if r.status_code != 401:
        raise SystemExit("wrong token should 401")

    r = _rpc(client, "tools/call", {"name": "batch_logs", "arguments": {}}, req_id=5)
    sse = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 6, "method": "ping"},
        headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"},
    )
    if "text/event-stream" not in sse.headers.get("content-type", ""):
        raise SystemExit(f"SSE content-type: {sse.headers.get('content-type')}")
    if "event: message" not in sse.text:
        raise SystemExit(f"SSE body: {sse.text!r}")
    _ = r, json  # silence unused if ping path changes


def _pkce() -> tuple[str, str]:
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def test_oauth() -> None:
    from tempfile import TemporaryDirectory

    token = "test-token-for-ci-only"
    prev = os.environ.get("MCP_APP_DIR")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        try:
            _test_oauth_inner(token)
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev


def _test_oauth_inner(token: str) -> None:
    client = TestClient(m.create_app(token))

    r = client.get("/.well-known/oauth-authorization-server")
    if r.status_code != 200:
        raise SystemExit(f"as metadata: {r.status_code} {r.text}")
    meta = r.json()
    if meta["token_endpoint_auth_methods_supported"] != ["none"]:
        raise SystemExit(f"auth methods: {meta}")
    if "S256" not in meta["code_challenge_methods_supported"]:
        raise SystemExit("S256 missing")
    if not meta["authorization_endpoint"].endswith("/oauth/authorize"):
        raise SystemExit(f"authorize: {meta['authorization_endpoint']}")

    r = client.get("/.well-known/openid-configuration")
    if r.status_code != 200:
        raise SystemExit(f"openid-configuration: {r.status_code} {r.text}")
    oidc = r.json()
    if oidc["authorization_endpoint"] != meta["authorization_endpoint"]:
        raise SystemExit(f"openid authorize: {oidc['authorization_endpoint']}")

    r = client.get("/.well-known/oauth-protected-resource")
    if r.status_code != 200 or "/mcp" not in r.json().get("resource", ""):
        raise SystemExit(f"pr metadata: {r.status_code} {r.text}")

    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    www = r.headers.get("www-authenticate", "")
    if r.status_code != 401 or "resource_metadata=" not in www:
        raise SystemExit(f"401 www: {r.status_code} {www}")

    verifier, challenge = _pkce()
    q = {
        "response_type": "code",
        "client_id": "stock-analyzer",
        "redirect_uri": "https://grok.com/oauth/callback",
        "state": "st1",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    r = client.get("/oauth/authorize", params=q)
    if r.status_code != 200 or "MCP_TOKEN" not in r.text:
        raise SystemExit(f"authorize get: {r.status_code} {r.text[:200]}")

    r = client.post(
        "/oauth/authorize",
        data={**q, "redirect_uri": "https://evil.example/cb", "token": token},
        follow_redirects=False,
    )
    if r.status_code != 400:
        raise SystemExit(f"evil redirect should 400, got {r.status_code}")

    r = client.post("/oauth/authorize", data={**q, "token": "wrong"}, follow_redirects=False)
    if r.status_code != 401:
        raise SystemExit(f"wrong token should 401, got {r.status_code}")

    r = client.post("/oauth/authorize", data={**q, "token": token}, follow_redirects=False)
    if r.status_code != 302:
        raise SystemExit(f"authorize post: {r.status_code} {r.text[:200]}")
    loc = r.headers.get("location") or ""
    if "code=" not in loc or "state=st1" not in loc:
        raise SystemExit(f"redirect: {loc}")
    from urllib.parse import parse_qs, urlparse

    code = parse_qs(urlparse(loc).query)["code"][0]

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": q["redirect_uri"],
            "client_id": "stock-analyzer",
            "code_verifier": verifier,
        },
    )
    if r.status_code != 200:
        raise SystemExit(f"token: {r.status_code} {r.text}")
    body = r.json()
    access = body["access_token"]
    refresh = body["refresh_token"]
    if body["token_type"] != "Bearer":
        raise SystemExit(body)

    r = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": q["redirect_uri"],
            "client_id": "stock-analyzer",
            "code_verifier": verifier,
        },
    )
    if r.status_code != 400:
        raise SystemExit(f"replayed code should 400, got {r.status_code} {r.text}")

    r = _rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
        token=access,
    )
    if r.status_code != 200:
        raise SystemExit(f"mcp with access token: {r.status_code} {r.text}")

    r = client.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh})
    if r.status_code != 200 or "access_token" not in r.json():
        raise SystemExit(f"refresh: {r.status_code} {r.text}")

    r = client.post(
        "/oauth/register",
        json={"client_name": "Grok", "redirect_uris": ["https://grok.com/oauth/callback"], "token_endpoint_auth_method": "none"},
    )
    if r.status_code != 201 or not r.json().get("client_id"):
        raise SystemExit(f"dcr: {r.status_code} {r.text}")

    if m.redirect_ok("https://evil.example/cb"):
        raise SystemExit("redirect_ok allowed evil host")
    if not m.redirect_ok("https://grok.com/oauth/callback"):
        raise SystemExit("redirect_ok rejected grok.com")
    if not m.redirect_ok("https://www.cursor.com/agents/mcp/oauth/callback"):
        raise SystemExit("redirect_ok rejected www.cursor.com")
    if not m.redirect_ok("https://cursor.com/agents/mcp/oauth/callback"):
        raise SystemExit("redirect_ok rejected cursor.com")
    if not m.redirect_ok("cursor://anysphere.cursor-mcp/oauth/callback"):
        raise SystemExit("redirect_ok rejected cursor:// callback")
    if m.redirect_ok("https://cursor.evil.example/cb"):
        raise SystemExit("redirect_ok allowed cursor lookalike host")


def test_db_tools() -> None:
    from tempfile import TemporaryDirectory

    token = "test-token-for-ci-only"
    prev = os.environ.get("MCP_APP_DIR")
    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["MCP_APP_DIR"] = d
        os.environ.pop("DATABASE_URL", None)
        dbp = Path(d) / "stock.db"
        import sqlite3

        conn = sqlite3.connect(dbp)
        conn.executescript(
            """
            CREATE TABLE stocks (code TEXT PRIMARY KEY, name TEXT, market TEXT, sector33 TEXT, scale_category TEXT);
            CREATE TABLE daily_prices (code TEXT, date TEXT, close REAL, adj_close REAL, adj_volume REAL, PRIMARY KEY(code, date));
            CREATE TABLE topix (date TEXT PRIMARY KEY, close REAL);
            CREATE TABLE statements (disclosure_no TEXT PRIMARY KEY, code TEXT, disclosed_date TEXT, type_of_period TEXT, type_of_document TEXT);
            CREATE TABLE indicators (code TEXT, date TEXT, stage INTEGER, rs_rank INTEGER, ma50 REAL, ma150 REAL, ma200 REAL, PRIMARY KEY(code, date));
            INSERT INTO stocks VALUES ('07203','デモ','', '', '');
            INSERT INTO stocks VALUES ('72030','トヨタ','プライム','輸送用機器','TOPIX Large70');
            INSERT INTO daily_prices VALUES ('72030','2026-08-20',3000,3000,1000);
            INSERT INTO daily_prices VALUES ('72030','2026-08-21',3010,3010,1100);
            INSERT INTO topix VALUES ('2026-08-21', 2800);
            INSERT INTO statements VALUES ('1','72030','2026-05-08','FY','FinancialStatements');
            INSERT INTO indicators VALUES ('72030','2026-08-21',2,85,2900,2800,2700);
            """
        )
        conn.commit()
        conn.close()
        try:
            if m.parse_code("7203") != "72030":
                raise SystemExit("parse_code 7203")
            try:
                m.parse_code("drop table")
            except ValueError:
                pass
            else:
                raise SystemExit("parse_code must reject junk")
            text = m.tool_db_stats({})
            if "72030" in text or "トヨタ" in text:
                pass
            if "stocks: 2" not in text or "demo=1" not in text:
                raise SystemExit(f"db_stats: {text}")
            if "daily_prices: 2" not in text or "2026-08-20..2026-08-21" not in text:
                raise SystemExit(f"db_stats prices: {text}")
            looked = m.tool_db_lookup({"code": "7203", "lines": 5})
            if "72030" not in looked or "トヨタ" not in looked or "adj=3010" not in looked:
                raise SystemExit(f"db_lookup: {looked}")
            missing = m.tool_db_lookup({"code": "99990"})
            if "not in stocks" not in missing:
                raise SystemExit(f"missing: {missing}")
            client = TestClient(m.create_app(token))
            r = _rpc(client, "tools/call", {"name": "db_stats", "arguments": {}}, req_id=90)
            if r.status_code != 200 or "stocks: 2" not in r.json()["result"]["content"][0]["text"]:
                raise SystemExit(f"rpc db_stats: {r.text}")
        finally:
            if prev is None:
                os.environ.pop("MCP_APP_DIR", None)
            else:
                os.environ["MCP_APP_DIR"] = prev
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url


def _http_status_body(text: str) -> tuple[int, object]:
    line, _, rest = text.partition("\n")
    if not line.startswith("HTTP "):
        raise SystemExit(f"missing HTTP status line: {text!r}")
    status = int(line.split()[1])
    rest = rest.strip()
    if not rest:
        return status, None
    try:
        return status, json.loads(rest)
    except json.JSONDecodeError:
        return status, rest


def _expect_value_error(fn, *args, needle: str) -> None:
    try:
        fn(*args)
    except ValueError as exc:
        if needle not in str(exc):
            raise SystemExit(f"ValueError {exc!r} missing {needle!r}")
    else:
        raise SystemExit(f"{fn.__name__}{args} should have raised")


def _stub_handle(method: str, path: str, raw: bytes, store: dict) -> tuple[int, object]:
    import re
    from urllib.parse import unquote

    def load_body() -> dict:
        if not raw:
            return {}
        body = json.loads(raw.decode("utf-8"))
        if not isinstance(body, dict):
            return {}
        return body

    list_re = re.compile(r"^/api/v1/tables$")
    get_re = re.compile(r"^/api/v1/tables/(\d{4,5})/(\d{4})$")
    el_re = re.compile(r"^/api/v1/tables/(\d{4,5})/(\d{4})/elements$")
    batch_re = re.compile(r"^/api/v1/tables/(\d{4,5})/(\d{4})/values/batch$")
    ingest_re = re.compile(r"^/api/v1/tables/(\d{4,5})/ingest$")

    if method == "GET" and list_re.fullmatch(path):
        by_code: dict[str, dict] = {}
        for code, year in sorted(store):
            item = by_code.setdefault(code, {"code": code, "name": None, "years": []})
            item["years"].append(year)
        return 200, list(by_code.values())

    m = get_re.fullmatch(path)
    if method == "GET" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        row = store.get((code, year))
        if row is None:
            return 404, {"error": {"code": "TABLE_NOT_FOUND", "message": "銘柄×年度の表がありません。"}}
        elements = []
        for el in row["order"]:
            vals = {str(k): v for k, v in row["values"].get(el, {}).items()}
            elements.append({"element": el, "values": vals})
        return 200, {"code": code, "year": year, "elements": elements}

    m = el_re.fullmatch(path)
    if method == "POST" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        body = load_body()
        element = str(body.get("element") or "").strip()
        force = body.get("force") is True
        key = (code, year)
        row = store.setdefault(key, {"order": [], "values": {}})
        if element in row["order"]:
            return 200, {"code": code, "year": year, "element": element, "created": False}

        def _norm(s: str) -> str:
            return "".join(str(s).split()).lower()

        similar = [e for e in row["order"] if _norm(e) == _norm(element)]
        if similar and not force:
            return 409, {
                "error": {
                    "code": "ELEMENT_SIMILAR_EXISTS",
                    "message": "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。",
                    "similar": similar,
                }
            }
        row["order"].append(element)
        row["values"][element] = {}
        return 201, {"code": code, "year": year, "element": element, "created": True}

    m = batch_re.fullmatch(path)
    if method == "PUT" and m:
        code, year = unquote(m.group(1)), int(m.group(2))
        body = load_body()
        element = str(body.get("element") or "").strip()
        items = body.get("values")
        key = (code, year)
        row = store.get(key)
        if row is None:
            return 404, {"error": {"code": "TABLE_NOT_FOUND", "message": "銘柄×年度の表がありません。"}}
        if element not in row["order"]:
            return 404, {"error": {"code": "ELEMENT_NOT_FOUND", "message": "要素が登録されていません。"}}
        if not isinstance(items, list):
            return 400, {"error": {"code": "INVALID_PARAM", "message": "values は {month, value} の配列です。"}}
        ops: dict[int, object] = {}
        for item in items:
            val = item.get("value")
            if val is not None and (isinstance(val, bool) or not isinstance(val, (int, float))):
                return 400, {
                    "error": {
                        "code": "INVALID_VALUE",
                        "message": "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。",
                    }
                }
            ops[int(item["month"])] = val
        for month, val in ops.items():
            if val is None:
                row["values"][element].pop(month, None)
            else:
                row["values"][element][month] = val
        return 200, {"code": code, "year": year, "element": element, "updated": list(ops.keys())}

    m = ingest_re.fullmatch(path)
    if method == "POST" and m:
        code = unquote(m.group(1))
        body = load_body()
        if "year" in body:
            return 400, {"error": {"code": "INVALID_PARAM", "message": "year must not be sent"}}
        return 200, {
            "code": code,
            "fy_end_month": 10,
            "layout": "fiscal",
            "as_of": body.get("as_of"),
            "mapping": {},
            "updated": [],
        }

    return 404, {"error": {"code": "NOT_FOUND", "message": path}}


def _monthly_stub():
    import threading
    from contextlib import contextmanager
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse as _urlparse

    @contextmanager
    def ctx():
        recorded: list[dict] = []
        store: dict = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # noqa: A003
                return

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

            def do_PUT(self) -> None:
                self._dispatch("PUT")

            def _dispatch(self, method: str) -> None:
                path = _urlparse(self.path).path
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                recorded.append({"method": method, "path": path, "host": self.headers.get("Host", ""), "body": raw})
                status, payload = _stub_handle(method, path, raw, store)
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd.server_address[1], recorded
        finally:
            httpd.shutdown()
            httpd.server_close()

    return ctx()


def test_monthly_tools() -> None:
    from tempfile import TemporaryDirectory
    from urllib.parse import urlparse

    if m.APP_HTTP_TIMEOUT != 10:
        raise SystemExit(f"APP_HTTP_TIMEOUT {m.APP_HTTP_TIMEOUT} != 10")
    if m.DEFAULT_APP_HTTP != "http://127.0.0.1:8000":
        raise SystemExit(f"DEFAULT_APP_HTTP {m.DEFAULT_APP_HTTP}")

    prev_http = os.environ.get("MCP_APP_HTTP")
    os.environ.pop("MCP_APP_HTTP", None)
    try:
        if m.app_http_base() != "http://127.0.0.1:8000":
            raise SystemExit(f"default base: {m.app_http_base()}")
        url = m.monthly_api_url("7203", 2024)
        parsed = urlparse(url)
        if parsed.hostname != "127.0.0.1" or parsed.port != 8000:
            raise SystemExit(f"url host: {url}")
        if parsed.path != "/api/v1/tables/7203/2024":
            raise SystemExit(f"url path not padded? {parsed.path}")
        if m.monthly_api_url() != "http://127.0.0.1:8000/api/v1/tables":
            raise SystemExit(f"list url: {m.monthly_api_url()}")
        if m.monthly_api_url("7203", 2024, "/elements") != "http://127.0.0.1:8000/api/v1/tables/7203/2024/elements":
            raise SystemExit("elements url")
        if m.monthly_api_url("7203", 2024, "/values/batch") != "http://127.0.0.1:8000/api/v1/tables/7203/2024/values/batch":
            raise SystemExit("batch url")
        if m.monthly_ingest_url("92790") != "http://127.0.0.1:8000/api/v1/tables/92790/ingest":
            raise SystemExit(f"ingest url: {m.monthly_ingest_url('92790')}")
        if m.monthly_api_url("92790", tail="/ingest") != "http://127.0.0.1:8000/api/v1/tables/92790/ingest":
            raise SystemExit("ingest via monthly_api_url")
        ingest_parsed = urlparse(m.monthly_ingest_url("7203"))
        if ingest_parsed.path != "/api/v1/tables/7203/ingest":
            raise SystemExit(f"ingest path has year?: {ingest_parsed.path}")
        if ingest_parsed.hostname != "127.0.0.1":
            raise SystemExit(f"ingest host: {ingest_parsed.hostname}")
        try:
            m.monthly_api_url("7203", 2024, "/etc/passwd")
        except ValueError:
            pass
        else:
            raise SystemExit("arbitrary tail should fail")
        try:
            m.monthly_api_url("7203", 2024, "/ingest")
        except ValueError:
            pass
        else:
            raise SystemExit("year-bearing ingest URL should fail")

        os.environ["MCP_APP_HTTP"] = "http://kabukpi.com/api"
        _expect_value_error(m.app_http_base, needle="loopback")
        os.environ["MCP_APP_HTTP"] = "http://8.8.8.8:8000"
        _expect_value_error(m.app_http_base, needle="loopback")
        os.environ["MCP_APP_HTTP"] = "https://example.com"
        _expect_value_error(m.app_http_base, needle="loopback")
        os.environ["MCP_APP_HTTP"] = "http://127.0.0.1:9"
        if m.app_http_base() != "http://127.0.0.1:9":
            raise SystemExit(f"loopback override: {m.app_http_base()}")
        os.environ["MCP_APP_HTTP"] = "http://localhost:8000/ignored"
        if m.app_http_base() != "http://localhost:8000":
            raise SystemExit(f"path should be stripped: {m.app_http_base()}")
    finally:
        if prev_http is None:
            os.environ.pop("MCP_APP_HTTP", None)
        else:
            os.environ["MCP_APP_HTTP"] = prev_http

    _expect_value_error(m.parse_monthly_code, "abc", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "12", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "7203/../etc", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "7203.0", needle="4-5")
    _expect_value_error(m.parse_monthly_code, "720300", needle="4-5")
    if m.parse_monthly_code("7203") != "7203":
        raise SystemExit("monthly code must not pad 7203")
    if m.parse_monthly_code("72030") != "72030":
        raise SystemExit("72030")
    _expect_value_error(m.parse_monthly_year, 24, needle="4-digit")
    _expect_value_error(m.parse_monthly_year, "2024/../x", needle="4-digit")
    _expect_value_error(m.parse_monthly_year, 20240, needle="4-digit")
    if m.parse_monthly_year(2024) != 2024 or m.parse_monthly_year("2024") != 2024:
        raise SystemExit("year parse")
    _expect_value_error(m.parse_monthly_month, 0, needle="1-12")
    _expect_value_error(m.parse_monthly_month, 13, needle="1-12")

    prev_url = os.environ.get("DATABASE_URL")
    with TemporaryDirectory() as d:
        os.environ["DATABASE_URL"] = f"sqlite:///{d}/stock.db"
        try:
            from fastapi.testclient import TestClient as FastAPIClient

            from app.main import app as fastapi_app

            api = FastAPIClient(fastapi_app)
            r = api.get("/api/v1/tables")
            if r.status_code != 200 or r.json() != []:
                raise SystemExit(f"TestClient empty tables: {r.status_code} {r.text}")
            r = api.post("/api/v1/tables/7203/2024/elements", json={"element": "売上高前年比"})
            if r.status_code != 201:
                raise SystemExit(f"TestClient add: {r.status_code} {r.text}")
            r = api.get("/api/v1/tables/7203/2024")
            if r.status_code != 200 or [e["element"] for e in r.json()["elements"]] != ["売上高前年比"]:
                raise SystemExit(f"TestClient get: {r.text}")
        finally:
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url

    prev_http = os.environ.get("MCP_APP_HTTP")
    try:
        with _monthly_stub() as (port, recorded):
            os.environ["MCP_APP_HTTP"] = f"http://127.0.0.1:{port}"
            token = "test-token-for-ci-only"
            client = TestClient(m.create_app(token))

            before = len(recorded)
            for bad in ("abc", "12", "7203/../etc", "7203.0"):
                text, is_error = m.call_tool("monthly_get", {"code": bad, "year": 2024})
                if not is_error:
                    raise SystemExit(f"bad code {bad!r} should isError: {text}")
                text, is_error = m.call_tool(
                    "monthly_add_element", {"code": bad, "year": 2024, "element": "x"}
                )
                if not is_error:
                    raise SystemExit(f"add bad code {bad!r} should isError")
                text, is_error = m.call_tool(
                    "monthly_put_values",
                    {"code": bad, "year": 2024, "element": "x", "values": [{"month": 1, "value": 1}]},
                )
                if not is_error:
                    raise SystemExit(f"put bad code {bad!r} should isError")
            if len(recorded) != before:
                raise SystemExit(f"bad code must not hit HTTP: {recorded[before:]}")

            text, is_error = m.call_tool("monthly_list", {})
            if is_error:
                raise SystemExit(f"monthly_list empty: {text}")
            status, body = _http_status_body(text)
            if status != 200 or body != []:
                raise SystemExit(f"empty list shape: {text!r}")

            r = _rpc(client, "tools/call", {"name": "monthly_list", "arguments": {}}, req_id=40)
            rpc_text = r.json()["result"]["content"][0]["text"]
            if r.json()["result"]["isError"] or "HTTP 200" not in rpc_text:
                raise SystemExit(f"rpc monthly_list: {r.text}")

            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "7203", "year": 2024, "element": "売上高前年比"},
            )
            if is_error:
                raise SystemExit(f"add: {text}")
            status, body = _http_status_body(text)
            if status != 201 or not isinstance(body, dict) or body.get("created") is not True:
                raise SystemExit(f"add created: {text}")

            text, is_error = m.call_tool("monthly_get", {"code": "7203", "year": 2024})
            if is_error:
                raise SystemExit(f"get after add: {text}")
            status, body = _http_status_body(text)
            if status != 200 or body["elements"][0]["element"] != "売上高前年比":
                raise SystemExit(f"get roundtrip: {text}")

            text, is_error = m.call_tool(
                "monthly_add_element",
                {"code": "7203", "year": 2024, "element": "売上高 前年比"},
            )
            if is_error:
                raise SystemExit(f"similar should still return HTTP text: {text}")
            status, body = _http_status_body(text)
            if status != 409 or not isinstance(body, dict) or body.get("error", {}).get("code") != "ELEMENT_SIMILAR_EXISTS":
                raise SystemExit(f"expected 409 ELEMENT_SIMILAR_EXISTS: {text}")

            text, is_error = m.call_tool(
                "monthly_put_values",
                {
                    "code": "7203",
                    "year": 2024,
                    "element": "売上高前年比",
                    "values": [{"month": 4, "value": 12.5}, {"month": 5, "value": None}],
                },
            )
            if is_error:
                raise SystemExit(f"put: {text}")
            status, body = _http_status_body(text)
            if status != 200:
                raise SystemExit(f"put status: {text}")

            text, is_error = m.call_tool(
                "monthly_put_values",
                {
                    "code": "7203",
                    "year": 2024,
                    "element": "売上高前年比",
                    "values": [{"month": 4, "value": "10%"}],
                },
            )
            if is_error:
                raise SystemExit(f"% value should be API error text: {text}")
            status, body = _http_status_body(text)
            if status != 400 or not isinstance(body, dict) or body.get("error", {}).get("code") != "INVALID_VALUE":
                raise SystemExit(f"expected INVALID_VALUE: {text}")

            r = _rpc(
                client,
                "tools/call",
                {"name": "monthly_get", "arguments": {"code": "7203/../etc", "year": 2024}},
                req_id=41,
            )
            if not r.json()["result"]["isError"]:
                raise SystemExit("rpc traversal code should isError")

            allowed = {
                ("GET", "/api/v1/tables"),
                ("GET", "/api/v1/tables/7203/2024"),
                ("POST", "/api/v1/tables/7203/2024/elements"),
                ("PUT", "/api/v1/tables/7203/2024/values/batch"),
            }
            for rec in recorded:
                host = rec["host"].split(":")[0]
                if host != "127.0.0.1":
                    raise SystemExit(f"non-loopback host: {rec}")
                if not rec["path"].startswith("/api/v1/tables"):
                    raise SystemExit(f"path not allowlisted: {rec}")
                if ".." in rec["path"] or "/etc" in rec["path"]:
                    raise SystemExit(f"traversal path: {rec}")
                if (rec["method"], rec["path"]) not in allowed:
                    raise SystemExit(f"unexpected monthly request: {rec}")
            if not any(rec["method"] == "GET" and rec["path"] == "/api/v1/tables" for rec in recorded):
                raise SystemExit("monthly_list did not hit GET /api/v1/tables")
            if not any(rec["method"] == "POST" and rec["path"] == "/api/v1/tables/7203/2024/elements" for rec in recorded):
                raise SystemExit("add_element path missing")
            if not any(rec["method"] == "PUT" and rec["path"] == "/api/v1/tables/7203/2024/values/batch" for rec in recorded):
                raise SystemExit("put_values path missing")

        os.environ["MCP_APP_HTTP"] = "http://127.0.0.1:1"
        text, is_error = m.call_tool("monthly_list", {})
        if not is_error:
            raise SystemExit(f"down FastAPI should isError: {text}")
        if "connection refused" not in text.lower() and "unreachable" not in text.lower():
            raise SystemExit(f"expected connection refused: {text!r}")
        if "Traceback" in text:
            raise SystemExit(f"traceback leaked: {text}")
    finally:
        if prev_http is None:
            os.environ.pop("MCP_APP_HTTP", None)
        else:
            os.environ["MCP_APP_HTTP"] = prev_http


def test_monthly_ingest_tool() -> None:
    prev_http = os.environ.get("MCP_APP_HTTP")
    try:
        with _monthly_stub() as (port, recorded):
            os.environ["MCP_APP_HTTP"] = f"http://127.0.0.1:{port}"
            token = "test-token-for-ci-only"
            client = TestClient(m.create_app(token))

            before = len(recorded)
            for bad in ("abc", "12", "7203/../etc", "7203.0"):
                text, is_error = m.call_tool(
                    "monthly_ingest",
                    {"code": bad, "months": [11, 12, 1], "rows": [{"element": "x", "values": {"11": 1}}]},
                )
                if not is_error:
                    raise SystemExit(f"ingest bad code {bad!r} should isError: {text}")
            if len(recorded) != before:
                raise SystemExit(f"ingest bad code must not hit HTTP: {recorded[before:]}")

            text, is_error = m.call_tool(
                "monthly_ingest",
                {
                    "code": "92790",
                    "year": 2025,
                    "months": [11, 12, 1, 2, 3, 4, 5, 6, 7],
                    "rows": [{"element": "全店 売上高", "values": {"11": 125.8, "12": 124.7, "1": 125.9}}],
                    "as_of": "2026-08-05",
                },
            )
            if is_error:
                raise SystemExit(f"monthly_ingest: {text}")
            status, body = _http_status_body(text)
            if status != 200 or not isinstance(body, dict) or body.get("code") != "92790":
                raise SystemExit(f"ingest body: {text}")

            r = _rpc(
                client,
                "tools/call",
                {
                    "name": "monthly_ingest",
                    "arguments": {
                        "code": "7203",
                        "months": [1, 2, 3],
                        "rows": [{"element": "売上高", "values": {"1": 1.0}}],
                    },
                },
                req_id=50,
            )
            rpc_text = r.json()["result"]["content"][0]["text"]
            if r.json()["result"]["isError"] or "HTTP 200" not in rpc_text:
                raise SystemExit(f"rpc monthly_ingest: {r.text}")

            if not recorded:
                raise SystemExit("monthly_ingest recorded nothing")
            allowed = {
                ("POST", "/api/v1/tables/92790/ingest"),
                ("POST", "/api/v1/tables/7203/ingest"),
            }
            for rec in recorded:
                host = rec["host"].split(":")[0]
                if host != "127.0.0.1":
                    raise SystemExit(f"non-loopback host: {rec}")
                if (rec["method"], rec["path"]) not in allowed:
                    raise SystemExit(f"unexpected ingest request: {rec}")
                if "/202" in rec["path"] or rec["path"].count("/") != 5:
                    raise SystemExit(f"ingest path must be /api/v1/tables/{{code}}/ingest: {rec}")
                payload = json.loads(rec["body"].decode("utf-8")) if rec["body"] else {}
                if "year" in payload:
                    raise SystemExit(f"ingest body must not include year: {payload}")
                if "months" not in payload or "rows" not in payload:
                    raise SystemExit(f"ingest body missing months/rows: {payload}")
            if not any(rec["path"] == "/api/v1/tables/92790/ingest" for rec in recorded):
                raise SystemExit("did not POST /api/v1/tables/92790/ingest")
    finally:
        if prev_http is None:
            os.environ.pop("MCP_APP_HTTP", None)
        else:
            os.environ["MCP_APP_HTTP"] = prev_http


def main() -> None:
    from tempfile import TemporaryDirectory

    test_since_and_grep()
    with TemporaryDirectory() as d:
        test_tail_grep(Path(d))
        test_pick_log(Path(d) / "pick")
    test_http_auth_and_tools()
    test_oauth()
    test_db_tools()
    test_monthly_tools()
    test_monthly_ingest_tool()
    print("ok: log mcp")


if __name__ == "__main__":
    main()
