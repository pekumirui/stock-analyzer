"""nginx の設定（deploy/nginx-site.sh）を組み立てて nginx -t を通し、起動して振り分けを確かめる。

公開は /monthly・公開 API の GET・静的ファイル、/app などは Basic 認証、MCP の経路は MCP へ。
nginx が無い手元では飛ばす。CI（CI=true）では nginx が無ければ落とす。
"""
from __future__ import annotations

import base64
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / "deploy" / "nginx-site.sh"
USER, PASSWORD = "u", "p"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _stub(name: str) -> tuple[ThreadingHTTPServer, int]:
    """どの経路に届いたかを本文で返すだけのアップストリーム。"""

    class Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            body = f"{name}:{self.command}:{self.path}".encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _reply  # noqa: N815

        def log_message(self, *args) -> None:  # noqa: ARG002
            return

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _render(tmp: Path, env: dict[str, str]) -> dict[str, str]:
    script = (
        f'source "{SITE}"\n'
        f'render_limit_conf > "{tmp}/limit.conf"\n'
        f'render_log_conf > "{tmp}/log.conf"\n'
        f'render_http_only > "{tmp}/http_only.conf"\n'
        f'render_https > "{tmp}/https.conf"\n'
    )
    subprocess.run(["bash", "-euo", "pipefail", "-c", script], env={**os.environ, **env}, check=True)  # noqa: S603,S607
    return {n: (tmp / f"{n}.conf").read_text(encoding="utf-8") for n in ("limit", "log", "http_only", "https")}


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
    conf.write_text(
        f"pid {tmp}/nginx.pid;\n"
        f"error_log {tmp}/error.log;\n"
        "events {}\n"
        "http {\n"
        f"    access_log off;\n"
        f"    client_body_temp_path {tmp}/cb;\n"
        f"    proxy_temp_path {tmp}/px;\n"
        f"    fastcgi_temp_path {tmp}/fc;\n"
        f"    uwsgi_temp_path {tmp}/uw;\n"
        f"    scgi_temp_path {tmp}/sc;\n"
        f"    include {tmp}/limit.conf;\n"
        f"    include {tmp}/log.conf;\n"
        f"    include {site};\n"
        "}\n",
        encoding="utf-8",
    )
    return conf


def _nginx_test(nginx: str, tmp: Path, conf: Path) -> None:
    r = subprocess.run([nginx, "-t", "-p", str(tmp), "-c", str(conf)],  # noqa: S603
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        _fail(f"nginx -t failed for {conf.name}:\n{r.stderr}")


def _self_signed(tmp: Path) -> Path:
    live = tmp / "live"
    live.mkdir()
    subprocess.run(  # noqa: S603,S607
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=localhost",
         "-keyout", str(live / "privkey.pem"), "-out", str(live / "fullchain.pem")],
        check=True, capture_output=True,
    )
    return live


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ARG002
        return None


def _request(base: str, path: str, method: str = "GET", auth: bool = False) -> tuple[int, str, dict[str, str]]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(base + path, method=method, data=b"{}" if method == "POST" else None)
    if auth:
        req.add_header("Authorization", "Basic " + base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode())
    try:
        with opener.open(req, timeout=5) as resp:  # noqa: S310 (127.0.0.1 のテスト用 nginx)
            return resp.status, resp.read().decode(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace"), dict(e.headers)


def check_routes(base: str) -> None:
    def want(path, status, body_prefix=None, method="GET", auth=False):
        code, body, headers = _request(base, path, method, auth)
        if code != status or (body_prefix and not body.startswith(body_prefix)):
            _fail(f"{method} {path}{' (auth)' if auth else ''}: want {status} {body_prefix or ''}, got {code} {body[:80]!r}")
        return headers

    h = want("/", 301)
    if h.get("Location", "").rstrip("/").split("/")[-1] != "monthly":
        _fail(f"GET / must redirect to /monthly: {h.get('Location')!r}")
    h = want("/monthly", 200, "app:GET:/monthly")
    if "frame-ancestors 'none'" not in h.get("Content-Security-Policy", ""):
        _fail("public /monthly must send CSP")
    want("/monthly/7203", 200, "app:GET:/monthly/7203")
    want("/monthly", 403, method="POST")
    want("/api/v1/tables", 200, "app:GET:")
    want("/api/v1/tables", 403, method="POST")
    want("/api/v1/tables/72030/2026", 200, "app:GET:")
    # 書き込みの経路は公開の location に当たらず、認証の内側
    want("/api/v1/tables/72030/2026/elements", 401, method="POST")
    want("/static/js/monthly.js", 200, "app:GET:")
    want("/robots.txt", 200, "app:GET:")
    want("/app", 401)
    want("/app", 200, "app:GET:/app", auth=True)
    want("/api/screener", 401)
    want("/docs", 401)
    want("/.well-known/openid-configuration", 200, "mcp:GET:")
    want("/.well-known/openid-configuration/mcp", 200, "mcp:GET:")
    want("/.well-known/oauth-authorization-server", 200, "mcp:GET:")
    want("/mcp", 200, "mcp:POST:", method="POST")


def test_nginx_public_routes() -> None:
    nginx = shutil.which("nginx") or ("/usr/sbin/nginx" if Path("/usr/sbin/nginx").exists() else None)
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
    htpasswd = tmp / "htpasswd"
    htpasswd.write_text(f"{USER}:{{PLAIN}}{PASSWORD}\n", encoding="utf-8")
    # root で動かすと nginx のワーカーは別ユーザーになる。mkdtemp の 0700 だと htpasswd を読めない
    tmp.chmod(0o755)
    htpasswd.chmod(0o644)
    live = _self_signed(tmp)
    env = {
        "HTPASSWD": str(htpasswd), "DOMAIN": "kabukpi.test", "WWW": "www.kabukpi.test",
        "SERVER_NAMES": "kabukpi.test www.kabukpi.test _", "CERT_LIVE": str(live),
        "ACME_ROOT": str(tmp / "acme"), "ACCESS_LOG": str(tmp / "access.log"),
        "APP_UPSTREAM": f"127.0.0.1:{app_port}", "MCP_UPSTREAM": f"127.0.0.1:{mcp_port}",
    }
    out = _render(tmp, env)
    if "auth_basic_user_file" not in out["http_only"]:
        _fail("htpasswd があるとき /app などに Basic 認証を付ける")
    port, ssl_port = _free_port(), _free_port()
    for name in ("http_only", "https"):
        site = tmp / f"site-{name}.conf"
        site.write_text(_local_listen(out[name], port, ssl_port), encoding="utf-8")
        _nginx_test(nginx, tmp, _main_conf(tmp, site))

    conf = _main_conf(tmp, tmp / "site-http_only.conf")
    proc = subprocess.Popen([nginx, "-p", str(tmp), "-c", str(conf), "-g", "daemon off;"],  # noqa: S603
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):
            try:
                _request(base, "/robots.txt")
                break
            except OSError:
                time.sleep(0.1)
        check_routes(base)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
        app_srv.shutdown()
        mcp_srv.shutdown()
    print("ok: nginx public routes")


if __name__ == "__main__":
    test_nginx_public_routes()
