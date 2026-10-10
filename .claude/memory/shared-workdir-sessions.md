---
name: shared-workdir-sessions
description: /workspace/stock-analyzer は remote-control が same-dir で複数セッションを同居させている。ブランチ切替が他セッションと衝突する
metadata:
  node_type: memory
  type: project
  originSessionId: 561c387f-08b5-51b3-85d9-11dc42b7c8e8
  modified: 2026-10-02T01:35:54.901Z
---

2026-10-02 時点、`claude remote-control --spawn=same-dir`（tmux `claude-rc`）から複数の Claude セッションが同じ `/workspace/stock-analyzer` で動いている。作業中に別セッションが `main` へ checkout + reset して HEAD がずれた（コミットは push 済みで無事だった）。

**Why:** 作業ツリーを共有していると、checkout / reset が互いの未コミット変更や HEAD を壊す。ユーザーは `--spawn worktree` への切替を望んでいる。

**How to apply:** 切替が済むまでは、編集前に `git worktree add` で自分専用の作業場所を作るか、push 前に `git branch --show-current` と `git log -1` で HEAD を確かめる。共有ツリーで勝手に checkout しない。remote-control 本体（tmux の親プロセス）は再起動しない。`claude remote-control --help` は終了しないことがあるので、background で起動して止める。

worktree に `.venv` をシンボリックリンクで置いたまま `git add -A` したら、リンクがコミットされて本番の venv を壊した（2026-10-02、#378 → #379 で修正。`.gitignore` は `.venv` に直した）。worktree ではコミット前に `git diff --cached --stat` で余計なファイルが無いか見る。
