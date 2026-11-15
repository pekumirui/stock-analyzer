---
name: verify-monthly-label-fixed-point
description: "月次の行名の正規化（canonicalize_element_label）、銘柄エイリアス（issuer_aliases.json）、月次の語彙（monthly_vocab.json / issuer_metrics.json）、月次 API の行名の扱い（_canonical_element / _year_alias / _lookup_element）を変えたときに使う。保存した行名を読み直しても同じ名前に戻ること（不動点）を、語彙の語の組み合わせを全部流して確かめる。"
---

# 月次の保存名が不動点かを確かめる

## 何を守っているか

- 約束: 月次の行を保存した名前は、同じ銘柄・同じ暦年でもう一度正規化とエイリアスに通しても、同じ名前に戻る。
- 破れたときの害: PUT / DELETE がその行を見つけられない。`jobs/monthly/monthly_names.py apply` が行名を付け替え、次の取込で元の名前の行がまた出来て、同じ系列が 2 行に割れる。

テストは書いた例しか見ない。この検証は、語彙にある語の 1 語と 2 語の組み合わせを全部流す。

## 道具

検証器ではなく、本物のコードに入力を全部流す。Python の文字列処理（正規表現と JSON の語彙）をそのまま検証できる道具が無いため。手で書いた模型は使わない。`tools/monthly/label_fixed_point.py` が `app/api/monthly/_common.py` の関数をそのまま呼ぶ。足した依存は無い。

## いつ流すか

次のどれかに触れたら流す。

| ファイル | 見ている所 |
|---|---|
| `app/monthly/ingest.py` | `canonicalize_element_label` / `normalize_element_tokens` / `parse_element_layers` とそこから呼ぶ `_peel_*` / `_stitch_*` / `_unglue_element` / `strip_yoy_fragments`、`load_issuer_aliases` / `apply_issuer_aliases`、`coerce_monthly_storage`、`reject_prior_period_element`、語の表（`_VOCAB` から作る定数、`_FLOW_WORDS`、`_STATUS_STORE_SPACED`、`_AREA_TREE`、`_ISSUER_STATUS_STORE_SEGMENT`、`_ISSUER_SEGMENT_AS_BREAKDOWN` など） |
| `app/api/monthly/_common.py` | `_canonical_element`（POST と ingest の保存名）、`_year_alias`（ingest の列の暦年）、`_lookup_element`（PUT / DELETE が行を探す名前） |
| `app/api/monthly/ingest.py` | `_canonical_element(as_of=)` のあとに `_year_alias` を掛ける順番 |
| `app/api/monthly/elements.py` | POST が `_canonical_element(year=)`、PUT / DELETE が `_lookup_element` を使うこと |
| `app/monthly/maintenance.py` | `monthly_name_drift` の式（スクリプトの `drift_name` が同じ式を写している） |
| `config/monthly/monthly_vocab.json` / `issuer_aliases.json` / `issuer_metrics.json` / `monthly_row_order.json` | 語とエイリアス |

最後にコードと合っていると確かめた commit: `a2916f7`。それより後に上のファイルが変わったかは次で見る。

```bash
git log --oneline a2916f7..HEAD -- app/monthly/ingest.py app/api/monthly/_common.py app/api/monthly/ingest.py app/api/monthly/elements.py app/monthly/maintenance.py config/monthly
```

何か出たら、その変更がこの検証を通っているか分からない。下の「検証をコードに合わせる」をやってから結果を信じる。

## 流し方

repo の根で。約 1 分。

```bash
.venv/bin/python tools/monthly/label_fixed_point.py
```

| 終了コード | 意味 | すること |
|---|---|---|
| 0 | 反例は既知のものと同じ | そのまま進む |
| 1 | 新しい反例がある | 下の「新しい反例が出たら」 |
| 2 | 既知の反例が消えた | 直したつもりなら `--update` で既知を書き直してコミット。直したつもりが無ければ、入力が減っていないか（語彙から語を消した、API が断るようになった）を見る |

1 つの入力がどう動くかは `--show` で見る。

```bash
.venv/bin/python tools/monthly/label_fixed_point.py --show 閉鎖店数売上 --unit number
.venv/bin/python tools/monthly/label_fixed_point.py --show 一品単価単価 --unit yoy_index --code 27910
```

出力は「どの道で保存したか: 保存名 → lookup で読み直した名前 / drift で読み直した名前」。`✗` が付いた所が戻らなかった所。

## 新しい反例が出たら

出力の 1 行はこう読む。

```
unit=number 入力 '閉鎖店数売上' → POST year=2026 で保存 '閉鎖 店舗数 売上高' → lookup で読み直すと '退店 売上高'
```

「`閉鎖店数売上` を unit=number で POST すると `閉鎖 店舗数 売上高` で保存される。その行を PUT で探すと `退店 売上高` を探しに行くので見つからない」。

本物のバグか、検証のほうのずれかはこう分ける。

1. `--show` で同じ入力を流し、再現するか見る。
2. 保存名を作る道（POST / ingest）と読み直す道（lookup / drift）が、今のコードでも「いつ流すか」の表のとおりか開いて確かめる。API が道を変えた（例: `_lookup_element` を使わなくなった）のにスクリプトが古い道を呼んでいるなら、検証のずれ。スクリプトを直す。
3. 道が合っていれば本物。入力が月次資料にまず出ない語の並びでも、反例は反例として扱う。約束を弱めたり、その入力を外したりして通さない。

本物なら、自分の変更を直す。変更が意図どおりで、反例を残すと決めたなら、人に反例を見せて了承を得てから `--update` で既知に入れる。既知に入れた理由は commit に書く。

エイリアスを足したときによく出る形: 付け替え先（dst）が正規化でさらに変わる名前になっている。dst を `--show <dst> --code <コード>` に通し、保存名がそのまま戻る名前にする。

## 既知の反例

`tools/monthly/label_fixed_point_known.jsonl`。1 行目は「number の行名に前年比の語」の類があるか、2 行目からは反例 1 件ずつ（`stored` が保存名、`reread` が読み直した名前、`input` がいちばん短い入力）。手で編集しない。`--update` で書き直す。

今ある反例は 2 種類。どちらも本番の `monthly_elements` には当たる行が無い（2026-11-15 に確認）。

1. unit=number の行名に「前年比」の語が残る。例: `前年比` を unit=number で POST すると `前年比` で保存され、PUT / DELETE は名前から unit を yoy と決めて `(前年比)` を探す。`_lookup_element` が保存した unit を見ないのが原因。件数が多く原因が 1 つなので、類としてまとめて数える。
2. POST で保存した名前が、もう一度通すと変わる（219 の保存名、number と前年比で 438 件）。ほとんどは状態付きの店舗数の語が 2 つ並ぶ入力（例: `閉店中店数閉鎖店数` → `閉店中 退店` → `中 退店`）と、`閉鎖店数` に指標がくっついた入力（`閉鎖店数売上`）。銘柄別は 27910 の `一品単価単価` と 75220 の `調理済み商品お届け数（千食）` の重なり。ingest の道は `_year_alias` でもう 1 回正規化するので、同じ入力でも反例にならない。

## 確かめている範囲

- 入力: 語彙の語（`common_words()` が `app/monthly/ingest.py` の表から集める）とふつうの語 7 つ・注記の類 10 個の、1 語と 2 語（空白あり・くっつき）。銘柄別は、その銘柄の指標語・エイリアスの行名とその語。
- unit: `number` / `yoy_index` / `yoy_pct`。
- 銘柄: `issuer_metrics.json` / `issuer_aliases.json` / `monthly_row_order.json` と銘柄別の表にある全銘柄、と銘柄指定なし。
- 暦年と as_of: エイリアスの `until` の境目の前後を全部。ingest は as_of の年までの列だけ。

確かめていないもの:

- 3 語以上の入力。語彙に無い語（ふつうの語 7 つ以外）。
- エイリアスの行名の語と共通の語の組み合わせ（銘柄の指標語とは組み合わせる）。
- `app/api/monthly/elements.py` の unit を変える PUT の付け替え、`same_yoy_element` などの行の寄せ、`monthly_element_sources`。
- `jobs/monthly/html_issuers/` が API の手前で掛ける行名の付け替え。
- DB の中身。今ある行のずれは `jobs/monthly/monthly_names.py plan` が見る。

通ったら「この範囲の入力では戻る」であって、どんな行名でも戻るという証明ではない。

## 検証をコードに合わせる

変更が意図どおりで、検証のほうを変える必要があるとき。

- 語彙の表を `app/monthly/ingest.py` に足した: `common_words()` か `issuer_names()` がその表を読んでいるか見て、読んでいなければ足す。
- 保存名を作る道・読み直す道を変えた: `written_names()` / `reread()` / `drift_name()` を今のコードの道に合わせる。
- 合わせたら、わざと壊して検証が終了コード 1 で落ちることを 1 回見る。見たら壊した所を戻し、終了コード 0 に戻ることも見る。落ちると確かめてある壊し方:
  - `app/api/monthly/_common.py` の `_canonical_element` から、エイリアスのあとの `canonicalize_element_label` を外す（新しい反例が数千件）。
  - `config/monthly/issuer_aliases.json` の 141A0 で、`until` が早いほうに `検証B 売上高` → `検証C 売上高`、遅いほうに `検証A 売上高` → `検証B 売上高` を足す（付け替え先がさらに付け替わる。新しい反例 1 件）。
- 検証が今のコードを表していると確かめられたら、このファイルの「最後にコードと合っていると確かめた commit」とその下の `git log` の commit を、確かめた commit に進める。確かめずに進めない。

ずれが大きいときは `/pstack:maintain-formal-verification-skill`。
