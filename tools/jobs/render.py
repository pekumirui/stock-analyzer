from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

from tools.dataflow.render import DOMAINS, ELK_LAYOUT, ENTRIES, HUB, Entry, entry_domain, load_entries
from tools.jobs.args import Arg, job_args
from tools.jobs.crontab import CRONTAB, Run, dow_text, load, schedule_text

ROOT = Path(__file__).resolve().parents[2]
OVERVIEW = "docs/JOBS.md"
PAGE_DIR = "docs/jobs/gen"
WORKFLOWS = ".github/workflows"
REBUILD = "python -m tools.jobs.render"
_LABEL = dict(DOMAINS)
_JOB_PATH = re.compile(r"jobs/[\w/]+\.py")
_EDGE_TABLES = 2


def _domain(job: str) -> str:
    return entry_domain(job, "job", {})


def _stem(job: str) -> str:
    return job.removeprefix("jobs/").removesuffix(".py")


def _code(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _hm(hour: int, minute: int) -> str:
    return f"{hour:02d}:{minute:02d}"


def workflow_jobs(root: Path) -> dict[str, list[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for path in sorted((root / WORKFLOWS).glob("*.yml")):
        for job in _JOB_PATH.findall(path.read_text(encoding="utf-8")):
            out[job].add(path.name)
    return {job: sorted(names) for job, names in out.items()}


def _gantt_label(run: Run) -> str:
    parts = [_stem(run.job).split("/")[-1]]
    if len(run.dows) != 7:
        parts.append(dow_text(run.dows))
    if len(run.hours) > 2:
        parts.append("毎時 " + "・".join(f"{m:02d}" for m in run.minutes) + " 分")
    if run.chain > 1:
        parts.append(f"続き {run.step + 1}/{run.chain}")
    return " ".join(parts)


def _gantt(runs: list[Run], tz: str | None) -> list[str]:
    lines = [
        "```mermaid",
        "gantt",
        f"  title 1 日の起動時刻（{tz or 'サーバの時刻'}）",
        "  dateFormat HH:mm",
        "  axisFormat %H:%M",
        "  todayMarker off",
    ]
    for key, label in DOMAINS:
        picked = sorted((r for r in runs if _domain(r.job) == key), key=lambda r: (r.fires[0], r.line, r.step))
        if not picked:
            continue
        lines.append(f"  section {label}")
        for run in picked:
            name = _gantt_label(run)
            if len(run.hours) > 2:
                lines.append(f"  {name} :{_hm(*run.fires[0])}, {_hm(*run.fires[-1])}")
            else:
                lines += [f"  {name} :milestone, {_hm(h, m)}, 0m" for h, m in run.fires]
    return lines + ["```", ""]


def _variants(runs: list[Run]) -> dict[tuple[str, str], list[Run]]:
    out: dict[tuple[str, str], list[Run]] = defaultdict(list)
    for run in runs:
        out[(run.job, run.args)].append(run)
    return out


def _when(runs: list[Run]) -> str:
    return "<br>".join(schedule_text(r) for r in sorted(runs, key=lambda r: (r.fires[0], r.line)))


def _chain_text(run: Run, runs: list[Run]) -> str:
    if run.chain == 1:
        return ""
    mates = sorted((r for r in runs if r.line == run.line), key=lambda r: r.step)
    return " → ".join(f"**{_stem(r.job)}**" if r is run else _stem(r.job) for r in mates)


def _overview(all_jobs: list[str], runs: list[Run], tz: str | None, actions: dict[str, list[str]]) -> str:
    cron_jobs = {r.job for r in runs}
    lines = [
        "# ジョブ設計",
        "",
        f"- 起動時刻は `{CRONTAB}`、引数は各ジョブの `add_argument`、読み書きする表は `{ENTRIES}`（`python -m pytest tests --dataflow=write` が作る）から拾う",
        "- 読み書きする表はテストが通った道だけなので下限。読み方は [DATA_FLOW.md](DATA_FLOW.md)",
        "- ジョブは `jobs/` のうち `__main__` を持つファイル。機能は `jobs/<機能>/` のフォルダ",
        f"- 時刻は {tz or 'サーバの時刻'}。所要時間は載せない（cron からは分からない）",
        "",
        "## 機能ごとのページ",
        "",
        "ジョブごとの引数・読み書きする表と、ジョブ間の依存図。",
        "",
        "| 機能 | cron のジョブ | cron に無いジョブ |",
        "|---|---|---|",
    ]
    for key, label in DOMAINS:
        own = [j for j in all_jobs if _domain(j) == key]
        if not own:
            continue
        on = [_stem(j).split("/")[-1] for j in own if j in cron_jobs]
        off = [_stem(j).split("/")[-1] for j in own if j not in cron_jobs]
        lines.append(f"| [{label}](jobs/gen/{key}.md) | {_code(on)} | {_code(off)} |")
    lines += [
        "",
        "## 1 日の時刻表",
        "",
        (
            "ひし形は 1 回の起動。帯はその間に毎時くり返し起動する（何分かは名前に出す）。帯の長さは所要時間ではない。"
            "「続き n/m」は crontab の 1 行に並べて順に走らせるジョブの n 番目。"
        ),
        "",
    ]
    lines += _gantt(runs, tz)
    lines += [
        "## cron のジョブ",
        "",
        "読み書きする表は機能ごとのページのジョブの節に出す。",
        "",
        "| ジョブ | 機能 | 起動 | 引数 |",
        "|---|---|---|---|",
    ]
    order = {k: i for i, (k, _) in enumerate(DOMAINS)}
    for (job, args), group in sorted(_variants(runs).items(), key=lambda kv: (order[_domain(kv[0][0])], kv[0])):
        lines.append(f"| `{_stem(job)}` | {_LABEL[_domain(job)]} | {_when(group)} | {f'`{_cell(args)}`' if args else ''} |")
    lines += [
        "",
        "## cron に無いジョブ",
        "",
        "手で流すか、GitHub Actions から流す。",
        "",
        "| ジョブ | 機能 | Actions |",
        "|---|---|---|",
    ]
    for job in sorted((j for j in all_jobs if j not in cron_jobs), key=lambda j: (order[_domain(j)], j)):
        lines.append(f"| `{_stem(job)}` | {_LABEL[_domain(job)]} | {_code(actions.get(job, []))} |")
    lines.append("")
    return "\n".join(lines)


def dependencies(entries: list[Entry], cron_jobs: set[str]) -> dict[tuple[str, str], list[str]]:
    jobs = [e for e in entries if e.kind == "job" and e.name in cron_jobs]
    out = {}
    for a in jobs:
        for b in jobs:
            shared = sorted(t for t in a.writes() if t not in HUB and b.rw.get(t) == "R")
            if a is not b and shared:
                out[(a.name, b.name)] = shared
    return out


def _dependency_graph(domain: str, deps: dict[tuple[str, str], list[str]]) -> list[str]:
    picked = {k: v for k, v in deps.items() if domain in (_domain(k[0]), _domain(k[1]))}
    if not picked:
        return []
    ids: dict[str, str] = {}

    def nid(job: str) -> str:
        return ids.setdefault(job, f"n{len(ids)}")

    lines = ["```mermaid", *ELK_LAYOUT, "flowchart LR"]
    for job in sorted({j for pair in picked for j in pair}):
        cls = "own" if _domain(job) == domain else "ext"
        lines.append(f'  {nid(job)}["{_stem(job)}"]:::{cls}')
    for (a, b), tables in sorted(picked.items()):
        label = "<br>".join(tables[:_EDGE_TABLES]) + (f"<br>ほか {len(tables) - _EDGE_TABLES}" if len(tables) > _EDGE_TABLES else "")
        lines.append(f'  {nid(a)} -->|"{label}"| {nid(b)}')
    lines += [
        "  classDef own fill:#e3ecff,stroke:#3b5bdb",
        "  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3",
        "```",
        "",
    ]
    return lines


def _arg_table(args: list[Arg]) -> list[str]:
    if not args:
        return ["引数: なし", ""]
    lines = ["| 引数 | 種類 | 既定 | 説明 |", "|---|---|---|---|"]
    for a in args:
        names = " / ".join(f"`{n}`" for n in a.names) + ("（位置）" if a.positional else "")
        default = f"`{_cell(a.default)}`" if a.default else ""
        lines.append(f"| {names} | {_cell(a.kind)} | {default} | {_cell(a.help)} |")
    return lines + [""]


def _domain_page(root: Path, domain: str, all_jobs: list[str], by_name: dict[str, Entry], runs: list[Run],
                 deps: dict[tuple[str, str], list[str]], actions: dict[str, list[str]]) -> str:
    lines = [
        f"# ジョブ設計: {_LABEL[domain]}",
        "",
        f"読み方と作り方は [JOBS.md](../../JOBS.md)。表との線は [データの流れ](../../dataflow/{domain}.md)。",
        "",
    ]
    graph = _dependency_graph(domain, deps)
    if graph:
        lines += [
            "## ジョブ間の依存",
            "",
            (
                "矢印の元が書いた表を、先が読むだけで使う（cron のジョブ同士。線の字はその表）。"
                f"灰色の点線の枠はほかの機能のジョブ。`{'`・`'.join(sorted(HUB))}` と、両方が書く表、ファイル（`data/`）の受け渡しは出ない。"
            ),
            "",
        ]
        lines += graph
    for job in [j for j in all_jobs if _domain(j) == domain]:
        mine = [r for r in runs if r.job == job]
        e = by_name.get(job)
        lines += [f"## {_stem(job)}", "", f"`{job}`", ""]
        if mine:
            for (_, args), group in sorted(_variants(mine).items()):
                lines.append(f"- 起動: {_when(group)}" + (f"（`{args}`）" if args else ""))
            for chain in sorted({_chain_text(r, runs) for r in mine} - {""}):
                lines.append(f"- 続けて走る順: {chain}")
        else:
            lines.append("- 起動: cron に無い" + (f"（Actions: {_code(actions[job])}）" if job in actions else ""))
        if e and e.calls:
            lines.append("- 呼ぶ API: " + _code(e.calls))
        lines.append("- 書く: " + (_code(e.writes()) if e and e.writes() else "（観測なし）"))
        lines.append("- 読むだけ: " + (_code(e.reads()) if e and e.reads() else "（観測なし）"))
        lines.append("")
        lines += _arg_table(job_args(root / job))
    return "\n".join(lines)


def render(root: Path = ROOT) -> dict[str, str]:
    all_jobs, entries = load_entries(root)
    tz, runs = load(root)
    missing = sorted({r.job for r in runs} - set(all_jobs))
    if missing:
        raise ValueError(f"{CRONTAB} にあるのに `__main__` を持つジョブに無い: {missing}")
    by_name = {e.name: e for e in entries if e.kind == "job"}
    actions = workflow_jobs(root)
    deps = dependencies(entries, {r.job for r in runs})
    out = {OVERVIEW: _overview(all_jobs, runs, tz, actions)}
    for key, _ in DOMAINS:
        if any(_domain(j) == key for j in all_jobs):
            out[f"{PAGE_DIR}/{key}.md"] = _domain_page(root, key, all_jobs, by_name, runs, deps, actions)
    return out


def stale(root: Path = ROOT) -> list[str]:
    want = render(root)
    bad = [p for p, text in want.items() if not (root / p).is_file() or (root / p).read_text(encoding="utf-8") != text]
    page_dir = root / PAGE_DIR
    if page_dir.is_dir():
        bad += [f"{PAGE_DIR}/{p.name}" for p in page_dir.glob("*.md") if f"{PAGE_DIR}/{p.name}" not in want]
    return sorted(bad)


def write(root: Path = ROOT) -> list[str]:
    changed = stale(root)
    want = render(root)
    for p in changed:
        path = root / p
        if p in want:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(want[p], encoding="utf-8")
        else:
            path.unlink()
    return changed


if __name__ == "__main__":
    for p in write():
        print(p)
