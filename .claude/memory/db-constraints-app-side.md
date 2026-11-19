---
name: db-constraints-app-side
description: 値の列挙は SQLAlchemy の Enum（アプリ側）で縛る。本番のテーブル作り直し・FK の有効化はしない
metadata:
  node_type: memory
  type: project
  originSessionId: 2829e06a-9c9e-52a8-ba51-c1030c9e5659
  modified: 2026-11-18T10:57:43.526Z
---

値の列挙は `app/models.py` の `_one_of(...)`（`Enum`、`validate_strings=True`）で縛る。FK を付けていない参照は列の `info={"ref": ...}` に宣言するだけ。DB の CHECK・FK は、新しく作るテーブルか、別の理由で作り直す機会にだけ付ける。

**Why:** 2026-11-18 に検討。ユーザーは決定的な動作のため作り直しを望んだが、約 24 テーブル（全体 16GB）を止めて作り直す費用に対し、守れるのは SQLAlchemy を通さない書き込みだけなので、アプリ側で始めると合意した。`PRAGMA foreign_keys` は無効で、`stocks` への参照は本番に違反が多い（`statements` 9,181 行、`xbrl_filings` 9,009 行）。

**How to apply:** 外部が決める値（J-Quants・EDINET のコード、Claude の読み取り結果）は型にしない。列挙を足す前に本番で実際の値を数える（列挙外の値が 1 行でもあると ORM の読み出しが落ちる）。関連: [[no-comments-generate-docs]]
