"""nginx の設定（deploy/nginx-site.sh）を組み立てて nginx -t を通し、起動して振り分けを確かめる。

公開は /monthly・公開 API の GET・静的ファイル、/app などは Cloudflare Access、MCP の経路は MCP へ。
/static/ は alias で nginx 直配信。monthly_row_order.json と ir_monthly_codes.json だけ proxy。
nginx が無い手元では飛ばす。CI（CI=true）では nginx が無ければ落とす。
"""
from __future__ import annotations

import gzip
import http.client
import os
import re
import shutil
import socket
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "deploy" / "nginx-site.sh"
PROVISION = ROOT / "deploy" / "provision.sh"
CF_HEADER, CF_GOOD = "Cf-Access-Jwt-Assertion", "good-token"
MIME_TYPES = Path("/etc/nginx/mime.types")


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _resolved_bin(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    fallback = Path("/usr/sbin") / name
    if fallback.is_file():
        return str(fallback)
    return None


def _run_resolved(
    exe: str, args: Sequence[str], **kwargs: Any,
) -> subprocess.CompletedProcess[Any]:
    if not os.path.isabs(exe):
        raise ValueError(exe)
    return subprocess.run([exe, *args], **kwargs)


def _popen_resolved(exe: str, args: Sequence[str], **kwargs: Any) -> subprocess.Popen[Any]:
    if not os.path.isabs(exe):
        raise ValueError(exe)
    return subprocess.Popen([exe, *args], **kwargs)


def _stub(name: str) -> tuple[ThreadingHTTPServer, int]:
    """どの経路に届いたかを本文で返すだけのアップストリーム。Access の確認は CF_GOOD だけ通す。"""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            if self.path == "/_auth/cf-access":
                self.send_response(204 if self.headers.get(CF_HEADER) == CF_GOOD else 401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = f"{name}:{self.command}:{self.path}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _reply

        def log_message(self, *args) -> None:
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _render(tmp: Path, env: dict[str, str]) -> dict[str, str]:
    bash = _resolved_bin("bash")
    if bash is None:
        _fail("bash が無い")
    script = tmp / "render.sh"
    script.write_text(
        f'source "{SITE}"\n'
        f'render_limit_conf > "{tmp}/limit.conf"\n'
        f'render_log_conf > "{tmp}/log.conf"\n'
        f'render_gzip_conf > "{tmp}/gzip.conf"\n'
        f'render_realip_conf > "{tmp}/realip.conf"\n'
        f'render_http_only > "{tmp}/http_only.conf"\n'
        f'render_https > "{tmp}/https.conf"\n',
        encoding="utf-8",
    )
    _run_resolved(bash, ["-euo", "pipefail", str(script)], env={**os.environ, **env}, check=True)
    return {
        n: (tmp / f"{n}.conf").read_text(encoding="utf-8")
        for n in ("limit", "log", "gzip", "realip", "http_only", "https")
    }


def _local_listen(site: str, port: int, ssl_port: int) -> str:
    """本番の 80 / 443 と IPv6 の listen を、テスト用の 127.0.0.1 のポートに置き換える。"""
    out = []
    for line in site.splitlines():
        s = line.strip()
        if s.startswith("listen [::]"):
            continue
        if s.startswith("listen 80"):
            line = line.replace("listen 80", f"listen 127.0.0.1:{port}")
        elif s.startswith("listen 443"):
            line = line.replace("listen 443", f"listen 127.0.0.1:{ssl_port}")
        out.append(line)
    return "\n".join(out) + "\n"


def _main_conf(tmp: Path, site: Path) -> Path:
    conf = tmp / f"nginx-{site.stem}.conf"
    mime_line = f"    include {MIME_TYPES};\n" if MIME_TYPES.is_file() else ""
    conf.write_text(
        f"pid {tmp}/nginx.pid;\n"
        f"error_log {tmp}/error.log;\n"
        "events {}\n"
        "http {\n"
        f"{mime_line}"
        "    gzip on;\n"
        f"    include {tmp}/gzip.conf;\n"
        f"    include {tmp}/log.conf;\n"
        f"    include {tmp}/realip.conf;\n"
        f"    access_log {tmp}/access.log timed;\n"
        f"    client_body_temp_path {tmp}/cb;\n"
        f"    proxy_temp_path {tmp}/px;\n"
        f"    fastcgi_temp_path {tmp}/fc;\n"
        f"    uwsgi_temp_path {tmp}/uw;\n"
        f"    scgi_temp_path {tmp}/sc;\n"
        f"    include {tmp}/limit.conf;\n"
        f"    include {site};\n"
        "}\n",
        encoding="utf-8",
    )
    return conf


def _nginx_test(nginx: str, tmp: Path, conf: Path) -> None:
    r = _run_resolved(nginx, ["-t", "-p", str(tmp), "-c", str(conf)],
                      capture_output=True, text=True, check=False)
    if r.returncode != 0:
        _fail(f"nginx -t failed for {conf.name}:\n{r.stderr}")


def _self_signed(tmp: Path) -> Path:
    openssl = _resolved_bin("openssl")
    if openssl is None:
        _fail("openssl が無い")
    live = tmp / "live"
    live.mkdir()
    _run_resolved(
        openssl,
        ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
         "-keyout", str(live / "privkey.pem"), "-out", str(live / "fullchain.pem")],
        check=True, capture_output=True,
    )
    return live


def _request(
    port: int,
    path: str,
    method: str = "GET",
    extra_headers: dict[str, str] | None = None,
    decode: bool = True,
) -> tuple[int, bytes | str, dict[str, str]]:
    headers: dict[str, str] = dict(extra_headers or {})
    body = b"{}" if method == "POST" else None
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(body))
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        raw = {k: v for k, v in resp.getheaders()}
        payload = resp.read()
        if decode:
            return resp.status, payload.decode(errors="replace"), raw
        return resp.status, payload, raw
    finally:
        conn.close()


def _header(headers: dict[str, str], name: str) -> str:
    want = name.lower()
    for key, value in headers.items():
        if key.lower() == want:
            return value
    return ""


def _location_block(conf: str, header: str) -> str:
    start = conf.find(header)
    if start < 0:
        _fail(f"missing {header}")
    rest = conf[start + len(header):]
    nxt = rest.find("\n    location ")
    if nxt < 0:
        return conf[start:]
    return conf[start:start + len(header) + nxt]


def test_nginx_static_direct_contracts() -> None:
    site = SITE.read_text(encoding="utf-8")
    gzip_block = _location_block(site, "render_gzip_conf() {")
    if "text/css" not in gzip_block or "application/javascript" not in gzip_block:
        _fail("gzip_types must include text/css and application/javascript")
    static_block = _location_block(site, "location ^~ /static/ {")
    if "proxy_pass" in static_block:
        _fail("location ^~ /static/ must not proxy_pass")
    if "alias ${APP_DIR}/static/;" not in static_block:
        _fail("location ^~ /static/ must alias ${APP_DIR}/static/")
    if 'add_header Cache-Control "no-cache" always;' not in static_block:
        _fail("location ^~ /static/ must set Cache-Control no-cache always")
    if "${pub_headers}" not in static_block:
        _fail("location ^~ /static/ must keep ${pub_headers} in the same location")
    for path in ("/static/monthly_row_order.json", "/static/ir_monthly_codes.json"):
        block = _location_block(site, f"location = {path} {{")
        if "proxy_pass" not in block:
            _fail(f"location = {path} must proxy_pass")
        if "alias " in block:
            _fail(f"location = {path} must not alias")
        if "${pub_headers}" not in block:
            _fail(f"location = {path} must keep ${{pub_headers}}")
    provision = PROVISION.read_text(encoding="utf-8")
    if "ensure_nginx_static_readable" not in provision:
        _fail("provision.sh must make static/ readable by the nginx worker")
    if "chmod o+x" not in provision or "chmod -R a+rX" not in provision:
        _fail("provision.sh must chmod o+x on $HOME and a+rX on static/")
    if "stock-analyzer-gzip.conf" not in provision or "render_gzip_conf" not in provision:
        _fail("provision.sh must write gzip_types (css/js) to conf.d")
    if "stock-analyzer-realip.conf" not in provision or "render_realip_conf" not in provision:
        _fail("provision.sh must write the Cloudflare real_ip conf to conf.d")
    print("ok: nginx static direct contracts")


def test_ensure_nginx_static_readable_chmod() -> None:
    text = PROVISION.read_text(encoding="utf-8")
    m = re.search(r"ensure_nginx_static_readable\(\) \{.*?\n\}\n", text, re.S)
    if not m:
        _fail("could not extract ensure_nginx_static_readable")
    tmp = Path(tempfile.mkdtemp(prefix="nginx-static-perm-"))
    home = tmp / "stock"
    app = home / "stock-analyzer"
    static = app / "static"
    static.mkdir(parents=True)
    css = static / "app.css"
    css.write_text("body{}\n", encoding="utf-8")
    home.chmod(0o750)
    app.chmod(0o750)
    static.chmod(0o750)
    css.chmod(0o640)
    fake_bin = tmp / "bin"
    fake_bin.mkdir()
    getent = fake_bin / "getent"
    getent.write_text(
        "#!/usr/bin/env bash\n"
        f'if [[ "$1" == passwd && "$2" == stock ]]; then\n'
        f'  echo "stock:x:1000:1000::{home}:/bin/bash"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
        encoding="utf-8",
    )
    getent.chmod(0o755)
    script = tmp / "run.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f"APP_DIR={app.as_posix()!r}\n"
        "STOCK_USER=stock\n"
        f"{m.group(0)}"
        "ensure_nginx_static_readable\n",
        encoding="utf-8",
    )
    env = {**os.environ, "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"}
    r = subprocess.run(
        ["bash", str(script)], env=env, capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        _fail(f"ensure_nginx_static_readable failed: {r.stderr or r.stdout}")
    if stat.S_IMODE(home.stat().st_mode) & 0o001 != 0o001:
        _fail(f"$HOME must be traversable by others, mode={oct(stat.S_IMODE(home.stat().st_mode))}")
    if stat.S_IMODE(css.stat().st_mode) & 0o004 != 0o004:
        _fail(f"static file must be world-readable, mode={oct(stat.S_IMODE(css.stat().st_mode))}")
    print("ok: nginx static dir permissions")


def check_routes(port: int) -> None:
    def want(path, status, body_prefix=None, method="GET"):
        code, body, headers = _request(port, path, method)
        if code != status or (body_prefix and not str(body).startswith(body_prefix)):
            _fail(f"{method} {path}: want {status} {body_prefix or ''}, got {code} {str(body)[:80]!r}")
        return headers

    h = want("/", 301)
    if _header(h, "Location").rstrip("/").split("/")[-1] != "monthly":
        _fail(f"GET / must redirect to /monthly: {_header(h, 'Location')!r}")
    h = want("/monthly", 200, "app:GET:/monthly")
    if "frame-ancestors 'none'" not in _header(h, "Content-Security-Policy"):
        _fail("public /monthly must send CSP")
    want("/monthly/7203", 200, "app:GET:/monthly/7203")
    want("/monthly", 403, method="POST")
    want("/api/v1/tables", 200, "app:GET:")
    want("/api/v1/tables", 403, method="POST")
    want("/api/v1/tables/72030/2026", 200, "app:GET:")
    want("/api/v1/tables/72030/2026/elements", 401, method="POST")
    css_path = ROOT / "static" / "css" / "app.css"
    js_path = ROOT / "static" / "js" / "monthly.js"
    css_text = css_path.read_text(encoding="utf-8")
    js_text = js_path.read_text(encoding="utf-8")
    code, body, headers = _request(port, "/static/css/app.css")
    if code != 200 or body != css_text:
        _fail(f"GET /static/css/app.css must be nginx alias, got {code} {str(body)[:80]!r}")
    if str(body).startswith("app:GET:"):
        _fail("GET /static/css/app.css must not hit upstream")
    if _header(headers, "Cache-Control") != "no-cache":
        _fail(f"static Cache-Control: {_header(headers, 'Cache-Control')!r}")
    if not _header(headers, "ETag"):
        _fail("static must send ETag")
    if not _header(headers, "Last-Modified"):
        _fail("static must send Last-Modified")
    if "nosniff" not in _header(headers, "X-Content-Type-Options"):
        _fail("static must keep pub_headers")
    code, body, headers = _request(port, "/static/css/app.css", method="HEAD")
    if code != 200 or body:
        _fail(f"HEAD /static/css/app.css: want 200 empty, got {code} {body!r}")
    if _header(headers, "Cache-Control") != "no-cache" or not _header(headers, "ETag"):
        _fail("HEAD /static/css/app.css must send Cache-Control and ETag")
    code, body, _headers = _request(port, "/static/js/monthly.js")
    if code != 200 or body != js_text:
        _fail(f"GET /static/js/monthly.js must be nginx alias, got {code} {str(body)[:80]!r}")
    if MIME_TYPES.is_file():
        code, raw, headers = _request(
            port, "/static/css/app.css", extra_headers={"Accept-Encoding": "gzip"}, decode=False,
        )
        if code != 200 or "gzip" not in _header(headers, "Content-Encoding"):
            _fail(f"gzip css: status={code} encoding={_header(headers, 'Content-Encoding')!r}")
        if gzip.decompress(raw).decode("utf-8") != css_text:
            _fail("gzip css body mismatch")
    want("/static/monthly_row_order.json", 200, "app:GET:/static/monthly_row_order.json")
    want("/static/ir_monthly_codes.json", 200, "app:GET:/static/ir_monthly_codes.json")
    want("/robots.txt", 200, "app:GET:")
    want("/app", 401)
    for path in ("/app", "/api/screener", "/docs"):
        code, body, _h = _request(port, path, extra_headers={CF_HEADER: CF_GOOD})
        if code != 200 or not str(body).startswith(f"app:GET:{path}"):
            _fail(f"{path} with a valid Access token must pass, got {code}")
    code, _body, h = _request(port, "/app", extra_headers={CF_HEADER: "forged"})
    if code != 401:
        _fail(f"/app with a bad Access token must be refused, got {code}")
    want("/_cf_access", 404)
    want("/api/screener", 401)
    want("/docs", 401)
    want("/.well-known/openid-configuration", 200, "mcp:GET:")
    want("/.well-known/openid-configuration/mcp", 200, "mcp:GET:")
    want("/.well-known/oauth-authorization-server", 200, "mcp:GET:")
    want("/mcp", 200, "mcp:POST:", method="POST")


def test_nginx_public_routes() -> None:
    nginx = _resolved_bin("nginx")
    if nginx is None:
        if os.environ.get("CI"):
            _fail("CI では nginx が要る（apt-get install nginx-light）")
        print("skip: nginx が無い")
        return
    app_srv, app_port = _stub("app")
    mcp_srv, mcp_port = _stub("mcp")
    tmp = Path(tempfile.mkdtemp(prefix="nginx-test-"))
    for d in ("cb", "px", "fc", "uw", "sc", "acme"):
        (tmp / d).mkdir()
    tmp.chmod(0o755)
    live = _self_signed(tmp)
    env = {
        "DOMAIN": "kabukpi.test", "WWW": "www.kabukpi.test",
        "SERVER_NAMES": "kabukpi.test www.kabukpi.test _", "CERT_LIVE": str(live),
        "ACME_ROOT": str(tmp / "acme"), "ACCESS_LOG": str(tmp / "access.log"),
        "APP_DIR": str(ROOT),
        "APP_UPSTREAM": f"127.0.0.1:{app_port}", "MCP_UPSTREAM": f"127.0.0.1:{mcp_port}",
    }
    out = _render(tmp, env)
    if "auth_request /_cf_access;" not in out["http_only"] or "auth_basic" in out["http_only"]:
        _fail("/app などは Access の auth_request だけで守る")
    if "real_ip_header CF-Connecting-IP;" not in out["realip"] or "set_real_ip_from" not in out["realip"]:
        _fail("realip.conf must trust CF-Connecting-IP from Cloudflare ranges")
    if f"alias {ROOT}/static/;" not in out["http_only"]:
        _fail(f"rendered site must alias {ROOT}/static/")
    if "text/css" not in out["gzip"] or "application/javascript" not in out["gzip"]:
        _fail("gzip.conf must include css/js types")
    port, ssl_port = _free_port(), _free_port()
    for name in ("http_only", "https"):
        site = tmp / f"site-{name}.conf"
        site.write_text(_local_listen(out[name], port, ssl_port), encoding="utf-8")
        _nginx_test(nginx, tmp, _main_conf(tmp, site))

    conf = _main_conf(tmp, tmp / "site-http_only.conf")
    proc = _popen_resolved(
        nginx, ["-p", str(tmp), "-c", str(conf), "-g", "daemon off;"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        for _ in range(50):
            try:
                _request(port, "/robots.txt")
                break
            except OSError:
                time.sleep(0.1)
        check_routes(port)
        access = (tmp / "access.log").read_text(encoding="utf-8")
        css_lines = [ln for ln in access.splitlines() if "/static/css/app.css" in ln]
        if not css_lines:
            _fail("access log missing /static/css/app.css")
        for ln in css_lines:
            if "urt=-" not in ln:
                _fail(f"static must not hit upstream (want urt=-): {ln}")
        json_lines = [ln for ln in access.splitlines() if "monthly_row_order.json" in ln]
        if not json_lines:
            _fail("access log missing /static/monthly_row_order.json")
        if all("urt=-" in ln for ln in json_lines):
            _fail("JSON route must still go to upstream")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        app_srv.shutdown()
        mcp_srv.shutdown()
    print("ok: nginx public routes")


if __name__ == "__main__":
    test_nginx_static_direct_contracts()
    test_ensure_nginx_static_readable_chmod()
    test_nginx_public_routes()
