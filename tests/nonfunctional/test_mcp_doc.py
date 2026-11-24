from __future__ import annotations

import textwrap

import pytest

from tools.jobs.render import OVERVIEW as JOBS_OVERVIEW
from tools.jobs.render import render as render_jobs
from tools.mcp.catalog import load_tools
from tools.mcp.render import PAGE, REBUILD, ROOT, render, stale


def test_mcp_doc_matches_tool_registration():
    bad = stale()
    assert not bad, f"mcp/*.py の TOOLS と合わない。`{REBUILD}` で作り直してコミットする: {bad}"


def test_jobs_overview_points_at_the_mcp_page():
    assert "jobs/gen/mcp.md" in render_jobs()[JOBS_OVERVIEW]


def test_every_registered_tool_is_a_section_with_its_required_args():
    page = render()[PAGE]
    tools = load_tools(ROOT)
    assert tools
    for tool in tools:
        start = page.index(f"## {tool.name}\n")
        body = page[start:]
        nxt = body.find("\n## ", 3)
        section = body if nxt < 0 else body[:nxt]
        assert f"`{tool.path}` `{tool.handler}`" in section
        assert tool.description in section
        for arg in tool.args:
            assert f"| `{arg.name}` |" in section
            if arg.required:
                assert f"| `{arg.name}` | はい |" in section


def test_catalog_reads_registration_without_running_it(tmp_path):
    mcp = tmp_path / "mcp"
    mcp.mkdir()
    (mcp / "b.py").write_text(textwrap.dedent("""\
        TOOLS = {
            "late": ("後", {"type": "object", "properties": {}}, tool_late),
        }
    """), encoding="utf-8")
    (mcp / "a.py").write_text(textwrap.dedent("""\
        TOOLS: dict = {
            "fs_list": (
                ("一覧。" f"上限は {LIMIT}。" "外へ出さない。"),
                {
                    "type": "object",
                    "properties": {
                        "root": {"type": "string", "enum": ["data", "logs"], "description": "どちらを見るか"},
                        "kind": {"type": "string", "enum": list(mod.KINDS), "description": "種別"},
                        "codes": {"type": "array", "items": {"type": "string"}, "description": "絞る"},
                        "rows": {
                            "type": "object",
                            "description": "入れ子は説明に残す",
                            "properties": {"month": {"type": "integer"}},
                        },
                    },
                    "required": ["root", "kind"],
                },
                fs_tools.tool_fs_list,
            ),
        }
    """), encoding="utf-8")
    tools = load_tools(tmp_path)
    assert [(t.path, t.name, t.handler) for t in tools] == [
        ("mcp/a.py", "fs_list", "fs_tools.tool_fs_list"),
        ("mcp/b.py", "late", "tool_late"),
    ]
    fs = tools[0]
    assert fs.description == "一覧。上限は {LIMIT}。外へ出さない。"
    assert [(a.name, a.type, a.required, a.description) for a in fs.args] == [
        ("root", "data / logs", True, "どちらを見るか"),
        ("kind", "mod.KINDS", True, "種別"),
        ("codes", "array[string]", False, "絞る"),
        ("rows", "object", False, "入れ子は説明に残す"),
    ]
    assert "month" not in {a.name for a in fs.args}
    page = render(tmp_path)[PAGE]
    assert page.index("## fs_list") < page.index("## late")
    assert "| `kind` | はい | mod.KINDS | 種別 |" in page
    assert "引数: なし" in page.split("## late", 1)[1]


def test_stale_when_the_page_disagrees(tmp_path):
    mcp = tmp_path / "mcp"
    mcp.mkdir()
    (mcp / "a.py").write_text('TOOLS = {"only": ("説明", {"type": "object", "properties": {}}, tool_only)}\n', encoding="utf-8")
    assert stale(tmp_path) == [PAGE]
    write_root = tmp_path
    from tools.mcp.render import write
    assert write(write_root) == [PAGE]
    assert stale(write_root) == []
    text = (write_root / PAGE).read_text(encoding="utf-8")
    (write_root / PAGE).write_text(text.replace("説明", "古い"), encoding="utf-8")
    assert stale(write_root) == [PAGE]


def test_catalog_refuses_a_registration_it_cannot_read(tmp_path):
    mcp = tmp_path / "mcp"
    mcp.mkdir()
    (mcp / "a.py").write_text("TOOLS = build_tools()\n", encoding="utf-8")
    with pytest.raises(ValueError, match="辞書ではない"):
        load_tools(tmp_path)
    (mcp / "a.py").write_text(textwrap.dedent("""\
        TOOLS = {"a": ("x", {"type": "object", "properties": {}}, tool_a)}
        TOOLS = {"a": ("y", {"type": "object", "properties": {}}, tool_a)}
    """), encoding="utf-8")
    with pytest.raises(ValueError, match="重なる"):
        load_tools(tmp_path)
