# 月次の行名正規化: 共通の指標辞書と銘柄別 config（設計案）

#545 の設計案。決まったら実装の PR で中身を `docs/DESIGN_monthly_labels.md`（正本）へ移し、このファイルは消す。関連: #533 / #534 / #546。

## 1. 何が起きたか

#534 は、件数指標（末尾が `数` の語）を層分解で指標として扱う変更だった。この変更が保存名の組み立て（`normalize_element_tokens`）にも効いた。たとえば `撮影件数 既存 売上高(前年比)` → `既存 売上高(前年比)`、`買上点数 全店 売上高(前年比)` → `全店 売上高(前年比)` になる（#534 のマージコミット 57380c0 で確認）。デプロイ後の起動時に `merge_yoy_duplicate_elements` が全行をこの新しい規則で正規化し直した。その結果、件数の前年比の行が同じ年の会社の売上高の行に寄せられて消え、23050 / 31740 / 81980 の 14 行が壊れた（79 セル削除、33 セル改名）。

規則を 1 つ変えると全銘柄に効く。それだけなら新しく取り込む行が変わるだけで済む。壊れたのは、**既存の行を規則から毎回作り直す経路**があるからだ。

### 既存の行を作り直す経路（origin/main 2f269d5）

| 経路 | 呼ばれる時 | 中身 |
|---|---|---|
| `db.merge_yoy_duplicate_elements` | `init_db`。アプリ起動・`jobs/run_batch.py` の毎回・`jobs/migrate.py` | 全行の `canonicalize_element_label(保存名)` を取り、変わる行を改名する。同じ年に同じ名前の行があれば値を寄せて片方を消す |
| `db.apply_issuer_element_aliases` | 同上 | 全行に canonicalize → alias → canonicalize を掛け、変わる行を改名・統合する |
| `db.fold_aliased_element` / `monthly_ingest.same_yoy_element` | ingest / POST の 1 行ごと | その年の既存行をすべて正規化し直し、入ってきた名前と同じになる行を改名・統合する |

`init_db` はバッチのたびに呼ばれる。そのため規則を変えた PR がマージされると、デプロイ直後に全銘柄の DB が黙って書き換わる。

### 数字（`/workspace/verify-live/stock.db`、本番コピー）

このコピーは 2026-10-09 19:44 の取り込みまでを含み、#534 の事故のあとの状態だ（81980 の `買上点数 全店 売上高(前年比)` などはもう無い）。

- `monthly_elements` は 8,474 行。(code, 保存名, unit) は 2,850 種、228 銘柄
- 全 2,850 種について、`canonicalize_element_label(保存名) == 保存名` が成り立つ（不動点）。成り立たない保存名は 0 件
- 層の指標（`label.metric`）は 121 種類ある
  - 複数の銘柄で使う指標: `売上高` 189 銘柄 / `店舗数` 93 / `客数` 89 / `客単価` 85 / `出店`・`退店` 各 28 / `連結売上高` 14 / `取扱高` 5。2 銘柄だけで使うものが少し（`管理戸数` / `営業日数` / `件数` など）
  - **105 種類は 1 銘柄でしか使わない**（`受託施設数` / `加工受託件数` / `ATM設置台数` / `CFD取引口座数` …）
  - 指標が空の保存名は 399 種、62 銘柄（71770 が 46 種、90070 が 38 種 …）
- #534 の件数 peel を保存名の組み立てにも効かせたままにすると、5 銘柄の 16 種の保存名が変わる（14350 / 32760 / 39830 / 46660 …）

共通の指標は 10 個ほどで足りる。残り 100 個超は 1 銘柄の都合なので、銘柄の外に効かせる理由がない。

## 2. 方針

3 つの柱からなる。①だけで #534 型の事故は止まる。②で止まったことを CI で確かめる。③で辞書を config に移す。

### ① 保存名は凍結したデータとして扱う

- 正規化の規則（`canonicalize_element_label` と alias）を掛けるのは、**入ってくる生の名前だけ**にする（ingest / POST / PUT の引数）
- 既存行の保存名は、規則から作り直さない。改名と統合は、人が承認した計画（下の `plan` / `apply`）でしか行わない
- 書き込み時の照合は、入ってきた名前を正規化した結果と保存名の**完全一致**だけにする。`fold_aliased_element` の「既存行を正規化し直して同じになったら寄せる」はやめる（不変条件があれば、作り直しても結果は変わらない）
- 実数と前年比の対の検査（`uncovered_yoy_number_twins`）は名前を作り直さないので、そのまま残す

### ② 不変条件を CI と起動時の検査で守る

**不変条件:** 全保存名 `e` について `canonicalize(e, unit, code) == e`。どの年の alias を掛けても変わらない。

- `tests/fixtures/monthly_names.json` に本番の (code, 保存名, unit) と、その `label` の層（segment / channel / scope / metric / breakdown / metric_kind）を置く。約 2,850 件
- `tests/test_monthly_names_snapshot.py` を足す（`ci_smoke.SUITES` に登録する）
  1. 全件が不動点であること
  2. 全件の `label` がスナップショットと一致すること
  - 落ちたときは、変わった (code, 保存名, 前 → 後) を一覧で出す。事故の前の保存名でスナップショットを取っていれば、#534 は 1. で落ちていた（上の `撮影件数 既存 売上高(前年比)` などが不動点でなくなる）
- 規則や辞書を変える PR は、このテストを通すか、`--update` でスナップショットを書き換える。書き換えると、**どの銘柄の何が変わるかが PR の差分に出る**。保存名の変更は ③ の手順で移行する。表示（`label`）だけの変更なら書き換えて終わり
- スナップショットは `tools/monthly_names_snapshot.py` が DB から作る（読み取り専用）。本番の新しい行は時間とともに増えるので、`monthly-html-ops-ingest` のウェーブ後など、ときどき更新する
- 起動時の `init_db` は検査だけにする。不動点でない保存名を数えてログに 1 行出し（MCP の `app_logs` で見える）、書き込みはしない

### ③ 指標の辞書を config に移す（共通 + 銘柄別）

新しい `config/monthly_metrics.json` を置く。

```json
{
  "common": [
    {"name": "売上高", "kind": "sales", "aliases": ["売上"], "match": "suffix", "fill_scope_yoy": true},
    {"name": "客数",   "kind": "customers", "aliases": ["来客数", "来客"], "match": "suffix"},
    {"name": "出店",   "kind": "open",  "aliases": ["出店数", "開店", "開店数", "開設", "開設数", "新規"]},
    {"name": "退店",   "kind": "close", "aliases": ["退店数", "閉店", "閉店数", "閉鎖", "閉鎖数", "撤退", "撤退数"]}
  ],
  "issuers": {
    "46660": [{"name": "件数", "kind": "count"}, {"name": "台数", "kind": "count"}],
    "26540": [{"name": "受託施設数", "kind": "count"}, {"name": "訪問介護事業所数", "kind": "count"}]
  }
}
```

（例は抜粋。`match` は `exact` / `suffix` / `prefix`。今の `_METRIC_PREFIX` と `_METRIC_STARTSWITH` に当たる）

- **common** には、今コードにある語彙を移す: `_METRIC_PREFIX` / `_METRIC_STARTSWITH` / `_STORE_FLOW_CANON` / `_row_metric` の kind の割り当て / yoy でスコープしか無い行に `売上高` を補う規則の対象。国・チャネル・スコープ・落とす語（`_COUNTRY_CANON` / `_CHANNEL_CANON` / `_SCOPE_CANON` / `_DROP_TOKS`）も同じファイルの `layers` に移す
- **コードに残すもの:** 手順（`_unglue_element` の保護と復元、`_stitch_*` の順番、状態語＋店舗数の正規表現、領域の木）。語彙ではなく文法なので、データにすると読みにくくなる
- **issuers** は、その銘柄の canonicalize と label にだけ効く。そのために `canonicalize_element_label` / `monthly_element_label` / `parse_element_layers` に `code` を足す。呼び出しは 18 か所あり、どれも code を持っている。`api._headline_tier_of` の `lru_cache` のキーにも code を足す
- 件数語を拾う汎用の規則（末尾が `数`）は、**層の分解の最後の手段**としてだけ残す。辞書に無い 100 種類超を今すぐ全部書かなくて済むようにするためだ。保存名の並べ替えには使わない（#546 の今の動きを規則として明文化する）
- **保存名の語順の並べ替えは残す。** 対象は、共通の辞書とその銘柄の辞書にある指標だけにする。並べ替えをやめると 2,850 種がほぼ全部変わるので、やめない

### 3 つの config の役割分担

| ファイル | 問いに答える | 効くところ | DB を書き換えるか |
|---|---|---|---|
| `config/monthly_metrics.json`（新） | この語は指標か、何の種類か | 生の名前の正規化（新しい行）と `label` | しない。変更の影響はスナップショットの差分に出る |
| `config/issuer_aliases.json` | この生の名前（または旧名）をどの保存名にするか | 入ってくる名前の改名。期間（`until`）つき | しない。既存行の付け替えは `plan` / `apply` で別に行う |
| `config/monthly_row_order.json` | どの行がどの節に入り、どの順に並ぶか | year GET の `tree` / `tree_metrics`（表示だけ） | しない（今と同じ） |

`monthly_row_order.json` の `metrics` は、節の葉を横持ちの指標に束ねる表示の設定で、辞書とは別のものだ。今は辞書も兼ねて書かれているところがある（26540 の `受託施設数` など）。辞書へ移したあとも、`metrics` は並び順の指定として残す。

## 3. 既存行の付け替え: `plan` / `apply`

起動時の自動書き換えの代わりに、`jobs/monthly_names.py` を置く。

- `plan`: 全行に今の規則と alias を掛ける。変わる行・統合する行・値が衝突するセルを JSON で出す（`/tmp` か Actions の artifact）。読み取り専用
- `apply --plan <file>`: その計画どおりにだけ書く。計画を作ったときの各行の `(code, year, 保存名)` と値のハッシュを確かめ、違っていたら何も書かずに止まる
- 本番で動かす経路: GitHub Actions の `workflow_dispatch`「Monthly names」。`dry_run` が既定。ジョブのログに plan を出し、`confirm: apply` のときだけ書く。MCP に任意シェルは足さない
- alias を足す PR は、マージ後にこのワークフローで付け替える。今は起動時に勝手に付け替わっているが、それを明示の 1 手順にする

## 4. 移行手順（小さい PR に分ける）

1. **スナップショットとテスト**（動きは変えない）: `tools/monthly_names_snapshot.py` と `tests/fixtures/monthly_names.json`、`tests/test_monthly_names_snapshot.py` を足す。この PR だけで #534 型の変更は CI で止まる
2. **起動時の自動書き換えをやめる**: `init_db` から `merge_yoy_duplicate_elements` / `apply_issuer_element_aliases` を外し、検査のログだけにする。`fold_aliased_element` は完全一致にする。`jobs/monthly_names.py` と Actions のワークフローを足す。マージの前に本番で `plan` を流し、計画が空であることを確かめる（空でなければ、それは今、起動のたびに書き換わっている行）
3. **common を config に移す**: 純粋な置き換え。スナップショットの差分が 0 件であることが同値の証明になる
4. **issuers を足して `code` を通す**: 46660（件数・台数）と 26540 から始める。スナップショットの差分がその銘柄の `label` だけに出ることを確かめる
5. **正本を更新する**: `docs/DESIGN_monthly_labels.md` に「保存名は凍結」「辞書の場所」「plan / apply」を書く。`DESIGN.md` の `init_db` の記述と、`AGENTS.md` の月次の約束 1 行を直す。このファイルは消す

2 と 3 は独立しているので順番を入れ替えてもよい。1 は最初にやる。

## 5. 決めてほしいこと

1. **起動時の自動書き換えをやめ、保存名を凍結することでよいか。** 推奨: はい。いま、起動時の書き換えに助けられているのは、alias を足したときの付け替えだけだ。これは `plan` / `apply` で代わりがきく
2. **`apply` を本番で動かす経路。** 推奨: Actions の `workflow_dispatch`（dry-run が既定）。代わりは人が SSH で行う
3. **本番の保存名一覧（約 2,850 件。開示資料の行名だけで、値は入れない）をリポジトリにコミットしてよいか。** 推奨: はい。値を入れずに不動点だけを確かめるなら、スナップショットは不要になる。ただしその場合、`label` の変化（木の位置が変わる）は検出できない
4. **件数語を拾う汎用の規則を、層の最後の手段として残すか。** 推奨: 残す。やめる場合は、105 種類を銘柄別の辞書に書く必要がある
5. **銘柄別の辞書のファイル配置。** 推奨: `monthly_metrics.json` の `issuers` に 1 ファイルでまとめる。代わりは `issuer_aliases.json` に同居させること（キーが銘柄で同じなので）

## 6. やらないこと

- 既存の保存名の語順を変える、または一斉に改名する
- 指標の辞書から計算値を作る（寄与度など。月次に無い値は次の段階）
- `tests/test_monthly_ingest.py` に銘柄の分岐を足す（銘柄の検査はスナップショットと `plan_issuer_alias_cases` が JSON から作る）
