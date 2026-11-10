---
name: agent-bench-onedrive
description: エージェント比較（MCP あり・なし）の結果は onedrive:agent-bench/<コード>-<会社名>/ に置く。deck-reader には入れない
metadata:
  node_type: memory
  type: feedback
  originSessionId: a7eb63a2-144d-525c-bbb9-3a86d7635db1
  modified: 2026-11-10T05:43:20.347Z
---

`tools/agent_bench.py` の結果（レポートとログ）は `onedrive:agent-bench/<証券コード>-<会社名>/` に、`<日付>_report.md` などの名前で置く。`deck-reader/` には混ぜない。

**Why:** ユーザーが deck-reader とは別の場所で、銘柄ごとに見たいと言った（2026-11-10、KAB-411）。最初に `deck-reader/kab-411/` に置いたら見つけられなかった。

**How to apply:** `agent_bench.py run --code <コード> --company <会社名> --prompt-file <プロンプト>` の既定の保存先がそこになっている。手元は上げたあとに消える。PDF の検証レポートは従来どおり [[reports-via-onedrive]]。
