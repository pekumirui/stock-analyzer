---
name: prod-readonly-via-grok
description: 本番 DB の重い読み取りは MCP でなく、使い捨てブランチのスクリプトを grok bot 経由で流す。全件の突き合わせは VPS でなく box
metadata:
  node_type: memory
  type: reference
  originSessionId: 3b09fd8e-c08c-59d3-9bb0-f5c641ec819d
  modified: 2026-11-26T11:00:00.000Z
---

kabukpi の MCP の `db_query` は、Claude 側の中継が 125 秒で接続を切る（nginx のログで 499 が `rt=125.0`。サーバー側の 180 秒より先）。1 回の結果が大きい（約 13 万字〜）と受け取りでも落ちる。全社の集計や、対応付けのロジックを全銘柄で動かす検証は MCP では回らない。

回る形（2026-11-13〜14 に KAB-450 / 451 / 454 / 456 で 10 回ほど使った）:

1. 単体のスクリプトを、マージしない使い捨てブランチ（`chore/KAB-xxx-check` など）に置いて push する。`/tmp` に 1 ファイルだけ取り出して動く形にする（`Path(__file__).resolve().parents[2]` は `/tmp` 直下で落ちる）。DB は `sqlite:///file:PATH?mode=ro&uri=true` で開く
2. grok bot に、`git fetch` → `git show origin/<branch>:<path> > /tmp/…` → リポジトリ直下で `PYTHONPATH=. .venv/bin/python /tmp/…` の手順を送る（grok-bot スキル）。VPS の作業ツリーは main のまま変えさせない
3. grok bot はユーザーの OK を取ってから流し、出力をこの Azure VM の `/tmp` に置く。メッセージには集計の数行だけ返させる
4. 終わったら使い捨てブランチを消す

新旧のロジックを比べるときは、`three_statements.py` の 2 つの版を `git show` でファイルに出し、別名のモジュールとして読み込んで同じ DB で動かす（`matches_diff.py` / `loo_check.py` / `split_diff.py`。消したブランチのコミット `fcc0cea` に最後の版が残っている）。全 3,661 銘柄で 30〜40 分。

**How to apply:** 本番の全社を見る検証は最初からこの形で頼む。MCP は 1 銘柄の確認と軽い集計にだけ使う。

**VPS のメモリに注意（2026-11-24、KAB-548）:** 短信 PDF 約 9 万件の fact を新旧で書き出した TSV（各 1.4GB・1,765 万行）を、全件を dict に載せて比べる `diff_facts.py` を VPS で流してもらい、3.4GB で OOM killer に止められてサイトが一時止まった。全件を読む比較は VPS で流さない。書き出し（1 件ずつ読んで書くだけ）は VPS、突き合わせは box で `sort` してから 1 行ずつ（grok bot の `diff_facts_stream.py` の方式）。頼む前に、スクリプトがメモリに何を載せるかを自分で見積もる。
