"""読み書きの観測結果を docs/DATA_FLOW.md の Markdown にする。"""
from __future__ import annotations

from collections import defaultdict

HEADER = """\
# データの流れ（バッチ・API × テーブル）

**このファイルは自動生成。手で直さない。** 作り直しは `python -m pytest tests --dataflow=write`。CI は `--dataflow=check` で実装と合うかを見る（作り方は `tools/dataflow/plugin.py`）。

テスト中に SQLite へ実際に流れた SQL を、呼んだバッチ・API に紐づけて集めたもの。

- 「書く」は INSERT / UPDATE / DELETE をした表（読んでもいる表を含む）。「読むだけ」は SELECT だけの表
- テストが通った道だけが載る。テストの無い分岐の読み書きは載らないので、表は**下限**として読む
- バッチは `jobs/` のうち `__main__` を持つファイル（銘柄ごとの `html_issuers` は除く）。`__main__` から関数の参照をたどって届く `app/` の読み書きも含む。別のプロセスで続けて走るもの（crontab で並べたジョブ）は別の行
- バッチが HTTP で API を呼ぶとき（月次の取込は loopback の API に POST する）は「呼ぶ API」に出し、その API の読み書きも足す
- API はルートごと
- 数えないもの: DDL（`CREATE` / `ALTER` など）、`init_db` のスキーマ直し、テストが下ごしらえで `upsert` などの汎用の関数を直に呼んだ分
- SQLAlchemy を通さず `sqlite3` で開く接続（`backup_db` など）も数える
"""


def _names(seen: dict[str, set[str]], pick) -> str:
    return ", ".join(f"`{t}`" for t in sorted(t for t, rw in seen.items() if pick(rw)))


def _entry_table(title: str, label: str, entries: dict[str, dict[str, set[str]]],
                 calls: dict[str, list[str]] | None = None) -> list[str]:
    head = f"| {label} | 書く | 読むだけ |"
    rule = "|---|---|---|"
    if calls is not None:
        head += " 呼ぶ API |"
        rule += "---|"
    lines = [f"## {title}", "", head, rule]
    for key in sorted(entries):
        seen = entries[key]
        row = f"| `{key}` | {_names(seen, lambda rw: 'W' in rw)} | {_names(seen, lambda rw: rw == {'R'})} |"
        if calls is not None:
            row += " " + "<br>".join(f"`{c}`" for c in calls.get(key, [])) + " |"
        lines.append(row)
    return lines + [""]


def render(
    jobs: dict[str, dict[str, set[str]]],
    job_apis: dict[str, list[str]],
    apis: dict[str, dict[str, set[str]]],
    all_jobs: list[str],
    all_routes: list[str],
    unknown: list[str],
) -> str:
    lines = [HEADER]
    lines += _entry_table("バッチ", "バッチ", jobs, job_apis)
    lines += _entry_table("API", "API", apis)

    writers: dict[str, list[str]] = defaultdict(list)
    readers: dict[str, list[str]] = defaultdict(list)
    for entries in (jobs, apis):
        for key, seen in entries.items():
            for t, rw in seen.items():
                if "W" in rw:
                    writers[t].append(key)
                if "R" in rw:
                    readers[t].append(key)
    lines += ["## テーブルから引く", "", "| テーブル | 書く | 読む |", "|---|---|---|"]
    for t in sorted(set(writers) | set(readers)):
        w = "<br>".join(f"`{k}`" for k in sorted(writers.get(t, [])))
        r = "<br>".join(f"`{k}`" for k in sorted(readers.get(t, [])))
        lines.append(f"| `{t}` | {w} | {r} |")
    lines.append("")

    lines += [
        "## SQL を観測しなかった入口",
        "",
        "テストが通っていないか、DB を使わない（ファイルの取得・バックアップなど）。どちらかはこの表からは分からない。",
        "",
    ]
    missing_jobs = [j for j in all_jobs if j not in jobs]
    missing_routes = [r for r in all_routes if r not in apis]
    lines += [f"- `{j}`" for j in missing_jobs] or ["- （バッチは無し）"]
    lines.append("")
    lines += [f"- `{r}`" for r in missing_routes] or ["- （API は無し）"]
    lines.append("")

    if unknown:
        lines += ["## モデルに無いテーブル", "", "`app/models.py` に無いのに SQL に出た名前。", ""]
        lines += [f"- `{t}`" for t in unknown]
        lines.append("")
    return "\n".join(lines)
