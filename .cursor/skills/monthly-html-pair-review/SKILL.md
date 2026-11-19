---
name: monthly-html-pair-review
description: >
  Second-pass pair review of stock-analyzer IR HTML monthly waves.
  Reads monthly_get for 2024/2025/2026 coverage, spot-checks DB numbers against source HTML,
  compares parse_issuer (after aliases) to ingested labels, and fails on twin/ghost element names.
  Does not ingest, POST, PUT, or DELETE.
  Use after a monthly-html-ops-ingest wave when a parent agent spawns a Task subagent,
  or when the user asks to ペアレビュー / pair-review an existing monthly HTML wave.
  Not for filling gaps or posting ingest JSON.
disable-model-invocation: true
---

# IR HTML 月次ペアレビュー

ウェーブ完了後の **2 回目の確認**。正本のチェック項目はこのファイルだけ。取り込み手順は `.cursor/skills/monthly-html-ops-ingest/SKILL.md`。

設計は `docs/jobs/monthly.md` / `docs/screen/monthly.md` / `docs/jobs/monthly_labels.md`。トラップは `.cursor/skills/monthly-html-ops-ingest/gotchas.md`。

## いつ使うか

- 親が `monthly-html-ops-ingest` のウェーブを終え、`Task` でこのスキルを読ませたとき
- ユーザーが既存ウェーブのペアレビューを依頼したとき

対象外: ギャップ埋め、`monthly_ingest`、エイリアス追加 PR、パーサー修正。

## 厳守

- **自分がレビュー担当。** さらに別のペアレビュー子を起動しない。
- **書き込み禁止:** `monthly_ingest` / `monthly_put_values` / `monthly_add_element` / `monthly_delete_element` / `monthly_set_industry`。読取は `monthly_get` / `monthly_list` とローカル `parse_issuer` のみ。
- 親（取り込んだエージェント）の自己証明を信用しない。`monthly_get` とソースを自分で取り直す。
- 2024 / 2025 / 2026 のいずれか未チェックなら不合格。
- ブランド名の変更をマージしない（報告のみ）。単位が怪しければ不合格にして止める。

## 最初の行動

1. このファイルを読み終える（親から渡されたプロンプトより先に、ここの判定基準を使う）。
2. 親またはユーザーから次を揃える。欠けていて推測できないなら不合格 + 質問で止める。

| 必須の引数 | 内容 |
|---|---|
| codes | ウェーブで触った銘柄（5 桁） |
| urls | ソース HTML（ライブ / アーカイブ） |
| as_of | 投入に使った日付 |
| year mapping | `plan_column_years` の結果、または fy_end と months |
| posted JSON | POST したペイロード、または同等の element/unit/暦月値 |
| years | 必ず **2024, 2025, 2026** |
| blockers | 証明済み 404 / アーカイブなし（あれば） |

## 手順

1. 触った code ごとに MCP `monthly_get` を **2024 / 2025 / 2026** で取る。
2. ソース HTML を取得し、ローカルで `parse_issuer`。銘柄固有は `canonicalize` → `apply_issuer_aliases` → `canonicalize`。
3. 必要なら `plan_column_years` で暦年を再現する（親の year バケツを検証する）。
4. 下の 4 項目をすべて埋める。1 つでも欠ければ不合格。
5. 親へ **pass または fail** と下の報告テンプレートを返す。Issue コメント本文も同じ形で返す（投稿は親でも可）。

## 網羅性マトリックス（必須）

ウェーブで触れたすべての銘柄コードについて、`monthly_get` で **2024、2025、および 2026** を確認する。年ごとに記録する:

| year | 何らかの非 null がある月 | 欠損 | 空で想定通りか（未開示 / アーカイブなし） |
|---|---|---|---|
| 2024 | … | … | … |
| 2025 | … | … | … |
| 2026 | … | … | … |

2024〜2026 のいずれかがチェックされなかった場合はレビューを不合格とする。最初のウェーブのあと、2024 は欠けやすい。Issue に証明済みのブロッカー（404、アーカイブなし）が無い限り、2024/1〜2 の欠損や 2024 全体の欠損は欠陥として扱う。

## 数値の一致（必須）

月があることだけでは足りない。DB の数値をソース HTML（または実際に POST された JSON）と抜き打ちで照合する。

- 少なくとも次を選ぶ: 序盤の月 1 つ、中盤の月 1 つ、終盤の月 1 つ（隣接する年度のページから埋めたなら暦年の 1〜2 月も）
- 各サンプル: 同じ要素、同じ暦月 → DB の値 == ソース（`yoy`: 保存された形式。`yoy_pct` の取り込みは +100 されて `yoy_index` になる）
- 不一致、ソースに数値があるのに無言で null、または年のバケツが違う（値が 2024 ではなく 2025 に入った）なら不合格

## HTML パース ↔ 手動投入の一致（必須）

ウェーブで触った銘柄について、ライブ/アーカイブ HTML を `parse_issuer` し（銘柄固有は `canonicalize` → `apply_issuer_aliases` → `canonicalize`）、投入後の `monthly_get` と比較する。

- 同じ暦月・同じ最終 element 名・同じ unit で値が一致すること
- パーサだけにある行 / DB だけにある双子・ゴースト行があれば不合格（意図的な運用マージ途中なら Issue に残件を書く）
- Cron の `.ingest.json` を再パースした場合は、その JSON の element が alias 後に DB 最終形と一致することも確認する

## 表記揺れなし（必須）

触れた銘柄コード × 年（2024〜2026）のすべてについて、`monthly_get` からすべての `element` 名をリストアップする。同じ指標が 2 つのラベルの下にある、または既知のゴーストのプレフィックス/サフィックスが残っているなら不合格。

次を見る:

- 1 つの行になるべきスコープの双子（例: HTML が全社のみの場合の `全店` vs `全社`、USMH）。HTML に本当に両方がある場合（アルペン）、不合格にしては**ならない**
- プレフィックスの残りカス: `売上（%）`、`U.S.M.H`、丸囲みの `①`/`②`、正規化で削除されるべきだった古い `(1)` の脚注
- 1 つの年の中で、月をまたいでブランド名が変わった 2 つの名前（例: マックスバリュ関東 vs イオンフードスタイル）。報告するが自動マージは**しない**。質問する
- 同じ指標に対して、双子の行にまたがって月が分割されている（名前 A に 3〜8 月、名前 B に 1〜2 月）

再確認:

- [ ] 単位がまともか（カウント行が `yoy_pct` になっていないか）
- [ ] 報告: マトリックス + 数値サンプル + ラベルリスト / 表記揺れの発見 + ブロッカー

## 報告テンプレート

親にそのまま返す:

```
# pair-review: <codes>

Verdict: pass | fail

## 網羅性
| year | 非 null 月 | 欠損 | 空で想定通りか |
|---|---|---|---|
| 2024 | | | |
| 2025 | | | |
| 2026 | | | |

## 数値サンプル
- code / year-month / element / source / db / match?

## parse ↔ monthly_get
- 一致した最終 element 名 / unit
- パーサのみ / DB のみの行

## element 名
- code × year のユニークなラベル一覧
- 双子・ゴースト: なし | あり（名前）

## ブロッカー
- 証明済み 404 / 判断が必要なブランド変更 / なし
```

## 関連

- 取り込み（親）: `.cursor/skills/monthly-html-ops-ingest/SKILL.md`
- トラップ: `.cursor/skills/monthly-html-ops-ingest/gotchas.md`
