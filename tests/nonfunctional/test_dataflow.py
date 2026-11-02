"""docs/DATA_FLOW.md を作る部品（tools/dataflow/）。SQL からテーブルを取る所と、関数の参照グラフ。"""
from __future__ import annotations

import textwrap
from pathlib import Path

from tools.dataflow.callgraph import CallGraph, match_route
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
