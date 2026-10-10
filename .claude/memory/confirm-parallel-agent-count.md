---
name: confirm-parallel-agent-count
description: 並列でエージェントを起動する前に、体数と範囲を提示して確認をとる
metadata:
  node_type: memory
  type: feedback
  originSessionId: f6fe147c-be6b-5af0-abb6-884be6de5925
  modified: 2026-09-28T11:35:32.608Z
---

「issue 切って並列で頼む」と言われても、エージェントの起動前に「何体・どの範囲か」を示して確認をとる。まず issue だけ切って止めるのが既定。

**Why:** 2026-09-28、テスト改善で issue を 9 本切り、確認なしでエージェントを 9 体同時に起動した。ユーザーは「とりあえず issue だけ」のつもりで、トークンを大量に使うところだった（「8 個って思ったより多かった」）。

**How to apply:** issue 作成までは進めてよい。エージェントの起動は、体数を示して了承を得てからにする。数が多いときは、まとめる案や少数ずつ回す案も出す。
