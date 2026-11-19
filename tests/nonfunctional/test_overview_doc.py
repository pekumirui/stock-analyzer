from __future__ import annotations

import textwrap

from tools.overview import modules, system
from tools.overview.render import MODULES_PAGE, REBUILD, ROOT, SYSTEM_PAGE, render, stale, write


def test_overview_doc_matches_sources():
    bad = stale()
    assert not bad, f"import・deploy/ と合わない。`{REBUILD}` で作り直してコミットする: {bad}"


def test_pages_are_only_the_two_generated_ones():
    assert set(render()) == {MODULES_PAGE, SYSTEM_PAGE}


def test_every_diagram_asks_for_the_elk_layout():
    for text in render().values():
        graphs = text.split("```mermaid\n")[1:]
        assert graphs and all(g.startswith("---\nconfig:\n  layout: elk\n---\nflowchart LR\n") for g in graphs)


def test_every_python_package_has_a_row_in_the_modules_table():
    text = render()[MODULES_PAGE]
    for rel in modules.python_files(ROOT):
        pkg = modules.package_of(rel)
        assert f"| `{pkg}` |" in text, pkg


def test_package_of_groups_by_feature_folder_and_boxes_vendor():
    assert modules.package_of("app/market/pts.py") == "app/market"
    assert modules.package_of("app/models.py") == "app"
    assert modules.package_of("app/xbrlp/x.py") == modules.VENDOR_BOX
    assert modules.package_of("jobs/fx_rates.py") == "jobs"
    assert modules.package_of("jobs/ops/backup_db.py") == "jobs/ops"
    assert modules.package_of("mcp/log_mcp.py") == "mcp"
    assert modules.package_of("analyzer/momentum/backtest.py") == "analyzer"


def test_dependencies_resolve_relative_absolute_and_package_imports(tmp_path):
    files = {
        "app/__init__.py": "",
        "app/db.py": "",
        "app/market/__init__.py": "",
        "app/market/a.py": "from . import b\nfrom ..db import x\n",
        "app/market/b.py": "",
        "app/xbrl/__init__.py": "",
        "app/xbrl/c.py": "def f():\n    from app.market.b import y\n    import jobs.ops.z\n",
        "jobs/__init__.py": "",
        "jobs/ops/__init__.py": "",
        "jobs/ops/z.py": "from app import market\nimport json\n",
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert modules.dependencies(tmp_path) == {
        "app/market": {"app"},
        "app/xbrl": {"app/market", "jobs/ops"},
        "jobs/ops": {"app/market"},
    }


def test_nginx_locations_are_read_from_the_here_doc(tmp_path):
    (tmp_path / "deploy").mkdir()
    (tmp_path / system.NGINX).write_text(textwrap.dedent("""\
        APP_UPSTREAM="${APP_UPSTREAM:-127.0.0.1:8000}"
        proxy_block=$(cat <<EOF
            location = / {
                return 301 /monthly;
            }
            location ^~ /app {
        ${auth_block}
                proxy_pass http://${APP_UPSTREAM};
            }
            location ^~ /static/ {
                alias ${APP_DIR}/static/;
            }
        EOF
        )
    """), encoding="utf-8")
    ngx = system.nginx(tmp_path)
    assert ngx.upstreams == {"APP_UPSTREAM": "127.0.0.1:8000"}
    assert [(x.modifier, x.path, x.upstream, x.access) for x in ngx.locations] == [
        ("=", "/", "", False),
        ("^~", "/app", "APP_UPSTREAM", True),
        ("^~", "/static/", "", False),
    ]
    assert ngx.locations[0].target == "301 で `/monthly` へ"


def test_real_nginx_and_units_are_found():
    assert len(system.nginx(ROOT).locations) > 10
    names = {u.name for u in system.units(ROOT)}
    assert {"stock-analyzer.service", "stock-analyzer-mcp.service", "r2-share-watch.timer"} <= names
    ports = {p for u in system.units(ROOT) for p in u.ports}
    assert {8000, 8010} <= ports


def test_stale_reports_changed_and_leftover_pages(tmp_path):
    import shutil

    shutil.copytree(ROOT / "deploy", tmp_path / "deploy", ignore=shutil.ignore_patterns("__pycache__"))
    for top in ("app", "jobs", "mcp", "tools", "analyzer"):
        shutil.copytree(ROOT / top, tmp_path / top, ignore=shutil.ignore_patterns("__pycache__"), dirs_exist_ok=True)
    assert write(tmp_path)
    assert stale(tmp_path) == []
    (tmp_path / MODULES_PAGE).write_text("x", encoding="utf-8")
    (tmp_path / "docs/overview/gone.md").write_text("x", encoding="utf-8")
    assert stale(tmp_path) == ["docs/overview/gone.md", MODULES_PAGE]
