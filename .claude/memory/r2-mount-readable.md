---
name: r2-mount-readable
description: R2 の月次 PDF はクラウドの作業環境で /mnt/r2 から直接読める（R2_* の鍵が env に無くても）
metadata:
  node_type: memory
  type: reference
  originSessionId: 60468697-12ed-5f08-9b9b-37804b45b5fd
  modified: 2026-10-05T23:40:14.762Z
---

R2 bucket `share` が `/mnt/r2` にマウントされていて、読み取りできる（2026-10-06 確認）。中身は `monthly/{code}/`（TDnet 発行体コード、例 `monthly/6036/`）、`xbrl/`、`xbrl-verify-sample*/`。issue に「資料: R2 monthly/…pdf」とあれば `pdftotext -layout /mnt/r2/monthly/<code>/<file>.pdf -` で直接読める。

`env` に `R2_ACCESS_KEY_ID` などが無くても見えたので、鍵が無いからと TDnet に取りに行く前にまずここを見る。TDnet の一覧は約 1 か月で消えるので、古い資料は R2 しか無い。読むだけで書き込まない（AGENTS.md の R2 は読み取りのみ）。関連: [[verify-live-db]]
