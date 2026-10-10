---
name: verify-live-db
description: "/workspace/verify-live/stock.db is the user's verification copy of prod; free to read and write"
metadata:
  node_type: memory
  type: project
  originSessionId: 2a03324d-e2ce-5af9-b12c-0389645d395b
  modified: 2026-09-27T08:52:58.793Z
---

`/workspace/verify-live/stock.db` (the repo's `stock.db` symlinks to it) is a copy of the production DB that the user built on purpose for verification. The user said it is fine to use it freely, writes included.

**Why:** It was confirmed on 2026-09-27 to match prod: counts, date ranges, and monthly values were identical. The only differences were statements +6 and earnings_events +3 on the local side. The XBRL tables are empty.

**How to apply:** Use it directly for real-data checks, such as ingest end-to-end runs or UI checks on port 8000, without first copying it to /tmp. It is still a local DB, so never commit it. XBRL verification still belongs in `stock_verify.db`.
