"""wiki.yml は GitHub ホステッドだけで、secret 未設定ならデプロイしない。"""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WIKI = ROOT / ".github" / "workflows" / "wiki.yml"
CICD = ROOT / ".github" / "workflows" / "cicd.yml"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _on(data: dict) -> dict:
    """PyYAML 1.1 はキー `on` を真偽値 True にする。"""
    value = data.get("on", data.get(True))
    return value if isinstance(value, dict) else {}


def test_wiki_workflow_uses_github_hosted_and_skips_unset_secrets() -> None:
    data = yaml.safe_load(WIKI.read_text(encoding="utf-8"))
    push = _on(data).get("push", {})
    if push.get("branches") != ["main"]:
        _fail("wiki workflow push must be main only")
    paths = push.get("paths") or []
    if "docs/**" not in paths:
        _fail("wiki push must watch docs/")
    if "**/AGENTS.md" not in paths:
        _fail("wiki push must watch AGENTS.md")
    if "**/*.md" in paths:
        _fail("wiki must not rebuild on every md (README/DESIGN/.cursor are out of scope)")
    job = data["jobs"]["build"]
    if job.get("runs-on") != "ubuntu-latest":
        _fail("wiki must run on ubuntu-latest, not self-hosted")
    if "self-hosted" in str(job.get("runs-on")):
        _fail("wiki must not use self-hosted runners")
    env = job.get("env") or {}
    if env.get("CLOUDFLARE_API_TOKEN") != "${{ secrets.CLOUDFLARE_API_TOKEN }}":
        _fail("missing CLOUDFLARE_API_TOKEN secret mapping")
    if env.get("CLOUDFLARE_ACCOUNT_ID") != "${{ secrets.CLOUDFLARE_ACCOUNT_ID }}":
        _fail("missing CLOUDFLARE_ACCOUNT_ID secret mapping")
    if env.get("CLOUDFLARE_PAGES_PROJECT") != "${{ vars.CLOUDFLARE_PAGES_PROJECT }}":
        _fail("project name must come from GitHub variable")
    steps = {step.get("name"): step for step in job["steps"]}
    skip = steps.get("Skip deploy (secrets unset)")
    deploy = steps.get("Deploy")
    if skip is None or deploy is None:
        _fail("need skip and deploy steps")
    if "CLOUDFLARE_API_TOKEN == ''" not in str(skip.get("if")):
        _fail("skip step must detect empty token")
    if "CLOUDFLARE_PAGES_PROJECT == ''" not in str(skip.get("if")):
        _fail("skip step must detect empty project variable")
    deploy_if = str(deploy.get("if"))
    if "CLOUDFLARE_API_TOKEN != ''" not in deploy_if:
        _fail("deploy must require token")
    run = deploy.get("run") or ""
    if "wrangler" not in run or "pages deploy" not in run:
        _fail("deploy must call wrangler pages deploy")
    if "--commit-dirty=true" not in run:
        _fail("deploy must pass --commit-dirty so generated site/ does not block upload")
    build = next(s for s in job["steps"] if s.get("name") == "Build")
    if "mkdocs build --strict" not in (build.get("run") or ""):
        _fail("build step must run mkdocs build --strict")


def test_cicd_runners_and_ignores_md() -> None:
    data = yaml.safe_load(CICD.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    test_runs_on = str(jobs["test-shard"].get("runs-on"))
    if "vars.CI_TEST_RUNNER ||" not in test_runs_on or "'ubuntu-latest'" not in test_runs_on or "self-hosted" in test_runs_on:
        _fail("CI/CD test-shard job must default to ubuntu-latest unless CI_TEST_RUNNER is set")
    if jobs["test"].get("runs-on") != "ubuntu-latest":
        _fail("CI/CD test job must run on ubuntu-latest")
    if jobs["lint"].get("runs-on") != "ubuntu-latest":
        _fail("CI/CD lint job must run on ubuntu-latest")
    for name in ("release", "deploy"):
        if "self-hosted" not in jobs[name].get("runs-on", []):
            _fail(f"CI/CD {name} job must stay on self-hosted")
    if "**/*.md" not in (_on(data).get("push", {}).get("paths-ignore") or []):
        _fail("CI/CD push must keep ignoring markdown")
    pr = _on(data).get("pull_request", {})
    if "paths" in pr or "paths-ignore" in pr:
        _fail("CI/CD pull_request must run on every change so Test / Lint can be required checks")


def test_cicd_push_skips_test_but_still_deploys() -> None:
    """push では test を省く。release / deploy は skipped の test を push のときだけ通す。"""
    jobs = yaml.safe_load(CICD.read_text(encoding="utf-8"))["jobs"]
    if jobs["test-shard"].get("if") != "github.event_name != 'push'":
        _fail("CI/CD test-shard job must be skipped on push only")
    if jobs["test"].get("if") != "${{ !cancelled() && github.event_name != 'push' }}":
        _fail("CI/CD test job must be skipped on push only and still run after a failed shard")
    passed = "(needs.test.result == 'success' || (github.event_name == 'push' && needs.test.result == 'skipped'))"
    for name in ("release", "deploy"):
        cond = str(jobs[name].get("if"))
        if "test" not in jobs[name].get("needs", []) or passed not in cond:
            _fail(f"CI/CD {name} must accept a skipped test only on push")
        if "needs.lint.result == 'success'" not in cond:
            _fail(f"CI/CD {name} must require lint")
        # skipped の needs があると、状態関数の無い if は評価されずに skipped になる
        if "always()" not in cond and "!cancelled()" not in cond:
            _fail(f"CI/CD {name} if needs a status function to run after a skipped test")


def test_cicd_test_fails_when_a_shard_does_not_pass() -> None:
    jobs = yaml.safe_load(CICD.read_text(encoding="utf-8"))["jobs"]
    if jobs["test"].get("needs") != ["test-shard"]:
        _fail("CI/CD test must wait for every shard")
    gate = jobs["test"]["steps"][0]
    if gate.get("if") != "needs.test-shard.result != 'success'" or "exit 1" not in gate.get("run", ""):
        _fail("CI/CD test must fail, not skip, when a shard fails")
    shard = jobs["test-shard"]
    run = next(step["run"] for step in shard["steps"] if step.get("name") == "Test")
    if "--shard=${{ matrix.shard }}/${{ strategy.job-total }}" not in run or "--dataflow=dump" not in run:
        _fail("CI/CD test-shard must run its own share and dump the dataflow")
    if "tools.dataflow.merge check" not in jobs["test"]["steps"][-1].get("run", ""):
        _fail("CI/CD test must check the merged dataflow")


def test_cicd_wiki_name_differs() -> None:
    data = yaml.safe_load(CICD.read_text(encoding="utf-8"))
    wiki = yaml.safe_load(WIKI.read_text(encoding="utf-8"))
    if wiki["name"] == data["name"]:
        _fail("wiki workflow name must differ from CI/CD")
