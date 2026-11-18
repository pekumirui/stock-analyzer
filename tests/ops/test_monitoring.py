"""監視（deploy/monitoring/）の振る舞い。設定の値（しきい値・時間・保持期間）は決め打ちしない。

実行ファイルが要るテストは MONITORING_BIN_DIR（deploy/monitoring/fetch.sh の出力先）か
/opt/monitoring/bin に無ければ skip する。CI は fetch.sh で用意する。
"""
from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import ClassVar

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MON = ROOT / "deploy" / "monitoring"
sys.path.insert(0, str(ROOT / "mcp"))
os.environ.setdefault("MCP_TOKEN", "test-token-for-ci-only")
os.environ["MCP_APP_DIR"] = str(ROOT)

import log_mcp as m

FAKE_URI = "/services/TFAKE/BFAKE/notarealsecret"
FORBIDDEN_WEBHOOK_PREFIX = "hooks.slack.com" + "/services/"


def _bin_dir() -> Path | None:
    for raw in (os.environ.get("MONITORING_BIN_DIR"), "/opt/monitoring/bin"):
        if raw and (Path(raw) / "victoria-metrics").is_file():
            return Path(raw)
    return None


def _need_bin(name: str) -> Path:
    d = _bin_dir()
    if d is None or not (d / name).is_file():
        pytest.skip(f"{name} not found (set MONITORING_BIN_DIR; deploy/monitoring/fetch.sh)")
    return d / name


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_http(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1):  # nosec B310 (loopback test server)
                return
        except OSError:
            time.sleep(0.2)
    raise AssertionError(f"not ready: {url}")


class _Recorder(http.server.BaseHTTPRequestHandler):
    """Records requests; answers what the class-level `reply` says."""

    calls: ClassVar[list[tuple[str, str, bytes]]] = []
    reply: bytes = b"ok"
    content_type = "application/json"

    def _record(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        type(self).calls.append((self.command, self.path, body))
        self.send_response(200)
        self.send_header("Content-Type", type(self).content_type)
        self.end_headers()
        self.wfile.write(type(self).reply)

    do_GET = _record
    do_POST = _record

    def log_message(self, *_args) -> None:
        return


def _serve(handler: type[_Recorder]) -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"



def test_no_webhook_in_repo_files() -> None:
    for path in MON.rglob("*"):
        if path.is_file():
            if FORBIDDEN_WEBHOOK_PREFIX in path.read_text(encoding="utf-8", errors="replace"):
                raise AssertionError(f"webhook URL committed in {path}")


def test_services_listen_on_loopback_only() -> None:
    units = sorted((MON / "systemd").glob("*.service"))
    assert units
    for unit in units:
        text = unit.read_text(encoding="utf-8")
        addrs = re.findall(r"-+[A-Za-z.]*listen[A-Za-z.-]*=(\S*)", text, re.I)
        assert addrs, f"{unit.name}: no listen flag"
        for addr in addrs:
            # 空は「待ち受けない」（Alertmanager の cluster）
            assert addr == "" or addr.startswith("127.0.0.1:"), f"{unit.name}: {addr}"
    scrape = yaml.safe_load((MON / "scrape.yml").read_text(encoding="utf-8"))
    for job in scrape["scrape_configs"]:
        for sc in job["static_configs"]:
            for target in sc["targets"]:
                assert target.startswith("127.0.0.1:"), target


def test_fluent_bit_watches_every_monitoring_unit() -> None:
    conf = (ROOT / "deploy" / "fluent-bit.conf").read_text(encoding="utf-8")
    watched = set(re.findall(r"Systemd_Filter\s+_SYSTEMD_UNIT=(\S+)", conf))
    for unit in (MON / "systemd").glob("*.service"):
        assert unit.name in watched, f"{unit.name} crash would not page"


def test_provision_applies_monitoring() -> None:
    text = (ROOT / "deploy" / "provision.sh").read_text(encoding="utf-8")
    assert "monitoring/apply.sh" in text and "monitoring_apply" in text
    # slack.env を書いた後で呼ぶ
    assert text.index("monitoring_apply") > text.index("write_slack_env \"$SLACK_WEBHOOK_URL\"")



def _fake_release(tmp: Path, members: dict[str, bytes]) -> tuple[Path, str]:
    archive = tmp / "fake.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(f"pkg-1.0/{name}")
            info.size = len(data)
            info.mode = 0o755
            tar.addfile(info, io.BytesIO(data))
    return archive, hashlib.sha256(archive.read_bytes()).hexdigest()


def _run_fetch(dest: Path, listing: Path) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "MON_ARCH": "amd64"}
    return subprocess.run(
        ["bash", str(MON / "fetch.sh"), str(dest), str(listing)],
        capture_output=True, text=True, env=env, check=False, timeout=60,
    )


def test_fetch_installs_verified_members(tmp_path: Path) -> None:
    archive, sha = _fake_release(tmp_path, {"tool-prod": b"#!/bin/sh\necho hi\n", "README": b"x"})
    listing = tmp_path / "list.txt"
    listing.write_text(
        "# comment\n"
        f"tool amd64 {sha} file://{archive} tool-prod:tool\n"
        f"tool arm64 {'0' * 64} file:///nonexistent tool-prod:tool\n",
        encoding="utf-8",
    )
    dest = tmp_path / "bin"
    proc = _run_fetch(dest, listing)
    assert proc.returncode == 0, proc.stderr
    assert sorted(p.name for p in dest.iterdir()) == ["tool"]
    assert os.access(dest / "tool", os.X_OK)


def test_fetch_rejects_checksum_mismatch(tmp_path: Path) -> None:
    good, sha = _fake_release(tmp_path, {"a": b"a"})
    listing = tmp_path / "list.txt"
    listing.write_text(
        f"ok amd64 {sha} file://{good} a:a\n"
        f"bad amd64 {'f' * 64} file://{good} a:b\n",
        encoding="utf-8",
    )
    dest = tmp_path / "bin"
    proc = _run_fetch(dest, listing)
    assert proc.returncode != 0
    assert "mismatch" in proc.stderr
    assert not dest.exists() or not any(dest.iterdir())


def test_binaries_list_covers_every_unit() -> None:
    names = set()
    for line in (MON / "binaries.txt").read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            cols = line.split()
            assert len(cols) == 5 and re.fullmatch(r"[0-9a-f]{64}", cols[2]), line
            assert cols[3].startswith("https://github.com/"), line
            names.update(pair.split(":")[1] for pair in cols[4].split(","))
    for unit in (MON / "systemd").glob("*.service"):
        exe = re.search(r"ExecStart=/opt/monitoring/bin/(\S+)", unit.read_text(encoding="utf-8"))
        assert exe and exe.group(1) in names, unit.name


_FAKE_SYSTEMCTL = """#!/bin/bash
# active な unit は STATE_DIR/<unit> があるもの
log="$FAKE_SYSTEMCTL_LOG"; dir="$FAKE_SYSTEMCTL_STATE"
case "$1" in
  is-active) [[ -f "$dir/${@: -1}" ]]; exit $? ;;
  start|restart) touch "$dir/$2"; echo "${1^^} $2" >> "$log" ;;
  enable) echo "ENABLE $2" >> "$log" ;;
  daemon-reload) echo "DAEMON_RELOAD" >> "$log" ;;
  *) echo "OTHER $*" >> "$log" ;;
esac
"""


class _Apply:
    def __init__(self, tmp: Path) -> None:
        self.prefix = tmp / "root"
        self.log = tmp / "systemctl.log"
        self.state = tmp / "active"
        self.state.mkdir()
        bins = tmp / "bins"
        bins.mkdir()
        for unit in (MON / "systemd").glob("*.service"):
            exe = re.search(r"ExecStart=/opt/monitoring/bin/(\S+)", unit.read_text(encoding="utf-8"))
            assert exe
            (bins / exe.group(1)).write_text("#!/bin/sh\n", encoding="utf-8")
        fake = tmp / "systemctl"
        fake.write_text(_FAKE_SYSTEMCTL, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
        self.slack_env = self.prefix / "etc" / "fluent-bit" / "slack.env"
        self.slack_env.parent.mkdir(parents=True)
        # repo の deploy/monitoring を写して使う（テストで書き換える）
        self.app = tmp / "app"
        self.src = self.app / "deploy" / "monitoring"
        shutil.copytree(MON, self.src)
        self.env = {
            **os.environ,
            "APP_DIR": str(self.app),
            "MON_PREFIX": str(self.prefix),
            "MON_SYSTEMCTL": str(fake),
            "MON_BIN_SRC": str(bins),
            "FAKE_SYSTEMCTL_LOG": str(self.log),
            "FAKE_SYSTEMCTL_STATE": str(self.state),
        }

    def write_slack(self, uri: str | None) -> None:
        if uri is None:
            self.slack_env.unlink(missing_ok=True)
            return
        self.slack_env.write_text(
            f"SLACK_WEBHOOK_URL=https://{FORBIDDEN_WEBHOOK_PREFIX}x\nSLACK_WEBHOOK_URI_INFRA={uri}\n",
            encoding="utf-8",
        )

    def run(self) -> tuple[subprocess.CompletedProcess[str], str]:
        self.log.write_text("", encoding="utf-8")
        proc = subprocess.run(
            ["bash", str(MON / "apply.sh")], capture_output=True, text=True, env=self.env, check=False, timeout=60
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        return proc, self.log.read_text(encoding="utf-8")


def _units() -> list[str]:
    return sorted(p.name for p in (MON / "systemd").glob("*.service"))


def test_apply_starts_once_then_leaves_running(tmp_path: Path) -> None:
    a = _Apply(tmp_path)
    a.write_slack(FAKE_URI)
    first, log = a.run()
    for unit in _units():
        assert f"START {unit}" in log, (unit, log)
    url_file = a.prefix / "etc" / "monitoring" / "slack_infra_url"
    assert url_file.read_text(encoding="utf-8") == "https://hooks.slack.com" + FAKE_URI
    assert not url_file.stat().st_mode & stat.S_IROTH
    assert FAKE_URI not in first.stdout + first.stderr

    _, log = a.run()
    assert "START" not in log and "RESTART" not in log and "DAEMON_RELOAD" not in log, log


def test_apply_restarts_only_what_changed(tmp_path: Path) -> None:
    a = _Apply(tmp_path)
    a.write_slack(FAKE_URI)
    a.run()
    etc_rules = a.prefix / "etc" / "monitoring" / "rules"

    src_rule = min((a.src / "rules").glob("*.yml"))
    src_rule.write_text(src_rule.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    _, log = a.run()
    assert re.findall(r"RESTART (\S+)", log) == ["monitoring-vmalert.service"], log
    assert (etc_rules / src_rule.name).read_bytes() == src_rule.read_bytes()

    extra = a.src / "rules" / "extra.yml"
    extra.write_text("groups: []\n", encoding="utf-8")
    a.run()
    assert (etc_rules / "extra.yml").exists()
    extra.unlink()
    _, log = a.run()
    assert not (etc_rules / "extra.yml").exists()
    assert re.findall(r"RESTART (\S+)", log) == ["monitoring-vmalert.service"], log

    a.write_slack(FAKE_URI + "2")
    _, log = a.run()
    assert re.findall(r"RESTART (\S+)", log) == ["monitoring-alertmanager.service"], log

    unit = a.src / "systemd" / "monitoring-node-exporter.service"
    unit.write_text(unit.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    _, log = a.run()
    assert "DAEMON_RELOAD" in log
    assert re.findall(r"RESTART (\S+)", log) == ["monitoring-node-exporter.service"], log

    (a.state / "monitoring-process-exporter.service").unlink()
    _, log = a.run()
    assert "START monitoring-process-exporter.service" in log
    assert "RESTART" not in log, log


def test_apply_without_webhook_skips_alertmanager(tmp_path: Path) -> None:
    a = _Apply(tmp_path)
    a.write_slack(None)
    proc, log = a.run()
    assert "START monitoring-alertmanager.service" not in log
    for unit in _units():
        if unit != "monitoring-alertmanager.service":
            assert f"START {unit}" in log, (unit, log)
    assert "SLACK_WEBHOOK_URI_INFRA" in proc.stdout

    a.write_slack("https://example.com/not-a-slack-path")
    _, log = a.run()
    assert "START monitoring-alertmanager.service" not in log



def _fake_app(tmp: Path, exit_code: int, sleep_s: float = 0) -> Path:
    app = tmp / "app"
    bindir = app / ".venv" / "bin"
    bindir.mkdir(parents=True)
    py = bindir / "python3"
    py.write_text(
        f"#!/bin/sh\nsleep {sleep_s}\necho running\nexit {exit_code}\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    return app


def _cron_run(app: Path, logs: Path, metrics_url: str, script: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "CRON_APP_DIR": str(app), "CRON_LOG_DIR": str(logs), "CRON_METRICS_URL": metrics_url}
    return subprocess.run(
        ["bash", str(ROOT / "deploy" / "cron-run.sh"), script],
        cwd=app, env=env, capture_output=True, text=True, check=False, timeout=60,
    )


def _parse_exposition(body: bytes) -> dict[str, tuple[dict[str, str], float]]:
    out = {}
    for line in body.decode().splitlines():
        match = re.fullmatch(r'([a-z_]+)\{([^}]*)\} (\S+)', line)
        assert match, line
        labels = dict(re.findall(r'(\w+)="([^"]*)"', match.group(2)))
        out[match.group(1)] = (labels, float(match.group(3)))
    return out


def test_cron_run_records_duration_and_exit_code(tmp_path: Path) -> None:
    class Handler(_Recorder):
        calls: ClassVar[list[tuple[str, str, bytes]]] = []

    server, base = _serve(Handler)
    try:
        app = _fake_app(tmp_path, exit_code=4, sleep_s=1.2)
        before = time.time()
        proc = _cron_run(app, tmp_path / "logs", base + "/api/v1/import/prometheus", "jobs/market/run_batch.py")
        after = time.time()
    finally:
        server.shutdown()
    assert proc.returncode == 4
    assert proc.stdout == "" and proc.stderr == ""
    assert len(Handler.calls) == 1
    method, path, body = Handler.calls[0]
    assert method == "POST" and path == "/api/v1/import/prometheus"
    metrics = _parse_exposition(body)
    assert {labels.get("cron_job") for labels, _ in metrics.values()} == {"run_batch"}
    values = {name: value for name, (_, value) in metrics.items()}
    exit_metric = [v for n, v in values.items() if "exit" in n]
    duration = [v for n, v in values.items() if "duration" in n]
    stamp = [v for n, v in values.items() if "timestamp" in n]
    assert exit_metric == [4]
    assert duration and 1 <= duration[0] <= after - before + 1
    assert stamp and before - 1 <= stamp[0] <= after + 1


def test_cron_run_unreachable_metrics_keeps_job_result(tmp_path: Path) -> None:
    app = _fake_app(tmp_path, exit_code=0)
    port = _free_port()
    started = time.monotonic()
    proc = _cron_run(app, tmp_path / "logs", f"http://127.0.0.1:{port}/api/v1/import/prometheus", "jobs/fx_rates.py")
    assert proc.returncode == 0
    assert proc.stdout == "" and proc.stderr == ""
    assert time.monotonic() - started < 10
    assert "running" in (tmp_path / "logs" / "fx_rates.log").read_text(encoding="utf-8")


def test_cron_run_metrics_can_be_disabled(tmp_path: Path) -> None:
    class Handler(_Recorder):
        calls: ClassVar[list[tuple[str, str, bytes]]] = []

    server, _base = _serve(Handler)
    try:
        proc = _cron_run(_fake_app(tmp_path, exit_code=0), tmp_path / "logs", "", "jobs/fx_rates.py")
    finally:
        server.shutdown()
    assert proc.returncode == 0
    assert Handler.calls == []



def _vector_reply() -> bytes:
    return json.dumps({
        "status": "success",
        "data": {"resultType": "vector", "result": [
            {"metric": {"__name__": "up", "job": "node"}, "value": [1767225600, "1"]},
        ]},
    }).encode()


def test_metrics_query_reads_loopback_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Handler(_Recorder):
        calls: ClassVar[list[tuple[str, str, bytes]]] = []
        reply = _vector_reply()

    server, base = _serve(Handler)
    monkeypatch.setenv("MCP_METRICS_HTTP", base)
    try:
        text, err = m.call_tool("metrics_query", {"query": "up"})
        assert not err, text
        assert 'up{job="node"}' in text and "2026-01-01 09:00:00 1" in text

        text, err = m.call_tool("metrics_query", {"query": "up", "start": "2h", "step": "1m"})
        assert not err, text
        text, err = m.call_tool("metrics_query", {"query": "up", "start": "6h"})
        assert not err, text
    finally:
        server.shutdown()
    methods = {c[0] for c in Handler.calls}
    assert methods == {"GET"}
    paths = [c[1].split("?")[0] for c in Handler.calls]
    assert paths == ["/api/v1/query", "/api/v1/query_range", "/api/v1/query_range"]
    from urllib.parse import parse_qs, urlparse

    auto = parse_qs(urlparse(Handler.calls[2][1]).query)
    span = float(auto["end"][0]) - float(auto["start"][0])
    step = int(auto["step"][0].rstrip("s"))
    assert span / step + 1 <= m.METRICS_MAX_POINTS

    for bad in ("http://10.0.0.1:8428", "https://127.0.0.1:8428", "http://example.com"):
        monkeypatch.setenv("MCP_METRICS_HTTP", bad)
        text, err = m.call_tool("metrics_query", {"query": "up"})
        assert err and "127.0.0.1" in text, (bad, text)


def test_metrics_query_rejects_bad_input() -> None:
    cases = [
        {},
        {"query": ""},
        {"query": "x" * (m.METRICS_MAX_QUERY_CHARS + 1)},
        {"query": "up", "step": "1m"},
        {"query": "up", "start": "1h", "end": "2h"},
        {"query": "up", "start": "30d", "step": "1s"},
        {"query": "up", "start": "1h", "step": "fast"},
        {"query": "up", "start": "../etc"},
    ]
    for args in cases:
        text, err = m.call_tool("metrics_query", args)
        assert err, (args, text)


def test_metrics_query_surfaces_query_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    class Handler(_Recorder):
        calls: ClassVar[list[tuple[str, str, bytes]]] = []
        reply = json.dumps({"status": "error", "errorType": "bad_data", "error": "parse error"}).encode()

    server, base = _serve(Handler)
    monkeypatch.setenv("MCP_METRICS_HTTP", base)
    try:
        text, err = m.call_tool("metrics_query", {"query": "up{"})
    finally:
        server.shutdown()
    assert err and "parse error" in text


def test_metrics_query_is_listed() -> None:
    names = {t["name"] for t in m.tools_list()}
    assert "metrics_query" in names



def test_rules_behave() -> None:
    tool = _need_bin("vmalert-tool")
    tests = sorted((MON / "tests").glob("*_test.yml"))
    assert tests
    proc = subprocess.run(
        [str(tool), "unittest", "--files", *[p.name for p in tests]],
        cwd=MON / "tests", capture_output=True, text=True, check=False, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout[-4000:] + proc.stderr[-4000:]


@pytest.fixture
def victoria(tmp_path: Path):
    exe = _need_bin("victoria-metrics")
    port = _free_port()
    proc = subprocess.Popen(
        [
            str(exe), f"-httpListenAddr=127.0.0.1:{port}", f"-storageDataPath={tmp_path / 'vm'}",
            "-search.latencyOffset=0s", "-loggerLevel=ERROR",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_http(base + "/health")
        yield base
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_cron_metrics_land_in_victoria_and_mcp_reads_them(
    tmp_path: Path, victoria: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = _cron_run(_fake_app(tmp_path, exit_code=2), tmp_path / "logs",
                     victoria + "/api/v1/import/prometheus", "jobs/xbrl/xbrl_parse.py")
    assert proc.returncode == 2
    urllib.request.urlopen(victoria + "/internal/force_flush", timeout=5).read()  # nosec B310 (loopback test server)
    monkeypatch.setenv("MCP_METRICS_HTTP", victoria)
    # 取り込みから検索できるまで少しかかる
    deadline = time.monotonic() + 15
    names = text = ""
    while time.monotonic() < deadline:
        names, err = m.call_tool("metrics_query", {"list_metrics": True, "grep": "cron_job"})
        assert not err, names
        exit_name = next((n for n in names.splitlines()[1:] if "exit" in n), None)
        if exit_name:
            text, err = m.call_tool(
                "metrics_query", {"query": f'last_over_time({exit_name}{{cron_job="xbrl_parse"}}[1h])'}
            )
            assert not err, text
            if re.search(r"cron_job=\"xbrl_parse\".* 2$", text, re.M):
                return
        time.sleep(0.5)
    raise AssertionError(f"cron metrics not queryable: {names!r} {text!r}")


def _am_config(tmp: Path, slack_url: str) -> Path:
    """repo の設定を使い、待ち時間だけ縮める。URL はテストの受け口。"""
    cfg = yaml.safe_load((MON / "alertmanager.yml").read_text(encoding="utf-8"))
    route = cfg["route"]
    route["group_wait"] = "1s"
    route["group_interval"] = "2s"
    url_file = tmp / "slack_url"
    url_file.write_text(slack_url, encoding="utf-8")
    for receiver in cfg["receivers"]:
        for sc in receiver.get("slack_configs", []):
            sc["api_url_file"] = str(url_file)
    path = tmp / "alertmanager.yml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    return path


def _post_alerts(base: str, alerts: list[dict]) -> None:
    req = urllib.request.Request(
        base + "/api/v2/alerts", data=json.dumps(alerts).encode(),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    urllib.request.urlopen(req, timeout=5).read()  # nosec B310 (loopback test server)


def _alert(severity: str, summary: str, ends: str | None = None) -> dict:
    out = {
        "labels": {"alertname": "HostCpuUsage", "severity": severity, "resource": "cpu"},
        "annotations": {"summary": summary, "value": "1%"},
    }
    if ends:
        out["endsAt"] = ends
    return out


def _wait_posts(handler: type[_Recorder], count: int, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(handler.calls) < count:
        time.sleep(0.2)


def test_alertmanager_notifies_once_inhibits_and_resolves(tmp_path: Path) -> None:
    exe = _need_bin("alertmanager")

    class Slack(_Recorder):
        # Slack の Incoming Webhook は text/plain の ok を返す
        calls: ClassVar[list[tuple[str, str, bytes]]] = []
        content_type = "text/plain"

    server, slack = _serve(Slack)
    port = _free_port()
    am = subprocess.Popen(
        [
            str(exe), f"--config.file={_am_config(tmp_path, slack + '/hook')}",
            f"--storage.path={tmp_path / 'am'}", f"--web.listen-address=127.0.0.1:{port}",
            "--cluster.listen-address=", "--log.level=error",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_http(base + "/-/ready")

        def texts() -> list[str]:
            return [c[2].decode() for c in Slack.calls]

        _post_alerts(base, [_alert("warning", "warn-probe")])
        _wait_posts(Slack, 1)
        assert len(Slack.calls) == 1 and "warn-probe" in texts()[0]

        _post_alerts(base, [_alert("warning", "warn-probe"), _alert("critical", "crit-probe")])
        _wait_posts(Slack, 2)
        assert len(Slack.calls) == 2 and "crit-probe" in texts()[1], texts()

        for _ in range(4):
            _post_alerts(base, [_alert("warning", "warn-probe"), _alert("critical", "crit-probe")])
            time.sleep(2)
        assert len(Slack.calls) == 2, texts()
        assert not any("warn-probe" in t for t in texts()[1:])

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _post_alerts(base, [_alert("warning", "warn-probe", now), _alert("critical", "crit-probe", now)])
        _wait_posts(Slack, 3)
        time.sleep(7)
        crit = [t for t in texts() if "crit-probe" in t]
        assert len(crit) == 2, texts()
        assert crit[0] != crit[1]
        settled = len(Slack.calls)
        time.sleep(5)
        assert len(Slack.calls) == settled, texts()
        assert all(c[1] == "/hook" and c[0] == "POST" for c in Slack.calls)
    finally:
        am.terminate()
        am.wait(timeout=10)
        server.shutdown()
        shutil.rmtree(tmp_path / "am", ignore_errors=True)
