---
name: vm-secrets-env
description: Azure VM では ~/.secrets.env に GH_TOKEN / EDINET_API_KEY / JQUANTS_API_KEY がある。PR 作成・マージは GH_TOKEN で API を叩く
metadata:
  node_type: memory
  type: reference
  originSessionId: fe4e9844-53f0-5277-837d-a4d9f5bda651
  modified: 2026-10-20T08:06:11.091Z
---

Azure VM（/home/azureuser/work）には `gh` が無く、git は SSH 鍵で push だけできる。PR の作成・マージは `~/.secrets.env` の `GH_TOKEN`（fine-grained、pekumirui/stock-analyzer、Contents と Pull requests の Read and write）で GitHub API を直接叩く。

- 読み込みは `set -a; . ~/.secrets.env; set +a`。値は表示しない（変数名だけ確かめる）
- 同じファイルに `EDINET_API_KEY` / `JQUANTS_API_KEY` もある
- 2026-10-20、最初は Contents 権限が無く `403 Resource not accessible by personal access token` / PR 作成が `not all refs are readable` になった。権限不足を疑う

関連: [[shared-workdir-sessions]]
