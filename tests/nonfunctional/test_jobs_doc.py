from __future__ import annotations

import textwrap

import pytest

from tools.dataflow.render import Entry
from tools.jobs.args import job_args
from tools.jobs.crontab import dow_text, load, parse, schedule_text
from tools.jobs.render import OVERVIEW, PAGE_DIR, REBUILD, ROOT, dependencies, render, stale, workflow_jobs


def test_jobs_doc_matches_crontab_arguments_and_dataflow():
    bad = stale()
    assert not bad, f"deploy/crontab・ジョブの引数・docs/dataflow/entries.json と合わない。`{REBUILD}` で作り直してコミットする: {bad}"


def test_every_cron_job_is_listed_with_its_schedule_and_has_a_section():
    _, runs = load(ROOT)
    pages = render()
    overview = pages[OVERVIEW]
    sections = "\n".join(text for p, text in pages.items() if p != OVERVIEW)
    assert runs
    for run in runs:
        stem = run.job.removeprefix("jobs/").removesuffix(".py")
        assert f"| `{stem}` |" in overview, run.job
        assert schedule_text(run) in overview, run
        assert f"## {stem}\n" in sections, run.job


def test_parse_expands_lists_ranges_and_chained_commands():
    tz, runs = parse(textwrap.dedent("""\
        # 説明
        CRON_TZ=Asia/Tokyo
        SHELL=/bin/bash
        0,30 16-23 * * 1-5 cd /x && deploy/cron-run.sh jobs/market/pts.py
        0 12-23 * * * cd /x && { deploy/cron-run.sh jobs/xbrl/fetch.py 2; deploy/cron-run.sh jobs/xbrl/parse.py --limit 3; }
        25 22 * * 0 cd /x && deploy/cron-run.sh jobs/market/weekly.py --years 5
        0 0 * * 2-6 cd /x && deploy/cron-run.sh jobs/market/pts.py
    """))
    assert tz == "Asia/Tokyo"
    assert [(r.job, r.args, r.step, r.chain) for r in runs] == [
        ("jobs/market/pts.py", "", 0, 1),
        ("jobs/xbrl/fetch.py", "2", 0, 2),
        ("jobs/xbrl/parse.py", "--limit 3", 1, 2),
        ("jobs/market/weekly.py", "--years 5", 0, 1),
        ("jobs/market/pts.py", "", 0, 1),
    ]
    assert runs[0].fires[0] == (16, 0) and runs[0].fires[-1] == (23, 30) and len(runs[0].fires) == 16
    assert [schedule_text(r) for r in runs] == [
        "平日 16〜23 時の毎時 00・30 分",
        "毎日 12〜23 時の毎時 00 分",
        "毎日 12〜23 時の毎時 00 分",
        "日曜 22:25",
        "火〜土曜 00:00",
    ]


def test_dow_text_treats_7_as_sunday_and_joins_gaps():
    _, runs = parse("0 5 * * 7,1,3 deploy/cron-run.sh jobs/a.py\n")
    assert runs[0].dows == (0, 1, 3)
    assert dow_text(runs[0].dows) == "日〜月・水曜"


@pytest.mark.parametrize("line", [
    "0 5 1 * * deploy/cron-run.sh jobs/a.py",
    "0 5 * 6 * deploy/cron-run.sh jobs/a.py",
    "0 5 * * * /usr/bin/true",
    "0 24 * * * deploy/cron-run.sh jobs/a.py",
    "0 5 * *",
])
def test_parse_refuses_lines_the_timetable_cannot_show(line):
    with pytest.raises(ValueError):
        parse(line + "\n")


def test_job_args_reads_add_argument_in_source_order(tmp_path):
    path = tmp_path / "job.py"
    path.write_text(textwrap.dedent("""\
        import argparse
        DEST = "x"
        ap = argparse.ArgumentParser()
        ap.add_argument("days", type=int, help="days back")
        ap.add_argument("--dest", default=DEST, help="a|b")
        ap.add_argument("-n", "--dry-run", action="store_true")
        ap.add_argument("--source", choices=("meti", "estat"))
        ap.add_argument("--code", action="append", nargs="+", help=f"{DEST} code")
        ap.add_argument("--kind", choices=KINDS)
    """), encoding="utf-8")
    args = job_args(path)
    assert [(a.names, a.kind, a.default, a.help) for a in args] == [
        (("days",), "int", "", "days back"),
        (("--dest",), "", "DEST", "a|b"),
        (("-n", "--dry-run"), "フラグ", "", ""),
        (("--source",), "meti / estat", "", ""),
        (("--code",), "append、nargs=+", "", "f'{DEST} code'"),
        (("--kind",), "KINDS", "", ""),
    ]
    assert args[0].positional and not args[1].positional


def test_dependencies_link_writer_to_read_only_reader_among_cron_jobs():
    entries = [
        Entry("jobs/a.py", "job", "common", {"t1": "W", "stocks": "W", "t3": "W"}, []),
        Entry("jobs/b.py", "job", "common", {"t1": "R", "stocks": "R", "t3": "W"}, []),
        Entry("jobs/manual.py", "job", "common", {"t1": "R"}, []),
        Entry("GET /api/x", "api", "market", {"t1": "R"}, []),
    ]
    assert dependencies(entries, {"jobs/a.py", "jobs/b.py"}) == {("jobs/a.py", "jobs/b.py"): ["t1"]}


def test_workflow_jobs_finds_jobs_run_from_actions(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "b.yml").write_text("run: python -u jobs/monthly/names.py ${MODE}\n", encoding="utf-8")
    (wf / "a.yml").write_text("run: python jobs/monthly/names.py\nrun: python jobs/x.py\n", encoding="utf-8")
    assert workflow_jobs(tmp_path) == {"jobs/monthly/names.py": ["a.yml", "b.yml"], "jobs/x.py": ["a.yml"]}


def test_pages_are_only_under_the_generated_folder():
    assert all(p == OVERVIEW or p.startswith(PAGE_DIR + "/") for p in render())


def test_dependency_graphs_ask_for_the_elk_layout():
    graphs = [g for p, text in render().items() if p != OVERVIEW for g in text.split("```mermaid\n")[1:]]
    assert graphs and all(g.startswith("---\nconfig:\n  layout: elk\n---\nflowchart LR\n") for g in graphs)
