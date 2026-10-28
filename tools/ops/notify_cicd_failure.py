"""CI/CD の Deploy VPS 経路が失敗したとき、Slack Incoming Webhook へ短い通知を送る。

webhook の値は標準出力にも標準エラーにも出さない。未設定・送信失敗でも終了コード 0。
手元確認:

  NOTIFY_KIND=deploy GITHUB_SHA=abc1234deadbeef \\
    COMMIT_SUBJECT='fix health check' \\
    RUN_URL=https://github.com/pekumirui/stock-analyzer/actions/runs/1 \\
    python3 tools/ops/notify_cicd_failure.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from urllib.parse import urlparse

SHORT_SHA_LEN = 7


def short_sha(sha: str, n: int = SHORT_SHA_LEN) -> str:
    sha = (sha or "").strip()
    return sha[:n] if sha else "unknown"


def first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return "(no subject)"


def commit_subject(explicit: str | None = None) -> str:
    text = explicit if explicit is not None else os.environ.get("COMMIT_SUBJECT", "")
    subject = first_line(text)
    if subject != "(no subject)":
        return subject
    try:
        out = subprocess.check_output(
            ["git", "log", "-1", "--format=%s"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "(no subject)"
    return first_line(out)


def run_url_from_env() -> str:
    explicit = (os.environ.get("RUN_URL") or "").strip()
    if explicit:
        return explicit
    server = (os.environ.get("GITHUB_SERVER_URL") or "https://github.com").rstrip("/")
    repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
    run_id = (os.environ.get("GITHUB_RUN_ID") or "").strip()
    if repo and run_id:
        return f"{server}/{repo}/actions/runs/{run_id}"
    return ""


def failed_job_labels(results: dict[str, str] | None = None) -> str:
    mapping = results if results is not None else {
        "test": os.environ.get("TEST_RESULT", ""),
        "lint": os.environ.get("LINT_RESULT", ""),
        "release": os.environ.get("RELEASE_RESULT", ""),
    }
    failed = [name for name, value in mapping.items() if value == "failure"]
    return ", ".join(failed)


def build_text(
    kind: str,
    sha: str,
    subject: str,
    run_url: str,
    *,
    results: dict[str, str] | None = None,
) -> str:
    sha_s = short_sha(sha)
    subj = first_line(subject)
    if kind == "gate":
        labels = failed_job_labels(results)
        detail = f"{labels} 失敗" if labels else "test/lint 失敗"
        headline = f"Deploy VPS 未実行 ({detail})"
    else:
        headline = "Deploy VPS 失敗"
    lines = [f"{headline}: `{sha_s}` {subj}"]
    if run_url:
        lines.append(run_url)
    return "\n".join(lines)


def build_payload(text: str) -> dict[str, str]:
    return {"text": text}


def is_https_webhook(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def post_webhook(url: str, payload: dict[str, str], timeout: float = 10) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "stock-analyzer-cicd",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310 (https のみ)
        resp.read()


def notify(
    *,
    kind: str,
    sha: str,
    subject: str,
    run_url: str,
    webhook: str,
    results: dict[str, str] | None = None,
    dry_run: bool = False,
    out=None,
    err=None,
) -> int:
    """Send or print the payload. Always returns 0. Never writes webhook to out/err."""
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    text = build_text(kind, sha, subject, run_url, results=results)
    payload = build_payload(text)
    if dry_run:
        json.dump(payload, out, ensure_ascii=False, indent=2)
        out.write("\n")
        return 0
    url = (webhook or "").strip()
    if not url:
        print("SLACK_WEBHOOK_URL_INFRA is unset; skip notify", file=err)
        return 0
    if not is_https_webhook(url):
        print("SLACK_WEBHOOK_URL_INFRA is not https; skip notify", file=err)
        return 0
    try:
        post_webhook(url, payload)
        print("slack notify sent", file=err)
    except urllib.error.HTTPError as exc:
        print(f"slack notify HTTP {exc.code}", file=err)
    except urllib.error.URLError:
        print("slack notify URL error", file=err)
    except Exception:
        print("slack notify failed", file=err)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print JSON payload to stdout; do not POST",
    )
    parser.add_argument(
        "--kind",
        choices=("deploy", "gate"),
        default=os.environ.get("NOTIFY_KIND", "deploy"),
    )
    args = parser.parse_args(argv)
    dry_run = args.dry_run or os.environ.get("DRY_RUN") == "1"
    return notify(
        kind=args.kind,
        sha=os.environ.get("GITHUB_SHA", ""),
        subject=commit_subject(),
        run_url=run_url_from_env(),
        webhook=os.environ.get("SLACK_WEBHOOK_URL_INFRA", ""),
        dry_run=dry_run,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("slack notify failed", file=sys.stderr)
        raise SystemExit(0)
