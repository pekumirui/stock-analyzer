---
name: no-sizes-in-docs
description: ドキュメントにファイルや DB の容量（○GB・○MB）を書かない
metadata:
  node_type: memory
  type: feedback
  originSessionId: 6608c3b4-add1-5c01-8dc0-ee78e2c30d2b
  modified: 2026-10-30T05:41:57.284Z
---

ドキュメントにファイル・DB・ディレクトリの容量を書かない（KAB-280 で backup 文書に `data/xbrl` 161MB や DB 9.8GB を書いたら「そもそも要らない」と言われた）。

**Why:** すぐ古くなり、実態と食い違う（`DESIGN_backup.md` の「約 1GB 級」が実際は gzip 6.1GB だった）。

**How to apply:** 容量は issue やチャットの判断材料にとどめ、md には書かない。必要なら本番で測る（grok bot に `du` / `df`）。
