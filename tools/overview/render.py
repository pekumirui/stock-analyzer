from __future__ import annotations

from pathlib import Path

from tools.dataflow.render import ELK_LAYOUT
from tools.jobs.crontab import CRONTAB, load
from tools.overview import modules, system

ROOT = Path(__file__).resolve().parents[2]
MODULES_PAGE = "docs/overview/modules.md"
SYSTEM_PAGE = "docs/overview/system.md"
PAGE_DIR = "docs/overview"
REBUILD = "python -m tools.overview.render"
APP_HUB = "app"
JOBS_HUB = "jobs"
HUBS = (APP_HUB, JOBS_HUB)
VENDOR_LABEL = "vendor"


def _code(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _family(pkg: str) -> str:
    if pkg == modules.VENDOR_BOX:
        return "app"
    return pkg.split("/")[0]


def _label(pkg: str) -> str:
    return VENDOR_LABEL if pkg == modules.VENDOR_BOX else pkg


def _header(title: str) -> list[str]:
    return [
        f"# {title}",
        "",
        "入口は [OVERVIEW.md](../OVERVIEW.md)。",
        "",
    ]


def _graph(edges: list[tuple[str, str]], reverse: set[tuple[str, str]], own: set[str]) -> list[str]:
    ids: dict[str, str] = {}

    def nid(pkg: str) -> str:
        return ids.setdefault(pkg, f"n{len(ids)}")

    lines = ["```mermaid", *ELK_LAYOUT, "flowchart LR"]
    for pkg in sorted({p for e in edges for p in e}):
        cls = "own" if pkg in own else "ext"
        lines.append(f'  {nid(pkg)}["{_label(pkg)}"]:::{cls}')
    for a, b in edges:
        arrow = "-.->" if (a, b) in reverse else "-->"
        lines.append(f"  {nid(a)} {arrow} {nid(b)}")
    lines += [
        "  classDef own fill:#e3ecff,stroke:#3b5bdb",
        "  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3",
        "```",
        "",
    ]
    return lines


def _edges(deps: dict[str, set[str]], keep, hubs: bool = False) -> list[tuple[str, str]]:
    return sorted((a, b) for a, targets in deps.items() for b in targets if (hubs or b not in HUBS) and keep(a, b))


def _is(pkg: str, *families: str) -> bool:
    return _family(pkg) in families


def modules_page(root: Path) -> str:
    deps = modules.dependencies(root)
    pkgs = sorted({modules.package_of(p) for p in modules.python_files(root)} - {modules.VENDOR_BOX}) + [modules.VENDOR_BOX]
    app_family = {p for p in pkgs if _family(p) == "app"}
    app_edges = _edges(deps, lambda a, b: a in app_family and b in app_family)
    forward = _edges(deps, lambda a, b: _is(a, "jobs") and _is(b, "app", "jobs"))
    back = _edges(deps, lambda a, b: a in app_family and a != modules.VENDOR_BOX and _is(b, "jobs"), hubs=True)
    jobs_edges = sorted(set(forward) | set(back))
    outer_edges = _edges(deps, lambda a, b: _is(a, "tools", "mcp", "analyzer") or _is(b, "tools", "mcp", "analyzer"))
    lines = _header("モジュールの依存") + [
        f"`{'`・`'.join(modules.SOURCE_ROOTS)}` の Python の `import` を AST で読み、パッケージ単位にまとめた。関数の中の `import` も数える。",
        "",
        "- パッケージは `app/<機能>`・`jobs/<機能>`・`tools/<名前>`・`mcp`・`analyzer`。各フォルダの直下のファイルは `app`・`jobs`・`tools` の名前で 1 つにする",
        "- `vendor` は `app/xbrlp` と `app/edinet_wrapper` をまとめた 1 つの箱",
        f"- ほぼ全部が使う共通（`{APP_HUB}` 直下の db・models・config など、`{JOBS_HUB}` 直下）へ向かう線は図から省く。表には出す",
        "- 青い枠はその図の主役、灰色の点線の枠はそれ以外。点線の矢印は `app` が `jobs` を使っている線（「jobs と app」の図）",
        "- `import` があるだけで、実行時に呼ぶかは見ない。テストは数えない",
        "",
        "## app の中",
        "",
    ]
    lines += _graph(app_edges, set(), app_family)
    lines += ["## jobs と app", "", "矢印は `import` する側から、される側へ。", ""]
    lines += _graph(jobs_edges, set(back), {p for p in pkgs if _is(p, "jobs")})
    if back:
        lines += [
            "点線の矢印は `app` が `jobs` を `import` している所（`jobs` 直下への線もここだけ出す）。ふつうは `jobs` が `app` を使う向きなので、逆向きを見分けるために出した。",
            "",
        ]
    lines += ["## tools・mcp・analyzer", "", "これらを向く線・これらから出る線。`app`・`jobs` 直下（共通）への線は省く。", ""]
    lines += _graph(outer_edges, set(), {p for p in pkgs if _is(p, "tools", "mcp", "analyzer")})
    lines += ["## パッケージごとの依存先", "", "図で省いた共通も含む。", "", "| パッケージ | 依存先 |", "|---|---|"]
    for pkg in pkgs:
        lines.append(f"| `{_label(pkg)}` | {_code(sorted(deps.get(pkg, set())))} |")
    lines.append("")
    return "\n".join(lines)


def _location_row(loc: system.Location, up: dict[str, str]) -> str:
    where = f"{loc.modifier} " if loc.modifier else ""
    path = loc.path.replace("\\$", "$")
    if loc.upstream:
        target = f"`{up.get(loc.upstream, loc.upstream)}` へ中継（`{loc.upstream}`）"
    else:
        target = loc.target or "（行き先を読めない）"
    access = "あり" if loc.access else ""
    internal = "内部だけ" if loc.internal else ""
    return f"| `{_cell(where + path)}` | {target} | {access} | {internal} |"


def system_page(root: Path) -> str:
    unit_list = system.units(root)
    ngx = system.nginx(root)
    tz, runs = load(root)
    jobs = sorted({r.job for r in runs})
    port_owner = {p: u for u in unit_list for p in u.ports}
    counts: dict[str, int] = {}
    for loc in ngx.locations:
        if loc.upstream:
            counts[loc.upstream] = counts.get(loc.upstream, 0) + 1
    static = [loc for loc in ngx.locations if loc.target.startswith(system.SERVED_BY_NGINX)]

    ids: dict[str, str] = {}
    graph = ["```mermaid", *ELK_LAYOUT, "flowchart LR"]

    def node(key: str, label: str, cls: str, shape: tuple[str, str] = ("[", "]")) -> str:
        if key not in ids:
            ids[key] = f"n{len(ids)}"
            graph.append(f'  {ids[key]}{shape[0]}"{label}"{shape[1]}:::{cls}')
        return ids[key]

    def link(a: str, b: str, label: str = "", arrow: str = "-->") -> None:
        graph.append(f'  {a} {arrow}|"{label}"| {b}' if label else f"  {a} {arrow} {b}")

    web = node("nginx", f"nginx<br>{system.NGINX}", "proc")
    for name, count in sorted(counts.items()):
        host = ngx.upstreams.get(name, "")
        port = host.rpartition(":")[2]
        owner = port_owner.get(int(port)) if port.isdigit() else None
        target = node(name, f"{owner.name}<br>{host}" if owner else host or name, "unit")
        link(web, target, f"{count} location")
    if static:
        folder = static[0].target.split("`")[1]
        link(web, node("static", f"{folder}<br>{system.SERVED_BY_NGINX}", "file", ("[(", ")]")), f"{len(static)} location")
    cron = node("cron", f"crontab<br>{CRONTAB}", "proc")
    runner = node("runner", "deploy/cron-run.sh", "proc")
    link(cron, runner)
    link(runner, node("jobs", f"jobs/ のジョブ {len(jobs)} 本<br>起動の定義 {len(runs)} 件", "job"))
    for u in unit_list:
        if u.kind == "timer" and u.triggers:
            link(node(u.name, f"{u.name}<br>{u.interval}", "unit"), node(u.triggers, u.triggers, "unit"), "起動")
    for u in unit_list:
        for after in u.after:
            if u.name in ids and any(v.name == after for v in unit_list):
                link(ids[u.name], node(after, after, "unit"), "After", "-.->")
    graph += [
        "  classDef proc fill:#e3ecff,stroke:#3b5bdb",
        "  classDef unit fill:#e6f7ec,stroke:#2b8a3e",
        "  classDef job fill:#fff4e0,stroke:#e8590c",
        "  classDef file fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3",
        "```",
        "",
    ]
    lines = _header("システム構成") + [
        "`deploy/` の systemd のユニット・crontab・nginx の設定のスクリプトから、書いてある範囲だけを拾った。推測で箱や線は足さない。",
        "",
        f"- systemd: `{system.DEPLOY}/*.service`・`*.timer` の `[Unit]` `[Service]` `[Timer]` `[Install]`",
        f"- cron: `{CRONTAB}`（`{tz or 'サーバの時刻'}`）。ジョブごとの時刻と引数は [JOBS.md](../JOBS.md)",
        f"- nginx: `{system.NGINX}` の `proxy_block`（シェルの here-doc）にある `location` と、その中の `proxy_pass`・`alias`・`return`・`auth_request`。正規表現で読むので、書き方を変えると読めなくなり、テストが落ちる",
        "- 図で nginx とユニットをつなぐのは、ユニットが開くポート（`Environment=…_PORT=` と `uvicorn-start.sh` の `--port`）が nginx の上流のポートと同じときだけ",
        "",
        "## 図",
        "",
        "実線は呼ぶ・中継する、点線は systemd の `After`（起動の順）。",
        "",
        *graph,
        "## systemd のユニット",
        "",
        "| ユニット | 説明 | 起動するもの | After | 入る先 | 再起動 | 待ち受けるポート |",
        "|---|---|---|---|---|---|---|",
    ]
    for u in unit_list:
        start = "<br>".join(f"`{_cell(c)}`" for c in u.start) or (f"タイマー: {u.interval}（起こす先 `{u.triggers}`）" if u.interval else "")
        ports = ", ".join(str(p) for p in u.ports)
        lines.append(
            f"| `{u.name}` | {_cell(u.description)} | {start} | {_code(u.after)} | {_code(u.wanted_by)} | {u.restart} | {ports} |"
        )
    lines += [
        "",
        "## nginx の location",
        "",
        f"上流は {_code(f'{k}={v}' for k, v in sorted(ngx.upstreams.items()))}（スクリプトの既定値。環境変数で変わる）。「Cloudflare Access」の列は `auth_request /_cf_access;` が入っているもの。",
        "",
        "| location | 行き先 | Cloudflare Access | 備考 |",
        "|---|---|---|---|",
    ]
    lines += [_location_row(loc, ngx.upstreams) for loc in ngx.locations]
    by_family: dict[str, int] = {}
    for r in runs:
        parts = r.job.split("/")
        key = parts[1] if len(parts) > 2 else "（jobs 直下）"
        by_family[key] = by_family.get(key, 0) + 1
    lines += [
        "",
        "## cron",
        "",
        f"`{CRONTAB}` の起動の定義は {len(runs)} 件、ジョブは {len(jobs)} 本。すべて `deploy/cron-run.sh` 経由。",
        "",
        "| 機能のフォルダ | 起動の定義 |",
        "|---|---|",
    ]
    lines += [f"| `{k}` | {v} |" for k, v in sorted(by_family.items())]
    lines += [
        "",
        "## ここからは拾えない",
        "",
        "- `deploy/` の外（Cloudflare の DNS・Access の設定、R2 のバケット、ConoHa の VPS）",
        "- `deploy/provision.sh`・`update.sh`・`fluent-bit.conf`・`monitoring/`・`logrotate` が作るもの（ログの送り先、監視、証明書など）。この生成器は読まない",
        "- ユニットが実際にどのユーザ・どの環境変数で動くか（`.env` の中身）",
        "- 本番で今動いているかどうか。ここに書いてあるのはリポジトリの定義だけ",
        "- DB（SQLite）とデータの置き場（`data/`）の位置。`deploy/README.md` を読む",
        "",
    ]
    return "\n".join(lines)


def render(root: Path = ROOT) -> dict[str, str]:
    return {MODULES_PAGE: modules_page(root), SYSTEM_PAGE: system_page(root)}


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
