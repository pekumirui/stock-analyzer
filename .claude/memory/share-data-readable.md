---
name: share-data-readable
description: 共有ファイル（月次 PDF・XBRL など）は VPS の data/。MCP fs_*（root=data）か box の /mnt/vps/data から読む
metadata:
  node_type: memory
  type: reference
  originSessionId: 60468697-12ed-5f08-9b9b-37804b45b5fd
  modified: 2026-10-19T22:40:00.000Z
---

共有データは VPS の `data/` にある（KAB-66 以降。R2 は使わない。2026-10-20 KPI）。

読む経路:

- MCP `fs_list` / `fs_read` / `fs_grep`（`root=data`）。`path` は `monthly/6036/` のように `data/` からの相対
- box の読み取り専用 sshfs: `/mnt/vps/data`。例: `pdftotext -layout /mnt/vps/data/monthly/<code>/<file>.pdf -`

中身は `monthly/{code}/`（TDnet 発行体コード、例 `monthly/6036/`）、`xbrl/`、`inventory/`、`stats/`。issue に「資料: monthly/…pdf」とあればまずここを見る。TDnet の一覧は約 1 か月で消えるので、古い資料は `data/` しか無い。読むだけで書き込まない。

FUSE `/home/stock/r2-share` と rclone `r2:share` は読まない。関連: [[verify-live-db]]
