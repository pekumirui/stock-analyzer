---
name: monthly-html-ops-ingest
description: >
  stock-analyzer 用の運用向け IR HTML 月次データ取り込み: アーカイブ/年次 URL を用いた暦年のギャップ埋め、複数テーブルページの結合、ライブ HTML で会計年度が証明できる場合のパーサーの as_of の上書き、issuer_aliases と運用マージによるラベルのゴースト（不要な表記揺れ）のクリーンアップ。
  ウェーブ終了時は Cursor Task で別エージェントを起動し `.cursor/skills/monthly-html-pair-review/SKILL.md` を読ませる。親は自己証明しない。
  HTML 月次データのバックフィル、Issue #180 スタイルの運用、monthly_ingest MCP の POST、2024年1〜2月や上期/下期の欠損、または DB の行を書き換えないエイリアスを追加した後の対応時に使用します。
---

# IR HTML 月次運用取り込み

本番 MCP (`monthly_*`) へのループバック。設計の正本は `DESIGN.md` / `docs/DESIGN_monthly.md` / `docs/DESIGN_monthly_labels.md`。このスキルは運用だけ。

## いつ使うか

- `config/ir_monthly.json` の HTML 発行体で、欠けている暦月を埋める
- GitHub Issue（#180 の型）でウェーブを追う
- エイリアスや正規化を出したあと、古い行ラベルを消す

対象外: パーサー修正（別 PR）、TDnet PDF、XBRL、SPA 検証（`verify-stock-analyzer`）。ペアレビューの判定は `.cursor/skills/monthly-html-pair-review/SKILL.md`。親はここで判定しない。

## パイプライン（正本）

1. ライブ HTML（年次/アーカイブ URL）をローカルで `html_issuers.parse_issuer` する
2. 任意: ペイロードを手で直す（`as_of`、月、要素名、表の結合）
3. `plan_column_years(months, fy_end_month, as_of)`。POST 前に暦年を証明する
4. 重複セルは `monthly_get` と突き合わせる
5. `monthly_ingest`（MCP）。**`year` は指定しない**。UPSERT してよい
6. `monthly_get` で確かめる
7. ウェーブ終了 → **ペアレビューを起動する**（下の節。自分でマトリックスを埋めない）

Cron は別: `ir_monthly_html_r2` は `.ingest.json` だけ書く。`ir_monthly_html_ingest` は銘柄ごとの最新ファイルだけ POST する。過去年は運用とアーカイブ URL が要る。

## 厳守

- エージェントが作った ingest JSON は使ってよい。パーサーの `as_of` を絶対視**しない**。ライブ HTML で会計年度が証明できるなら `as_of` / ラベルを上書きする（例: かっぱ寿司のナビにある最大の `YYYY年度` が as_of を汚染する）。パーサーのバグがあっても取り込みを止めない。
- HTML パーサーは `issuer_aliases` を適用**しない**。エイリアスは `POST …/ingest`（`app/api.py` の `_canonical_element`: 正規化 → `apply_issuer_aliases` → 正規化）の中だけ走る。エイリアスを足しても既存の DB 行は書き換わらない（起動時も。保存名は凍結）。マージ後に Actions の **Monthly names** を plan → apply で流して既存行を付け替える。
- 新しいデータに year 付き PUT をしない（`monthly_put_values` は訂正専用）。
- ブランド名の変更をマージしない（例: マックスバリュ関東 ↔ イオンフードスタイル）。
- ワタミ型の誤単位（実数なのに `yoy_pct` タグ）を POST しない。
- 不明なら聞く（as_of/年の対応、アルペンのような二重スコープ、単位の曖昧さ）。
- **取り込んだエージェントはウェーブ完了を証明してはならない。** 網羅性マトリックス・数値抜き打ち・表記揺れ判定を自分で書かない。自己証明は不合格。

## ツール

| ツール | 用途 |
|---|---|
| MCP `monthly_get` / `monthly_ingest` / `monthly_put_values` / `monthly_add_element` / `monthly_delete_element` | 本番ループバック |
| `config/ir_monthly.json` | `index_url`, `follow_re`, html 発行体 |
| `.venv/bin/python` + `parse_issuer` / `plan_column_years` | ローカルパース + 年マッピング |
| `config/issuer_aliases.json` + `docs/DESIGN_monthly_labels.md` | 新しいリネームルール（コードの PR） |
| `config/issuer_metrics.json` | 件数指標の語（`受託施設数` など）。件数の行を入れたら足す（コードの PR。DB は変わらない） |
| GitHub Issue | 作業前/作業後の進捗 |
| Cursor `Task` (`subagent_type: generalPurpose`) | ウェーブ完了後、ペアレビュー子を起動する |

VPS: `ssh stock-vps` + `curl http://127.0.0.1:8000/api/v1/tables/...` は MCP と同じ API。

## POST 前のチェックリスト

コピーしてチェックする:

```
- [ ] ソース URL を取得した。パース結果に必要な月がある
- [ ] 本番の statements（または既知の情報）から fy_end を確認した。plan_column_years を出力した
- [ ] as_of を自分で選んだ（パーサーのデフォルトまたは上書き）。うるう日も可 (2024-02-29)
- [ ] 要素名が DB の最終形と一致している（エイリアスが VPS にまだ無いならゴーストのプレフィックスを付けない。クリーンな名前を送る）
- [ ] **HTML パース ↔ 手動投入の一致**: `parse_issuer`（必要なら alias 適用後）の element / unit / 暦月値が、POST する JSON および投入後の `monthly_get` と一致する（ズレたらパース修正か alias、または POST 名の修正）
- [ ] monthly_get との重複: 競合する null でないセルがない（または一致を証明したあとだけ force）
- [ ] 新しいセルに year-PUT していない
- [ ] Issue コメント: URL、as_of、マッピングする年、何を埋めるか
- [ ] ウェーブ完了後: Task でペアレビュー子を起動した（自分でマトリックスを書いていない）
```

## ギャップ埋めの手順

1. **2024 / 2025 / 2026** の `monthly_get` を取る。**銘柄ごと**に欠損月を列挙する（3年分、各ウェーブごと）
2. アーカイブ/年次 URL を探す（`index_url` だけではない）。Cron/`follow_re` + `select_latest_per_code` では、同一 URL だけの発行体はバックフィルできない
3. パース → `plan_column_years`。"2024" とラベルされたページが 1〜2 月を暦年 **2025** に載せるなら、暦年 2024/1〜2 は **前年度** のページを取る
4. 複数テーブル（上期+下期）は POST 前にマージする。パーサーは表を 1 つしか持たない
5. `monthly_ingest` を POST する。`monthly_get` で確かめる
6. "2024" ページのウェーブのあと、**2024 年の 1〜12 月**をもう一度見る（最初のウェーブでは 1〜2 月や下期が空のまま残ることが多い）
7. ウェーブの終了 → **ペアレビューを起動する**（次節。これなしで Issue をクローズしない）

## ペアレビュー起動（必須）

親（このスキルを実行しているエージェント）はレビュー本体をやらない。チェック項目をこのファイルから実行してはならない。`monthly-html-pair-review` を自分で読んで埋めるのも自己証明であり不合格。

ユーザーが「自分がレビューする」と明示したときだけ Task を省略できる。そのときは Issue に「ペアレビュー未実施」を残し、クローズしない。

ギャップ埋めでもラベルの表記揺れを消すときでも、POST / 投入後 `monthly_get` が終わったら:

1. Cursor **`Task`** を呼ぶ。`subagent_type`: `generalPurpose`。`run_in_background`: 使わない（子の pass/fail を待つ）。`environment`: `cloud` にしない。
2. `description`: `Pair-review monthly HTML`（銘柄が少なければ code を足してよい）。
3. `prompt` の **最初の行** を次にする: `Read .cursor/skills/monthly-html-pair-review/SKILL.md then follow it. Do not ingest, POST, PUT, or DELETE.`
4. 同じ `prompt` に次をすべて渡す（子は親の会話を見ない）:

```
codes: …
urls: …
as_of: …
fy_end / plan_column_years: …
posted JSON (or path / equivalent cells): …
years to check: 2024, 2025, 2026
known blockers: …
```

5. 子が **pass / fail** と報告テンプレートを返すまで、Issue をクローズしない。不合格なら追加 POST やクローズをしない（残件を Issue に書く）。
6. 子の報告を Issue コメントにする（マトリックス + 数値サンプル + ラベル一覧 + ブロッカー）。

## ラベルの表記揺れを消す手順

1. ルールの証明: `issuer_aliases` に対する単独の正規化（grep / ローカル適用）
2. ルールが無ければ小さな PR（エイリアスまたは正規化）+ DESIGN_monthly_labels の 1 行
3. 本番での運用（エイリアスは古い行を移行しない）:
   - src と dst で同じ月の競合を見る
   - 必要なら dst を `monthly_add_element` する（近いなら `force`）
   - `monthly_put_values` でコピー → src を null クリア → 空になった src を `monthly_delete_element` する
4. 全店↔全社の統合を作らない。正本では **全社 ≠ 全店**。アルペンは両方残す。USMH のゴースト `全店` → `全社` は、HTML が全社のみのときだけ。
5. マージが終わったら **ペアレビュー起動**（前節）。

## 停止条件

止まって聞く（POST しない）:

- as_of / 暦年の対応が分からない（かっぱ寿司型）
- 単位がおかしく見える（件数なのに `yoy_pct`）
- アーカイブが 404 で、その月を証明する他の URL が無い
- マージすると等しくない値が衝突する
- ブランド/スコープのリネーム判断が要る

## 関連

- ペアレビュー（子）: `.cursor/skills/monthly-html-pair-review/SKILL.md`
- トラップ表: [gotchas.md](gotchas.md)
- データ着信後の UI 証明: `.cursor/skills/verify-stock-analyzer/SKILL.md`
