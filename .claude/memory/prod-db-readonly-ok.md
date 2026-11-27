---
name: prod-db-readonly-ok
description: 本番 DB は読み取りだけならアプリ（手元の uvicorn など）から直接つないでよい。使うのはユーザー 1 人
metadata:
  type: feedback
---

本番の DB に、読み取りだけならアプリから直接つないでよいとユーザーが言っている（2026-11-27、KAB-597）。使っているのはユーザー本人だけ。

**Why:** KAB-597 の画面確認で、手元の DB に XBRL が無く、`/home/azureuser/vps-data/data/stock.db` も 0 バイトだった。MCP で取った値を応答に差し込んで撮るしかなく、手間がかかった。
**How to apply:** 画面や API を実データで確かめたいときは、本番 DB を読み取り専用（`?mode=ro`）で開いて手元のアプリを立ててよい。書き込み・削除・作り直しはしない。重い全件の読み取りは [[prod-readonly-via-grok]] のとおり。つなぎ方はまだ決まっていない（[[azure-vm-screenshots]]）。
