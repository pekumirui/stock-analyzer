# agent_forecast

## 問い

決算予想をエージェントにさせるとき、どの情報を渡せば精度が上がるか。月次（PDF そのもの / stock-analyzer の月次 API）は役に立つか。精度と消費トークンを比べる。

## やり方

- 銘柄 × 決算期を 1 つ選び、発表の前日を締切にする。締切までに公表された情報だけを渡して、対象四半期の単独の売上高・粗利・販管費・営業利益・経常利益・純利益（百万円）と EPS（円）を予想させる（経常利益以下はコメ兵 1Q の F_opus_all から。それ以前の回は 4 項目）
- 渡す情報の組み合わせ（条件 A〜F）ごとに、別プロセスの `claude -p` を 1 回ずつ起動する
- 実績の短信と比べた誤差、会社予想から逆算した値（4Q のみ）、トークン数・費用を表にする

| 条件 | 財務 JSON | 月次 PDF | 月次 API（MCP） | 説明資料 |
|---|---|---|---|---|
| A | ○ | | | |
| B | ○ | ○ | | |
| C | ○ | | ○ | |
| D | ○ | | | ○ |
| E | ○ | ○ | | ○ |
| F | ○ | | ○ | ○ |

- 財務 JSON: 短信 XBRL から作る。累計・四半期単独（累計の差）・セグメント・各四半期末の BS・会社予想。同じ期は新しい短信（翌年の前年同期比較）を採る
- 月次 PDF: 月次速報など（TDnet）
- 月次 API: 本番の `/api/v1/tables` を締切の月で切ったスナップショット。`monthly_mcp.py` が本番の月次 MCP と同じ `monthly_list` / `monthly_get` で返す
- 説明資料: 決算説明（会）資料・決算補足資料・中期経営計画など（TDnet）

## 隔離

- 作業フォルダは `cond_<X>/`。道具は Read / Glob / Grep（C・F は月次 MCP も）。Bash・Web・書き込みは無い
- 読めるのは `Read(//<root>/cond_<X>/**)` の許可ルールの中だけ。`-p` では許可外の操作は自動で拒否され、`permission_denials` に残る（採点表の「拒否」）
- `--setting-sources ""` と `--strict-mcp-config` で、リポジトリの指示・メモリ・他の MCP を読ませない
- 全許可モードは使わない。ログイン情報は複製しない（元の HOME のまま動かす）
- 正解（`answer/`）と月次スナップショット（`mcp/`）は `cond_<X>/` の外に置く
- モデルの知識の期限が締切より後だと、学習データから漏れうる。結果を読むときに確認する

## 他の銘柄で回す

材料と結果はリポジトリの外（既定 `/workspace/forecast-exp/<id>/`、`<id>` は設定ファイル名）に置く。PDF はコミットしない。スクリプトは標準ライブラリだけ。

### 1. 設定ファイルを作る（`targets/<code4>_FY<YYYY>Q<n>.json`）

`targets/6036_FY2026Q4.json` を写して書き換える。開示の一覧は IR Bank MCP の `list_disclosures`（`edinet_code` は `get_organization` か EDINET コードリスト）で、締切までの分を拾う。

| キー | 中身 |
|---|---|
| `name` / `code`（5 桁）/ `edinet_code` / `profile` | 会社名、コード、プロンプトに出す説明（例「証券コード 6036、非連結、6 月決算」） |
| `cutoff` / `announce` | 締切（発表の前日）と発表日 |
| `target` | `fiscal_year_end`（決算期末 `YYYY-MM`）と `quarter`（1〜4） |
| `tanshin` | 短信の `date` / `doc_id` / `stem`。stem は `FY<決算期末>_Q<1|2|3|FY>`。対象期の前の四半期まで、3 年分くらい |
| `monthly` / `briefing` | 渡す PDF の `date` / `doc_id`（TDnet）/ `title`。英語版は入れない。訂正は入れる |
| `monthly_api_range` | 月次 API を渡す範囲 `["YYYY-MM", "YYYY-MM"]`。終わりは締切までに公表された月。月次が無い銘柄は消す（C・F は作られない） |
| `cost_reclass` | 原価と販管費の区分変更があれば、境目の短信の日付 `from` とプロンプトに足す `note`。無ければ消す |
| `segment_labels` | XBRL のセグメント名（英語）→ 日本語。省略すると英語のまま |
| `readme` | 条件フォルダの README に書く各資料の説明 |

### 2. 材料をそろえる

```bash
T=targets/6036_FY2026Q4.json; R=/workspace/forecast-exp/6036_FY2026Q4; cd analyzer/agent_forecast
```

1. 短信 XBRL: エージェントが IR Bank MCP の `get_disclosure(doc_id, include=["xbrl_facts"])` を短信ごとに呼び、応答をそのまま `$R/raw/xbrl/<stem>.json` に置く（MCP なのでスクリプトにできない）
2. 実績: 発表日の短信 zip（R2 の `xbrl/<code4>/` など）を `$R/answer/` に置く。条件フォルダからは読めない
3. 残りはスクリプト

```bash
python3 fetch_docs.py --target $T              # 月次・説明資料の PDF（IR Bank）
python3 build_financials.py --target $T        # raw/financials.json。組替があれば「注意」が出る → cost_reclass を書く
python3 build_monthly_snapshot.py --target $T  # 本番の月次 API を切る（月次がある銘柄だけ）
python3 build_conditions.py --target $T        # cond_A〜F と mcp/
```

### 3. 回して採点する

実行ログと結果はベンダーごとに分ける（`$R/results/<vendor>/`、`results/<vendor>/`）。`run.py` は Claude（`claude -p`）用。他のベンダーのエージェントで回すときは、同じ条件フォルダ・同じプロンプト（`$R/results/prompt.md`）・同じ隔離（作業フォルダの外を読めない、シェルと Web なし）で起動し、ログを `$R/results/<vendor>/` に置く。

Cursor の Grok は `claude -p` が無いので、条件フォルダを `/tmp` にコピーして Task サブエージェント（`cursor-grok-4.6-high`）を 1 条件ずつ起動する。C・F の月次 MCP はスナップショット JSON の Read で代用する。返ってきた予想は `pack_cursor_log.py` で score.py が読める JSONL にする（トークンは読んだファイルの文字数からの推定）。

使ったモデルは、ログの init と `modelUsage` から `score.py` が取って表に出す。

```bash
python3 run.py --target $T A                  # まず 1 条件で確かめる。費用は claude.ai の利用枠から。ログは $R/results/claude/
python3 run.py --target $T B C D E F
python3 run.py --target $T --tag _2 E F       # 同じ条件をもう一度（ぶれを見る）
python3 score.py --target $T --vendor claude --markdown results/claude/<id>.md   # 画面にはモデル名も出る
python3 slim_logs.py --target $T --vendor claude   # 軽量版の実行ログ → results/claude/<id>_logs/
# Cursor Grok 4.6: 条件フォルダを隔離して Task で回し、返ってきた予想を JSONL にする
python3 pack_cursor_log.py --target $T --cond A --text-file out.txt --meta-file meta.json
python3 score.py --target $T --vendor grok --markdown results/grok/<id>.md
python3 slim_logs.py --target $T --vendor grok
```

`results/<vendor>/<id>.md` に結論を書き足し、軽量版の実行ログ（文章・ツール呼び出し・トークン。読んだ中身は除く）と一緒に、`$R/raw/financials.json` を `results/<id>_financials.json` に写してコミットする（材料のうち作り直しに MCP が要るのはこれだけ）。

## 結果

| 対象 | 実行日 | 結論 |
|---|---|---|
| [ＫｅｅＰｅｒ技研 2026/6 期 4Q（Claude）](results/claude/6036_FY2026Q4.md) | 2026-10-07 | 売上は月次で決まり（誤差 −20%→−5%）、営業利益は月次＋説明資料で −52%→−11〜13%。月次は API の方が PDF より少ないトークン（E・F 3 回平均で −23%）で同じ精度、結果も安定。E・F 以外は 1 回 |
| [ＫｅｅＰｅｒ技研 2026/6 期 4Q（Grok 4.6）](results/grok/6036_FY2026Q4.md) | 2026-10-07 | 売上は月次で決まり（A −21%→B/C/E/F −5%）。営業利益は月次で −47%→−19〜25% まで改善し、説明資料の上乗せは小さい（E/F −18〜19%）。月次 API は PDF より推定トークンが少ない。各条件 1 回 |
| [コメ兵ホールディングス 2027/3 期 1Q（Claude）](results/claude/2780_FY2027Q1.md) | 2026-10-07 | 売上は月次で決まり 8 回とも誤差 ±0.6% 以内。営業利益は Sonnet（E +1.9/−0.9、F −5.2/−0.5）が Opus（E −6.6/+0.5、F −12.3/−6.6）より近い。Opus は説明資料の販管費計画で 1Q の販管費を多めに置いた。費用は Sonnet が 1/3〜1/5。E・F を Opus / Sonnet で 2 回ずつ |
