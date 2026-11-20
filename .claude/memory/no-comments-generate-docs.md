---
name: no-comments-generate-docs
description: コメントと docstring は禁止。文書と図はコードから生成し、AI が書く日本語の説明を減らす
metadata:
  node_type: memory
  type: feedback
  originSessionId: 2829e06a-9c9e-52a8-ba51-c1030c9e5659
  modified: 2026-11-18T10:57:38.606Z
---

ソースコードにコメントも docstring も足さない。経緯（issue 番号・障害の話）も書かない。知らないと壊す制約は、テスト・名前・型で表す。文書はコードから生成し、図（Mermaid の ER 図など）で表す方向に寄せる。

**Why:** コメントは古くなっても信用されてしまい、もっと良い方法が見つかったときの邪魔になる（2026-11-18）。実際に `app/models.py` の値の列挙コメントは 34 列中 3 列が本番と食い違っていた。ユーザーは「AI が書く日本語をなるべく消したい」「どこが変わるのか図を見ればわかる状態にしたい」。

**How to apply:** 説明を書きたくなったら、まず Enum・型・テスト・生成物で表せないかを考える。`python -m tools.lint.no_comments` が増加を CI で落とす（台帳は `tools/lint/no_comments_baseline.json`）。スキーマは `python -m tools.schema.render` で `docs/schema/` を作り直す。md に移すのは最後の手段。関連: [[db-constraints-app-side]]
