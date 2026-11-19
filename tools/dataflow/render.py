"""読み書きの観測結果を docs/DATA_FLOW.md（概要）・docs/dataflow/<機能>.md・docs/dataflow/viewer.html にする。

正は観測結果だけ。3 つとも同じ入力から作り、手では直さない。
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

TEMPLATE = Path(__file__).with_name("viewer.html")
OVERVIEW = "docs/DATA_FLOW.md"
PAGE_DIR = "docs/dataflow"
VIEWER = f"{PAGE_DIR}/viewer.html"
ENTRIES = f"{PAGE_DIR}/entries.json"

# (キー, 見出し)。並びはページと HTML の並び
DOMAINS: list[tuple[str, str]] = [
    ("market", "株価・決算"),
    ("monthly", "月次"),
    ("xbrl", "XBRL・決算書類"),
    ("edinet", "EDINET・大量保有"),
    ("supply", "サプライ"),
    ("common", "共通・運用"),
]
_ORDER = {k: i for i, (k, _) in enumerate(DOMAINS)}
_LABEL = dict(DOMAINS)
# テーブル名の頭 → 機能。どれにも当たらなければ market
TABLE_PREFIX: list[tuple[str, str]] = [
    ("monthly_", "monthly"),
    ("xbrl_", "xbrl"),
    ("tdnet_", "xbrl"),
    ("edinet_", "edinet"),
    ("large_holding", "edinet"),
    ("material_", "supply"),
]
# ほぼ全部の入口が読む表。API の機能を決めるときと、図の読む線からは外す（表には出す）
HUB = frozenset({"stocks"})

HEADER = """\
# データの流れ（バッチ・API × テーブル）

**このファイルと `docs/dataflow/` は自動生成。手で直さない。** 作り直しは `python -m pytest tests --dataflow=write`。CI は `--dataflow=check` で実装と合うかを見る（作り方は `tools/dataflow/`）。

テスト中に SQLite へ実際に流れた SQL を、呼んだバッチ・API に紐づけて集めたもの。

- 「書く」は INSERT / UPDATE / DELETE をした表（読んでもいる表を含む）。「読むだけ」は SELECT だけの表
- テストが通った道だけが載る。テストの無い分岐の読み書きは載らないので、表は**下限**として読む
- バッチは `jobs/` のうち `__main__` を持つファイル（銘柄ごとの `html_issuers` は除く）。`__main__` から関数の参照をたどって届く `app/` の読み書きも含む。別のプロセスで続けて走るもの（crontab で並べたジョブ）は別の行
- バッチが HTTP で API を呼ぶとき（月次の取込は loopback の API に POST する）は「呼ぶ API」に出し、その API の読み書きも足す
- API はルートごと
- 数えないもの: DDL（`CREATE` / `ALTER` など）、`init_db` のスキーマ直し、テストが下ごしらえで `upsert` などの汎用の関数を直に呼んだ分
- SQLAlchemy を通さず `sqlite3` で開く接続（`backup_db` など）も数える
"""


@dataclass
class Entry:
    name: str
    kind: str  # "job" | "api"
    domain: str
    rw: dict[str, str]  # テーブル → "W" | "R"
    calls: list[str]

    def writes(self) -> list[str]:
        return sorted(t for t, v in self.rw.items() if v == "W")

    def reads(self) -> list[str]:
        return sorted(t for t, v in self.rw.items() if v == "R")


def table_domain(table: str) -> str:
    for prefix, domain in TABLE_PREFIX:
        if table.startswith(prefix):
            return domain
    return "market"


def _pick(tables: list[str]) -> str | None:
    count: dict[str, int] = defaultdict(int)
    for t in tables:
        count[table_domain(t)] += 1
    if not count:
        return None
    return min(count, key=lambda d: (-count[d], _ORDER[d]))


def entry_domain(name: str, kind: str, rw: dict[str, str]) -> str:
    """バッチは `jobs/<機能>/` のフォルダで、それ以外のバッチは共通。API は書く表、無ければ読む表（`stocks` 以外）の多い機能。"""
    if kind == "job":
        parts = name.split("/")
        return parts[1] if len(parts) > 2 and parts[1] in _ORDER else "common"
    writes = [t for t, v in rw.items() if v == "W"]
    reads = [t for t, v in rw.items() if v == "R" and t not in HUB]
    return _pick(writes) or _pick(reads) or "market"


def _entries(jobs, job_apis, apis) -> list[Entry]:
    out = []
    for kind, src in (("job", jobs), ("api", apis)):
        for name in sorted(src):
            rw = {t: "W" if "W" in s else "R" for t, s in sorted(src[name].items())}
            out.append(Entry(name, kind, entry_domain(name, kind, rw), rw, list(job_apis.get(name, []))))
    return out


def _code(names) -> str:
    return ", ".join(f"`{n}`" for n in names)


def _mermaid(entries: list[Entry], domain: str, kind: str) -> list[str]:
    """機能の入口（バッチか API）とテーブルの図。左に入口、右に表の 2 列。書くは実線の矢印、読むだけは点線。"""
    def edges(e: Entry) -> list[str]:
        return [t for t in e.rw if e.rw[t] == "W" or t not in HUB]

    picked = [e for e in entries if e.domain == domain and e.kind == kind and edges(e)]
    if not picked:
        return []
    tables = sorted({t for e in picked for t in edges(e)})
    ids: dict[str, str] = {}

    def nid(key: str) -> str:
        return ids.setdefault(key, f"n{len(ids)}")

    lines = ["```mermaid", "flowchart LR"]
    for e in picked:
        lines.append(f'  {nid("e:" + e.name)}["{e.name.removeprefix("jobs/")}"]:::{kind}')
    for t in tables:
        cls = "tbl" if table_domain(t) == domain else "ext"
        lines.append(f'  {nid("t:" + t)}[("{t}")]:::{cls}')
    for e in picked:
        for t in edges(e):
            arrow = "-->" if e.rw[t] == "W" else "-.-"
            lines.append(f"  {nid('e:' + e.name)} {arrow} {nid('t:' + t)}")
    lines += [
        "  classDef job fill:#e3ecff,stroke:#3b5bdb",
        "  classDef api fill:#e6f7ec,stroke:#2b8a3e",
        "  classDef tbl fill:#fff4e0,stroke:#e8590c",
        "  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3",
        "```",
        "",
    ]
    return lines


def _entry_table(label: str, entries: list[Entry], with_calls: bool) -> list[str]:
    head = f"| {label} | 書く | 読むだけ |"
    rule = "|---|---|---|"
    if with_calls:
        head += " 呼ぶ API |"
        rule += "---|"
    lines = [head, rule]
    for e in entries:
        row = f"| `{e.name}` | {_code(e.writes())} | {_code(e.reads())} |"
        if with_calls:
            row += " " + "<br>".join(f"`{c}`" for c in e.calls) + " |"
        lines.append(row)
    return lines + [""]


def _domain_page(domain: str, entries: list[Entry], tables: list[str]) -> str:
    label = _LABEL[domain]
    jobs = [e for e in entries if e.domain == domain and e.kind == "job"]
    apis = [e for e in entries if e.domain == domain and e.kind == "api"]
    own = [t for t in tables if table_domain(t) == domain]
    lines = [
        f"# データの流れ: {label}",
        "",
        "**自動生成。手で直さない。** 読み方と作り方は [DATA_FLOW.md](../DATA_FLOW.md)。全体の表と影響の調べ方は [ビューア](viewer.html)。",
        "",
        (
            "図の見方: 左が入口、右が表。実線の矢印は書く、点線は読むだけ。灰色の点線の枠はほかの機能の表。"
            f"`{'`・`'.join(sorted(HUB))}` はほぼ全部が読むので、読む線は省く（表には出す。線が残らない入口は図から外す）。"
        ),
        "",
    ]
    if jobs:
        lines += ["## バッチ", ""]
        lines += _mermaid(entries, domain, "job")
        lines += _entry_table("バッチ", jobs, any(e.calls for e in jobs))
    if apis:
        lines += ["## API", ""]
        lines += _mermaid(entries, domain, "api")
        lines += _entry_table("API", apis, False)
    if own:
        lines += [
            "## テーブル",
            "",
            "ほかの機能のバッチ・API も含めて、その表を書く・読む入口。",
            "",
        ]
        for t in own:
            w = [e for e in entries if e.rw.get(t) == "W"]
            r = [e for e in entries if e.rw.get(t) == "R"]
            lines += [f"### {t}", ""]
            lines.append("- 書く: " + (_code(_tagged(w, domain)) if w else "（観測なし）"))
            lines.append("- 読むだけ: " + (_code(_tagged(r, domain)) if r else "（観測なし）"))
            lines.append("")
    return "\n".join(lines)


def _tagged(entries: list[Entry], domain: str) -> list[str]:
    return [e.name if e.domain == domain else f"{e.name}（{_LABEL[e.domain]}）" for e in entries]


def _overview(entries: list[Entry], tables: list[str], model_tables: list[str],
              all_jobs: list[str], all_routes: list[str], unknown: list[str]) -> str:
    lines = [HEADER]
    lines += [
        "## 見るところ",
        "",
        "- 機能ごとのページ（図と表）: 下の表から。入口（バッチ・API）は `jobs/<機能>/` のフォルダと、書く表の多い機能で分ける",
        "- 全体の表・テーブルを変えたときの影響: [ビューア](dataflow/viewer.html)（wiki で開く。行か列を選ぶと、つながる入口とテーブルだけを図にする）",
        "- エージェントは機能ごとのページか、この下の表を読む。HTML は人が見る用",
        "",
        "| 機能 | バッチ | API | テーブル |",
        "|---|---|---|---|",
    ]
    for key, label in DOMAINS:
        n_job = sum(e.domain == key and e.kind == "job" for e in entries)
        n_api = sum(e.domain == key and e.kind == "api" for e in entries)
        own = [t for t in tables if table_domain(t) == key]
        if not (n_job or n_api or own):
            continue
        lines.append(f"| [{label}](dataflow/{key}.md) | {n_job} | {n_api} | {_code(own)} |")
    lines.append("")

    writers = {t for e in entries for t, v in e.rw.items() if v == "W"}
    shown = {t for e in entries if e.kind == "api" for t in e.rw}
    lines += [
        "## 設計の点検",
        "",
        "表は下限なので、ここに出たものがすぐに誤りとは限らない（テストの無い道で書く・読むかもしれない）。",
        "",
        "書く入口が観測されなかった表（読まれるだけ）:",
        "",
    ]
    lines += [f"- `{t}`" for t in tables if t not in writers] or ["- （無し）"]
    lines += ["", "画面の API から読まれない表（バッチの中だけで使う表）:", ""]
    lines += [f"- `{t}`" for t in tables if t not in shown] or ["- （無し）"]
    lines += ["", "`app/models.py` にあるのに、どの入口の読み書きにも出なかった表:", ""]
    lines += [f"- `{t}`" for t in model_tables if t not in tables] or ["- （無し）"]
    lines.append("")

    lines += [
        "## SQL を観測しなかった入口",
        "",
        "テストが通っていないか、DB を使わない（ファイルの取得・バックアップなど）。どちらかはこの表からは分からない。",
        "",
    ]
    names = {e.name for e in entries}
    lines += [f"- `{j}`" for j in all_jobs if j not in names] or ["- （バッチは無し）"]
    lines.append("")
    lines += [f"- `{r}`" for r in all_routes if r not in names] or ["- （API は無し）"]
    lines.append("")

    if unknown:
        lines += ["## モデルに無いテーブル", "", "`app/models.py` に無いのに SQL に出た名前。", ""]
        lines += [f"- `{t}`" for t in unknown]
        lines.append("")
    return "\n".join(lines)


def _viewer(entries: list[Entry], tables: list[str]) -> str:
    """テンプレートの `/*DATA*/` に観測結果の JSON を埋める。差分を読めるよう 1 入口 1 行。"""
    def dump(obj) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    rows = [
        dump({"name": e.name, "kind": e.kind, "domain": e.domain, "rw": e.rw, "calls": e.calls})
        for e in entries
    ]
    data = (
        "{\n"
        f'"domains":{dump([{"key": k, "label": v} for k, v in DOMAINS])},\n'
        f'"tables":{dump({t: table_domain(t) for t in tables})},\n'
        '"entries":[\n' + ",\n".join(rows) + "\n]}"
    )
    template = TEMPLATE.read_text(encoding="utf-8")
    if template.count("/*DATA*/") != 1:
        raise RuntimeError(f"dataflow: {TEMPLATE.name} に /*DATA*/ がちょうど 1 つ要る")
    return template.replace("/*DATA*/", data)


def _entries_json(entries: list[Entry], all_jobs: list[str]) -> str:
    doc = {
        "jobs": sorted(all_jobs),
        "entries": [{"name": e.name, "kind": e.kind, "domain": e.domain, "rw": e.rw, "calls": e.calls} for e in entries],
    }
    return json.dumps(doc, ensure_ascii=False, indent=1) + "\n"


def load_entries(root: Path) -> tuple[list[str], list[Entry]]:
    doc = json.loads((root / ENTRIES).read_text(encoding="utf-8"))
    return doc["jobs"], [Entry(**e) for e in doc["entries"]]


def render(
    jobs: dict[str, dict[str, set[str]]],
    job_apis: dict[str, list[str]],
    apis: dict[str, dict[str, set[str]]],
    all_jobs: list[str],
    all_routes: list[str],
    unknown: list[str],
    model_tables: list[str] | None = None,
) -> dict[str, str]:
    """リポジトリからの相対パス → 中身。"""
    entries = _entries(jobs, job_apis, apis)
    tables = sorted({t for e in entries for t in e.rw}, key=lambda t: (_ORDER[table_domain(t)], t))
    out = {OVERVIEW: _overview(entries, tables, sorted(model_tables or []), all_jobs, all_routes, unknown)}
    for key, _ in DOMAINS:
        if any(e.domain == key for e in entries) or any(table_domain(t) == key for t in tables):
            out[f"{PAGE_DIR}/{key}.md"] = _domain_page(key, entries, tables)
    out[VIEWER] = _viewer(entries, tables)
    out[ENTRIES] = _entries_json(entries, all_jobs)
    return out
