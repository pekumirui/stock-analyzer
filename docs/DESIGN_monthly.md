# 月次タブ

参照。見た目と操作の正本。取込と unit は `DESIGN.md`。実装の約束は `AGENTS.md`。書き方は `docs/SCREEN_SPEC.md`。

## 誰のための画面か

月次売上を業種グループで眺め、気になった発行体の年次表まで降りる人。

## 画面と遷移

月次タブは TOP から始まる。TOP はグループチップと発行体表。チップをクリックすると表を絞り込む。会社名をクリックすると Detail へ。Detail は戻る操作、系列切替つきの棒グラフ、その発行体の暦年表。戻ると TOP に戻り、直前のチップ選択を保つ。年のドロップダウンは無い。Detail に左の発行体一覧は置かない。

## データ形

`monthly_issuers` が発行体ごとの月次属性を持つ。`stocks.sector33` とは別物。

| 列 | 型 | 意味 |
|---|---|---|
| `code` | TEXT PK | `stocks.code` と同じ 5 桁。FK は付けない |
| `industry` | TEXT NOT NULL | 月次業種。既定は `その他` |
| `last_ingested_at` | datetime NULL | その code への ingest が成功するたびに更新 |

書き込みは先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`。既存の `monthly_tables` / `monthly_elements` / `monthly_values` は変えない。

**新着。** 仮想グループ。`last_ingested_at` の JST 日付が今日を含む直近 7 日。業種グループと重複してよい。JST の日付が今日なら、行に NEW を付ける。

**系列の対応。** PDF の `element` 名を ID にはしない。一覧とグラフは次の規則で行を拾う。`sort_order` が先の行を使う。

| 系列 | 条件 |
|---|---|
| 全店 | 名前に `全店` を含み `既存` を含まず、unit が `yoy_index` または `yoy_pct` |
| 既存店 | 名前に `既存` を含み、unit が `yoy_index` または `yoy_pct` |
| 店舗数 | 名前に `店舗数` を含み、unit が `number` |

最新セルは値のある最大の `(year, month)`。無いときは空。表示の 100=フラットは既存の `fmtMonthlyCell` と同じ。`yoy_index` はそのまま。`yoy_pct` は `100+v`。

AI による業種付けはしない。`industry` の更新は MCP と `PUT /api/v1/tables/{code}/industry`。SPA からは書かない。

## TOP

### 目的

グループを選び、その発行体の最新前年比を横並びで見る。

### 要素

`#monthly-groups` がチップ行。先頭は常に 新着。その後ろに、発行体が 1 社以上いる `industry` を日本語ロケールで並べる。件数 0 のチップは出さない。

`#monthly-tbl` の列は左からこの順。

| 列 | 出所 |
|---|---|
| 会社名 | `stocks.name`。今日 ingest なら NEW |
| 市場 | `stocks.market` |
| 業種 | `stocks.sector33` |
| 月次業種 | `monthly_issuers.industry` |
| 全店(前年比) | 系列マップの最新セル |
| 既存店(前年比) | 系列マップの最新セル |

コード列は出さない。行には `data-code` を付ける。

### 操作

チップをクリックすると、そのグループの行だけ残す。新着は `is_new` が真の行。業種チップは `industry` が一致する行。会社名をクリックすると Detail を開く。チップ以外の列は遷移しない。

### 空とエラー

月次表が 1 件も無いときは「登録された月次データはありません」。系列が無いセルは空白。一覧 API が失敗したらその文を出す。表は出さない。

### スマホと PC

同じ構造。チップは折り返す。表は横スクロール。幅 1100px で二段組にしない。

## Detail

### 目的

1 発行体の系列を月次で比べ、PDF の全項目を年ごとに読む。

### 要素

`#monthly-back` が TOP へ戻る。見出しは会社名と code。

`#monthly-series` は単一の `<select>`。既定の option は `MONTHLY_SERIES`（全店売上高(前年比) / 既存店売上高(前年比) / 店舗数）。その 3 系列に 1 行も当たらない発行体は、その発行体の実在する element 名を option にする。

`#monthly-chart` は年ごとのクラスタ棒グラフ。積み上げではない。X は 1月〜12月。系列は、選んだ指標に値が 1 つでもある暦年。既定の指標は 全店売上高(前年比)。前年比グラフは 100 に水平線を引き、100 未満の棒を目立たせる。店舗数に 100 線は無い。フォールバック時は選んだ行をそのまま描く。ライブラリは足さない。`static/index.html` 内の SVG または canvas。Y軸に目盛の数字を描く。左ガターに軸、下に月ラベルと年凡例。互いに重ねない。スマホでは viewBox を画面幅に合わせ、系列名・月・凡例・Y軸を他サイトの月次売上グラフと同じくらい読める大きさにする。

Detail は `.panel` 枠を付けない。グラフと表を同じカードに入れない。背景は白。main の左右余白は Detail では潰して横幅を取る。

`#monthly-table-wrap` は暦年ごとの表を新しい年が上になるよう重ねる。overflow-x は auto。各表の左上セルは暦年。赤地・白文字・太字。行は PDF の全要素。項目列は太字。セルは縦横罫線。ゼブラは付けない。選択中の系列行だけ `--row-sel`。1〜12月列は表示値の最長に揃えた同一幅（`--monthly-m-w`）。空セルと項目列は幅の根拠にしない。項目列は min-width で潰れない。PC の左端 th/td は sticky、不透明な白（暦年セルは赤）、z-index。月の数字がラベルの下や上に出ない。右端の月列は sticky にしない。スマホでは左端 sticky を外し、項目列を短くして月列が見えるようにする。横スワイプで月列が普通に動く。

### 操作

`#monthly-back` で TOP に戻る。直前のチップを保つ。グラフの指標は `#monthly-series` の切替、または年表の該当行をクリックして変える。年の選択 UI は置かない。

### 空とエラー

years が空なら「データなし」。選んだ系列に値が無い年はグラフから落とす。年表は残す。チャート用の 3 行が PDF に無くても、年表は全行のまま。その場合セレクトは実在の行名（ロボットホーム 14350 など）。

### スマホと PC

TOP と同じ。年表は横スクロール。項目列は min-width で潰れない。PC は左端 sticky。スマホは `@media (max-width:640px)` で左端 sticky を外し、項目列を短くする。右端の月列はピン留めしない。チャートの Y軸数字・系列名・月・凡例はスマホでも読める大きさ。二段組にしない。Detail のカード枠は無い。表の外も中も白。

## API

`GET /api/v1/tables` は `{code, name, years}` を維持し、次を足す。`market`, `sector33`, `industry`, `last_ingested_at`, `is_new`, `ingested_today`, `all_store_yoy`, `existing_store_yoy`。

`GET /api/v1/tables/{code}` と `GET /api/v1/tables/{code}/{year}` の形は変えない。Detail の年表は今どおり years を並列 GET する。

`PUT /api/v1/tables/{code}/industry` の body は `{industry}`。stocks に無い code、月次表が無い code は 4xx。

ingest は `monthly_issuers` を upsert し `last_ingested_at` を今にする。industry は既にあれば触らない。無ければ `その他`。

エラー包みは既存どおり `{"error": {"code": "...", "message": "..."}}`。

## 対象外

AI による月次業種の自動分類。業種マスタの固定リスト。SPA からの industry 編集。型の調査タブ。PDF 取込そのものの置き換え。新しいフロントライブラリ。
