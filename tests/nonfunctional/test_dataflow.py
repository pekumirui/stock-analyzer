"""docs/DATA_FLOW.md を作る部品（tools/dataflow/）。SQL からテーブルを取る所、関数の参照グラフ、書き出し。"""
from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path

from tools.dataflow.callgraph import CallGraph, match_route
from tools.dataflow.render import entry_domain, render, table_domain
from tools.dataflow.sql import tables


def _t(sql):
    w, r = tables(sql)
    return sorted(w), sorted(r)


def test_select_join_and_comma_from():
    assert _t('SELECT s.code FROM stocks AS s JOIN "daily_prices" d ON d.code = s.code') == ([], ["daily_prices", "stocks"])
    assert _t("SELECT * FROM topix, indicators WHERE 1") == ([], ["indicators", "topix"])


def test_writes_do_not_count_target_as_read():
    assert _t("DELETE FROM indicators WHERE code = ?") == (["indicators"], [])
    assert _t("INSERT OR REPLACE INTO fx_rates (d) VALUES (?)") == (["fx_rates"], [])
    assert _t("UPDATE xbrl_financials SET x = (SELECT y FROM xbrl_facts)") == (["xbrl_financials"], ["xbrl_facts"])


def test_upsert_set_is_not_a_table():
    sql = "INSERT INTO stocks (code) VALUES (?) ON CONFLICT (code) DO UPDATE SET name = excluded.name"
    assert _t(sql) == (["stocks"], [])


def test_strings_comments_ctes_and_ddl_are_ignored():
    assert _t("SELECT 'FROM stocks' FROM topix -- JOIN indicators") == ([], ["topix"])
    assert _t("WITH last AS (SELECT code FROM daily_prices) SELECT * FROM last") == ([], ["daily_prices"])
    assert _t("CREATE TABLE x AS SELECT * FROM stocks") == ([], [])
    assert _t("SELECT * FROM (SELECT code FROM stocks)") == ([], ["stocks"])


def test_match_route():
    assert match_route("/api/v1/tables/\x00/ingest", "/api/v1/tables/{code}/ingest")
    assert not match_route("/api/v1/tables/\x00", "/api/v1/tables/{code}/ingest")
    assert match_route("/api/v1/tables/", "/api/v1/tables")


def _tree(tmp: Path, files: dict[str, str]) -> CallGraph:
    for rel, src in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src), encoding="utf-8")
    return CallGraph(tmp)


def test_callgraph_follows_imports_callbacks_and_methods(tmp_path):
    g = _tree(tmp_path, {
        "app/__init__.py": "",
        "app/lib.py": """
            def used(): helper()
            def helper(): pass
            def unused(): pass
            class Box:
                def run(self): self.inner()
                def inner(self): pass
        """,
        "jobs/__init__.py": "",
        "jobs/job.py": """
            from app import lib
            from app.lib import Box
            def main(pool):
                pool.map(lib.used, [])
                Box().run()
            if __name__ == "__main__":
                main(None)
        """,
    })
    reach = g.reachable(g.main_roots("jobs.job"))
    assert ("app.lib", "used") in reach and ("app.lib", "helper") in reach
    assert ("app.lib", "Box.inner") in reach
    assert ("app.lib", "unused") not in reach
    assert ("app.lib", "helper") not in g.reachable(g.main_roots("jobs.job"), frozenset({("app.lib", "used")}))


def test_callgraph_finds_http_api_urls(tmp_path):
    g = _tree(tmp_path, {
        "jobs/__init__.py": "",
        "jobs/push.py": """
            def main(client, origin, code):
                client.post_json(f"{origin}/api/v1/tables/{code}/ingest", {})
                url = f"{origin}/api/v1/tables/{code}"
            if __name__ == "__main__":
                main(None, "", "")
        """,
    })
    assert g.api_urls(g.reachable(g.main_roots("jobs.push"))) == {
        ("POST", "/api/v1/tables/\x00/ingest"),
        ("GET", "/api/v1/tables/\x00"),
    }


def test_domains_of_tables_and_entries():
    assert table_domain("monthly_values") == "monthly"
    assert table_domain("large_holdings") == "edinet"
    assert table_domain("stocks") == "market"
    assert entry_domain("jobs/monthly/x.py", "job", {"stocks": "R"}) == "monthly"
    assert entry_domain("jobs/golden_db.py", "job", {"monthly_values": "W"}) == "common"
    assert entry_domain("GET /a", "api", {"stocks": "R", "xbrl_filings": "R"}) == "xbrl"
    assert entry_domain("PUT /a", "api", {"monthly_values": "W", "xbrl_filings": "R", "xbrl_facts": "R"}) == "monthly"
    assert entry_domain("GET /a", "api", {"stocks": "R"}) == "market"


def _render():
    jobs = {"jobs/monthly/ingest.py": {"monthly_values": {"R", "W"}, "statements": {"R"}, "stocks": {"R"}},
            "jobs/monthly/fetch.py": {"stocks": {"R"}}}
    apis = {"GET /api/v1/tables": {"monthly_values": {"R"}, "stocks": {"R"}}}
    return render(jobs, {"jobs/monthly/ingest.py": ["GET /api/v1/tables"]}, apis,
                  ["jobs/monthly/fetch.py", "jobs/monthly/ingest.py", "jobs/ops/backup.py"],
                  ["GET /api/v1/tables", "GET /api/topix"], [], ["monthly_values", "statements", "stocks", "topix"])


def test_render_writes_overview_pages_and_viewer():
    out = _render()
    assert set(out) == {"docs/DATA_FLOW.md", "docs/dataflow/market.md", "docs/dataflow/monthly.md",
                        "docs/dataflow/viewer.html", "docs/dataflow/entries.json"}
    overview = out["docs/DATA_FLOW.md"]
    assert "[月次](dataflow/monthly.md) | 2 | 1 |" in overview
    assert "- `jobs/ops/backup.py`" in overview and "- `GET /api/topix`" in overview
    assert "どのバッチ・API の読み書きにも出なかった表:\n\n- `topix`" in overview


def test_mermaid_draws_entry_to_table_and_skips_hub_reads():
    page = _render()["docs/dataflow/monthly.md"]
    graph = page.split("```mermaid", 1)[1].split("```", 1)[0]
    ids = dict(re.findall(r"^  (n\d+)\[\(?\"([^\"]+)\"", graph, re.M))
    names = {v: k for k, v in ids.items()}
    assert f"{names['monthly/ingest.py']} --> {names['monthly_values']}" in graph
    assert f"{names['monthly/ingest.py']} -.- {names['statements']}" in graph
    assert "stocks" not in names and "monthly/fetch.py" not in names
    assert '[("statements")]:::ext' in graph
    assert graph.startswith("\n---\nconfig:\n  layout: elk\n---\nflowchart LR\n")


def test_viewer_embeds_parseable_data():
    html = _render()["docs/dataflow/viewer.html"]
    raw = re.search(r'<script type="application/json" id="data">(.*?)</script>', html, re.S).group(1)
    data = json.loads(raw)
    assert data["entries"][0]["name"] == "jobs/monthly/fetch.py"
    assert data["tables"]["monthly_values"] == "monthly"
    assert data["entries"][1]["rw"] == {"monthly_values": "W", "statements": "R", "stocks": "R"}
    assert "<script" not in raw
    assert not re.search(r"""(?:src|href)=["']?(?:https?:)?//""", html)


def test_entries_json_round_trips_through_load_entries(tmp_path):
    from tools.dataflow.render import ENTRIES, load_entries

    path = tmp_path / ENTRIES
    path.parent.mkdir(parents=True)
    path.write_text(_render()[ENTRIES], encoding="utf-8")
    jobs, entries = load_entries(tmp_path)
    assert jobs == ["jobs/monthly/fetch.py", "jobs/monthly/ingest.py", "jobs/ops/backup.py"]
    assert [(e.name, e.kind, e.domain) for e in entries][:2] == [
        ("jobs/monthly/fetch.py", "job", "monthly"), ("jobs/monthly/ingest.py", "job", "monthly"),
    ]
    assert entries[1].writes() == ["monthly_values"] and entries[1].reads() == ["statements", "stocks"]
