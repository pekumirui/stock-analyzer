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
- **フロント**: `static/index.html` + `static/css/` + `static/js/` の vanilla JS SPA (lightweight-charts)。スマホ対応
- **バッチ**: `jobs/market/run_batch.py` を cron / スケジューラで日次実行

## 2. J-Quants API

**V2 の Light プラン**を使う。エンドポイント・項目・プラン別の取得期間とレートリミットは J-Quants の MCP で引く。ここには本ツールの選び方だけ書く。

- 取込は**日付ループ**。`date` だけ指定して全上場銘柄のその日を 1 リクエストで取る（銘柄ループはしない）。調整済み株価は API の値をそのまま使う（CSV 一括 + 自前調整はしない）
- `jquants.py` はエンドポイント群ごとにスライディングウィンドウで自主規制する。パスが `/fins/` で始まるものはプランと別枠。429 は指数バックオフ（5→10→20→40→60 秒）で最大 6 回再試行
- RS に要る 252 営業日 + α は Light の取得期間で足りる

## 3. DBスキーマ

```sql
stocks            -- 銘柄マスタ
  code TEXT PK    -- 5桁コード (例 "72030", "262A0")。先頭・末尾は数字、途中は 0-9A-Z
  name, sector33, market, product_category, updated_at
  edinet_code     -- EDINET コード。jobs/edinet/edinet_codes.py が付ける。無い銘柄は NULL (docs/DESIGN_edinet.md)
  -- product_category = J-Quants ProdCat
  -- 既存DBは migrate で列追加。値は次の日次バッチの銘柄マスタ更新で埋まる
  -- (株価・財務の再取得は不要)

daily_prices      -- 日足
  code, date PK(複合)
  open, high, low, close, volume, turnover      -- 調整前(報告時生値)
  adj_factor
  adj_open, adj_high, adj_low, adj_close, adj_volume  -- 調整済(分析はこちらを使う)
  market_cap                                     -- 時価総額(百万円)
  upper_limit, lower_limit, ex_rt                -- J-Quants UL / LL / ExRT (UL / LL は取込前の行が NULL)

topix             -- TOPIX日足
  date PK, open, high, low, close

statements        -- 決算短信 (J-Quants /fins/summary ほぼ生保存)
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

monthly_element_sources  -- 送られてきたままの行名 → 今の保存名。正規化の差分スナップショットの入力 (KAB-17)
  code, year, source_label, unit PK  -- source_label は前後の空白だけ落とした受信のまま。unit も受信のまま (yoy_pct 可)
  element                 -- 今の保存名。付け替え・統合 (rename_monthly_element) に付いていく。行を消すと一緒に消える
  via                     -- api (POST elements・ingest の既定) / ir_html / ir_xls / ir_json (ingest の via) / doc (PUT doc の新規行) / legacy
  first_seen_at, last_seen_at
  -- source_label は一度入れたら書き換えない。記録前からある行はデプロイ時に保存名そのものを legacy で入れる

pts_snapshots     -- 株探の PTS ナイトタイムセッション ランキング。取得 1 回ごとにランキング全行
  fetched_at, ranking, code PK  -- 取得時刻 JST / night_up・night_down・night_volume / 5桁コード (FK なし)
  session_date            -- 比べている通常取引の終値の日（株探の見出し「D日終値」から。取得日ではない）
  as_of                   -- 株探の「HH:MM現在」。同じ session_date・ranking・as_of は書かない
  rank, name, market, base_close, pts_price, pts_chg_pct, volume
  -- 索引 (session_date, ranking)

edinet_filers / edinet_documents / edinet_list_days
                  -- EDINET の提出者・書類一覧。docs/DESIGN_edinet.md
large_holding_reports / large_holdings
                  -- EDINET 大量保有報告書（書類 1 行 / 提出者・共同保有者 1 行）。docs/DESIGN_edinet.md §5
edinet_buybacks / edinet_buyback_resolutions
                  -- EDINET 自己株券買付状況報告書（書類 1 行 / 決議 1 行）。docs/DESIGN_edinet.md §6
edinet_tender_offers / edinet_tender_offer_docs
                  -- EDINET 公開買付け（案件 1 行 / 書類 1 行）。docs/DESIGN_edinet.md §7

fx_rates          -- ECB 対EUR 日次レート (Frankfurter api.frankfurter.dev/v1)
  date, quote PK  -- quote は ISO 4217（USD / JPY 等）。EUR 自身は持たない
  rate            -- 1 EUR = rate quote。任意ペアは EUR クロス（quote/base）

material_monthly  -- サプライタブの素材ごとの月次量 (e-Stat)
  material_id, month, metric, source PK  -- material_id は config/supply/supply_chain.json の id (FK なし)
                                          -- month は YYYY-MM、metric は production / shipment / inventory / import / export
                                          --   / processing (原油処理) / domestic_sales (国内向販売)
                                          -- source は seidou:<報告番号> (生産動態) / sekiyu (石油統計) / trade (貿易)
  value, unit     -- 単位は統計のまま (t / kl / 本)。kl と t は換算しない
  revision        -- 1 速報 / 2 確報 / 3 年報 (年間補正後)。下がる上書きはしない
  origin          -- 取り込んだ e-Stat の表 ID か Excel のファイル名
  is_estimate     -- 按分の推計値 (いまは常に false)
  updated_at
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
- `via` は経路（`api` / `ir_html` / `ir_xls` / `ir_json`）。省略は `api`。それ以外は 400。書いた行ごとに元の行名を `monthly_element_sources` に残す（dry_run では残さない）

**銘柄マスタとの関係。**

- 書き込みは `stocks` に行がある code だけ。ingest / POST elements / 訂正 PUT / industry PUT は先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`
- FK は付けない（既存テーブルへ ADD できない）。`PRAGMA foreign_keys` も有効化しない
- デプロイごとの `jobs/migrate.py`（`app/monthly/maintenance.run_deploy_maintenance`）が `stocks` に無い月次行を DELETE する。アプリ起動・各ジョブの `init_db` は触らない（本番の名証単独 30320 / 30660 など。冪等）。順は `monthly_values` → `monthly_element_sources` → `monthly_elements` → `monthly_tables` → `monthly_issuers`

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

**月次ファイルの保存（`jobs/*_r2.py`）。** 保存先はリポジトリの `data/monthly/{code}/`。`SHARE_ROOT` を置くと、その直下の `monthly/{code}/` になる。cron と運用は `deploy/README.md`。共通の約束は次のとおり。

- `{code}` は TDnet の発行体コードそのまま（9279 / 262A）。J-Quants 5 桁にしない。stocks 照合だけ display_code + 末尾 0（9282→92820, 262A→262A0）
- stocks に無いコードはダウンロードしない。DB が読めない／map が空なら警告のみで継続
- 既存ファイルは消さない。IR（PDF/xlsx/HTML）は同じ URL の上書き公開を日付付きで残し、中身の sha256 が同じなら skip
- 3 本のバッチ（TDnet PDF / IR PDF・xlsx / IR HTML）の cron は分ける
- 日付・HTTP・銘柄マスタ・ファイル名の共通処理は `jobs/_r2common.py`（`app` は import しない）。`http_get` は TDnet も IR も同じ。識別 UA はジョブごとで、403 のときだけブラウザ UA に一度切り替え、中間証明書の欠落は AIA で補う。429 / 408 / 5xx は Retry-After があればそれに従い（最大 60 秒）、無ければ指数バックオフ＋jitter（5→10→20→40→60 秒、最大 6 回）。打ち切ったら例外を上げる。発行体ログは原則 `fail issuer {code} fetch:`（IR HTML の 429 だけ下の例外）
- 新しいファイルは `data/` に書く。rclone FUSE（`/home/stock/r2-share`）の切断プロトコルは、dest がまだそのマウント上にあるときに働く。`data/` の dest は `/proc/mounts` を見ない。マウントが切れたら各社 `fail` を並べない。開始時と処理中に dest を probe し、ECONNABORTED / ENOTCONN / ESTALE / ルート消失なら `RestartSec` と同じ 5 秒を挟んで再試行し、駄目なら `R2 マウント切断` の 1 行で exit≠0。正常時の行は変えない。マウントの再起動は `deploy/r2-share.service` と `r2-share-watch.timer`。デプロイはハッシュが変わったときだけ `r2-share.service` を restart する（`deploy/README.md`）

TDnet 月次 PDF（`jobs/monthly/tdnet_monthly_r2.py`）:

- やのしん list をキーワード union。`月次` が無い売上速報・○月度・受注速報・売上推移・業績速報なども取る。`is_monthly_title` が四半期短信・自己株式・新株予約権の月間行使・業績予想を落とす。`月度` 単体では取らず、売上/受注/IRニュースなどのヒントが要る。日次 cron と backfill は同じ判定
- ダウンロード時にインフラファンドを除外する。商品区分は REIT と同じ `013`/`022` しか無いので、名称に「インフラ」を含むものだけ落とす（マスタ欠落時は TDnet 社名の「インフラ投資法人」）
- 通常の REIT は残す。決算速報の REIT 全除外とは別。`MONTHLY_SKIP_CODES`（display_code の4桁）に入った銘柄は保存しない。8985 ジャパン・ホテル・リート投資法人、8798 アドバンスクリエイト（グラフ画像のみ）、8739 スパークス（運用資産残高のストック表）は取得対象外
- stocks に無いコード（名証単独の 3032 / 3066 など）は `unknown-stock` で skip。東証マスタには載せない

TDnet 月次 PDF の自動投入（`jobs/monthly/tdnet_monthly_pdf_sidecar.py` → `jobs/monthly/tdnet_monthly_pdf_ingest.py`）:

- 銘柄ごとの書式は宣言しない。DB に開示月（ファイル名日付の前暦月）の値がある前回の PDF（最大 3 本さかのぼる）から、DB の各行を再現する PDF の行を探して学ぶ。unit は DB から。DB が指数で PDF が増減率なら `yoy_pct`（+100 で一致）
- PDF は `pdftotext -tsv` の語の座標で読む。月見出しの行と列がそろう数値の行（横組み）と、月が行ラベルの表（縦組み）。上期計・累計などの列は捨てる。見出しに年（`25年4月`）があれば使い、無ければ行ごとに値のある右端の月から左へ暦年を振る
- 書くのは各 code の最新 PDF だけで、DB の最新月がその開示月より古いときだけ。次のどれかなら書かない（`manual`）: 開示月に値のある DB の行を全部は再現できない／使う表までの見出しの骨格（数値・日付を伏せる）か使う行の並びが前回と違う／前回無かった行・表に今月の値がある（表の序数込み。見出しと行名が既存と同じ新ブロックも含む）／DB に既にある月と 5 か月に 1 つより多く食い違う／件数・金額が直近 12 か月の中央値から 5 倍以上外れる／前回同じ値だった候補行が今回は違う
- sidecar は PDF の隣の `….pdf-ingest.json`（HTML の `.ingest.json` と別の名前。HTML の ingest は拾わない）。形は ingest と同じで year は無い。values は値のある月だけ（null はセルを消すので送らない。月が重複すれば列番号キー）。`columns` に列ごとの暦年月、`learned_from` に学んだ PDF を残す
- DB 投入は `jobs/monthly/tdnet_monthly_pdf_ingest.py`。latest-per-code・sha256 ledger・行に `force` を付けない点は HTML の ingest と同じ。POST の前に `dry_run` し、mapping が `columns` と違えば POST しない。ledger は `/var/log/app/pdf_ingest_ledger.json`
- 前回の DB の値を正とするので、過去に人が入れ違えた行はそのまま引き継ぐ。速報の訂正は翌月の sidecar が全列を書き直すので追いつく

IR 月次 PDF/xlsx/xls（`jobs/monthly/ir_monthly_r2.py`）:

- TDnet に月次が無い発行体が対象。`config/monthly/ir_monthly.json` の issuers（URL 確認済みだけ載せる）
- 表計算と pdf が両方あるページは表計算だけ取る。xlsx があれば xlsx、なければ xls（大東建託 `history_*.xls`）
- AWS ELB が識別 UA を 403 する発行体は、同じ `http_get` がブラウザ UA に一度だけフォールバックする
- 中間証明書が欠ける／付け違う発行体は、同じ `http_get` がリーフの AIA (CA Issuers) から中間を補って検証し直す。検証は切らない（7544 スリーエフ）
- 大東建託 (1878) の `history.xls`、ユナイテッドアローズ (7606) の xlsx は `jobs/monthly/ir_monthly_xls_ingest.py` が `config/monthly/ir_monthly_xls.json` の列・行指定で ingest JSON を作り loopback POST する（`via=ir_xls`）。year は送らない。汎用 xlsx パーサは無い。銘柄クラスは作らない。HTML ingest の cron には混ぜない
- cron は `--all --post`（10:40 / 16:40。取得の後）。config の全銘柄について `data/monthly/<code>/` の最新ファイル（ファイル名の日付）を送る。毎回全列を送るので、速報の訂正（1878 の前月の空室数など）も次の回で追いつく。同じ値のセルはサーバが書き換えない（`last_ingested_at` も動かない）。銘柄ごとに `post` / `fail` を 1 行、最終行 `ir_monthly_xls_ingest done posted= skipped= failed=`。1 銘柄の失敗で残りを止めず、失敗があれば exit≠0（KAB-117）

IR 月次 JSON（`jobs/monthly/ir_monthly_json_ingest.py`）:

- スギHD (7649) の公開 CloudFront JSON。E-IR と同じく GET して ingest JSON を書き、loopback `POST /api/v1/tables/{code}/ingest` する（`via=ir_json`。year は送らない）。crontab は HTML ingest と同じ 10:30 / 16:30（`deploy/cron-run.sh`）。銘柄クラスは作らない。HTML ingest のレジストリには混ぜない
- JSON の `year` は HTML `data-year`（暦年。2027年2月期の3月は year=2026 month=3）。今年度だけ。過去年度の表は入れない
- 伸び率は +100 して `yoy_index`、`digits` で小数1桁。フィールドと行名は `config/monthly/ir_monthly_json.json`
- `values` は値のある月だけ（`{月: 値}`。null は既存セルを消すので送らない）。同じ値のセルはサーバが書き換えない。失敗時は最終行 `failed=K` で exit≠0

IR 月次 HTML（`jobs/monthly/ir_monthly_html_r2.py`）:

- `http_get` が打ち切った 429 は `failed` にせず `skip issuer {code} rate-limited:`。同一ホストは残りを `skip issuer {code} host-cooled {host}`。summary の `failed=` と exit=1 は 404・パース・I/O などだけ（`failed>0`）。再試行回数は増やさない
- `.html`（原本）と `.ingest.json`（派生。`POST /api/v1/tables/{code}/ingest` と同じ形）を隣に置く。自動 POST しない
- DB 投入は別スクリプト `jobs/monthly/ir_monthly_html_ingest.py`。code ごとにファイル名日付が最新の `.ingest.json` を loopback へ POST する。year は送らない。sha256 が前回成功分と同じなら POST しない。行に `force` は付けない（同名で unit が違う行・類似名は 409 で止め、人が `--force --only` で入れる）。`--months` は暦年の `YYYY-MM` を `only_months` として 1 回 POST する（年はサーバの mapping。部分投入は ledger を更新しない）。cron は producer（10:20 / 16:20）の後（10:30 / 16:30）。ledger は dest ではなく `/var/log/app/html_ingest_ledger.json`
- 対象は `config/monthly/ir_monthly.json` の html。セレクタは json（`follow_re` / `source=eir` / `parse`）。`parse: true` は `jobs/monthly/html_issuers` の `parse`（`PARSERS`。`i<code>.py` を置けば載る）。各モジュールが表・行・単位を宣言する。`parse: false` はモジュールを作らない
- unit 判定の context は page 見出しに加え、当該 table 直上の section とページ内の `(単位：%)` / `単位:%` を含む。ページに単位表示が無く指数な銘柄、および見出しが伸び率でもセルが符号なし100基準の銘柄（3148 クリエイトSD）は、モジュールで `yoy_index` を宣言する。見出しに 増減比/伸び率 があれば `単位:%` より `yoy_pct` を優先（ツルハ・サガミ）
- 年度別ページは `follow_re` で最新年を辿る。画像・本文のみは `parse: false`（HTML は残し JSON は書かない）
- E-IR は Pronexus JSONP の月次 item `text` を `.html` として保存する（sha256 はその HTML）
- `as_of` は開示日。なければ表の最終値の年月（セルの年 or 表直前の期見出し / `YYYY年M月期` / `YYYY年度` / `YYYY年M月～YYYY年M月`）。取れなければ JSON は書かない
- 複数年度のアーカイブは期が新しい表を優先。同じ期末なら最終値の新しい表を優先。12か月埋まっている表は最終値では選ばない
- ミニストップ (9946) は `jobs/monthly/html_issuers/i9946.py`。`全店売上` を選び、`売上高合計` だけ残す。選んだ表の次の表が同じ行（売上高合計前年比 / CVS前年比 / 店内加工FF前年比）で月が続く（3〜8 → 9〜2）ときは同じ行に足す。`1店1日当り` と CVS / 店内加工FF は採らない
- やまや (9994) は `jobs/monthly/html_issuers/i9994.py`。既存店と全店をマージし、卸計は選ばない。`当 年`→`売上高`、`前 年` は落とす。unit は `yoy_index`
- ロック・フィールド (2910) は `jobs/monthly/html_issuers/i2910.py`。`既存店売上` / `全店売上` をマージし、業態名の行に見出しのスコープを付ける（付けないと既存店の数値が裸の `売上高(前年比)` になる）。表の最下行 `合計` は会社の `既存店/全店 売上高前年同月比`（canonicalize で根の `既存/全店 売上高(前年比)`）
- フジ (8278) は `jobs/monthly/html_issuers/i8278.py`。改装の店舗面積リストは選ばず、営業速報に総店舗数だけ足す
- 同ページの店舗数表と売上表は、月軸と表のセル年が同じとき同一 payload に足す。行に `YYYY年M月` がある表の as_of はページ全体の「現在」よりセルの年を優先（6537）
- クリエイトSD (3148) は `jobs/monthly/html_issuers/i3148.py`。見出しが伸び率でもモジュールが `yoy_index` を宣言する
- 1表に複数年が並ぶアーカイブ（大黒天物産）は同月の先勝ち（新しい方）。`YYYY年（令和N年）度` 年度帯は見出しに入れない
- ページ見出しが売上の前年比でも、客数・客単価・店舗数・拠点数・営業所数・開店/閉店/出店/退店/開設/撤退の行には `売上高前年同月比` を足さない。店舗数・拠点数・出退店は件数なので unit も `number`
- 数値の無い 既存店/全店 の帯行は、直後の指標行に付ける
- 1表に上期と下期の月見出しが続くとき（神戸物産の単体月次）は後半の月も同じ行に足す。行名に全店は付けない
- 神戸物産 (3038) は `jobs/monthly/html_issuers/i3038.py`。同じページの「店舗仕入れ前年対比」は月が揃うとき足す。セグメントは仕入れ。脚注の商品出荷は行名にしない
- year は書かない。`△1.9` は `-1.9`。unit は見出しから（上の表）。`累計` / `通期計` / `上期計` は months に入れない
- HTML ingest JSON の `months` は末尾の空列を落とす（値のある最終月で終わる）。途中の欠月は残す
- パーサーを直したら保存済み HTML を `--reparse`。HTML が同じでも、現行パーサの `.ingest.json` が保存済みと違えば hash skip 時に書き直す（古い sidecar を latest-per-code に残さない）

TDnet 短信 XBRL は取得済み ZIP（`jobs/xbrl/tdnet_xbrl_r2.py`）を `xbrl_filings` / `xbrl_facts` / `xbrl_financials` に載せる。
スキーマとジョブは [`docs/DESIGN_xbrl.md`](docs/DESIGN_xbrl.md)。EDINET の提出者・書類一覧は [`docs/DESIGN_edinet.md`](docs/DESIGN_edinet.md)。決算速報 UI の正本は当面 `statements` のまま。XBRL 層で上書きしない。

**PTS ランキング（`pts_snapshots`）。** 株探 `https://kabutan.jp/warning/` の `pts_night_price_increase` / `pts_night_price_decrease` / `pts_night_volume_ranking`（夜間 PTS。株価は 15 分ディレイ）。取得は `app/market/pts.py`、ジョブは `jobs/market/pts_snapshot.py`。キー不要。画面は `docs/DESIGN_pts.md`。

- 1 ページ 15 銘柄。各ランキング 5 ページ（75 銘柄）まで。リクエストの間は 3 秒
- 1 ページ目の見出し「通常取引 D日終値」の D を、今日以前で一番近いその日にして `session_date` にする。夕方の開始前や休日は前のセッションのまま出るため。前夜の最終（株探のナイトタイムは 06:00 まで）は翌日 15:30 の回が拾う
- 1 ページ目の「HH:MM現在」が同じセッション・ランキングで保存済みなら、残りのページを取らずに飛ばす
- 3 本とも取得に失敗したら非ゼロ終了。cron は平日 15:30〜24:00 の 30 分ごと（`deploy/crontab`）
- 翌営業日の始値は `daily_prices` の `session_date` より後の最初の日足の `open`（調整前）。19:30 の日次バッチの後に埋まる

**VPS パッケージ一覧（`jobs/ops/vps_packages_r2.py`）。** 読み取り専用。`data/inventory/` へ書く。中身と cron は [`deploy/README.md`](deploy/README.md)。

**為替（`fx_rates`）。** Frankfurter v1（`https://api.frankfurter.dev/v1`、ECB、基準通貨 EUR、キー不要。旧 `api.frankfurter.app` は 301）。UI は出さない。

- 保存は対 EUR だけ。USD/JPY などは `EURJPY / EURUSD`。ヘルパーは `app.fx.cross_rate`
- 時系列エンドポイント `/{from}..{to}` を約 1 年チャンクで取る。UPSERT で冪等
- 初回は `python jobs/fx_rates.py --backfill`（1999-01-04〜）。以後は最終保存日以降の差分（その日を含めて取り直す）
- 日次 cron は 08:00 JST（ECB 公表 16:00 CET の翌朝）。`deploy/crontab` → provision が反映
- 休日・非公表日は指定日以前の直近営業日（`as_of`）。期間 MCP は公表日だけ返す
- MCP: `fx_rate`（指定日）/ `fx_rates`（期間）

**素材の月次量（`material_monthly`）。** 統計の品目と素材の対応は `config/supply/material_stats.json`。共通部分は `app/supply/material_stats.py`、取得は `app/supply/estat.py`（e-Stat API、`ESTAT_APP_ID`。無ければ飛ばす）と `app/supply/meti.py`（経産省 Excel。生産動態統計と資源・エネルギー統計（石油）。キー不要）。ジョブは `jobs/supply/material_stats.py`。画面は `docs/DESIGN_supply.md`。

- 素材ごとに統計を 1 つ決め、指標ごとに付け替えない。需給をまとめて出す統計がある素材はその統計から全指標を取る（原油と石油製品 7 素材は資源・エネルギー統計（石油））。それ以外は生産動態統計 + 貿易統計。対応表の検査は 1 つの (素材, 指標) を 2 つの統計に載せると落とす。対応表から外した (素材, 指標, 統計) の行はジョブの最初に消す
- 生産・出荷・月末在庫は経済産業省生産動態統計。同じ `source`（`seidou:<報告番号>`）に 2 か所から書き、`revision` が下がる上書きはしない（同じなら後勝ち）
  - 経産省「統計表一覧」の Excel（シート `実数表` は縦持ち）: 速報 `h2daaYYYYMMs.xlsx`（revision 1、翌月末。品目と指標が少ない）、確報 `h2daaYYYYMM_jikei.xlsx`（2、翌々月中旬。その年の 1 月から）、年間補正号 `h2daaYYYY_hosei_jikei.xlsx`（3、前年分）。既定の User-Agent は 403 になるので名乗った User-Agent を付ける
  - 一覧に載らない過去年の 12 月の確報 `kakuho/YYYY12/h2daaYYYY12_jikei.xlsx`（2、その年の 1〜12 月）。e-Stat の年報に無く、年間補正号も一覧から消えた年の品目を埋める（銑鉄計の 2024 年）。`--december 2024` で手で一度だけ読む（取り込み済みなら落とさない）。2022 年以前は置いていない
  - e-Stat（00550200）の「YYYY年 時系列表(<報告番号>_…)」（revision 3）。年報だけで当年は無い。2021〜2024 年の表は品目が `cat02`、月が `cat01`、2025 年は `cat01` と `time`。月は `202501` と `2022年1月` のどちらも読む
- 品目は品名で引く（NFKC）。品目番号は年で振り直される（化学繊維は 2023 年、AS・ABS 樹脂は 2024 年に 1 品目へ）。年や速報で品目の分け方が違うものは候補の並びで書き、品名が全部その表にある最初の並びを使う。揃う並びが無い表（速報に無い二輪タイヤや硫安など）ではその素材を書かない
- 素材に足す品目が 1 つでも秘匿（`X`）や不詳の月は、その月・指標を書かない（過小な合計で系列が跳ねるのを避ける）。`-` は 0、空欄（未公表）は無し。秘匿の年がある品目は対応表から外し、`note` に書く（ポリスチレン発泡用、NBR・EPDM、PET 容器用）。単位 `千本` は `本` に、`kg` は `t` に直す（ダイカスト・飲料缶。貿易統計とそろえる）
- 資源・エネルギー統計（石油）（`source` は `sekiyu`）: 原油（生産・輸入・原油処理・在庫）、ガソリン・ナフサ・ジェット燃料油 + 灯油・軽油・A重油 + B・C重油・アスファルト・液化石油ガス（生産・在庫・輸入・輸出・国内向販売）、天然ガスの生産。在庫は月末在庫。液化石油ガスとアスファルトは t、ほかは kl
  - 年報 `sekiyuka/xls/h2dhhpeYYYYk.xlsx`（revision 3、その年の 1 月から翌年 3 月）。横持ちの表（原油地域別、国別輸入の「合計」/ 原油処理 / 原油在庫 / 石油製品月別国内向販売 / 月別輸入 / 月別輸出。2021 年は表題が「国内向月別販売」）。最初の数値の行（平成・令和）より上の見出しから数値の列を決める。単位「kl、アスファルト以降:t」はアスファルトの列から右が t。在庫は別の形の「石油製品月別業態別在庫」（月ごとに 計 / 製油所 / 製造・輸入業者 の 3 行。計 だけ取る）。製品の月別生産は無い
  - 確報 `sekiyuka/xls/seYYYYMMkakji.xlsx`（revision 2、前年 10 月からの 15 か月の時系列）。一覧の最新 1 本と、年報のある年の 12 月号（`seYYYY12kakji.xlsx`。一覧には今年の分しか載らないが過去の分も同じ名前で置いてある）。12 月号で年報に無い製品の生産を埋める。品目ごとのブロック（（４）ガソリン …）を見出し行「年・期・月」で切る。見出しの無い左ブロックは上の続き（原油の在庫）
  - 速報 `sekiyuso/result/xls/h2j*j.xlsx`（revision 1、当月だけ）。シート「需給概要」（列が品目、行が指標）と 1 行目が「原油受払」のシート
  - 品目名は空白を除いた NFKC。ジェット燃料油 + 灯油、A重油 + B・C重油 のように足すものは 1 つでも欠けた月を書かない。`―` は 0
  - 取れなかったら（WAF の確認・空の本文・一覧ページ）行単位の `fail meti …` は page しない。ジョブは止まった件数と fail の件数 K を数え、最終行を `material_stats done written=N failed=K` にし、K≥1 なら非ゼロ終了する（fluent-bit の `failed=` と cron-run の `fail cron`。ジョブ出力では summary の 1 行だけが page する）。cron は `--ledger /var/log/app/material_stats_ledger.json` に生産動態・石油統計ごとの「最後に全部取れた日」を残し、そこから `METI_FAIL_PAGE_DAYS`（既定 3）日たっても取れなければ `material_stats meti <kind> not fetched for N days …` を出す（`failed=` は付けない）
  - 落とした Excel は `data/stats/meti/{seidou,sekiyu}/<ファイル名>` に控えを残す（検証と再投入用。`SHARE_ROOT` があればその直下）。中身が同じなら書かず、同じ名前で中身が変わったら `<名前>_YYYYMMDD` で別に残す（前の版は消さない）。変わらないファイル（final）は控えがあればそこから読み、経産省へは行かない。取り込み済みでも控えが無ければ一度落として残す。`--reread` は取り込み済みの final も控えから読み直す（対応表を変えたとき）、`--refetch` は経産省から落とし直す（正誤訂正）。控え先が書けなければ `warn meti archive off` を出して控えなしで取り込む
  - METI（CloudFront）は同じ IP から続けて取ると AWS WAF が bot 確認を返す（202、`x-amzn-waf-action: challenge`、本文は空）。2 秒あけて取り、確認が返ったら 60 秒待って 1 回だけ取り直す。それでも確認ならその回の取得をやめて次の cron に回す（取り直しを重ねると長引く。一覧ページで返っても同じ）。空や xlsx でない本文は `fail` を出してその 1 本を飛ばす
  - 変わらないファイル（年報・年間補正号・過去年の 12 月の確報）は取り込み済みのファイル名なら落とさない。ただし石油統計は、対応表の項目がそのファイル名の行でそろっていなければ（項目を足した後の初回）落とし直す。12 月の確報は年報に無い項目（製品の生産）だけ見る。正誤訂正のときは `--refetch`。cron は生産動態 07:15、石油統計 07:45 とずらす
- 輸入・輸出: 普通貿易統計（00350300）の「全国分 品別国別表 輸入 / 輸出」（`source` は `trade`。表題で速報の月は revision 1、ほかは 2）。石油統計で取る素材は載せない。統計品目番号 9 桁の前方一致で素材に寄せ、国は合計する。輸入と輸出で番号体系が違う
- 輸出入の相手国別（`material_trade_country`。素材 × 月 × 輸出入 × 国）: 同じ表の値を国ごとに足して別に持つ。国は貿易統計の国コード 3 桁（`137_サウジアラビア` の `137`）と国名。revision は `trade` と同じで、下がる上書きはしない。`trade.items` の素材に加え、国合計は石油統計が正の素材も `trade.country_items` に書けば国別だけ取る（原油 2709、LPG 271112・271113）。対応表から外した (素材, 輸出入) の行はジョブの最初に消す
- 数量は 単位1 があれば 数量1、無ければ 数量2（化学品の多くは KG）。KG と MT は t に、KL は kl に、NO は 本 に寄せる。0 の月は書かない（API が埋める。`docs/DESIGN_supply.md`）。未公表の月は 0 で入っているので、表題の「(1-7月：確報、8月：速報)」の最後の月より後は捨てる。前年の表は 2021 年からの 5 年分を持つ
- 冪等。初回は `python jobs/supply/material_stats.py --years 2021-<今年>`。cron は経産省 Excel が毎日（生産動態 07:15、石油統計 07:45）、e-Stat が月曜 07:10（前年と今年）
- 見掛け消費（生産 + 輸入 − 輸出 − 在庫の増加分）は保存せず API で出す。4 つと前月在庫が同じ単位で揃う月だけ。revision は材料の最小


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
損益が変わったものは「訂正」として残す。**EPSは比較に含めない。**
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
決算発表後に買えた最初の値（調整後）を基準価格にし、各期間の `(株価リターン − TOPIXリターン)` を算出。テーブルで +1d/+5d/+20d/+60d/現在 を表示。買えない値（発表前の始値、張り付いた値）は使わない。

- 大引け（2024-11-05 から 15:30、それまで 15:00）以降の発表は翌営業日の始値
- それより前（場中）の発表は発表当日の終値。発表後の最初の値は Light では取れない。当日がストップ高で引けた（終値が高値で `upper_limit` が 1。NULL の過去の行は前日終値から制限値幅いっぱい上げたか）なら引けでは買えないので翌営業日の始値
- 始値で買う日が寄らずのストップ高・ストップ安なら翌営業日へ送り、寄った日まで続ける。寄らずは四本値が同じで、`upper_limit` / `lower_limit` が 1（NULL の過去の行は前日終値から制限値幅いっぱい動いたか。`app/market/price_limit.py`）。寄ってすぐ張り付いた日も日足では区別できないので寄らずとみなす
- +Nd は始値で買ったなら基準日を、終値で買ったなら翌営業日を 1 日目として N 日目の終値。TOPIX も基準と同じ値（始値か終値）から
チャート下のイベント重ね合わせ（既定オフ）で決算発表日に「決」マーカーを出せる。今後のイベント種別も同じチェック列に足す。決算速報タブだけ、選択中の発表日は重ね合わせオフでも1件出す。週足・月足では発表日を含む足（期間末）に載せる。

## 5. API設計 (抜粋)

| Method/Path | 内容 |
|---|---|
| `GET /api/earnings?date=2026-08-14&today=2026-10-02` | `{kind, actuals, scheduled}`。`actuals` は開示1件が1行で、QoQ(Q単独の前四半期比) と YoY(累計の前年同期比) を4指標ずつ返す。金額は `single`（Q単独。算出できなければ null）と `cumulative`（累計）で、円。決算短信本体のみ (予想修正のみの開示、および損益が初回と同一の再提出は除外。損益が変わった再提出は `restated` を立てて残す)。`scheduled` は `{code, name, periods:[{fq, fye, label}]}` で銘柄1行。どちらの行にも `sector33`（マスタに無ければ null）。`kind` は `actual` / `scheduled` / `mixed`。過去は `actual` で `scheduled` は空。今日は開示と、そのコードが今日の開示に無い未発表。未来は予定だけ。`today` を省略するとサーバの今日。台帳が lookback を埋める前の未来には `ready: false`（空なら画面は 予定未取得）。`product_category` が REIT (`013`/`022`) の銘柄は出さない |
| `GET /api/earnings/detail/{code}?disclosure_no=…` | 選択銘柄の累計テーブル (過去4期+当期の同一四半期 + 中間予想 + 通期予想 + 前期通期実績。本決算は予想が `raw_json` の来期予想 `NxF*` で、前期通期は出さない) と3か月決算テーブル (直近12四半期のQ単独)。各行に会計期末 (`26.11`) または Q単独の四半期末 (`25.08`) と発表日を付ける。同一銘柄が同日に複数開示する日があるため `disclosure_no` で一意に引く (`?date=` でも可)。どちらも無ければ最新の実績開示（決算速報の銘柄検索が使う）。実績が無ければ 404。`revisions` は通期予想の修正履歴 (`kind` = `forecast_initial` / `forecast` / `forecast_revised` / `actual_fy`、`growth` は前の行からの修正率で同値は `state: flat`)。選んだ開示の日までの開示から作る。データ源は `revision_source`（既定 `EARNINGS_PARAMS["revision_source"]` = `jquants`）で、`?rev_source=jquants|xbrl` で上書きできる。`jquants` は `statements` の `fy_*` と本決算の `NxF*`（期初）と本決算の実績。`xbrl` は `xbrl_financials` の `forecast`/`FY`（連結優先、レンジは中点）と `annual` の実績。組み立ては `app/revisions.py`、データ源は `app/market/revisions.py` / `app/xbrl/revisions.py` |
| `GET /api/earnings/calendar?month=2026-08&today=2026-10-02` | 日付 → `{kind, n}`。`pending` は今日かつ未発表がいるときだけ。件数は銘柄の distinct。疎。一覧と同じく本体のみを数え、REIT も除外する。`today` を省略するとサーバの今日 |
| `GET /api/chart/{code}?tf=D|W|M&ma=50,150,200&rel=topix` | 株価チャートデータ。W/M は日足を ISO週 / 暦月で集約 (足の日付は期間の最終営業日)。MA 期間は営業日換算 (週=÷5、月=÷20) |
| `GET /api/earnings-performance/{code}` | 決算日からの対TOPIXリターン |
| `GET /api/ranking/earnings-perf?month=YYYY-MM` | 決算月ごとの決算発表後パフォーマンス（現在まで）のランキング。`docs/DESIGN_ranking.md` |
| `GET /api/screener?stage=2&rs_min=70&sort=rs` | ステージフィルタ |
| `GET /api/pts?date=2026-10-16&ranking=night_up&at=2026-10-16 23:30:00` | PTS ランキング 1 回分と翌営業日の始値とのギャップ。`ranking` は `night_up` / `night_down` / `night_volume`（ほかは 400）。`date` 省略で最新のセッション、`at`（`fetched_at`）省略かつ不一致で最新の取得。形は `docs/DESIGN_pts.md` |
| `GET /api/stocks/search?q=トヨタ` | 銘柄検索。名前の部分一致（q を NFKC とひらがな→カタカナに寄せたものでも引く）とコードの前方一致。最大 20 件 |
| `GET /api/supply/graph` | サプライタブの素材と流れ（`config/supply/supply_chain.json`）。形は `docs/DESIGN_supply.md` |
| `GET /api/supply/monthly/{material_id}` | 素材の月次量（`material_monthly`）と見掛け消費。形は `docs/DESIGN_supply.md`。素材が無ければ 404 |
| `POST /api/v1/tables/{code}/{year}/elements` | 月次表の要素登録。`unit` 必須。表が無ければ作成。保存名をトークン正規化（yoy は末尾 `(前年比)`）と銘柄エイリアスのあと、保存名と完全一致する行を探す（既存行は作り直さない。`docs/DESIGN_monthly_labels.md`「同一行・保存名の凍結」）。完全一致は冪等。類似は 409 (force=true で強制)。unit の食い違いは 409 `UNIT_CONFLICT` (force=true で上書き)。`stocks` に無ければ 4xx `NOT_IN_MASTER` |
| `POST /api/v1/tables/{code}/ingest` | PDF 列順 + as_of 時点の最新 `fiscal_year_end` から暦年を割り当てて登録。year は受け取らない。`as_of` と各行の `unit` は必須。`dry_run: true` は書かずに mapping と書くはずのセルを返す。`only_months` は `YYYY-MM` で列を絞る。マスタ無しでは 4xx `NOT_IN_MASTER`。mapping の年月が as_of より後なら 400 `FUTURE_COLUMN`。新規行で、`strip_yoy_fragments` 後の名前が同じ yoy と number の既存行が今回の `rows` に無いときは 409 `YOY_NUMBER_CONFLICT`（`force=true` で作成。両方を同じ POST に含めれば通る）。銘柄エイリアスで同じ保存名に寄る行（別表の同じ数字）は 1 行にまとめる。同じ列で値か unit が食い違えば 409 `ROW_MERGE_CONFLICT`（後勝ちで上書きしない） |
| `GET /api/v1/tables` | 月次表がある銘柄一覧 (code 順)。各要素は `{code, name, years, market, sector33, industry, last_ingested_at, is_new, ingested_today, all_store_yoy, existing_store_yoy}`。name/market/sector33 は stocks を LEFT JOIN (無ければ null)。industry は無ければ `その他`。years は昇順。表が無ければ `[]` |
| `PUT /api/v1/tables/{code}/industry` | 月次業種。body `{industry}`。stocks 無ければ 4xx `NOT_IN_MASTER`。月次表が無ければ 4xx `TABLE_NOT_FOUND` |
| `GET /api/v1/tables/{code}/{year}` | 月次表。要素は登録順。各行は `{element, unit, label, values}`。`element` は保存名。`label` は GET 派生（display / group / kinds / 7層）。`values` は 1〜12。無い月は null（0 は実ゼロ）。1年通して値のない行は出さない。全銘柄で親子関係を足す。`config/monthly/monthly_row_order.json` に `tree` があればそれ、無ければ自動の木（`tree_auto: true`）。各行に `tree: {path, leaf, metric}`（根から自分の節までの名前の列、節の中での行名、指標名。指標に振り分けていなければ null。自動の木は全行に指標が付き、3 年を通した並び `order` も付く）、表に `tree`（節 `{name, heading, part, elements, children}` の木。形は config、自動なら表示する 3 年で決まり年で変えない。`elements` はその年の保存名）と `tree_metrics`（指標名の並び。横持ちの一番上の行の順。自動の木は既存 → 全店 → 店舗 の見る順。自動の木は指標どうしの親子 `tree_metric_parent` も付く）を足す。config の木でどの節にも当たらない行に `tree` は付かない（自動の木は全行に付く）。書式は `docs/DESIGN_monthly.md`「親子関係」。表が無ければ 404 |
| `GET /api/v1/tables/{code}` | その銘柄の年度一覧 |
| `DELETE /api/v1/tables/{code}/{year}/elements` | 空の月次行を消す。body `{element}`。値が残っていれば 409 `ELEMENT_HAS_VALUES`。表は残す。未登録は 404。マスタ無しは 4xx `NOT_IN_MASTER` |
| `PUT /api/v1/tables/{code}/{year}/values/batch` | 複数月を検証してから一括 commit。マスタ無しは 4xx `NOT_IN_MASTER` |
| `GET /api/v1/tables/{code}/{year}/doc` | `PUT /doc` の元になる文書。`{code, year, version, elements}`。行は `sort_order` 順で、値の無い行も出す。各行 `{element, unit, values, tree?}`（`values` は `"1"`〜`"12"`、`tree` は木の path）。`version` は行名・unit・並び・値のハッシュ。公開の nginx の口には載せない（Basic の後ろ） |
| `PUT /api/v1/tables/{code}/{year}/doc` | 文書を丸ごと返して 1 トランザクションで書く。body `{version, elements, allow_structure_change?, dry_run?}`。行名は渡したまま（正規化・エイリアスを掛けない）。`was` で改名（値はサーバーが移す）、文書に無い行は削除、`was` の無い新しい名前は作成（`unit` 必須、末尾に並ぶ）。`values` を渡した行は 12 か月を置き換え（無い月・null は削除）、省いた行は値をそのまま。`version` が違えば 409 `VERSION_MISMATCH`。改名・作成・削除・unit 変更は `allow_structure_change=true` のときだけ（無ければ 409 `STRUCTURE_CHANGED`）。`dry_run=true` は書かずに `changes` を返す。config の木に入らない行は `warnings` の `NOT_IN_TREE` |

月次 API のエラーは `{"error": {"code": "...", "message": "..."}}`。閲覧 GET は公開。書き込みは loopback のみ（nginx 経由は 403）。本番の個人ツールは `/app` と Basic 認証。

### 決算速報の画面

画面は `docs/DESIGN_earnings.md`。書き方は `docs/SCREEN_SPEC.md`。

### 銘柄タブの画面

画面は `docs/DESIGN_stock.md`。書き方は `docs/SCREEN_SPEC.md`。原文の保存は `docs/DESIGN_xbrl.md` §15。

### PTS タブの画面

画面は `docs/DESIGN_pts.md`。書き方は `docs/SCREEN_SPEC.md`。

### 月次タブの画面

画面は `docs/DESIGN_monthly.md`。書き方は `docs/SCREEN_SPEC.md`。

### サプライタブの画面

画面とマスタの形は `docs/DESIGN_supply.md`。書き方は `docs/SCREEN_SPEC.md`。

## 6. 日次バッチ (19:30 JST)

1. `/equities/master` で銘柄マスタ更新 (ProdCat 含む)
2. `/equities/bars/daily?date=当日` + `/indices/bars/daily/topix` 取込。戻り値は全部保存する。
   AdjFactor ≠ 1（権利落ち日）の銘柄は `/equities/bars/daily?code=` で全期間を取り直す。
   権利落ちで過去の Adj* がさかのぼって直るので、差分だけでは DB の過去分が古いまま残る
3. `/fins/summary?date=当日` と `/fins/summary?date=前営業日` 取込。前営業日は DB の株価で当日より前の最終日
4. indicators 再計算 (MA/52週/RS/ステージ。RSユニバースは内国株券のみ) → earnings_events 更新。
   2〜3 が 0 件（前営業日は新しく増えた件数）で indicators が株価の最終日まで済んでいれば飛ばす。
   指標の全置換はチャンクごとに commit する（1 本のライターロックを `busy_timeout` 以上握らない）
5. `/fins/earnings-date?date=当日` と `date=昨日`。昨日は暦日。4 の後。株価が 0 件でも行う。失敗しても 4 の結果は残し、プロセスは非ゼロで終わる

定常の夜は **計7リクエスト**（分割・併合の銘柄があればその数だけ増える）。内訳はマスタ、株価、TOPIX、財務の当日、財務の前営業日、発表予定の当日、発表予定の昨日。発表予定の2本は `/fins/` の枠。台帳が空の初回は指標のあと PubDate を `EARNINGS_DATE_LOOKBACK_DAYS` 日さかのぼる。この歩きは 7 に入らない。決算開示は15時以降が多いため、当日夜(19:30)に1回実行する。
19:30 より後の開示（全体の 0.3% 程度）は、翌営業日の回が前営業日の決算を取り直して拾う。

冪等設計 (UPSERT) のため何度実行しても安全。バックフィルは株価と決算を同じ日に commit できた日付を
スキップするので、中断しても再実行すれば続きから埋まる。カレンダーが空、またはその日が失敗したら非ゼロ終了。

`run_batch.py --refetch-prices` は DB にある日足を日付ごとに全部取り直し、指標を計算し直す。
対象は UL / LL が NULL の行が残る日だけなので、中断しても再実行で続きから。失敗した日は飛ばして最後に非ゼロ終了。
1 日 1 リクエストで、5 年分なら約 1,250 日 ≒ 20〜40 分。

## 7. ホスティング

本番は **ConoHa VPS + 同じディスク上の SQLite**。Neon / Vercel Postgres の無料枠 (0.5GB) には 5 年分日足が乗らない。アプリはサーバレス前提ではないので作り直さない。

| 役割 | 置き場 |
|---|---|
| API + SPA | 同一 VPS の uvicorn（systemd、ワーカー1） |
| DB | `stock.db`（VPS の SSD）。`DATABASE_URL` は今の SQLite のまま |
| バッチ | crontab（19:30 JST の J-Quants 日次。株探 PTS は平日 15:30〜24:00 の 30 分ごと。為替は 08:00 JST。パッケージ一覧は 06:40 JST。SQLite バックアップは 05:00 JST） |
| バックアップ | VPS ローカル gzip（直近 1 世代）+ OneDrive（日次 7・週次 4）。手順は [`docs/DESIGN_backup.md`](docs/DESIGN_backup.md) |
| 公開 | nginx + Let's Encrypt。月次閲覧は `/monthly`（Basic なし）。個人ツールは `/app` + Basic 認証 |
| 開発 | 今どおり Windows + `.\run.ps1` |

推奨プランは **メモリ 2GB / SSD 100GB**（東京）。512MB は Python + pandas + 1GB 級 SQLite で足りない。時間課金で立ててから長期契約を判断する。

データ量: 4,000銘柄×5年×250日 ≈ 500万行。SQLite 実ファイルは 1〜2GB になり得る（本番は約 1.1GB）。VPS ディスクなら余裕。
ローカル backfill 完了後に `stock.db` を scp する（サーバで取り直さない）。

見本は `deploy/`（systemd / nginx / crontab）。SQLite は複数ライターに弱いので uvicorn はワーカー1。日次バッチは定常で 7 リクエスト。接続は WAL + `busy_timeout=30s`。指標の全置換はチャンク commit。月次の書き込み API は `database is locked` を短い回数だけ再試行する。`jobs/migrate.py`（deploy 含む）は同じエラーを指数バックオフで再試行する。

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

### ゴールデンデータの DB（開発・クラウドのエージェント・テスト）

`./run.sh golden`（`jobs/golden_db.py`）が空の `stock.db` を作り（スキーマは `init_db`）、`tests/golden/db/<table>.csv` を表ごとに入れる。数秒。`.env` は要らない。クラウドのエージェントの `install.sh` は `stock.db` が無ければこれで作る（`STOCK_DB=full` なら代わりに backfill 2）。

- 中身は実データの切り出し。株価・決算・決算予定は `tests/golden/db/codes.txt` の約45銘柄（ステージ2・RS 上位、時価総額上位、REIT 1、月次の主要銘柄）。銘柄マスタ・TOPIX・決算予定の取得記録は全件。月次は本番の全銘柄・全年。為替は株価と同じ期間。素材は e-Stat / 経産省の直近の年。期間と件数は `tests/golden/db/manifest.json`
- `indicators` は持たず、読み込みのあとに `compute_indicators` で計算し直す（RS ランクは約45銘柄の中の順位になる）
- XBRL は持たない（本番 ZIP は `data/xbrl`）。`monthly_element_sources` は読み込みのあとに `legacy` で埋める
- 作った DB は `PRAGMA user_version = 1`。`install.sh` はこれを見て、ゴールデンデータの DB には日次 batch を回さない（全銘柄の当日分が混ざるため）
- 既に行がある DB には入れない。作り直しは `./run.sh reset y` → `./run.sh golden`
- データの作り直しは `python tools/golden/build_golden_db.py --db stock.db --api https://kabukpi.com`（backfill 済みの `stock.db` と月次の公開 GET から。`--skip market|monthly|fx|material` で表の組を飛ばす）。スキーマを変えたら作り直す。`tests/test_golden_db.py` は CSV の列がモデルと違えば落ちる
