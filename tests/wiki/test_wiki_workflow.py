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
    if _on(data).get("push", {}).get("branches") != ["main"]:
        _fail("wiki workflow push must be main only")
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


def test_cicd_still_self_hosted_and_ignores_md_on_push() -> None:
    data = yaml.safe_load(CICD.read_text(encoding="utf-8"))
    test_job = data["jobs"]["test"]
    if "self-hosted" not in test_job.get("runs-on", []):
        _fail("existing CI/CD test job must stay on self-hosted")
    paths_ignore = _on(data).get("push", {}).get("paths-ignore") or []
    if "**/*.md" not in paths_ignore:
        _fail("CI/CD push must keep ignoring markdown")
    wiki = yaml.safe_load(WIKI.read_text(encoding="utf-8"))
    if wiki["name"] == data["name"]:
        _fail("wiki workflow name must differ from CI/CD")
