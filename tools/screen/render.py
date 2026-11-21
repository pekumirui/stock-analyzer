from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from tools.dataflow.render import ELK_LAYOUT, ENTRIES, HUB, Entry, load_entries
from tools.screen.sources import JS_DIR, SCREENS_JS, SHARED, Call, Screen, calls, file_tab, match, screens, sub_tabs, tabs

ROOT = Path(__file__).resolve().parents[2]
OVERVIEW = "docs/SCREENS.md"
PAGE_DIR = "docs/screen/gen"
REBUILD = "python -m tools.screen.render"
SHARED_LABEL = "共通"


def _code(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _cell(text) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _hash(screen: Screen, path: str) -> str:
    return f"#{screen.tab}/{path}" if path else f"#{screen.tab}"


def api_routes() -> dict[str, dict]:
    from app.main import app
    from tools.dataflow.plugin import api_entries

    paths = app.openapi()["paths"]
    out = {}
    for label in api_entries():
        method, path = label.split(" ", 1)
        out[label] = paths.get(path, {}).get(method.lower(), {})
    return out


def _type(schema: dict) -> str:
    options = [s for s in schema.get("anyOf", [schema]) if s.get("type") != "null"]
    parts = []
    for s in options:
        text = s.get("type", "")
        if "enum" in s:
            text = " / ".join(str(v) for v in s["enum"])
        elif "format" in s:
            text += f"（{s['format']}）"
        elif "pattern" in s:
            text += f"（`{s['pattern']}`）"
        parts.append(text)
    return " / ".join(parts)


def _params(op: dict) -> list[str]:
    params = op.get("parameters", [])
    if not params and "requestBody" not in op:
        return ["引数: なし", ""]
    lines = ["| 引数 | 場所 | 型 | 必須 | 既定 |", "|---|---|---|---|---|"]
    for p in params:
        schema = p.get("schema", {})
        default = f"`{_cell(schema['default'])}`" if schema.get("default") is not None else ""
        lines.append(f"| `{p['name']}` | {p['in']} | {_cell(_type(schema))} | {'○' if p.get('required') else ''} | {default} |")
    if "requestBody" in op:
        lines.append("| （本文） | body | JSON | ○ |  |")
    return lines + [""]


def _tab_labels(all_screens: list[Screen], tab_names: list[str]) -> dict[str, str]:
    by_id = {s.id: s.label for s in all_screens}
    return {**{t: by_id[t] for t in tab_names}, SHARED: SHARED_LABEL}


def _transition(all_screens: list[Screen], tab_names: list[str], labels: dict[str, str]) -> list[str]:
    ids = {s.id: f"s{i}" for i, s in enumerate(all_screens)}
    lines = ["```mermaid", *ELK_LAYOUT, "flowchart LR"]
    for tab in tab_names:
        lines.append(f'  subgraph {tab}["{labels[tab]}"]')
        for s in all_screens:
            if s.tab == tab:
                lines.append(f'    {ids[s.id]}["{s.label}<br>{_hash(s, s.paths[-1])}"]')
        lines.append("  end")
    for src, dst, default in transitions(all_screens):
        lines.append(f"  {ids[src]} {'==>' if default else '-->'} {ids[dst]}")
    return lines + ["```", ""]


def transitions(all_screens: list[Screen]) -> list[tuple[str, str, bool]]:
    by_id = {s.id: s for s in all_screens}
    out = []
    for s in all_screens:
        for src in s.froms:
            default = src == s.default_from
            siblings = by_id[src].tab == s.tab and s.id in by_id[src].froms
            if default or not siblings:
                out.append((src, s.id, default))
    return out


def _overview(all_screens: list[Screen], tab_names: list[str], labels: dict[str, str],
              by_tab: dict[str, dict[str, list[Call]]], unused: list[str], callers: dict[str, list[str]]) -> str:
    lines = [
        "# 画面設計",
        "",
        (
            f"- 画面と遷移は `{SCREENS_JS}` の `ROUTES` と `BREADCRUMBS`、画面が呼ぶ API は `{JS_DIR}/*.js` の `/api/…` の文字列、"
            f"API の引数は FastAPI の OpenAPI、読み書きする表は `{ENTRIES}` から拾う"
        ),
        "- API の応答の形は載らない（ルートが応答の型を宣言していない）。形は各タブの手書きの画面仕様にある",
        "- 読み書きする表はテストが通った道だけなので下限。読み方は [DATA_FLOW.md](DATA_FLOW.md)",
        "",
        "## タブごとのページ",
        "",
        "タブの画面、画面が呼ぶ API と表の図、API の引数。API は呼んでいる JS のファイル名の頭（`stock-price.js` なら銘柄）でタブに分ける。",
        "",
        "| タブ | 画面 | JS | 呼ぶ API |",
        "|---|---|---|---|",
    ]
    for tab in [*tab_names, SHARED]:
        own = [s.label for s in all_screens if s.tab == tab]
        files = sorted({c.file for cs in by_tab[tab].values() for c in cs})
        lines.append(f"| [{labels[tab]}](screen/gen/{tab}.md) | {'、'.join(own)} | {_code(files)} | {len(by_tab[tab])} |")
    lines += [
        "",
        "## 画面遷移",
        "",
        (
            "枠はタブ、箱は画面（名前とハッシュ）。矢印は「元の画面から来られる」（パンくずの戻り先になる画面）。"
            "太い矢印は、来た元が分からないときの戻り先。タブの切り替えはどの画面からもできるので線にしない。"
            "同じタブの中で互いに行き来できる画面どうしの細い線も省く（下の一覧の「来られる元」には出す）。"
        ),
        "",
    ]
    lines += _transition(all_screens, tab_names, labels)
    lines += [
        "## 画面の一覧",
        "",
        "| 画面 | id | タブ | ハッシュ | 既定の戻り先 | 来られる元 |",
        "|---|---|---|---|---|---|",
    ]
    for s in all_screens:
        hashes = "<br>".join(f"`{_hash(s, p)}`" for p in s.paths)
        lines.append(
            f"| {s.label} | `{s.id}` | {labels[s.tab]} | {hashes} | {f'`{s.default_from}`' if s.default_from else ''} | {_code(s.froms)} |"
        )
    lines += [
        "",
        "## 画面の JS が呼ばない API",
        "",
        "ジョブや MCP、リンクの URL から使う。",
        "",
        "| API | 呼ぶジョブ |",
        "|---|---|",
    ]
    lines += [f"| `{label}` | {_code(callers.get(label, []))} |" for label in unused]
    lines.append("")
    return "\n".join(lines)


def _flow(found: dict[str, list[Call]], by_name: dict[str, Entry]) -> list[str]:
    ids: dict[str, str] = {}

    def nid(key: str) -> str:
        return ids.setdefault(key, f"n{len(ids)}")

    files = sorted({c.file for cs in found.values() for c in cs})
    tables = sorted({t for label in found if label in by_name for t in by_name[label].rw if t not in HUB or by_name[label].rw[t] == "W"})
    lines = ["```mermaid", *ELK_LAYOUT, "flowchart LR"]
    lines += [f'  {nid("f:" + f)}["{f}"]:::js' for f in files]
    lines += [f'  {nid("a:" + a)}["{a}"]:::api' for a in sorted(found)]
    lines += [f'  {nid("t:" + t)}[("{t}")]:::tbl' for t in tables]
    for label in sorted(found):
        for f in sorted({c.file for c in found[label]}):
            lines.append(f"  {nid('f:' + f)} --> {nid('a:' + label)}")
        for t, rw in sorted(by_name[label].rw.items()) if label in by_name else []:
            if t in tables:
                lines.append(f"  {nid('a:' + label)} {'-->' if rw == 'W' else '-.-'} {nid('t:' + t)}")
    lines += [
        "  classDef js fill:#f3e8ff,stroke:#7048e8",
        "  classDef api fill:#e6f7ec,stroke:#2b8a3e",
        "  classDef tbl fill:#fff4e0,stroke:#e8590c",
        "```",
        "",
    ]
    return lines


def _tab_page(tab: str, labels: dict[str, str], all_screens: list[Screen], found: dict[str, list[Call]],
              by_name: dict[str, Entry], ops: dict[str, dict]) -> str:
    lines = [
        f"# 画面設計: {labels[tab]}",
        "",
        "読み方と作り方は [SCREENS.md](../../SCREENS.md)。",
        "",
    ]
    own = [s for s in all_screens if s.tab == tab]
    if own:
        lines += ["## 画面", "", "| 画面 | id | ハッシュ |", "|---|---|---|"]
        lines += [f"| {s.label} | `{s.id}` | {'<br>'.join(f'`{_hash(s, p)}`' for p in s.paths)} |" for s in own]
        lines.append("")
    if not found:
        return "\n".join(lines + ["この JS からは API を呼ばない。", ""])
    lines += [
        "## 呼ぶ API と表",
        "",
        f"左から JS のファイル、API、表。API から表への実線は書き込み、点線は読み取りだけ。`{'`・`'.join(sorted(HUB))}` を読み取るだけの線は省く。",
        "",
    ]
    lines += _flow(found, by_name)
    lines += ["| API | 呼ぶ JS | 書き込み | 読み取りだけ |", "|---|---|---|---|"]
    for label in sorted(found):
        e = by_name.get(label)
        where = _code(sorted({c.file for c in found[label]}))
        lines.append(f"| `{label}` | {where} | {_code(e.writes()) if e else ''} | {_code(e.reads()) if e else ''} |")
    lines += ["", "## API の引数", ""]
    for label in sorted(found):
        lines += [f"### {label}", ""]
        lines += _params(ops[label])
    return "\n".join(lines)


def render(root: Path = ROOT) -> dict[str, str]:
    _, entries = load_entries(root)
    by_name = {e.name: e for e in entries if e.kind == "api"}
    callers: dict[str, list[str]] = defaultdict(list)
    for e in entries:
        for label in e.calls:
            callers[label].append(e.name)
    ops = api_routes()
    all_screens = screens(root)
    tab_names = tabs(root)
    labels = _tab_labels(all_screens, tab_names)
    subs = sub_tabs(all_screens)
    by_tab: dict[str, dict[str, list[Call]]] = {t: defaultdict(list) for t in [*tab_names, SHARED]}
    lost = []
    for call in calls(root):
        label = match(call, list(ops))
        if label is None:
            lost.append(f"{call.file}:{call.line} {call.method} {call.url}")
            continue
        by_tab[file_tab(call.file, tab_names, subs)][label].append(call)
    if lost:
        raise ValueError(f"{JS_DIR} が呼ぶのに app/api に無い API: {lost}")
    used = {label for found in by_tab.values() for label in found}
    out = {OVERVIEW: _overview(all_screens, tab_names, labels, by_tab, sorted(set(ops) - used), callers)}
    for tab in [*tab_names, SHARED]:
        out[f"{PAGE_DIR}/{tab}.md"] = _tab_page(tab, labels, all_screens, by_tab[tab], by_name, ops)
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
