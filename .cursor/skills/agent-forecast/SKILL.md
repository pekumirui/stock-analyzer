---
name: agent-forecast
description: >
  stock-analyzer の決算予想エージェント実験を実施する。銘柄×決算期の設定、IR Bank の短信 XBRL、
  月次 PDF / 月次 API スナップショット、説明資料、条件 A〜F の隔離ラン、採点までを回す。
  Use when the user invokes /agent-forecast, or asks to run analyzer/agent_forecast,
  条件 A〜F, claude -p, Cursor Grok 4.6 の決算予想、月次 PDF vs 月次 API の精度とトークン比較。
disable-model-invocation: true
---

# agent-forecast

実験の正本は `analyzer/agent_forecast/README.md`。条件表・隔離の意味・コミット対象はそちら。このファイルは Cursor エージェントが同じ手順を実施する手順書。

## 最初の行動

1. `analyzer/agent_forecast/README.md` を読む（正本。ここに無い約束は作らない）。
2. ユーザー入力から対象とベンダーを決める。足りないものだけ聞く。
3. 下のチェックリストをコピーして進める。

ユーザーが銘柄・決算期・ベンダーを付けていればそのまま使う（例: `/agent-forecast 6036 FY2026Q4 grok`）。設定ファイルだけならフェーズ 2 から。材料済みならフェーズ 3 から。

| 決めること | 既定 |
|---|---|
| 銘柄 × 決算期 | 必須。`targets/<code4>_FY<YYYY>Q<n>.json` |
| ベンダー | 必須。`claude`（`claude -p`）または `grok`（Cursor Task `cursor-grok-4.6-high`） |
| 条件 | 未指定なら A を 1 回試してから B〜F。月次が無い銘柄は C・F を作らない |
| `--tag` | 省略。同じ条件の再実行だけ `_2` |

## 厳守

- 予想エージェントに渡すのは締切（発表前日）までに公表された情報だけ。対象期の短信・実績 zip・`answer/` を `cond_<X>/` に入れない
- 予想を回している間、親は `$R/answer/` と実績数字を Task プロンプトに載せない。採点のときだけ読む
- 作業フォルダはリポジトリの外（既定 `/workspace/forecast-exp/<id>/`）。PDF・zip・jsonl 全文はコミットしない
- スクリプトは標準ライブラリだけ。`cd analyzer/agent_forecast` して `python3 <script>.py --target $T`
- 全許可モードは使わない。予想子に Bash / Web / 書き込みを渡さない
- `tests/monthly/test_monthly_ingest.py` に銘柄分岐を足さない。この実験は `app/` を触らない
- チャットやログに API キーを出さない

## チェックリスト

```
- [ ] T=targets/<id>.json と R=/workspace/forecast-exp/<id>
- [ ] 設定 JSON（締切までの tanshin / monthly / briefing。英語版なし、訂正は入れる）
- [ ] $R/raw/xbrl/<stem>.json（MCP get_disclosure の応答をそのまま）
- [ ] $R/answer/ に発表日の短信 zip（条件フォルダからは読めない）
- [ ] fetch_docs → build_financials（組替注意が出たら cost_reclass）
- [ ] 月次がある銘柄だけ build_monthly_snapshot
- [ ] build_conditions（cond_A〜F と mcp/）
- [ ] ベンダーごとに隔離して回す（まず A）
- [ ] score.py → slim_logs.py → 結論を md に書く
- [ ] results の要約・軽量ログ・financials.json だけコミット（PDF 禁止）
```

## フェーズ 1 設定

`targets/6036_FY2026Q4.json` を写して `targets/<code4>_FY<YYYY>Q<n>.json` を書く。キーの意味は README の表。

IR Bank MCP（名前空間 `IRBANK Connect`。呼ぶ前に `GetDynamicTools`）:

1. `get_organization`（または `screen_securities` の `name`）で `edinet_code` と 5 桁 `code`
2. `list_disclosures`（`edinet_code`、`from`/`to` は締切まで）。`document_type` は付けない（TDnet は種別 NULL で落ちる）
3. 締切以下の短信・月次速報・説明資料・中計を拾う。英語版は入れない。訂正は入れる
4. `tanshin` は対象期の前の四半期まで、3 年分くらい。`stem` は `FY<決算期末>_Q<1|2|3|FY>`（例 `FY2026-06_Q3`）
5. `cutoff` = 発表の前日。`announce` = 発表日。各資料の `date` ≤ `cutoff`（`load_target` が assert する）
6. 月次が本番 API にあるときだけ `monthly_api_range`（終わりは締切までに公表された月）。無ければキーごと消す
7. `profile` はプロンプト用（連結/非連結、決算月）。`readme` は条件フォルダの説明文

## フェーズ 2 材料

```bash
T=targets/<id>.json; R=/workspace/forecast-exp/<id>; cd analyzer/agent_forecast
```

**短信 XBRL（スクリプトにできない）:** 各 `tanshin` について MCP `get_disclosure(doc_id, include=["xbrl_facts"])`。応答を加工せず `$R/raw/xbrl/<stem>.json` に書く。

**実績 zip:** 発表日の短信。`data/xbrl/<code4>/`（MCP `fs_*` の `root=data`、または box の `/mnt/vps/data/xbrl/<code4>/`）。取得先は `/tmp`、最終的に `$R/answer/`。無ければ人手の zip を待つ。

残り:

```bash
python3 fetch_docs.py --target $T
python3 build_financials.py --target $T
# 月次がある銘柄だけ
python3 build_monthly_snapshot.py --target $T
python3 build_conditions.py --target $T
```

`build_financials` が組替を警告したら `cost_reclass.from` に境目の短信日と `note` を書いて再実行。プロンプトは `common.render_prompt`（`prompt.md`）。自分でプロンプト文面を変えない。

## フェーズ 3 回す

プロンプトを `$R/results/prompt.md` に出す（Claude の `run.py` も同じファイルを書く）:

```bash
python3 -c "from pathlib import Path; from common import load_target, render_prompt, root_of; t=load_target(Path('$T')); r=root_of(t,None); r.joinpath('results').mkdir(parents=True, exist_ok=True); r.joinpath('results/prompt.md').write_text(render_prompt(t))"
```

ログの置き場は `$R/results/<vendor>/`。リポジトリ側の写しは `analyzer/agent_forecast/results/<vendor>/`。

### Claude（`claude -p`）

```bash
python3 run.py --target $T A
python3 run.py --target $T B C D E F
python3 run.py --target $T --tag _2 E F   # ぶれを見るときだけ
```

`claude` が無い環境では Grok パスへ。許可ルールは `run.py` が付ける。親が cond の外を読ませない。

### Grok 4.6（Cursor Task）

`claude -p` の許可ルールは使えない。条件ごとに **別の** Task を 1 本。`subagent_type`: `generalPurpose`。`model`: `cursor-grok-4.6-high`。`run_in_background` は使わない。`environment`: `cloud` にしない。並列にしない。

準備（親。条件 `$C`）:

```bash
rm -rf /tmp/grok-forecast/$C
mkdir -p /tmp/grok-forecast
cp -a "$R/cond_$C" "/tmp/grok-forecast/$C"
# C / F だけ月次スナップショット（MCP の代用 + pack_cursor_log のトークン）
mkdir -p /tmp/grok-forecast/mcp
test -d "$R/mcp/snapshot" && cp -a "$R/mcp/snapshot" /tmp/grok-forecast/mcp/snapshot
find "/tmp/grok-forecast/$C" -type f | sort
```

Glob は `/tmp` を辿れず `/workspace` を返すことがある。**ファイル一覧を Task プロンプトに貼る。** 子に Glob させない。PDF が多い条件（B/D/E/F）は対象四半期の月次ファイル名も列挙する。

子に渡してよいパス:

- `/tmp/grok-forecast/$C/**`
- C/F のみ `/tmp/grok-forecast/mcp/snapshot/*.json`（`monthly_list` / `monthly_get` の代用）

渡してはいけない: `$R`、`/workspace`、`answer/`、他条件、`results/`、実績数字、親のメモリ。

Task 本文は `$R/results/prompt.md` の全文 + 下の隔離文。子の返答（```json ブロック）を `$R/results/grok/${C}${TAG}_out.txt` に保存する。

隔離文（条件名とファイル一覧を埋める）:

```
あなたは隔離された予想ランです。条件 $C。

読んでよいのは次だけ:
- /tmp/grok-forecast/$C/ 以下（一覧をプロンプトに貼ってある。Glob するな。/workspace のヒットは開くな）
- （C/F のみ）/tmp/grok-forecast/mcp/snapshot/index.json と <year>.json。これが monthly_list / monthly_get の代用。本番 MCP は呼ぶな

Bash / Web / 書き込み / 他条件 / /workspace / answer は使うな。今日より後の出来事は使うな。
最後に prompt 指定の JSON を 1 つだけ出す。
```

親が記録する meta（`$R/results/grok/${C}${TAG}_meta.json`）:

```json
{
  "files_read": ["README.md", "data/financials.json"],
  "monthly_calls": [],
  "denied_attempts": []
}
```

- `files_read` は cond 内の相対パス。C/F の snapshot JSON は **入れない**（トークンが二重になる）
- C/F の snapshot Read は `monthly_calls` へ: `{"tool":"monthly_list","code":"<code4>"}` / `{"tool":"monthly_get","code":"<code4>","year":2026}`
- `/workspace` を開こうとした・Glob がワークスペースを返した、は `denied_attempts` に一文で残す
- 子が `/workspace` や `$R` を読んだらその回は無効。フォルダを作り直して打ち直す

```bash
python3 pack_cursor_log.py --target $T --cond $C --text-file $R/results/grok/${C}_out.txt --meta-file $R/results/grok/${C}_meta.json --cond-dir /tmp/grok-forecast/$C --model cursor-grok-4.6-high
```

`--tag _2` のときは `--tag _2` を足す。`--cond-dir` を省略すると `/tmp/grok-forecast/<条件>`。

## フェーズ 4 採点とコミット

```bash
python3 score.py --target $T --vendor <claude|grok> --markdown results/<vendor>/<id>.md
python3 slim_logs.py --target $T --vendor <claude|grok>
```

`results/<vendor>/<id>.md` に結論を書き足す（売上/利益に月次と説明資料が効いたか、PDF vs API のトークン、何回回したか）。表は `score.py` が出す。

リポジトリへ写すもの（PDF・zip・jsonl 全文は不可）:

- `analyzer/agent_forecast/results/<vendor>/<id>.md`
- `analyzer/agent_forecast/results/<vendor>/<id>_logs/`（`slim_logs.py` の出力）
- `$R/raw/financials.json` → `analyzer/agent_forecast/results/<id>_financials.json`
- 新しい `targets/<id>.json`

README の結果表に 1 行足す。数字は実行時点のもの。

## 対象外

- パーサー修正、月次 ingest、SPA 検証
- 予想子に本番の `monthly_*` MCP を繋ぐこと（締切後の月が混ざる）。スナップショットだけ
- 条件フォルダをシンボリックリンクで作ること（外を指す）
