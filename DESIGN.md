# 日本株分析ツール 設計書

## 1. 全体像

```
┌─────────────┐   日次バッチ(19:30 JST)   ┌──────────────┐
│ J-Quants API │ ─────────────────────→ │  DB           │
│ (Light plan) │   上場銘柄/株価/財務/TOPIX │  SQLite        │
└─────────────┘                          │  (開発PC / VPS) │
┌─────────────┐   補完(必要時)            └──────┬───────┘
│ IRBank MCP  │ ─────────────────────→        │
└─────────────┘                                ↓
                                    ┌──────────────────┐
                                    │ FastAPI (REST API) │
                                    │ + 静的SPA配信       │
                                    └──────┬───────────┘
                                           │ HTTPS
                                 ┌─────────┴─────────┐
                                 │  Web / スマホブラウザ │
                                 └───────────────────┘
```

- **言語**: Python 3.10+ / FastAPI / SQLAlchemy / pandas
- **フロント**: `static/index.html` + `static/css/` + `static/js/` の vanilla JS SPA (lightweight-charts)。スマホ対応。フレームワークを足さない
- **バッチ**: `jobs/run_batch.py` を cron / スケジューラで日次実行

## 2. J-Quants API V2 / Light プランの前提

> **V1は閉鎖予定のためV2を使用する。** V1とは認証方式・エンドポイント・レスポンス構造・
> カラム名がすべて変わっている。

### 認証
APIキー方式。ダッシュボードで発行したキーを `x-api-key` ヘッダーに付与するだけ。
有効期限がないためトークンリフレッシュ処理は不要（V1のidToken/refreshTokenは廃止）。

### 使用エンドポイント

| 用途 | V2エンドポイント | 備考 |
|---|---|---|
| 上場銘柄一覧 | `/v2/equities/master` | 17/33業種・市場区分・貸借信用区分・商品区分(ProdCat) |
| 株価四本値 | `/v2/equities/bars/daily` | **調整済OHLCV(AdjO/H/L/C/Vo)と時価総額を直接取得可** |
| TOPIX四本値 | `/v2/indices/bars/daily/topix` | from/to指定 |
| 財務情報 | `/v2/fins/summary` | 決算短信サマリ |
| 決算発表予定日 | `/v2/fins/earnings-date` | 発表日 (`date` = PubDate) の全銘柄。`SchDate` が空なら未定 |
| 取引カレンダー | `/v2/markets/calendar` | 営業日判定 (HolDiv=="1") |

レスポンスは全て `{"data": [...], "pagination_key": "..."}` 形式。
カラム名は短縮形（`Open`→`O`, `Close`→`C`, `AdjustmentClose`→`AdjC` 等）。

### データ範囲 (Light)
過去5年。RS計算に必要な252営業日+αには十分。

### レートリミット ★設計上の最重要制約

| プラン | 上限 |
|---|---|
| Free | 5 req/分 |
| **Light** | **60 req/分** |
| Standard | 120 req/分 |
| Premium | 500 req/分 |

加えて `/v2/fins/summary`、`/v2/fins/details`、`/v2/fins/earnings-date` はプランと別枠で60 req/分。`jquants.py` はパスが `/fins/` で始まるものを同じ枠にする。
超過すると429、大幅超過だと5分間の全遮断。

→ `jquants.py` でエンドポイント群ごとにスライディングウィンドウのトークンバケットを持ち、
自主規制する。429を受けたら指数バックオフ (5→10→20→40→60秒) で最大6回再試行。

### 取得方式 ★V1設計からの変更点

公式ベストプラクティスに「**1銘柄ずつ×全日付での取得を避けてください**」と明記されている。
`date` のみ指定すれば全上場銘柄のその日のデータが1リクエストで取れる。

| 方式 | リクエスト数(5年) | Lightでの所要時間 |
|---|---|---|
| 銘柄ループ (旧設計) | 約4,000銘柄 × 2 = 8,000 | 約2時間 |
| **日付ループ (採用)** | 約1,220営業日 × 2 = 2,440 | **約45分** |

さらに高速な選択肢として、Lightプラン以上ではダッシュボードから**CSV一括ダウンロード**が
利用できる。ただしCSVには調整済株価が含まれず自前計算が必要なため、
本ツールでは調整済株価をAPIから直接取れる日付ループ方式を採用する。
（10年以上の長期バックテストをやりたくなったらCSV + 自前調整を検討）

## 3. DBスキーマ

```sql
stocks            -- 銘柄マスタ
  code TEXT PK    -- 5桁コード (例 "72030", "262A0")。先頭・末尾は数字、途中は 0-9A-Z
  name, sector33, market, product_category, updated_at
  -- product_category = J-Quants ProdCat
  --   011 内国株券 / 012 優先出資 / 013 REIT / 014 ETF
  --   021 外国株券 / 022 外国REIT / 023 外国ETF / 024 外国株預託証券
  -- 既存DBは migrate で列追加。値は次の日次バッチの銘柄マスタ更新で埋まる
  -- (株価・財務の再取得は不要)

daily_prices      -- 日足
  code, date PK(複合)
  open, high, low, close, volume, turnover      -- 調整前(報告時生値)
  adj_factor
  adj_open, adj_high, adj_low, adj_close, adj_volume  -- 調整済(分析はこちらを使う)
  market_cap                                     -- 時価総額(百万円)

topix             -- TOPIX日足
  date PK, open, high, low, close

statements        -- 決算短信 (J-Quants /fins/statements ほぼ生保存)
  disclosure_no PK
  code, disclosed_date, disclosed_time
  type_of_period          -- 1Q/2Q/3Q/FY
  fiscal_year_start, fiscal_year_end
  net_sales, operating_profit, ordinary_profit, profit, eps   -- 累計実績
  f2_net_sales, f2_operating_profit, f2_ordinary_profit, f2_profit, f2_eps  -- 半期(2Q)会社予想
  fy_net_sales, fy_operating_profit, fy_ordinary_profit, fy_profit, fy_eps  -- 通期会社予想
  is_revision             -- 予想修正開示フラグ
  raw_json                -- 元データ全項目 (将来の項目追加に備え保持)

indicators        -- 日次算出テクニカル (バッチで更新, 最新日分を保持+履歴)
  code, date PK(複合)
  ma50, ma150, ma200
  ma200_slope_1m          -- 200日線の1ヶ月変化率
  high_52w, low_52w
  rs_raw, rs_rank         -- RS生値と1-99ランク
  stage                   -- 1/2/3/4 (判定不能はNULL)
  avg_volume_50

earnings_events   -- 決算発表イベントと発表後パフォーマンス
  code, disclosed_date PK(複合)
  base_price              -- 発表当日(場中発表)or翌営業日始値
  return_1d, return_5d, return_20d, return_60d, return_to_date
  alpha_1d, ... alpha_to_date   -- 対TOPIX超過リターン

earnings_date_pubs -- 決算発表予定の発表履歴。現在の予定は (code, fq_name) の最新 pub_date
  code, fq_name, pub_date PK
  sch_date                -- NULL は未定。欠測ではない
  fye                     -- MMDD の4文字。0299 は 2月末
  co_name
  -- FK は付けない。sch_date の索引は付けない。日付で先に絞ると移動前の日が戻る

earnings_date_fetches -- 取り終えた発表日。空の応答も n=0 で残す。pubs の写しではない
  pub_date PK
  n

monthly_issuers   -- 発行体ごとの月次属性。月次業種は stocks.sector33 とは別
  code PK                 -- stocks.code と同じ 5桁。FK は付けない
  industry                -- 月次業種。既定は その他
  last_ingested_at        -- ingest でセルが変わったときだけ更新。NULL は新着ではない

monthly_tables    -- 銘柄×年度の月次表 (暦月1〜12。会計月ではない)
  code, year PK           -- code は J-Quants 5桁 (9279→92790, 262A→262A0)。year は西暦4桁
  -- stocks に無い code は書かない。FK は付けない (既存テーブルへ ADD できない / PRAGMA foreign_keys も有効化しない)

monthly_elements  -- 表の行。element は登録時の正規名、sort_order が登録順
  code, year, element PK
  unit                  -- number | yoy_index。入力 JSON は yoy_pct も可。ingest は yoy_index と value+100
  -- 行名は docs/DESIGN_monthly_labels.md。GET は保存名。SPA は表示名で行を畳む

monthly_values    -- セル。値は数値のみ (単位・カンマ付き文字列は拒否)
  code, year, element, month PK
  value
```

月次の year は暦年。PDF 取込は `POST /api/v1/tables/{code}/ingest`。

- `months` は PDF 左→右。年は最終列の月と開示日 `as_of` 時点の最新 `statements.fiscal_year_end` から右→左へ列ごとに決める
- 同じ月番号が2回あっても列が違えば年が違う（Genky 型 13 列の Jun…Jun など）
- layout は calendar（先頭が1で昇順）/ fiscal（会計年度開始月始まり）/ それ以外は partial。列の並びでは拒否しない
- fiscal で期末月より後の as_of（新FY先頭月に限らない）は完了した FY に割り当てる。mapping の年月が as_of より後なら 400 `FUTURE_COLUMN`（当期FYの期末前に期末月列がある場合は除く）
- `months` に重複が無いとき `values` は `{月: 値}`。重複があるときは列順の配列（または列番号キー `"0"`…）
- 応答 `mapping` も一意月は `{月: 年}`、重複時は列順 `[{month, year}, …]`
- クライアントは year を渡さない。年付き PUT は訂正用。決算期変更があっても as_of 時点の1か月だけを使う
- `as_of` は YYYY-MM-DD 必須。省略・null は 400 `AS_OF_REQUIRED`。サーバ日付は使わない
- `dry_run: true` は `fy_end_month` / `layout` / `mapping` と、書くはずのセル（`updated`）を返す。DB には書かない。新着にもならない
- `only_months` は `YYYY-MM` の配列。mapping を決めたあと、その暦年の列だけ書く。省略時は全列。当たらない月は書かない

**銘柄マスタとの関係。**

- 書き込みは `stocks` に行がある code だけ。ingest / POST elements / 訂正 PUT / industry PUT は先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`
- FK は付けない（既存テーブルへ ADD できない）。`PRAGMA foreign_keys` も有効化しない
- `init_db` / migrate は `stocks` に無い月次行を DELETE する（本番の名証単独 30320 / 30660 など。冪等）。順は `monthly_values` → `monthly_elements` → `monthly_tables` → `monthly_issuers`

**unit。** PDF 見出しが入力タグを決める。値の大きさから推測しない。入力は `number` / `yoy_index` / `yoy_pct`。保存は `number` / `yoy_index`。`yoy_pct` は書かない。ingest の各行と `POST .../elements` は `unit` 必須。省略・null は 400 `UNIT_REQUIRED`。GET の NULL は number として出す。PUT values は訂正で、保存形（指数）を送る。unit も `+100` もしない。

| 入力 unit | 見出し | 保存 |
|---|---|---|
| `yoy_index` | 前年比 100 基準（`単位:%` のみ等） | `yoy_index` のまま（125.8）。表示もそのまま。100=フラット |
| `yoy_pct` | ▲/△ 付き変化率、または見出しの 増減比/伸び率 | `yoy_index`、`value+100`（-7.1 → 92.9） |
| `number` | 店舗数など件数 | `number` のまま |

前年比の表の表示は保存値のまま（指数 100=フラット）。▲ や +X.X% にはしない。
number で表示単位がある指標は項目名の末尾に `（単位）` を付け、セルは四捨五入した数字だけにする。対応は [`docs/DESIGN_monthly_labels.md`](docs/DESIGN_monthly_labels.md)。GET の JSON と保存名は単位なし。

**行名。** 正本は [`docs/DESIGN_monthly_labels.md`](docs/DESIGN_monthly_labels.md)。
ingest / POST は `canonicalize_element_label` してから既存行を探す。GET は保存名。SPA は `fmtMonthlyElement`。

ingest が新しい行を作るとき、同じ (code, year) に `strip_yoy_fragments` 後の名前が同じで unit が yoy と number で違う既存行があり、その既存行が今回の `rows` に無いなら 409 `YOY_NUMBER_CONFLICT`。`force=true` で作る。同じ POST に両方あるときは作る（実数と前年比の併存）。既にある行の更新は対象外。`POST .../elements` はこの検査をしない。

**R2 への保存（`jobs/*_r2.py`）。** 保存先は `/home/stock/r2-share/monthly/{code}/`。cron と運用は `deploy/README.md`。共通の約束は次のとおり。

- `{code}` は TDnet の発行体コードそのまま（9279 / 262A）。J-Quants 5 桁にしない。stocks 照合だけ display_code + 末尾 0（9282→92820, 262A→262A0）
- stocks に無いコードはダウンロードしない。DB が読めない／map が空なら警告のみで継続
- 既存ファイルは消さない。IR（PDF/xlsx/HTML）は同じ URL の上書き公開を日付付きで残し、中身の sha256 が同じなら skip
- 3 本のバッチ（TDnet PDF / IR PDF・xlsx / IR HTML）の cron は分ける
- 日付・HTTP・銘柄マスタ・ファイル名の共通処理は `jobs/_r2common.py`（`app` は import しない）。`http_get` は TDnet も IR も同じ。識別 UA はジョブごとで、403 のときだけブラウザ UA に一度切り替え、中間証明書の欠落は AIA で補う。429 / 408 / 5xx は Retry-After があればそれに従い（最大 60 秒）、無ければ指数バックオフ＋jitter（5→10→20→40→60 秒、最大 6 回）。打ち切ったら例外を上げる。発行体ログは通常 `fail issuer {code} fetch:`。HTML 月次の持続 429 だけ下の HTML 節

TDnet 月次 PDF（`jobs/tdnet_monthly_r2.py`）:

- やのしん list をキーワード union。`月次` が無い売上速報・○月度・受注速報・売上推移・業績速報なども取る。`is_monthly_title` が四半期短信・自己株式・新株予約権の月間行使・業績予想を落とす。`月度` 単体では取らず、売上/受注などのヒントが要る
- ダウンロード時にインフラファンドを除外する。商品区分は REIT と同じ `013`/`022` しか無いので、名称に「インフラ」を含むものだけ落とす（マスタ欠落時は TDnet 社名の「インフラ投資法人」）
- 通常の REIT は残す。決算速報の REIT 全除外とは別。`MONTHLY_SKIP_CODES`（display_code の4桁）に入った銘柄は保存しない。8985 ジャパン・ホテル・リート投資法人、8798 アドバンスクリエイト（グラフ画像のみ）、8739 スパークス（運用資産残高のストック表）は取得対象外
- stocks に無いコード（名証単独の 3032 / 3066 など）は `unknown-stock` で skip。東証マスタには載せない

TDnet 月次 PDF の自動投入（`jobs/tdnet_monthly_pdf_sidecar.py` → `jobs/tdnet_monthly_pdf_ingest.py`）:

- 銘柄ごとの書式は宣言しない。DB に開示月（ファイル名日付の前暦月）の値がある前回の PDF（最大 3 本さかのぼる）から、DB の各行を再現する PDF の行を探して学ぶ。unit は DB から。DB が指数で PDF が増減率なら `yoy_pct`（+100 で一致）
- PDF は `pdftotext -tsv` の語の座標で読む。月見出しの行と列がそろう数値の行（横組み）と、月が行ラベルの表（縦組み）。上期計・累計などの列は捨てる。見出しに年（`25年4月`）があれば使い、無ければ行ごとに値のある右端の月から左へ暦年を振る
- 書くのは各 code の最新 PDF だけで、DB の最新月がその開示月より古いときだけ。次のどれかなら書かない（`manual`）: 開示月に値のある DB の行を全部は再現できない／使う表までの見出しの骨格（数値・日付を伏せる）か使う行の並びが前回と違う／前回無かった行・表に今月の値がある（表の序数込み。見出しと行名が既存と同じ新ブロックも含む）／DB に既にある月と 5 か月に 1 つより多く食い違う／件数・金額が直近 12 か月の中央値から 5 倍以上外れる／前回同じ値だった候補行が今回は違う
- sidecar は PDF の隣の `….pdf-ingest.json`（HTML の `.ingest.json` と別の名前。HTML の ingest は拾わない）。形は ingest と同じで year は無い。values は値のある月だけ（null はセルを消すので送らない。月が重複すれば列番号キー）。`columns` に列ごとの暦年月、`learned_from` に学んだ PDF を残す
- DB 投入は `jobs/tdnet_monthly_pdf_ingest.py`。latest-per-code・sha256 ledger・行に `force` を付けない点は HTML の ingest と同じ。POST の前に `dry_run` し、mapping が `columns` と違えば POST しない。ledger は `/var/log/app/pdf_ingest_ledger.json`
- 前回の DB の値を正とするので、過去に人が入れ違えた行はそのまま引き継ぐ。速報の訂正は翌月の sidecar が全列を書き直すので追いつく

IR 月次 PDF/xlsx/xls（`jobs/ir_monthly_r2.py`）:

- TDnet に月次が無い発行体が対象。`config/ir_monthly.json` の issuers（URL 確認済みだけ載せる）
- 表計算と pdf が両方あるページは表計算だけ取る。xlsx があれば xlsx、なければ xls（大東建託 `history_*.xls`）
- AWS ELB が識別 UA を 403 する発行体は、同じ `http_get` がブラウザ UA に一度だけフォールバックする
- 中間証明書が欠ける／付け違う発行体は、同じ `http_get` がリーフの AIA (CA Issuers) から中間を補って検証し直す。検証は切らない（7544 スリーエフ）
- 大東建託 (1878) の `history.xls` は `jobs/ir_monthly_xls_ingest.py` が ingest JSON を作り loopback POST する。year は送らない。汎用 xlsx パーサは無い。HTML ingest の cron には混ぜない

IR 月次 HTML（`jobs/ir_monthly_html_r2.py`）:

- fetch が 429 で打ち切られた発行体は `skip 429 issuer {code}` とし `failed` に数えない（他銘柄の saved/skip を exit=1 にしない。`failed=` も 0 のままなので `#alerts-batch` は出ない）。再取得は次 cron か `--only {code}`。403・書き込み失敗・ingest 失敗は従来どおり `fail issuer` / `failed` で exit=1
- `.html`（原本）と `.ingest.json`（派生。`POST /api/v1/tables/{code}/ingest` と同じ形）を隣に置く。自動 POST しない
- DB 投入は別スクリプト `jobs/ir_monthly_html_ingest.py`。code ごとにファイル名日付が最新の `.ingest.json` を loopback へ POST する。year は送らない。sha256 が前回成功分と同じなら POST しない。行に `force` は付けない（同名で unit が違う行・類似名は 409 で止め、人が `--force --only` で入れる）。`--months` は暦年の `YYYY-MM` を `only_months` として 1 回 POST する（年はサーバの mapping。部分投入は ledger を更新しない）。cron は producer（10:20 / 16:20）の後（10:30 / 16:30）。ledger は dest/R2 ではなく `/var/log/app/html_ingest_ledger.json`
- 対象は `config/ir_monthly.json` の html。セレクタは json（`follow_re` / `source=eir` / `parse`）。`parse: true` は `jobs/html_issuers` の `parse`（`PARSERS`。`i<code>.py` を置けば載る）。各モジュールが表・行・単位を宣言する。`parse: false` はモジュールを作らない
- unit 判定の context は page 見出しに加え、当該 table 直上の section とページ内の `(単位：%)` / `単位:%` を含む。ページに単位表示が無く指数な銘柄、および見出しが伸び率でもセルが符号なし100基準の銘柄（3148 クリエイトSD）は、モジュールで `yoy_index` を宣言する。見出しに 増減比/伸び率 があれば `単位:%` より `yoy_pct` を優先（ツルハ・サガミ）
- 年度別ページは `follow_re` で最新年を辿る。画像・本文のみは `parse: false`（HTML は残し JSON は書かない）
- E-IR は Pronexus JSONP の月次 item `text` を `.html` として保存する（sha256 はその HTML）
- `as_of` は開示日。なければ表の最終値の年月（セルの年 or 表直前の期見出し / `YYYY年M月期` / `YYYY年度` / `YYYY年M月～YYYY年M月`）。取れなければ JSON は書かない
- 複数年度のアーカイブは期が新しい表を優先。同じ期末なら最終値の新しい表を優先。12か月埋まっている表は最終値では選ばない
- ミニストップ (9946) は `jobs/html_issuers/i9946.py`。`全店売上` を選び、`売上高合計` だけ残す。`1店1日当り` と CVS / 店内加工FF は採らない
- やまや (9994) は `jobs/html_issuers/i9994.py`。既存店と全店をマージし、卸計は選ばない。`当 年`→`売上高`、`前 年` は落とす。unit は `yoy_index`
- ロック・フィールド (2910) は `jobs/html_issuers/i2910.py`。`既存店売上` / `全店売上` をマージし、業態名の行に見出しのスコープを付ける（付けないと既存店の数値が裸の `売上高(前年比)` になる）
- フジ (8278) は `jobs/html_issuers/i8278.py`。改装の店舗面積リストは選ばず、営業速報に総店舗数だけ足す
- 同ページの店舗数表と売上表は、月軸と表のセル年が同じとき同一 payload に足す。行に `YYYY年M月` がある表の as_of はページ全体の「現在」よりセルの年を優先（6537）
- クリエイトSD (3148) は `jobs/html_issuers/i3148.py`。見出しが伸び率でもモジュールが `yoy_index` を宣言する
- 1表に複数年が並ぶアーカイブ（大黒天物産）は同月の先勝ち（新しい方）。`YYYY年（令和N年）度` 年度帯は見出しに入れない
- ページ見出しが売上の前年比でも、客数・客単価・店舗数・拠点数・営業所数・開店/閉店/出店/退店/開設/撤退の行には `売上高前年同月比` を足さない。店舗数・拠点数・出退店は件数なので unit も `number`
- 数値の無い 既存店/全店 の帯行は、直後の指標行に付ける
- 1表に上期と下期の月見出しが続くとき（神戸物産の単体月次）は後半の月も同じ行に足す。行名に全店は付けない
- 神戸物産 (3038) は `jobs/html_issuers/i3038.py`。同じページの「店舗仕入れ前年対比」は月が揃うとき足す。セグメントは仕入れ。脚注の商品出荷は行名にしない
- year は書かない。`△1.9` は `-1.9`。unit は見出しから（上の表）。`累計` / `通期計` / `上期計` は months に入れない
- HTML ingest JSON の `months` は末尾の空列を落とす（値のある最終月で終わる）。途中の欠月は残す
- パーサーを直したら保存済み HTML を `--reparse`。HTML が同じでも、現行パーサの `.ingest.json` が保存済みと違えば hash skip 時に書き直す（古い sidecar を latest-per-code に残さない）

TDnet 短信 XBRL は取得済み ZIP（`jobs/tdnet_xbrl_r2.py`）を `xbrl_filings` / `xbrl_facts` / `xbrl_financials` に載せる。
スキーマとジョブは [`docs/DESIGN_xbrl.md`](docs/DESIGN_xbrl.md)。決算速報 UI の正本は当面 `statements` のまま。XBRL 層で上書きしない。


### Q単独値の算出 (保存せずAPI側で導出)
`Q単独 = 当期累計 − 前四半期累計`(1Qはそのまま)。前年同期のQ単独と比較しYoY成長率を計算。EPSも同様。

`CurPerType` は `1Q/2Q/3Q/4Q/5Q/FY` を取る。FYと4Qは通期累計として同一視。
5Q(決算期変更等の変則期)は前後関係が定義できないためQ単独を算出しない。

QoQ は `Q単独(当四半期) vs Q単独(直前四半期)`。1Qの相手は前事業年度の4Q単独になるため
季節性が断絶する(UI側で注記する)。算出しないと5月の決算集中日にQoQ列が全社空欄になる。

### 速報一覧に出す開示 ★注意
予想修正のみの開示(`EarnForecastRevision` 等)は実績値が空なので除外する。

遅延企業は過去の期をまとめて再提出することがあり、そのまま出すと1銘柄で何行も並ぶ。
既出期間の再提出は **売上・営利・経利・純利が初回と一致するものを除外**し、
損益が変わったものは「訂正」として残す。**EPSは比較に含めない** —
分割・増資で遡及修正されるだけのことがあり、訂正扱いにすると
損益が変わっていない再提出まで一覧に残ってしまう。

複数期を同時に「初めて」開示する日があるため、1銘柄1行には潰さない。
カレンダーの件数も同じルールで数え、一覧の行数と一致させる。

### 前年同期の突合
事業年度末の完全一致ではなく **±45日の最近傍一致** で引く(`config.EARNINGS_PARAMS`)。
決算期変更で年度末がずれる銘柄と、2/29決算(`date.replace(year=…)` が ValueError になる)を同時に吸収する。

### 増減率の表現 (黒字転換)
前期が0以下だと比率に意味がないため、増減率は `{pct, state}` で返す。
`state` は `up`/`down`/`turn_profit`(黒転)/`turn_loss`(赤転)/`loss`(赤字継続)/`na`。
単純に `None` にすると赤字からの黒字転換が「–」に潰れる(実データで営業赤字は約4,350件)。
売上はマイナスを取らないので黒転の概念を適用しない。

### 予想修正の検知
同一事業年度の直前開示の通期営業利益予想と比較し、±0.1%以上の差があれば
上方/下方修正として修正率(%)付きでバッジ表示する。

### 会計基準による欠損 ★注意
IFRS・米国基準の開示には**経常利益の概念が無く `OdP` は空欄**になる。
経常利益でのスクリーニングやソートは欠損前提で扱うこと(営業利益を主指標にする)。

## 4. 分析ロジック

### 4.1 レラティブストレングス (RS)
```
rs_raw = ((C-C63)/C63*0.4 + (C-C126)/C126*0.2 + (C-C189)/C189*0.2 + (C-C252)/C252*0.2) * 100
```
adj_close を使用。`stocks.product_category = 011`(内国株券) の銘柄だけで降順に並べ、上位1%→99、次の1%→98…と1-99を割当。
REIT・ETF・優先出資はユニバースに入れない (ミネルヴィニのRSは普通株同士の相対)。
ProdCat がまだ1件も入っていないDBでは従来どおり全銘柄でランクする (migrate 直後のフォールバック)。
ステージ判定・スクリーナーも同じユニバース。チャート検索では REIT も引ける。

### 4.2 ミネルヴィニ・ステージ判定
優先順位付きルールベース (Stage2テンプレート → Stage4 → Stage1 → Stage3 → その他)。

**Stage 2 (全条件AND)**:

- C > MA150 かつ C > MA200
- MA150 > MA200
- MA200 が1ヶ月以上上昇 (`ma200_slope_1m > 0`)
- MA50 > MA150 > MA200
- C > MA50
- C ≥ 52週安値×1.3
- C ≥ 52週高値×0.75
- RSランク ≥ 70

**Stage 4**: C < MA200 かつ MA200下降 (`ma200_slope_1m < -閾値`) かつ Cが52週安値の1.1倍以内

**Stage 1**: C < 52週安値×1.3 圏で MA200±8%以内に株価が滞留 (直近60日の帯域内比率で判定) かつ MA200がほぼ横ばい (|slope| < 閾値)

**Stage 3**: 直近まで Stage2 だったが MA200 が横ばい化 (+ボラ拡大: 直近20日ATR/株価が上昇)

閾値は `config.py` に集約し調整可能。

### 4.3 決算日からの相対パフォーマンス
発表日基準価格から各営業日の `(株価リターン − TOPIXリターン)` を算出。テーブルで +1d/+5d/+20d/+60d/現在 を表示。
チャート下のイベント重ね合わせ（既定オフ）で決算発表日に「決」マーカーを出せる。今後のイベント種別も同じチェック列に足す。決算速報タブだけ、選択中の発表日は重ね合わせオフでも1件出す。週足・月足では発表日を含む足（期間末）に載せる。

## 5. API設計 (抜粋)

| Method/Path | 内容 |
|---|---|
| `GET /api/earnings?date=2026-08-14&today=2026-10-02` | `{kind, actuals, scheduled}`。`actuals` は開示1件が1行で、QoQ(Q単独の前四半期比) と YoY(累計の前年同期比) を4指標ずつ返す。決算短信本体のみ (予想修正のみの開示、および損益が初回と同一の再提出は除外。損益が変わった再提出は `restated` を立てて残す)。`scheduled` は `{code, name, periods:[{fq, fye, label}]}` で銘柄1行。`kind` は `actual` / `scheduled` / `mixed`。過去は `actual` で `scheduled` は空。今日は開示と、そのコードが今日の開示に無い未発表。未来は予定だけ。`today` を省略するとサーバの今日。台帳が lookback を埋める前の未来には `ready: false`（空なら画面は 予定未取得）。`product_category` が REIT (`013`/`022`) の銘柄は出さない |
| `GET /api/earnings/detail/{code}?disclosure_no=…` | 選択銘柄の累計テーブル (過去4期+当期の同一四半期 + 中間予想 + 通期予想 + 前期通期実績) と3か月決算テーブル (直近12四半期のQ単独)。各行にカレンダー範囲 (`26.04-06`) と発表日を付ける。同一銘柄が同日に複数開示する日があるため `disclosure_no` で一意に引く (`?date=` でも可) |
| `GET /api/earnings/calendar?month=2026-08&today=2026-10-02` | 日付 → `{kind, n}`。`pending` は今日かつ未発表がいるときだけ。件数は銘柄の distinct。疎。一覧と同じく本体のみを数え、REIT も除外する。`today` を省略するとサーバの今日 |
| `GET /api/chart/{code}?tf=D|W|M&ma=50,150,200&rel=topix` | 株価チャートデータ。W/M は日足を ISO週 / 暦月で集約 (足の日付は期間の最終営業日)。MA 期間は営業日換算 (週=÷5、月=÷20) |
| `GET /api/earnings-performance/{code}` | 決算日からの対TOPIXリターン |
| `GET /api/screener?stage=2&rs_min=70&sort=rs` | ステージフィルタ |
| `GET /api/stocks/search?q=トヨタ` | 銘柄検索 |
| `POST /api/v1/tables/{code}/{year}/elements` | 月次表の要素登録。`unit` 必須。表が無ければ作成。保存名をトークン正規化（yoy は末尾 `(前年比)`）してから既存行を探す。正規化後に同一なら同一行（短い保存名は正規名へリネーム）。完全一致は冪等。類似は 409 (force=true で強制)。fold の値衝突は 409 `FOLD_CONFLICT`。unit の食い違いは 409 `UNIT_CONFLICT` (force=true で上書き)。`stocks` に無ければ 4xx `NOT_IN_MASTER` |
| `POST /api/v1/tables/{code}/ingest` | PDF 列順 + as_of 時点の最新 `fiscal_year_end` から暦年を割り当てて登録。year は受け取らない。`as_of` と各行の `unit` は必須。`dry_run: true` は書かずに mapping と書くはずのセルを返す。`only_months` は `YYYY-MM` で列を絞る。マスタ無しでは 4xx `NOT_IN_MASTER`。mapping の年月が as_of より後なら 400 `FUTURE_COLUMN`。新規行で、`strip_yoy_fragments` 後の名前が同じ yoy と number の既存行が今回の `rows` に無いときは 409 `YOY_NUMBER_CONFLICT`（`force=true` で作成。両方を同じ POST に含めれば通る） |
| `GET /api/v1/tables` | 月次表がある銘柄一覧 (code 順)。各要素は `{code, name, years, market, sector33, industry, last_ingested_at, is_new, ingested_today, all_store_yoy, existing_store_yoy}`。name/market/sector33 は stocks を LEFT JOIN (無ければ null)。industry は無ければ `その他`。years は昇順。表が無ければ `[]` |
| `PUT /api/v1/tables/{code}/industry` | 月次業種。body `{industry}`。stocks 無ければ 4xx `NOT_IN_MASTER`。月次表が無ければ 4xx `TABLE_NOT_FOUND` |
| `GET /api/v1/tables/{code}/{year}` | 月次表。要素は登録順。各行は `{element, unit, label, values}`。`element` は保存名。`label` は GET 派生（display / group / kinds / 7層）。`values` は 1〜12。無い月は null（0 は実ゼロ）。1年通して値のない行は出さない。表が無ければ 404 |
| `GET /api/v1/tables/{code}` | その銘柄の年度一覧 |
| `DELETE /api/v1/tables/{code}/{year}/elements` | 空の月次行を消す。body `{element}`。値が残っていれば 409 `ELEMENT_HAS_VALUES`。表は残す。未登録は 404。マスタ無しは 4xx `NOT_IN_MASTER` |
| `PUT /api/v1/tables/{code}/{year}/values/batch` | 複数月を検証してから一括 commit。マスタ無しは 4xx `NOT_IN_MASTER` |

月次 API のエラーは `{"error": {"code": "...", "message": "..."}}`。閲覧 GET は公開。書き込みは loopback のみ（nginx 経由は 403）。本番の個人ツールは `/app` と Basic 認証。

### 決算速報の画面

画面は `docs/DESIGN_earnings.md`。書き方は `docs/SCREEN_SPEC.md`。

### 月次タブの画面

画面は `docs/DESIGN_monthly.md`。書き方は `docs/SCREEN_SPEC.md`。

## 6. 日次バッチ (19:30 JST)

1. `/equities/master` で銘柄マスタ更新 (ProdCat 含む)
2. `/equities/bars/daily?date=当日` + `/indices/bars/daily/topix` 取込
3. `/fins/summary?date=当日` と `/fins/summary?date=前営業日` 取込。前営業日は DB の株価で当日より前の最終日
4. indicators 再計算 (MA/52週/RS/ステージ。RSユニバースは内国株券のみ) → earnings_events 更新。
   2〜3 が 0 件（前営業日は新しく増えた件数）で indicators が株価の最終日まで済んでいれば飛ばす
5. `/fins/earnings-date?date=当日` と `date=昨日`。昨日は暦日。4 の後。株価が 0 件でも行う。失敗しても 4 の結果は残し、プロセスは非ゼロで終わる

定常の夜は **計7リクエスト**。内訳はマスタ、株価、TOPIX、財務の当日、財務の前営業日、発表予定の当日、発表予定の昨日。発表予定の2本は `/fins/` の枠。台帳が空の初回は指標のあと PubDate を `EARNINGS_DATE_LOOKBACK_DAYS` 日さかのぼる。この歩きは 7 に入らない。決算開示は15時以降が多いため、当日夜(19:30)に1回実行する。
19:30 より後の開示（全体の 0.3% 程度）は、翌営業日の回が前営業日の決算を取り直して拾う。

冪等設計 (UPSERT) のため何度実行しても安全。バックフィルは株価と決算を同じ日に commit できた日付を
スキップするので、中断しても再実行すれば続きから埋まる。カレンダーが空、またはその日が失敗したら非ゼロ終了。

## 7. ホスティング

本番は **ConoHa VPS + 同じディスク上の SQLite**。Neon / Vercel Postgres の無料枠 (0.5GB) には 5 年分日足が乗らない。アプリはサーバレス前提ではないので作り直さない。

| 役割 | 置き場 |
|---|---|
| API + SPA | 同一 VPS の uvicorn（systemd、ワーカー1） |
| DB | `stock.db`（VPS の SSD）。`DATABASE_URL` は今の SQLite のまま |
| バッチ | crontab（19:30 JST） |
| 公開 | nginx + Let's Encrypt。月次閲覧は `/monthly`（Basic なし）。個人ツールは `/app` + Basic 認証 |
| 開発 | 今どおり Windows + `.\run.ps1` |

推奨プランは **メモリ 2GB / SSD 100GB**（東京）。512MB は Python + pandas + 1GB 級 SQLite で足りない。時間課金で立ててから長期契約を判断する。

データ量: 4,000銘柄×5年×250日 ≈ 500万行。SQLite 実ファイルは 1〜2GB になり得る（本番は約 1.1GB）。VPS ディスクなら余裕。
ローカル backfill 完了後に `stock.db` を scp する（サーバで取り直さない）。

見本は `deploy/`（systemd / nginx / crontab）。SQLite は複数ライターに弱いので uvicorn はワーカー1。日次バッチは定常で 7 リクエスト。接続は WAL + `busy_timeout=30s`。`jobs/migrate.py`（deploy 含む）は `database is locked` を指数バックオフで再試行する。

Postgres 切替は将来オプション。今は使わない。`DATABASE_URL` だけ変えれば SQLAlchemy は両対応のまま。

## 8. 将来拡張

- **米国株**: データソース差替え(Polygon/Alpaca等)を想定し、`source` 列と市場別カレンダー・通貨を吸収するアダプタ層を`jquants.py`と同一インターフェースで追加
- **IRBank MCP**: J-Quantsに無い項目 (セグメント、株主、信用残日次) の補完
- 通知 (Stage2入り銘柄・上方修正をLINE/メール)
- **銘柄KPI（業種で項目が違う）**: 小売の月次売上・製造の受注残など。J-Quants `/fins/summary` には無い。業種ごとの列を `statements` に足さない。無い銘柄は行を持たない疎な時系列にする

```
stock_kpis  PK(code, metric, period)
  code / metric (monthly_sales, order_backlog, …)
  period (2026-07 / 2026Q1) / value / unit / source / disclosed_date
```

業種テンプレ（`config.py`）が「小売なら monthly_sales を見る」と決める。テンプレにあっても値が無い銘柄は UI で「なし」。決算予測は、その銘柄に実際にある metric だけ使う。取込は後で IR など。テーブルも今は作らない。

## 9. ローカル実行手順

[README.md](README.md) のクイックスタートとコマンド一覧。
