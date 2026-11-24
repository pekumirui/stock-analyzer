from __future__ import annotations

from pathlib import Path

from tools.mcp.catalog import Tool, load_tools

ROOT = Path(__file__).resolve().parents[2]
PAGE = "docs/jobs/gen/mcp.md"
REBUILD = "python -m tools.mcp.render"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _code(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _arg_table(tool: Tool) -> list[str]:
    if not tool.args:
        return ["引数: なし", ""]
    lines = ["| 引数 | 必須 | 型 | 説明 |", "|---|---|---|---|"]
    for arg in tool.args:
        lines.append(
            f"| `{arg.name}` | {'はい' if arg.required else ''} | {_cell(arg.type)} | {_cell(arg.description)} |"
        )
    return lines + [""]


def _page(tools: list[Tool]) -> str:
    lines = [
        "# MCP のツール",
        "",
        "画面を持たない。`mcp/*.py` のモジュール直下の `TOOLS`（名前、説明、引数の schema、関数）を AST で読む。`mcp` は import しない。",
        "",
        f"- 作り直しは `{REBUILD}`",
        "- 読み書きする表は出さない。dataflow の観測は API とジョブだけで、MCP は HTTP と生の SQL が混ざる。`db_query` は渡された SELECT 次第で表が変わる",
        "- 引数は schema の `properties` の直下だけ。入れ子のフィールドは引数の説明に残る",
        "- 説明の `{式}` はソースの f-string で、実行時に埋まる部分",
        "",
        "## 一覧",
        "",
        "| ツール | 登録 | 関数 | 必須の引数 |",
        "|---|---|---|---|",
    ]
    for tool in tools:
        required = _code(arg.name for arg in tool.args if arg.required)
        lines.append(f"| `{tool.name}` | `{tool.path}` | `{tool.handler}` | {required} |")
    lines.append("")
    for tool in tools:
        lines += [
            f"## {tool.name}",
            "",
            f"`{tool.path}` `{tool.handler}`",
            "",
            tool.description,
            "",
        ]
        lines += _arg_table(tool)
    return "\n".join(lines)


def render(root: Path = ROOT) -> dict[str, str]:
    return {PAGE: _page(load_tools(root))}


def stale(root: Path = ROOT) -> list[str]:
    want = render(root)
    return [p for p, text in want.items() if not (root / p).is_file() or (root / p).read_text(encoding="utf-8") != text]


def write(root: Path = ROOT) -> list[str]:
    changed = stale(root)
    want = render(root)
    for p in changed:
        path = root / p
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(want[p], encoding="utf-8")
    return changed


if __name__ == "__main__":
    for p in write():
        print(p)
