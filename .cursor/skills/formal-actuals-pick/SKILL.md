---
name: formal-actuals-pick
description: "同じ期の実績（BS・PL・CF、決算速報の数字、売上総利益）が複数の出どころ（有報・短信 XBRL・短信 PDF）と複数の版（元・訂正・一部だけの訂正）で届いたとき、項目ごとにどの値を出すかの規則を TLA+ で全順序検査する。app/xbrl/three_statements.py の build_columns・_newest・_pdf_base・_revisions・_revised・_fills・SOURCES・PDF_FULL_RATIO、app/market/analytics.py の find_statement、app/xbrl/gross_profit.py の _pick を変えたとき、または「訂正したのに業績詳細の数字が古い」「訂正の後に科目が消えた」「伸び率が訂正前の値」を調べるときに使う。"
---

# formal-actuals-pick

1 銘柄の 1 つの期について、書類が何本、どの順で、どんな欠け方で来ても、項目ごとに出す値が「最後にその項目を言った書類の値」になるかを、TLC で全部の並びについて確かめる。

- 約束: 有報がその項目を言っていれば、最後に言った有報の値。言っていなければ、短信（XBRL と PDF を合わせて提出順）で最後に言った書類の値。`statements` の行は、その期の決算短信を古い順に畳んだ値。値のある項目はその行の値、空の項目は、その行で値のある項目がそれまでの値とすべて同じならそれまでの値、1 つでも違えば空
- 破れたときの害: 業績詳細の三表で訂正前の値が出る・科目が消える。決算速報・概要・株価チャートの伸び率が訂正前の値で計算される・EPS が空になる
- 模型は「保存された行が正しい」前提で、選び方だけを見る。読み違い・取込漏れは別の検査（IR BANK のゴールデン、J-Quants との突き合わせ、BS の合計の検査）

## 走らせる

```sh
tools/formal/check.sh actuals_pick MCThree.cfg
tools/formal/check.sh actuals_pick MCRow.cfg
tools/formal/check.sh actuals_pick MCGross.cfg
tools/formal/check.sh actuals_pick FindingPartialXbrl.cfg
tools/formal/check.sh actuals_pick FindingPdfAfterXbrl.cfg
tools/formal/check.sh actuals_pick FindingBigPartialPdf.cfg
tools/formal/check.sh actuals_pick FindingOrphanPartialPdf.cfg
tools/formal/check.sh actuals_pick FindingPartialRow.cfg
tools/formal/check.sh actuals_pick FindingSameDayRow.cfg
tools/formal/check.sh actuals_pick FindingSameDayGross.cfg
```

- Java と `tla2tools.jar` は [formal-tdnet-pdf-retire](../formal-tdnet-pdf-retire/SKILL.md) と同じ
- どれも 10 秒以内。模型は `tools/formal/actuals_pick/ActualsPick.tla`

今のコードでの正しい結果:

| cfg | 期待 | 意味 |
|---|---|---|
| `MCThree.cfg` | `No error has been found`（152,527 distinct states） | 三表の回帰検査。書類 6 本まで |
| `MCRow.cfg` | `No error has been found`（50,065 distinct states） | `find_statement` の回帰検査。行 4 本まで。空欄のある行と同じ日の 2 本目を含む |
| `MCGross.cfg` | `No error has been found`（71,076 distinct states） | 売上総利益の回帰検査。開示 5 本まで |
| `FindingPartialXbrl.cfg` | `Matches` 違反 | 既知の限界 1 |
| `FindingPdfAfterXbrl.cfg` | `Matches` 違反 | 既知の限界 2 |
| `FindingBigPartialPdf.cfg` | `Matches` 違反 | 既知の限界 3 |
| `FindingOrphanPartialPdf.cfg` | `Matches` 違反 | 既知の限界 4 |
| `FindingPartialRow.cfg` | `No error has been found` | 直した不具合（KAB-571） |
| `FindingSameDayRow.cfg` | `No error has been found` | 直した不具合（KAB-570） |
| `FindingSameDayGross.cfg` | `Matches` 違反 | 既知の限界 5 |

Finding の cfg が通るようになったら、その食い違いが直ったということ。直した変更の PR で、その cfg の期待を「違反なし」に書き換え、下の一覧から外す。回帰検査に入れたいなら、`MC*.cfg` の対応する `Allow*` を `TRUE` にする。

反例を読むときは pstack の `/statechart` スキルを使う。

## 模型とコードの対応

`View` で 3 つの場所を切り替える。`seq` は 1 銘柄・1 つの期（三表は 1 つの連結区分・1 つの表）の書類を、本当に出た順に並べたもの。

| 模型 | コード |
|---|---|
| `View = "three"` | `build_columns` の 1 列（1 つの期末・1 つの表） |
| `src = "edinet"` / `"tdnet"` / `"pdf"` | `xbrl_filings.source` の `edinet` / `tdnet` / `tdnet_pdf`。優先は `SOURCES` の順 |
| `has` | その書類のその表に値がある項目。`Items`（2 項目）が全部入り |
| `rows` | その表の行数。`10` が全部入り、`8` が行数 7 割以上の一部だけの訂正、`2` が 7 割未満の一部だけの訂正。`PDF_FULL_RATIO` の判定に使う |
| `XbrlCode` | 同じ出どころで `_newest` の 1 本だけを使う |
| `PdfCode` | `_pdf_base`（行数が最大の 7 割以上で最新）を土台に、`_revisions`（土台より後）を新しい順に `_revised` で重ねる |
| `ThreeCode` | 上位の出どころに無い項目だけを下位で埋める |
| `ThreeSpec` | あるべき値。有報の中で最後に言った値、無ければ短信（XBRL と PDF を合わせて提出順）で最後に言った値 |
| `View = "row"` | `find_statement` が 1 つの期（年度末・四半期）について選ぶ行。`seq` は `statements` の決算短信の行で、J-Quants と XBRL を合わせた後の値 |
| `slot` | `disclosed_date`。同じ `slot` の中の並びは `disclosure_no` の昇順（履歴の並び） |
| `RowCode` | 開示日・開示時刻・開示番号の順で最後の行。空の項目は `carried_values` で畳んだ値（返すのは `CarriedStatement`。行は書き換えない）。発表日は最初の決算短信の日で、模型には入れていない |
| `RowSpec` | `RowFold`。行を古い順に畳み、値のある項目はその行の値、空の項目は、その行の値のある項目がそれまでの値とすべて同じならそれまでの値、違えば空 |
| `View = "gross"` | `_pick` が 1 つの開示（`src = "tanshin"` の各行）に付ける売上総利益。`has = {"a"}` はその開示の短信の filing に売上総利益がある |
| `GrossCode` | その開示の filing、無ければ開示日以前（その日の終わりまで）で最新の短信、無ければ最初の EDINET |
| `GrossSpec` | その開示までで最後に売上総利益を言った短信、無ければ最初の EDINET |

`CanAppend` は現実にありうる並びだけに絞る前提で、`Allow*` は食い違いを起こす並びを入れるかどうか。

| 定数 | 真にすると入る並び |
|---|---|
| `AllowPartialXbrl` | 表の一部の項目だけを持つ有報・短信 XBRL |
| `AllowPdfAfterXbrl` | 短信 XBRL の後に出た短信 PDF |
| `AllowBigPartialPdf` | 行数が 7 割以上ある、一部だけの訂正の PDF |
| `AllowOrphanPartial` | 同じ系統（有報 / 短信）に全部入りの書類が無いうちに来た、一部だけの訂正 |
| `AllowPartialRow` | 空欄のある `statements` の行 |
| `AllowSameDay` | 同じ開示日の 2 本目（`row` は決算短信の行、`gross` は短信の開示） |

ほかの前提: 項目は 2 つ、値は 2 種類。1 本の書類は言う項目すべてに同じ値を言う。全部入りの書類はどの項目も持つ（訂正で科目が無くなることは入れていない）。三表は提出時刻が全部違う（`_newest` の `id` での決着は入れていない）。有報が言った項目は、後から短信の訂正が来ても有報の値（`docs/jobs/xbrl.md` の優先のとおり。本番で有報より後に出た短信は XBRL 88 件・PDF 871 件）。`_fills` の BS の制限（主の表示リンクに無い要素は補わない）、PDF の行と XBRL の要素の対応付け、連結と個別の継ぎ合わせ、会計基準での表の分割、決算期変更、`find_statement` の年度末の近傍探しと `is_revision` の後回し、`merge_into` と `upsert_statements` の取込順、同じ開示の ZIP と PDF の両立（[formal-tdnet-pdf-retire](../formal-tdnet-pdf-retire/SKILL.md)）は入れていない。

`RowSpec` の空欄の読み方は KAB-571 で決めた規則で、J-Quants の仕様からは出てこない。`/v2/fins/summary` の仕様は、空文字列は「値の記載がなかったことのみを示す」もので、前回からの変更の有無を表さないと書いている。訂正であることを示す項目も無い。値のある項目が元と同じなら空欄は載せていないだけ（`95560` の訂正はサマリーの無い ZIP から作った行で、本文の EPS は元と同じ 54.75）、違えば空欄の項目も変わりうる（古い EPS を新しい利益と混ぜない）、と読む。模型の「同じ」は値が等しいこと。コードは金額の差 100 万円未満、EPS の差 0.01 未満を同じとみなす（百万円と千円の単位の違い）。

同じ期の 2 本目の行は訂正とは限らない。2025 年以降の決算短信の 2 本目 1,159 行のうち、TDnet の題名に「訂正」「修正」があるのは 653 行。ほかは期中レビュー完了後の出し直し（数字はたいてい同じ）と数値データの追加（元の短信に XBRL が無かった）。模型は 2 本目の種類を区別しない。

模型では見えない誤りの例: 13320 の 2025/03 の訂正短信 `20250521559959` は「数値データの訂正はありません」なのに、J-Quants の行の `NP` が非支配株主の分を含む 272.13 億（正しくは 253.81 億）で、EPS などが空。行そのものの誤りなので、一覧で捨てて直す（KAB-573）。

## 性質

- `Matches`: コードが出す値が、あるべき値と同じ（`three` と `row` は項目ごと、`gross` は開示ごと）

## 直した不具合（2026-11-25、KAB-568 で発見し、KAB-570・KAB-571 で直した。どれも本物の関数に反例の並びを流して再現した）

1. **訂正の行の空欄で値が消える**（`FindingPartialRow.cfg`、KAB-571）: `find_statement` は新しい行を丸ごと選んでいたので、訂正の行で空の欄は、元の短信に値があっても空になる。本番で 71 組（J-Quants の行 37、短信 XBRL から作った行 34）。ほとんどが EPS。95560 の 2026/07 は EPS 54.75・BPS 706.44 が空になる。銘柄の概要の BPS・自己資本比率（`app/api/stock.py` の `_latest_with_date`）は値のある行まで遡るので起きない
2. **同じ日の 2 本目が選ばれない**（`FindingSameDayRow.cfg`、KAB-570）: 同じ開示日に同じ期の決算短信が 2 本あると、先に出た方を選んでいた。本番で 110 組、うち数字が違うのは 9 組。81420 の 2023/01 は売上 2,155 億でなく 1,885 億になっていた

## 既知の限界（コードは模型のとおり動くが、本番にその並びが無いか、上位の出どころに隠れている）

1. **一部の項目だけの XBRL が最新になると、載っていない項目が消える**（`FindingPartialXbrl.cfg`）: XBRL は同じ出どころの最新 1 本だけを使い、PDF のようには重ねない。本番では、同じ期に 2 本以上ある組（短信 XBRL 69、有報 7,586）で、最新の書類の事実が古い書類の 7 割未満なのは、本表の事実がほぼ無い訂正だけ（有報 6 組、短信 XBRL はサマリーだけの訂正 8 本）。その表に事実が無い書類は列の候補に入らないので、起きていない。XBRL の訂正は表を丸ごと載せ直している
2. **短信 XBRL の後に出た訂正の PDF は使われない**（`FindingPdfAfterXbrl.cfg`）: 短信 XBRL にある項目は、後から出た PDF の値で置き換わらない。本番で 0 件（訂正に ZIP があれば PDF の filing は作らない。`statements` に行の無い訂正は PDF を読まない）
3. **行数が 7 割以上ある一部だけの訂正の PDF は土台になり、載っていない行が消える**（`FindingBigPartialPdf.cfg`）: 本番で、土台が最大行数より 3 行以上少ない訂正の PDF は、調べた範囲（書類全体の事実が最大の 9 割未満の 62 組）で 3 表（35490 の 2023/05 の CF 37/47 行、57210 の 2025/03 の BS 50/57 行、76190 の 2024/03 の CF 31/41 行）。どれも有報がある期。書類全体が 9 割以上の 77 組は表ごとに見ていない
4. **元の PDF が無く、一部だけの訂正の PDF が 2 本あると、古い方の行が消える**（`FindingOrphanPartialPdf.cfg`）: 本番で 6 組、どれも有報がある期
5. **その開示に売上総利益が無いと、同じ日に後から出た短信の値が付く**（`FindingSameDayGross.cfg`）: `_pick` は開示日の終わりまでの短信を見る。本番の件数は数えていない

限界 3・4 は有報の無い期（2024 年 4 月以降の 1Q・3Q、有報が出る前の本決算）で起きれば画面に出る。

## 本物の不具合か、模型の誤りか

- `three` の反例は、各書類を `three_statements.Filing`（`source`・`submitted`・`by_statement` に `Fact` の並び）に置き換えて `build_columns` に流し、列の `values` を `ThreeSpec` と比べる。PDF だけの列は `pdf:` のキーのままで比べられる
- `row` の反例は、各行を `SimpleNamespace`（`disclosure_no`・`disclosed_date`・`fiscal_year_end`・`type_of_period`・`is_revision`・`METRIC_KEYS` の欄）に置き換えて `find_statement` に流す
- `gross` の反例は、候補を `SimpleNamespace`（`source`・`doc_id`・`submitted_at`・`value`）に置き換えて `_pick` に流す
- 同じ並びが本番にあるかを `xbrl_filings`（同じ `code`・`fiscal_year_end`・`source`）と `statements`（同じ `code`・`fiscal_year_end`・`type_of_period`）で数える。`xbrl_facts` を全件なめる集計は時間切れになるので、銘柄で分けるか、対象の filing を先に絞る
- 違わなければ模型の誤り。模型を直す。性質を弱めて通さない

## コードを変えたら

1. 変更が `description` に挙げた関数に触れるかを見る。触れなければ何もしない
2. 触れるなら、`ThreeCode`・`RowCode`・`GrossCode` を変更に合わせて直す。新しい出どころは `Filings` と `ThreeCode` の順に足す
3. 10 本の cfg を流し、上の表の期待と比べる。`MC*.cfg` が落ちたら変更が約束を壊したので、反例をコードの言葉で報告する
4. 模型がコードを正しく写していると確かめたら、下の同期コミットを進める

## 過去データ

- 三表は読むたびに `xbrl_facts` から組み直すので、規則を直せば過去の表示もすぐ直る
- `statements` の行と `xbrl_financials` は取込時に作って保存する。行の作り方を直したら作り直す（`xbrl_normalize --rebuild`、`xbrl_statements --since`）。`find_statement` の選び方だけを直すなら作り直しは要らない

## 同期

- 対象: `app/xbrl/three_statements.py`（`build_columns` と `description` に挙げた関数）、`app/market/analytics.py`（`find_statement`）、`app/xbrl/gross_profit.py`
- 最後に模型とコードの一致を確かめたコミット: `8973649`
- `git log 8973649..HEAD -- app/xbrl/three_statements.py app/market/analytics.py app/xbrl/gross_profit.py` に何か出たら、模型を見直してから同期コミットを進める
