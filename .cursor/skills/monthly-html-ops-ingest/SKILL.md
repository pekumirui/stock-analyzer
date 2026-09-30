---
name: monthly-html-ops-ingest
description: >
  stock-analyzer 用の運用向け IR HTML 月次データ取り込み: アーカイブ/年次 URL を用いた暦年のギャップ埋め、複数テーブルページの結合、ライブ HTML で会計年度が証明できる場合のパーサーの as_of の上書き、issuer_aliases と運用マージによるラベルのゴースト（不要な表記揺れ）のクリーンアップ。
  ウェーブ終了時は Cursor Task で別エージェントを起動し `.cursor/skills/monthly-html-pair-review/SKILL.md` を読ませる。親は自己証明しない。
  HTML 月次データのバックフィル、Issue #180 スタイルの運用、monthly_ingest MCP の POST、2024年1〜2月や上期/下期の欠損、または DB の行を書き換えないエイリアスを追加した後の対応時に使用します。
---

# IR HTML 月次運用取り込み (IR HTML monthly ops ingest)

stock-analyzer MCP (`monthly_*`) を経由した本番環境へのループバックです。設計については `DESIGN.md` / `docs/DESIGN_monthly.md` / `docs/DESIGN_monthly_labels.md` を正とします。このスキルは **運用専用 (ops only)** です。

## いつ使用するか (When to use)

- `config/ir_monthly.json` にある HTML 発行体の欠損している暦月を埋める場合
- GitHub Issue (#180 のパターン) での一連の作業（ウェーブ）を追跡する場合
- エイリアス/正規化 (canonicalize) をリリースした後に、古い行ラベルをクリーンアップする場合

対象外: パーサーのコード変更 (別の PR で対応)、TDnet の PDF、XBRL、SPA の検証 (`verify-stock-analyzer` を使用)。ペアレビューのチェック項目は `.cursor/skills/monthly-html-pair-review/SKILL.md`（親はこのファイルで判定しない）。

## パイプライン (正本) (Pipeline (truth))

1. ライブ HTML (年次/アーカイブ URL) → ローカルで `html_issuers.parse_issuer` を使ってパースする
2. 任意: ペイロードを手動で編集する (`as_of`、月、要素名、テーブルの結合)
3. `plan_column_years(months, fy_end_month, as_of)` — POST 前に暦年を証明する
4. 重複するセルについて `monthly_get` との競合チェックを行う
5. `monthly_ingest` (MCP) — **`year` は指定しない**。UPSERT は可能
6. `monthly_get` で検証する
7. ウェーブ終了 → **ペアレビュー起動**（下の節。自分でマトリックスを埋めない）

Cron のパスは異なります: `ir_monthly_html_r2` は `.ingest.json` のみを書き出します。`ir_monthly_html_ingest` は **銘柄ごとに最新のもの (latest-per-code)** を POST します。過去の年については、運用作業とアーカイブ URL が必要です。

## 厳守事項 (Hard rules)

- エージェントが作成した ingest JSON は許可されます。パーサーの `as_of` を絶対視**しない**でください。ライブ HTML で会計年度が証明できる場合は、`as_of` / ラベルを上書きしてください（例：かっぱ寿司 (Kappa) のナビゲーションにある最大の `YYYY年度` が as_of を汚染するケース）。パーサーのバグ ≠ 取り込み不可、です。
- HTML パーサーは `issuer_aliases` を適用**しません**。エイリアスは `POST …/ingest` (`app/api.py` の `_canonical_element`: 正規化 → `apply_issuer_aliases` → 正規化) の中でのみ実行されます。エイリアスを追加しても既存の DB の行は書き換えられ**ません** — その後の運用マージが必要です。
- 新しいデータに対して year を指定した PUT を行わ**ない**でください (`monthly_put_values` は訂正専用です)。
- ブランド名の変更をマージし**ない**でください (例: マックスバリュ関東 ↔ イオンフードスタイル)。
- ワタミ (Watami) クラスの誤った単位（実数なのに `yoy_pct` タグが付いている）を POST し**ない**でください。
- 不明な点（as_of/年のマッピング、アルペン (Alpen) のような二重スコープ、単位の曖昧さ）がある場合は質問してください。
- **取り込んだエージェントはウェーブ完了を証明してはならない。** 網羅性マトリックス・数値抜き打ち・表記揺れ判定を自分で書かない。自己証明は不合格。

## ツール (Tools)

| ツール | 用途 |
|---|---|
| MCP `monthly_get` / `monthly_ingest` / `monthly_put_values` / `monthly_add_element` / `monthly_delete_element` | 本番ループバック |
| `config/ir_monthly.json` | `index_url`, `follow_re`, html 発行体 |
| `.venv/bin/python` + `parse_issuer` / `plan_column_years` | ローカルパース + 年マッピング |
| `config/issuer_aliases.json` + `docs/DESIGN_monthly_labels.md` | 新しいリネームルール (コードの PR) |
| GitHub Issue | 作業前/作業後の進捗 |
| Cursor `Task` (`subagent_type: generalPurpose`) | ウェーブ完了後、ペアレビュー子を起動する |

VPS: `ssh stock-vps` + `curl http://127.0.0.1:8000/api/v1/tables/...` は MCP と同じ API です。

## POST 前のチェックリスト (Pre-POST checklist)

コピーしてチェックを入れてください:

```
- [ ] ソース URL を取得した; パース結果に必要な月が含まれている
- [ ] 本番の statements (または既知の情報) から fy_end を確認した; plan_column_years を出力した
- [ ] as_of を意図的に選択した (パーサーのデフォルトまたは上書き) — うるう日も可 (2024-02-29)
- [ ] 要素名が DB の最終形と一致している (エイリアスが VPS にまだない場合はゴーストのプレフィックスを付けない: クリーンな名前を送信する)
- [ ] **HTML パース ↔ 手動投入の一致**: `parse_issuer`（必要なら alias 適用後）の element / unit / 暦月値が、POST する JSON および投入後の `monthly_get` と一致する（ズレたらパース修正か alias、または POST 名の修正）
- [ ] monthly_get との重複: 競合する null でないセルがない (または一致を証明した後にのみ force を使用する)
- [ ] 新しいセルに対して year-PUT を行っていない
- [ ] Issue コメント: URL、as_of、マッピングする年、何を埋めるか
- [ ] ウェーブ完了後: Task でペアレビュー子を起動した（自分でマトリックスを書いていない）
```

## ギャップ埋めプレイブック (Fill-gap playbook)

1. **2024 / 2025 / 2026** の `monthly_get` を実行する — **銘柄ごと**に欠損月をリストアップする (3年分すべて、各ウェーブごと)
2. アーカイブ/年次 URL を探す (`index_url` だけではない)。Cron/`follow_re` + `select_latest_per_code` では、同一 URL のみの発行体のバックフィルはできない
3. パース → `plan_column_years`。もし "2024" とラベル付けされたページが 1〜2 月を暦年の **2025** にマッピングする場合、暦年の 2024/1〜2 には **前年度 (previous FY)** のページを取得する
4. 複数テーブル (上期+下期): POST 前にマージする — パーサーは 1 つのテーブルしか保持しない
5. `monthly_ingest` を POST する; `monthly_get` で検証する
6. "2024" ページのウェーブの後: **2024 年の 1〜12 月**を体系的に再スキャンする (最初のウェーブでは 1〜2 月や下期が空のまま残ることが多い)
7. ウェーブの終了 → **ペアレビューを起動する**（次節。これなしで Issue をクローズしないこと）

## ペアレビュー起動 (必須) (Spawn pair review)

親（このスキルを実行しているエージェント）はレビュー本体をやらない。チェック項目をこのファイルから実行してはならない。`monthly-html-pair-review` を自分で読んで埋めるのも自己証明であり不合格。

ユーザーが「自分がレビューする」と明示したときだけ Task を省略できる。そのときは Issue に「ペアレビュー未実施」を残し、クローズしない。

ギャップ埋め・ラベルクリーンアップのどちらでも、POST / 投入後 `monthly_get` が終わったら:

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

## ラベルクリーンアッププレイブック (Label cleanup playbook)

1. ルールの証明: `issuer_aliases` に対する単独の正規化 (grep / ローカル適用)
2. ルールが欠落している場合 → 小さな PR (エイリアスまたは正規化) + DESIGN_monthly_labels の 1 行追加
3. 本番環境での運用 (エイリアスは古い行を移行しない):
   - ソース (src) と宛先 (dst) で同じ月の競合チェックを行う
   - 必要に応じて dst を `monthly_add_element` する (類似している場合は `force`)
   - `monthly_put_values` でコピー → src を null クリア → 空になった src を `monthly_delete_element` する
4. 全店↔全社の統合をでっち上げないこと。グローバルにおいて **全社 ≠ 全店** である。アルペンは両方を保持する。USMH のゴースト `全店` → `全社` は、HTML が全社のみの場合にのみ行う。
5. マージが終わったら **ペアレビュー起動**（前節）。

## 停止条件 (Stop conditions)

停止して質問すること (POST しないこと):

- as_of / 暦年のマッピングが不明確 (かっぱ寿司クラス)
- 単位が間違っているように見える (カウントに対する `yoy_pct`)
- アーカイブが 404 であり、その月を証明する他の URL がない
- マージすると等しくない値が衝突する
- ブランド/スコープのリネームの判断が必要な場合

## 関連 (Related)

- ペアレビュー（子）: `.cursor/skills/monthly-html-pair-review/SKILL.md`
- トラップテーブル: [gotchas.md](gotchas.md)
- データ着信後の UI 証明: `.cursor/skills/verify-stock-analyzer/SKILL.md`
