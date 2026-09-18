# 月次タブ

参照。見た目と操作の正本。取込と unit は `DESIGN.md` §3。行名は [`DESIGN_monthly_labels.md`](DESIGN_monthly_labels.md)。書き方は `docs/SCREEN_SPEC.md`。

## 誰のための画面か

月次売上を業種グループで眺め、気になった発行体の年次表まで降りる人。

## 画面と遷移

- 月次タブは TOP から始まる。TOP は検索欄、グループチップ、発行体表
- 検索欄に名前、コード、または業種名を入れると、月次登録発行体だけが候補になる。Enter または候補クリックで表を絞り込む。会社名をクリックすると Detail へ
- チップをクリックすると検索絞り込みを外し、そのグループの行だけ残す
- Detail は戻る操作、系列切替つきの棒グラフ、その発行体の暦年表。左の発行体一覧は置かない。年のドロップダウンは無い
- 戻ると TOP に戻り、直前のチップ選択と検索絞り込みを保つ
- 再読込は直近のタブを開く。Detail を見ていたらその発行体の Detail に戻る

## データ形

`monthly_issuers` が発行体ごとの月次属性を持つ。`stocks.sector33` とは別物。

| 列 | 型 | 意味 |
|---|---|---|
| `code` | TEXT PK | `stocks.code` と同じ 5 桁。FK は付けない |
| `industry` | TEXT NOT NULL | 月次業種。既定は `その他` |
| `last_ingested_at` | datetime NULL | その code への ingest が成功するたびに更新 |

書き込みは先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`。既存の `monthly_tables` / `monthly_elements` / `monthly_values` は変えない。
ingest の列割り当てと暦年は `DESIGN.md` §3。

**新着。** 仮想グループ。`last_ingested_at` の JST 日付が今日を含む直近 7 日。業種グループと重複してよい。JST の日付が今日なら、行に NEW を付ける。

**系列の対応。** PDF の `element` 名を ID にはしない。一覧とグラフは次の規則で行を拾う。`sort_order` が先の行を使う。

| 系列 | 条件 |
|---|---|
| 全店 | 名前に `全店` または `全社` を含み `既存` を含まず、unit が `yoy_index` または `yoy_pct` |
| 既存店 | 名前に `既存` を含み、unit が `yoy_index` または `yoy_pct` |
| 店舗数 | 名前に `店舗数` を含み、unit が `number`。純増/新設/改装/業態転換は除く |

最新セルは値のある最大の `(year, month)`。無いときは空。表示の 100=フラットは保存の `yoy_index` のまま。
年表の number は、表示単位がある指標だけ四捨五入（`fmtMonthlyCell`）。単位は項目名（`fmtMonthlyElement`）。JSON と保存名は単位なし。
その年に値が1つも無い行は年表に出さない。系列セレクトは年をまたいだ行を残す。

**行名。** 保存と正規化の正本は [`DESIGN_monthly_labels.md`](DESIGN_monthly_labels.md)。画面の行キーは `fmtMonthlyElement`。
年ごとの保存名が違っても表示名が同じなら1行。その年の値は、その年の保存 element のうち表示名が一致するものから取る。以下「表示名で畳む」と呼ぶ。

AI による業種付けはしない。`industry` の更新は MCP と `PUT /api/v1/tables/{code}/industry`。SPA からは書かない。

## TOP

### 目的

グループを選び、その発行体の最新前年比を横並びで見る。

### 要素

`#monthly-sym` と `#monthly-suggest` をチップより上に置く。プレースホルダは「銘柄名 or コード」。候補行は 4 桁コードと正式名称。候補は月次登録発行体だけ。

`#monthly-groups` がチップ行。先頭は常に 新着。その後ろに、発行体が 1 社以上いる `industry` を日本語ロケールで並べる。件数 0 のチップは出さない。

`#monthly-tbl` の列は左からこの順。独立したコード列は出さない。行には `data-code` を付ける。セルは縦横罫線。表は列の中身の幅（空列で画面を埋めない）。

| 列 | 出所 |
|---|---|
| 会社名 | code + 半角スペース + 会社名の先頭 5 文字。今日 ingest なら NEW。このセルだけ Detail へ |
| 市場 | プライムは 東証P、スタンダードは 東証S、グロースは 東証G。先頭に色付き記号 |
| 業種 | `stocks.sector33`。末尾の「業」を外し先頭 5 文字 |
| 月次業種 | `monthly_issuers.industry` |
| 全店(前年比) | 系列マップの最新セル |
| 既存店(前年比) | 系列マップの最新セル |

### 操作

入力中は `#monthly-suggest` だけ更新する。表は変えない。Enter は入力文字列を確定し、表を絞り込む。候補をクリックすると入力を正式名称にして同じ絞り込みをする。どちらも TOP に留まる。空の Enter は絞り込みを外し、チップの表に戻す。

照合は折りたたみ後に、名前の部分一致、コード 4 桁または 5 桁の前方一致、月次業種 `industry` の部分一致、`sector33` の部分一致。折りたたみは NFKC とひらがな→カタカナ。IME の compositionend でも候補を更新する。候補は最大 20 件。

チップをクリックすると検索絞り込みを外し、そのグループの行だけ残す。新着は `is_new` が真の行。業種チップは `industry` が一致する行。会社名をクリックすると Detail を開く。チップ以外の列は遷移しない。

### 空とエラー

月次表が 1 件も無いときは「登録された月次データはありません」。系列が無いセルは空白。一覧 API が失敗したらその文を出す。表は出さない。

検索の候補が 0 件なら `#monthly-suggest` を隠す。確定した検索でヒットが 0 件なら「該当する発行体はありません」を出す。「登録された月次データはありません」は、発行体が 1 件も無いときと API 失敗のときに出す。

### スマホと PC

同じ構造。チップは折り返す。表は横スクロール。縦横罫線。幅 1100px で二段組にしない。

## Detail

### 目的

1 発行体の系列を月次で比べ、PDF の全項目を年ごとに読む。

### 要素

`#monthly-back` が TOP へ戻る。見出しは会社名と code。

`#monthly-series` は単一の `<select>`。option は年表に出る表示名と同じ ID・同じ順（表示名で畳む）。ゲート銘柄は leftover ごとに `optgroup` を付けてよい。option の value はフル表示名のまま。
チャートは選んだ行をそのまま描く。既定は 全店売上高(前年比) に当たる行があればそれ、無ければ先頭行。系列名はここに出す。チャート内には題名を描かない。

`#monthly-chart` は年ごとのクラスタ棒グラフ。積み上げではない。

- X は 1月〜12月。出す年は、選んだ行に値が 1 つでもある暦年のうち最新とその前2年（例: 2026 / 2025 / 2024）。年表と同じ3年
- 色は暦年で固定し、表示順では付け替えない。`MONTHLY_YEAR_COLORS`（#0077b6 / #2e8b57 / #b8860b / #8e4ec6）を `year % 4` で引く。凡例も同じ色
- 前年比グラフは 100 に水平線。100 未満でも棒は年の色のみ（赤の半透明重ねは付けない）。店舗数や実数の行に 100 線は無い
- ライブラリは足さない。`static/js/monthly.js` 内の SVG または canvas
- 系列名の題は描かない。Y軸に目盛の数字。左ガターに軸、下に月ラベルと年凡例。互いに重ねない
- スマホでは viewBox を画面幅に合わせ、月・凡例・Y軸を他サイトの月次売上グラフと同じくらい読める大きさにする

グラフと表を同じカードに入れない。

`#monthly-table-wrap` は暦年ごとの表を新しい年が上になるよう重ねる。

- 出すのは最新暦年とその前2年（データがある年。例: 2026 / 2025 / 2024）。チャートと同じ3年。overflow-x は auto
- 各表の左上セルは暦年。赤地・白文字・太字。1〜12月の見出しは薄いグレー（#eceff2）、太字、中央揃え
- 行は表示する年の表示名の和集合（表示名で畳む）で、どの年表も同じ順。その年に無い行は空セル
- 保存順と GET の和集合は `union_monthly_elements` / SPA の `monthlyRowOrder`。国なし → 日本 → 海外。セグメント無しが先、名前付きは新しい年から見た初出
- 各塊は 全店 → 既存 → チャネル（直営+FC → 直営 → FC）→ 売上高 → 客数 → 客単価 → 出店 → 退店 → 純増/新設/改装/業態転換 → 店舗数 → その他
- スコープ無しの店舗数は全店枠に入れず、既存の後へ（クスリのアオキ 合計店舗数 → `店舗数`）
- 出店/退店は指標で、セグメントにしない（ワッツ `100円ショップ 出店 店舗数` が売上高より前の塊にならない）
- 純増/新設/改装/業態転換は名前を変えず店舗数の前
- 直営はチャネルなので `焼肉 直営 店舗数` を全店の先頭に上げない。ブランド付きはセグメント（`カラオケ 全店 売上高(前年比)` は カラオケ）
- ゲート銘柄だけ、年表は `monthlyRowOrder` のあとに表示だけの第二レイアウトを掛ける。DB・API・`union_monthly_elements` は変えない
- ゲートは表示する最新暦年。`monthlyRowSegment` が空でなく、rank 名（`stripMonthlyDisplayUnit`）へ `parseMonthlyLayers` したとき metric・channel・scope のいずれかがあり、同じ leftover がその年に 2 行以上あるとき、その leftover をゲートする
- ゲート 0 件の発行体は現状どおり。項目列は 1 列。行順は上の scope 優先のまま
- ゲートがあるとき、ゲートしなかった行は会社全体の 1 ブロックにまとめる（`日本+海外` が和集合の末尾でも会社ブロックへ戻す）。ゲート leftover は `monthlyRowOrder` の初出順で続く。節の内側だけ channel → scope → metric。国では並べない。国とゲートしていない leftover は葉ラベルだけ（例: `日本 店舗数`, `日本+海外 店舗数`）
- ゲート表の左は stub 3 列（チャネル | スコープ | 葉）。連続するチャネルとスコープは rowspan。スコープ `既存` は stub 文言 `既存店`。空のチャネルとスコープは `—`。ゲート leftover に入るとき `tr.monthly-sec` を 1 行。ラベルは `td.monthly-sec-label` で stub 3 列ぶん `colspan=3`、続けて月セル 12（`td.num.monthly-sec-month` に `1月`…`12月`。年表ヘッダの月と同じ太字・中央・`#eceff2`。数値は入れない）。月の縦罫線を節行でも切らない。グレー帯は `monthly-sec-label` だけ。`data-series` なし、クリックしない。PC で sticky なのはラベルセルだけ（全幅 1 セルだと sticky が効かず横スクロールで消える。月セルまで sticky にするとラベルの上に積み重なる）
- `data-series` と系列 ID は `fmtMonthlyElement` の表示名のまま。`#monthly-series` は年表と同順。ゲート leftover は `<optgroup label="焼肉">`。会社全体は optgroup「全体」。option の value はフル ID。グループ下の表示は葉でよい
- 項目列は太字。セルは縦横罫線。ゼブラは付けない。選択中の系列行だけ `--row-sel`。ゲート表の stub 3 列と節ラベルも 700 で揃える。左揃え・上揃え（rowspan のチャネル/スコープは塊の先頭行の横に出る）。中央揃えや 600/700 の混在はしない
- 1〜12月列は表示値の最長に揃えた同一幅（`--monthly-m-w`）。空セルと項目列は幅の根拠にしない。項目列は min-width で潰れない。ゲート表は `min-width:100%` で画面幅に広げない（`table-layout:fixed` が余りを全列に比例配分し、stub と月列が太る。1400px で月列が 53px → 86px になっていた）。幅は列幅の合計。右に余白が出てよい。ゲート無しの表は従来どおり
- PC の左端 th/td は sticky、不透明な白（暦年セルは赤）、z-index。右端の月列は sticky にしない。ゲート表の stub 3 列も PC では左に sticky する。stub 列の幅は PC で `--stub-ch` 5em / `--stub-sc` 4.25em / `--stub-leaf` 9.5em（`直営+FC` / `既存店` / `日本+海外 店舗数` が 1 行に収まる最小。stub の左右 padding は 6px）。sticky の `left` はこの変数から導く。幅だけ変えて left を直し忘れない
- スマホ（`@media (max-width:640px)`）は左端 sticky を外し、項目列を短くして月列が見えるようにする。第2 sticky 列は足さない。節の月ラベルも年表ヘッダと同じ見た目（グレーはブランドラベルセルだけ）。stub 幅は同じ変数を 2em / 2em / 4em に上書き。stub 列は折返し可。名前領域の合計はおおよそ 8em

### 操作

`#monthly-back` で TOP に戻る。直前のチップを保つ。グラフの指標は `#monthly-series` の切替、または年表の該当行をクリックして変える。年の選択 UI は置かない。

### 空とエラー

years が空なら「データなし」。選んだ系列に値が無い年はグラフから落とす。年表は残す。チャート用の 3 行が PDF に無くても、年表は全行のまま。

### スマホと PC

TOP と同じ構造。年表は横スクロール。sticky と項目列の扱いは上の要素のとおり。二段組にしない。

## API

`GET /api/v1/tables` は `{code, name, years}` に次を足す。
`market`, `sector33`, `industry`, `last_ingested_at`, `is_new`, `ingested_today`, `all_store_yoy`, `existing_store_yoy`。

`GET /api/v1/tables/{code}` と `GET /api/v1/tables/{code}/{year}` の形は `DESIGN.md` §5。Detail の年表は years を並列 GET する。画面の空セルは `-`。

`PUT /api/v1/tables/{code}/industry` の body は `{industry}`。stocks に無い code、月次表が無い code は 4xx。

ingest は `monthly_issuers` を upsert し `last_ingested_at` を今にする。industry は既にあれば触らない。無ければ `その他`。

エラー包みは既存どおり `{"error": {"code": "...", "message": "..."}}`。

## 対象外

AI による月次業種の自動分類。業種マスタの固定リスト。SPA からの industry 編集。型の調査タブ。PDF 取込そのものの置き換え。新しいフロントライブラリ。
