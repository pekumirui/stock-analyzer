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
- **バッチ**: `scripts/run_batch.py` を cron / スケジューラで日次実行

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

加えて `/v2/fins/summary` と `/v2/fins/details` はプランと別枠で60 req/分。
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

monthly_issuers   -- 発行体ごとの月次属性。月次業種は stocks.sector33 とは別
  code PK                 -- stocks.code と同じ 5桁。FK は付けない
  industry                -- 月次業種。既定は その他
  last_ingested_at        -- ingest 成功のたびに更新。NULL は新着ではない

monthly_tables    -- 銘柄×年度の月次表 (暦月1〜12。会計月ではない)
  code, year PK           -- code は J-Quants 5桁 (9279→92790, 262A→262A0)。year は西暦4桁
  -- stocks に無い code は書かない。FK は付けない (既存テーブルへ ADD できない / PRAGMA foreign_keys も有効化しない)

monthly_elements  -- 表の行。element は登録時の正規名、sort_order が登録順
  code, year, element PK
  unit                  -- number | yoy_index | yoy_pct。保存値は PDF 原文のまま
  -- 行名は docs/DESIGN_monthly_labels.md。GET は保存名。SPA は表示名で行を畳む

monthly_values    -- セル。値は数値のみ (単位・カンマ付き文字列は拒否)
  code, year, element, month PK
  value
```

月次の year は暦年。PDF 取込は `POST /api/v1/tables/{code}/ingest`。

- `months` は PDF 左→右。年は最終列の月と開示日 `as_of` 時点の最新 `statements.fiscal_year_end` から右→左へ列ごとに決める
- 同じ月番号が2回あっても列が違えば年が違う（Genky 型 13 列の Jun…Jun など）
- layout は calendar（先頭が1で昇順）/ fiscal（会計年度開始月始まり）/ それ以外は partial。列の並びでは拒否しない
- `months` に重複が無いとき `values` は `{月: 値}`。重複があるときは列順の配列（または列番号キー `"0"`…）
- 応答 `mapping` も一意月は `{月: 年}`、重複時は列順 `[{month, year}, …]`
- クライアントは year を渡さない。年付き PUT は訂正用。決算期変更があっても as_of 時点の1か月だけを使う

**銘柄マスタとの関係。**

- 書き込みは `stocks` に行がある code だけ。ingest / POST elements / 訂正 PUT / industry PUT は先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`
- FK は付けない（既存テーブルへ ADD できない）。`PRAGMA foreign_keys` も有効化しない
- `init_db` / migrate は `stocks` に無い月次行を DELETE する（本番の名証単独 30320 / 30660 など。冪等）。順は `monthly_values` → `monthly_elements` → `monthly_tables` → `monthly_issuers`

**unit。** PDF 見出しから付ける。値の大きさから推測しない。保存値は原文のまま。

| unit | 見出し | 保存 | 表の表示 |
|---|---|---|---|
| `yoy_index` | 前年比 100 基準 | そのまま（125.8） | そのまま。100=フラット |
| `yoy_pct` | ▲/△ 付き変化率 | 符号付きそのまま（-7.1） | `100+v`。▲ や +X.X% にはしない |
| `number` | 店舗数など件数 | そのまま | そのまま |

**行名。** 正本は [`docs/DESIGN_monthly_labels.md`](docs/DESIGN_monthly_labels.md)。
ingest / POST は `canonicalize_element_label` してから既存行を探す。GET は保存名。SPA は `fmtMonthlyElement`。

**R2 への保存（`scripts/*_r2.py`）。** 保存先は `/home/stock/r2-share/monthly/{code}/`。cron と運用は `deploy/README.md`。共通の約束は次のとおり。

- `{code}` は TDnet の発行体コードそのまま（9279 / 262A）。J-Quants 5 桁にしない。stocks 照合だけ display_code + 末尾 0（9282→92820, 262A→262A0）
- stocks に無いコードはダウンロードしない。DB が読めない／map が空なら警告のみで継続
- 既存ファイルは消さない。IR（PDF/xlsx/HTML）は同じ URL の上書き公開を日付付きで残し、中身の sha256 が同じなら skip
- 3 本のバッチ（TDnet PDF / IR PDF・xlsx / IR HTML）の cron は分ける

TDnet 月次 PDF（`scripts/tdnet_monthly_r2.py`）:

- ダウンロード時にインフラファンドを除外する。商品区分は REIT と同じ `013`/`022` しか無いので、名称に「インフラ」を含むものだけ落とす（マスタ欠落時は TDnet 社名の「インフラ投資法人」）
- 通常の REIT（8985 ジャパン・ホテル・リート投資法人など）は残す。決算速報の REIT 全除外とは別

IR 月次 PDF/xlsx（`scripts/ir_monthly_r2.py`）:

- TDnet に月次が無い発行体が対象。`config/ir_monthly.json` の issuers（URL 確認済みだけ載せる）
- xlsx と pdf が両方あるページは xlsx だけ取る
- ingest はまだ MCP 経由。パーサーは無い

IR 月次 HTML（`scripts/ir_monthly_html_r2.py`）:

- `.html`（原本）と `.ingest.json`（派生。`POST /api/v1/tables/{code}/ingest` と同じ形）を隣に置く。自動 POST しない
- DB 投入は別スクリプト `scripts/ir_monthly_html_ingest.py`。code ごとにファイル名日付が最新の `.ingest.json` を loopback へ POST する。year は送らない。sha256 が前回成功分と同じなら POST しない。cron は producer と分ける
- 対象は `config/ir_monthly.json` の html。セレクタは json（`table` / `follow_re` / `source=eir` / `parse`）。銘柄クラスは作らない
- 年度別ページは `follow_re` で最新年を辿る。画像・本文のみは `parse: false`（HTML は残し JSON は書かない）
- E-IR は Pronexus JSONP の月次 item `text` を `.html` として保存する（sha256 はその HTML）
- `as_of` は開示日。なければ表の最終値の年月（セルの年 or 表直前の期見出し / `YYYY年M月期` / `YYYY年度` / `YYYY年M月～YYYY年M月`）。取れなければ JSON は書かない
- 複数年度のアーカイブは期が新しい表を優先。同じ期末なら最終値の新しい表を優先。12か月埋まっている表は最終値では選ばない
- ページ見出しが売上の前年比でも、客数・客単価・店舗数の行には `売上高前年同月比` を足さない
- 数値の無い 既存店/全店 の帯行は、直後の指標行に付ける
- year は書かない。`△1.9` は `-1.9`。unit は見出しから（上の表）。`累計` / `通期計` / `上期計` は months に入れない
- HTML ingest JSON の `months` は末尾の空列を落とす（値のある最終月で終わる）。途中の欠月は残す
- パーサーを直したら保存済み HTML を `--reparse`

TDnet 短信 XBRL は取得済み ZIP（`scripts/tdnet_xbrl_r2.py`）を `xbrl_filings` / `xbrl_facts` / `xbrl_financials` に載せる。
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
| `GET /api/earnings?date=2026-08-14` | その日の決算速報一覧。1行 = 1開示で QoQ(Q単独の前四半期比) と YoY(累計の前年同期比) を4指標ずつ返す。決算短信本体のみ (予想修正のみの開示、および損益が初回と同一の再提出は除外。損益が変わった再提出は `restated` を立てて残す)。`product_category` が REIT (`013`/`022`) の銘柄は出さない |
| `GET /api/earnings/detail/{code}?disclosure_no=…` | 選択銘柄の累計テーブル (過去4期+当期の同一四半期 + 中間予想 + 通期予想 + 前期通期実績) と3か月決算テーブル (直近12四半期のQ単独)。各行にカレンダー範囲 (`26.04-06`) と発表日を付ける。同一銘柄が同日に複数開示する日があるため `disclosure_no` で一意に引く (`?date=` でも可) |
| `GET /api/earnings/calendar?month=2026-08` | 日別の決算件数 (カレンダー表示用。一覧と同じく本体のみを数え、REIT も除外する) |
| `GET /api/chart/{code}?tf=D|W|M&ma=50,150,200&rel=topix` | 株価チャートデータ。W/M は日足を ISO週 / 暦月で集約 (足の日付は期間の最終営業日)。MA 期間は営業日換算 (週=÷5、月=÷20) |
| `GET /api/earnings-performance/{code}` | 決算日からの対TOPIXリターン |
| `GET /api/screener?stage=2&rs_min=70&sort=rs` | ステージフィルタ |
| `GET /api/stocks/search?q=トヨタ` | 銘柄検索 |
| `POST /api/v1/tables/{code}/{year}/elements` | 月次表の要素登録。表が無ければ作成。保存名をトークン正規化（yoy は末尾 `(前年比)`）してから既存行を探す。正規化後に同一なら同一行（短い保存名は正規名へリネーム）。完全一致は冪等。類似は 409 (force=true で強制)。`stocks` に無ければ 4xx `NOT_IN_MASTER` |
| `POST /api/v1/tables/{code}/ingest` | PDF 列順 + as_of 時点の最新 `fiscal_year_end` から暦年を割り当てて登録。year は受け取らない。マスタ無しでは 4xx `NOT_IN_MASTER` |
| `GET /api/v1/tables` | 月次表がある銘柄一覧 (code 順)。各要素は `{code, name, years, market, sector33, industry, last_ingested_at, is_new, ingested_today, all_store_yoy, existing_store_yoy}`。name/market/sector33 は stocks を LEFT JOIN (無ければ null)。industry は無ければ `その他`。years は昇順。表が無ければ `[]` |
| `PUT /api/v1/tables/{code}/industry` | 月次業種。body `{industry}`。stocks 無ければ 4xx `NOT_IN_MASTER`。月次表が無ければ 4xx `TABLE_NOT_FOUND` |
| `GET /api/v1/tables/{code}/{year}` | 月次表。要素は登録順。`values` は 1〜12。無い月は null（0 は実ゼロ）。1年通して値のない行は出さない。表が無ければ 404 |
| `GET /api/v1/tables/{code}` | その銘柄の年度一覧 |
| `DELETE /api/v1/tables/{code}/{year}/elements` | 空の月次行を消す。body `{element}`。値が残っていれば 409 `ELEMENT_HAS_VALUES`。表は残す。未登録は 404。マスタ無しは 4xx `NOT_IN_MASTER` |
| `PUT /api/v1/tables/{code}/{year}/values/batch` | 複数月を検証してから一括 commit。マスタ無しは 4xx `NOT_IN_MASTER` |

月次 API のエラーは `{"error": {"code": "...", "message": "..."}}`。認証は既存アプリと同じ (アプリ本体には無く、本番は nginx Basic 認証)。

### 決算表の表示
- タブは **累計が先**、3か月が後。既定は累計
- **累計**: 同一四半期の年次比較の下に、通期予想・中間予想・前期通期を行として足す
  - 2Q/3Q の期は期首〜当期末 (`26.04-09`) で、Q単独の3か月 (`26.07-09`) と区別する。年をまたぐ累計は `25.10-26.06`
  - 通期行に 4Q バッジは付けない。5Q は通期に潰さず `27.3期 5Q`
- **3か月**: Q単独を時系列。決算期は当該3か月 (`26.07-09`)。FY単独だけ 4Q と書く
- 最新実績行を強調。増減率は各セル下に出す (前年同期比の重複行は置かない)
- 営利率 = 営業利益 / 売上。IFRS・米国基準は経常利益列を淡色

### 月次タブの画面

画面は `docs/DESIGN_monthly.md`。書き方は `docs/SCREEN_SPEC.md`。

## 6. 日次バッチ (19:30 JST)

1. `/equities/master` で銘柄マスタ更新 (ProdCat 含む)
2. `/equities/bars/daily?date=当日` + `/indices/bars/daily/topix` 取込
3. `/fins/summary?date=当日` 取込
4. indicators 再計算 (MA/52週/RS/ステージ。RSユニバースは内国株券のみ) → earnings_events 更新

日次は **計4リクエスト** で完了する。決算開示は15時以降が多いため、
当日夜(19:30)と翌朝6:00の2回実行して取りこぼしを防ぐ。

冪等設計 (UPSERT) のため何度実行しても安全。バックフィルは取得済みの日付を
スキップするので、中断しても再実行すれば続きから埋まる。

## 7. ホスティング

本番は **ConoHa VPS + 同じディスク上の SQLite**。Neon / Vercel Postgres の無料枠 (0.5GB) には 5 年分日足が乗らない。アプリはサーバレス前提ではないので作り直さない。

| 役割 | 置き場 |
|---|---|
| API + SPA | 同一 VPS の uvicorn（systemd、ワーカー1） |
| DB | `stock.db`（VPS の SSD）。`DATABASE_URL` は今の SQLite のまま |
| バッチ | crontab（19:30 JST と翌朝 6:00） |
| 公開 | nginx + Let's Encrypt。個人利用は Basic 認証 |
| 開発 | 今どおり Windows + `.\run.ps1` |

推奨プランは **メモリ 2GB / SSD 100GB**（東京）。512MB は Python + pandas + 1GB 級 SQLite で足りない。時間課金で立ててから長期契約を判断する。

データ量: 4,000銘柄×5年×250日 ≈ 500万行。SQLite 実ファイルは 1〜2GB になり得る（本番は約 1.1GB）。VPS ディスクなら余裕。
ローカル backfill 完了後に `stock.db` を scp する（サーバで取り直さない）。

見本は `deploy/`（systemd / nginx / crontab）。SQLite は複数ライターに弱いので uvicorn はワーカー1。日次バッチは 4 リクエストで短い。

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
