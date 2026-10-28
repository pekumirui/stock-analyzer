"""Deploy VPS 失敗の Slack 通知。webhook をログに出さず、失敗しても exit 0。"""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]

from tools.ops.notify_cicd_failure import (  # noqa: E402
    build_payload,
    build_text,
    failed_job_labels,
    is_https_webhook,
    main,
    notify,
    short_sha,
)

WEBHOOK = "https://example.invalid/hook/T000/B000/dummytoken"
CICD = ROOT / ".github" / "workflows" / "cicd.yml"
SCRIPT = "tools/ops/notify_cicd_failure.py"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _workflow() -> tuple[str, dict]:
    text = CICD.read_text(encoding="utf-8")
    return text, yaml.safe_load(text)


def test_payload_deploy_failure() -> None:
    text = build_text(
        "deploy",
        "abcdef1234567890",
        "fix health check\n\nbody",
        "https://github.com/pekumirui/stock-analyzer/actions/runs/99",
    )
    if text != (
        "Deploy VPS 失敗: `abcdef1` fix health check\n"
        "https://github.com/pekumirui/stock-analyzer/actions/runs/99"
    ):
        _fail(f"unexpected deploy text: {text!r}")
    payload = build_payload(text)
    if payload != {"text": text}:
        _fail(f"payload must be {{text: ...}}, got {payload!r}")


def test_payload_gate_lists_failed_jobs() -> None:
    text = build_text(
        "gate",
        "deadbeefcafebabe",
        "break tests",
        "https://example.invalid/run",
        results={"test": "failure", "lint": "success", "release": "skipped"},
    )
    if not text.startswith("Deploy VPS 未実行 (test 失敗): `deadbee` break tests"):
        _fail(f"unexpected gate text: {text!r}")
    if failed_job_labels({"test": "success", "lint": "failure"}) != "lint":
        _fail("failed_job_labels should return lint")


def test_short_sha_and_https() -> None:
    if short_sha("abc") != "abc" or short_sha("") != "unknown":
        _fail("short_sha edge cases")
    if not is_https_webhook("https://example.invalid/hook"):
        _fail("https webhook must be accepted")
    if is_https_webhook("http://example.invalid/hook"):
        _fail("http webhook must be rejected")
    if is_https_webhook("not-a-url"):
        _fail("bare string must be rejected")


def test_dry_run_prints_json_not_webhook() -> None:
    buf = io.StringIO()
    err = io.StringIO()
    code = notify(
        kind="deploy",
        sha="0123456789abcdef",
        subject="release v1",
        run_url="https://github.com/pekumirui/stock-analyzer/actions/runs/1",
        webhook=WEBHOOK,
        dry_run=True,
        out=buf,
        err=err,
    )
    if code != 0:
        _fail(f"dry-run must exit 0, got {code}")
    payload = json.loads(buf.getvalue())
    if "Deploy VPS 失敗" not in payload["text"] or "`0123456`" not in payload["text"]:
        _fail(f"dry-run payload missing expected text: {payload}")
    dumped = buf.getvalue() + err.getvalue()
    if WEBHOOK in dumped or "dummytoken" in dumped:
        _fail("dry-run must not print webhook")


def test_missing_webhook_exits_zero() -> None:
    err = io.StringIO()
    code = notify(
        kind="deploy",
        sha="abc",
        subject="x",
        run_url="https://example.invalid/run",
        webhook="",
        out=io.StringIO(),
        err=err,
    )
    if code != 0:
        _fail("unset webhook must exit 0")
    if "unset" not in err.getvalue():
        _fail("unset webhook should say skip notify")
    if WEBHOOK in err.getvalue() or "dummytoken" in err.getvalue():
        _fail("unset path must not mention webhook")


def test_send_failure_exits_zero_without_url() -> None:
    err = io.StringIO()

    def boom(*_a, **_k):
        raise urllib.error.URLError(WEBHOOK)

    with patch("tools.ops.notify_cicd_failure.post_webhook", side_effect=boom):
        code = notify(
            kind="deploy",
            sha="abc",
            subject="x",
            run_url="https://example.invalid/run",
            webhook=WEBHOOK,
            out=io.StringIO(),
            err=err,
        )
    if code != 0:
        _fail("send failure must exit 0")
    logged = err.getvalue()
    if WEBHOOK in logged or "dummytoken" in logged:
        _fail(f"send failure logged webhook: {logged!r}")
    if "URL error" not in logged:
        _fail(f"expected URL error log, got {logged!r}")


def test_cli_dry_run_uses_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NOTIFY_KIND", "gate")
    monkeypatch.setenv("GITHUB_SHA", "fffffffff0000000")
    monkeypatch.setenv("COMMIT_SUBJECT", "fail lint")
    monkeypatch.setenv("RUN_URL", "https://github.com/pekumirui/stock-analyzer/actions/runs/2")
    monkeypatch.setenv("TEST_RESULT", "success")
    monkeypatch.setenv("LINT_RESULT", "failure")
    monkeypatch.setenv("SLACK_WEBHOOK_URL_INFRA", WEBHOOK)
    monkeypatch.chdir(tmp_path)
    buf = io.StringIO()
    err = io.StringIO()
    with patch("sys.stdout", buf), patch("sys.stderr", err):
        code = main(["--dry-run"])
    if code != 0:
        _fail(f"cli dry-run must exit 0, got {code}")
    payload = json.loads(buf.getvalue())
    if "lint 失敗" not in payload["text"] or "`fffffff`" not in payload["text"]:
        _fail(f"cli payload: {payload}")
    if WEBHOOK in buf.getvalue() + err.getvalue():
        _fail("cli dry-run leaked webhook")


def test_cicd_deploy_notify_step() -> None:
    _text, data = _workflow()
    deploy = data["jobs"]["deploy"]
    notify_step = deploy["steps"][-1]
    if "failure()" not in str(notify_step.get("if", "")):
        _fail("deploy notify step must use failure()")
    if "cancelled()" not in str(notify_step.get("if", "")):
        _fail("deploy notify step must exclude cancelled")
    if notify_step.get("continue-on-error") is not True:
        _fail("deploy notify step must continue-on-error")
    env = notify_step.get("env") or {}
    if env.get("SLACK_WEBHOOK_URL_INFRA") != "${{ secrets.SLACK_WEBHOOK_URL_INFRA }}":
        _fail("deploy notify must use secrets.SLACK_WEBHOOK_URL_INFRA")
    if env.get("NOTIFY_KIND") != "deploy":
        _fail("deploy notify NOTIFY_KIND must be deploy")
    if SCRIPT not in (notify_step.get("run") or ""):
        _fail(f"deploy notify must run {SCRIPT}")
    if "echo" in (notify_step.get("run") or "") and "SLACK_WEBHOOK" in (notify_step.get("run") or ""):
        _fail("deploy notify must not echo webhook")


def test_cicd_gate_notify_job() -> None:
    _text, data = _workflow()
    job = data["jobs"].get("notify-skipped-deploy")
    if not job:
        _fail("missing notify-skipped-deploy job")
    cond = job.get("if") or ""
    for needle in (
        "always()",
        "!cancelled()",
        "github.event_name == 'push'",
        "github.ref == 'refs/heads/main'",
        "needs.test.result == 'failure'",
        "needs.lint.result == 'failure'",
        "needs.deploy.result != 'failure'",
    ):
        if needle not in cond:
            _fail(f"notify-skipped-deploy if missing {needle!r}")
    if "pull_request" in cond:
        _fail("gate notify is limited to push; do not special-case pull_request in if")
    if job.get("continue-on-error") is not True:
        _fail("notify-skipped-deploy must not fail the workflow")
    step = job["steps"][-1]
    if step.get("continue-on-error") is not True:
        _fail("gate notify step must continue-on-error")
    env = step.get("env") or {}
    if env.get("NOTIFY_KIND") != "gate":
        _fail("gate notify NOTIFY_KIND must be gate")
    if env.get("SLACK_WEBHOOK_URL_INFRA") != "${{ secrets.SLACK_WEBHOOK_URL_INFRA }}":
        _fail("gate notify must use secrets.SLACK_WEBHOOK_URL_INFRA")
    if SCRIPT not in (step.get("run") or ""):
        _fail(f"gate notify must run {SCRIPT}")


def test_cicd_does_not_log_webhook_or_curl_verbose() -> None:
    _text, data = _workflow()
    notify_steps = (
        data["jobs"]["deploy"]["steps"][-1],
        data["jobs"]["notify-skipped-deploy"]["steps"][-1],
    )
    for step in notify_steps:
        run = step.get("run") or ""
        name = step.get("name")
        if "curl -v" in run or "curl --verbose" in run:
            _fail(f"notify step {name} uses verbose curl")
        if "SLACK_WEBHOOK" in run:
            _fail(f"notify step {name} must not mention webhook in the run script")
        if "print(" in run:
            _fail(f"notify step {name} must not print from the run script")


def test_script_exists_and_omits_webhook_from_source_logs() -> None:
    src = (ROOT / SCRIPT).read_text(encoding="utf-8")
    if "print(url)" in src or "print(webhook)" in src:
        _fail("notify script must not print url/webhook")
    if "exc.reason" in src or "str(exc)" in src:
        _fail("notify script must not stringify exceptions (may include URL)")
