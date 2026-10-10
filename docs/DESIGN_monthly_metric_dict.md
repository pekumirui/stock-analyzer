# 月次の行名: 銘柄別の指標辞書（設計案）

#545 の設計案。決まったら実装の PR で中身を `docs/DESIGN_monthly_labels.md`（正本）へ移し、このファイルは消す。関連: #533 / #534 / #546。

## 1. 何が起きたか

#533 は、末尾が `数` の語（受託施設数・総レセプト数など）が指標として分解されないという issue だった。#534 はこれを全銘柄に効く汎用の規則（`_is_generic_count_metric`。既存語に当たらない `…数` は件数指標）で直した。

この規則は保存名の組み立てにも効いた。たとえば `撮影件数 既存 売上高(前年比)` → `既存 売上高(前年比)`、`買上点数 全店 売上高(前年比)` → `全店 売上高(前年比)` になる（#534 のマージコミット 57380c0 で確認）。デプロイ後の起動時に `merge_yoy_duplicate_elements` が全行をこの規則で正規化し直し、23050 / 31740 / 81980 の 14 行を壊した（79 セル削除、33 セル改名）。#546 は保存名の組み立てからこの規則を外したが、層の分解（`label`）には残っている。

件数指標は銘柄ごとの語で、ほかの銘柄と共有するものがほとんど無い。2026-10-09 の本番コピーで `metric_kind=count` になる指標は **42 銘柄・95 語**ある。複数の銘柄に出るのは `管理戸数` / `営業日数` / `契約社数` / `保有契約件数` / `件数` くらいで、残りは 1 銘柄だけで使っている（`受託施設数`、`ATM設置台数`、`CFD取引口座数` …）。それを推測で拾う汎用の規則を全銘柄に掛けたことが、事故の起点だった。

## 2. 方針

- 件数指標は **銘柄別の辞書** に書く。その銘柄の行名を分解するときだけ効く
- 汎用の規則（末尾が `数` なら件数）は **消す**
- 銘柄別の辞書は **層の分解（`label` と木）にだけ効かせる**。保存名（`canonicalize_element_label`）は読まない。だから辞書に語を足しても保存名は変わらず、起動時の `merge_yoy_duplicate_elements` は何もしない

共通の語彙（売上高・店舗数・客数・客単価・出店・退店など。`_METRIC_PREFIX` / `_STORE_FLOW_CANON`）は今のままコードに置く。

## 3. 置き場所と形

新しいファイル `config/issuer_metrics.json` に置く。

```json
{
  "26540": ["受託施設数", "訪問介護事業所数", "居宅介護支援事業所数", "有料老人ホーム数"],
  "46660": ["件数", "台数", "貸出拠点数"],
  "70620": ["総レセプト数(鍼灸込み)", "看護小規模多機能型居宅介護施設・ホスピス数"]
}
```

- キーは J-Quants の 5 桁コード（`issuer_aliases.json` / `monthly_row_order.json` と同じ）
- 値は指標語の並び。`metric_kind` はすべて `count`（店舗数の `stores`、客数の `customers` などは共通の語彙のまま）
- 照合はトークンの完全一致。トークンの区切りは今の `_unglue_element` に従う。前方一致・後方一致はしない（`売上個数` が `個数` に当たらないように）
- 末尾の注記（`(注)` / `（注）`）は今と同じく剥がしてから照合する

`issuer_aliases.json` に同居させない。あちらは期間（`until`）つきの改名で、書き込みと既存行の付け替えに効く。辞書は表示にだけ効くので、混ぜると「足したら DB が動くのか」が読み取れなくなる。

| ファイル | 役割 | 保存名を変えるか |
|---|---|---|
| `config/issuer_aliases.json` | 生の名前・旧名 → 保存名（期間つき） | 変える |
| `config/issuer_metrics.json`（新） | この銘柄で指標として読む語 | 変えない（`label` と木だけ） |
| `config/monthly_row_order.json` | 節と並び（`tree` / `metrics`） | 変えない |

## 4. コードの変更

- `monthly_ingest.py`
  - `_is_generic_count_metric` / `_COUNT_METRIC_EXCLUDE` / `_KNOWN_COUNT_METRIC_TOKS` を消す。`_peel_metric` / `parse_element_layers` の `peel_generic_count` 引数も消す
  - `parse_element_layers(element, *, code=None)` にする。code があれば、`ISSUER_METRICS[code]` に完全一致するトークンを指標にする（`_METRIC_PREFIX` に当たらなかったとき）
  - `monthly_element_label` / `monthly_series_kind` / `_layers_of` と、そこから呼ばれる `monthly_row_*` に `code` を通す
  - `normalize_element_tokens` / `canonicalize_element_label` は code を受け取らない（今と同じ）。これで「辞書は保存名に効かない」がコードの形で保証される
  - `拠点数` の見出し落とし（#546。別指標があるときだけ落とす）は保存名の規則なので残す
- `api.py`: `monthly_element_label(e.element, unit)` の 2 か所に `e.code` を渡す。`_headline_tier_of` の `lru_cache` のキーに code を足す
- `monthly_tree.py`: `monthly_element_label` / `union_monthly_elements` の呼び出しに code を渡す（自動の木の組み立ては code を持っている）

## 5. 移行

辞書の初期値は、今の汎用の規則が `count` にしている 42 銘柄・95 語を本番から機械的に書き出して作る（`tools/` の使い捨てではなく、PR の中で一度だけ生成する）。これで、規則を消しても全行の `label` は変わらない。

確かめ方:

1. 本番コピーの全 (code, 保存名, unit) について、変更の前後で `monthly_element_label` が一致する
2. 全保存名について `canonicalize_element_label` が変わらない（保存名に触れていないこと）
3. 銘柄の分岐はテストに書かない。`tests/test_monthly_ingest.py` には、辞書の仕組み（code の有無で層が変わる、完全一致だけ、保存名は変わらない）を架空のコードで確かめるテストを足す
4. 既存の `test_generic_count_metric_peel` / `test_count_metric_keeps_pre534_stored_names` は、辞書の前提に書き直す。保存名の期待はそのまま

## 6. 運用

- 新しい銘柄で件数指標が出たら、`config/issuer_metrics.json` にその語を足す。足すまでは今の #533 以前と同じで、語はセグメントに残る（木では会社の下に行名の葉）。DB は壊れない
- `monthly-html-ops-ingest` の手順書に「件数の行を入れたら辞書に足す」を 1 行足す
- 正本の `docs/DESIGN_monthly_labels.md` の指標の行（「末尾が `数` の件数」）と `…数` の箇条を、辞書の説明に置き換える

## 7. この案でやらないこと

- 共通の語彙を config に移すこと
- 起動時の自動書き換え（`merge_yoy_duplicate_elements` / `apply_issuer_element_aliases`）をやめること、保存名のスナップショット

共通の語彙を変えると、今後も全銘柄の保存名に効き、起動時に既存行が書き換わる。この経路は残る。件数指標がその語彙から外れるので、#534 のように 1 銘柄の都合で共通の語彙を触る機会は減る。必要になったら別 issue にする。
