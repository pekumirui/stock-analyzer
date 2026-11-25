# API の応答と規約

FastAPI が公開する REST エンドポイントの応答構造・エラー規約・認証境界をまとめる。どの画面がどの API を呼び、どのテーブルを読み書きするかの対応表は [画面設計](../SCREENS.md) と [データの流れ](../DATA_FLOW.md)（いずれも自動生成）を参照する。画面表示に合わせた JSON の具体例は各タブの画面仕様書に置く。

## 認証・公開範囲・キャッシュ

本番環境（nginx + Cloudflare）での公開範囲とキャッシュ制御は以下のとおりに分ける。API 応答は p95 で 400ms 以下に収める（[`app/api/AGENTS.md`](../../app/api/AGENTS.md)）。

| 経路・対象 | 公開範囲と認証 | キャッシュ・制限 |
|---|---|---|
| `GET /api/v1/tables`、`GET /api/v1/tables/{code}`、`GET /api/v1/tables/{code}/{year}` | 認証なしで公開する | `Cache-Control: public, max-age=60` を付与する |
| 月次の全書き込み API（`POST`・`PUT`・`DELETE /api/v1/tables/...`） | loopback（`127.0.0.1`）からの呼び出しのみ許可する。`X-Forwarded-For` または `X-Real-IP` ヘッダーがあるプロキシ経由の要求は `403 FORBIDDEN` で拒否する（`forbid_proxied_monthly_write`） | ジョブと同一ホスト上の MCP からのみ呼ぶ |
| 上記以外の `/api/*`、および `GET /api/v1/tables/{code}/{year}/doc` | 本番では Cloudflare Access の背後に置く | `/api/chart/{code}`、`/api/topix`、短信原文 HTML はコンテンツハッシュの `ETag` を返し、`If-None-Match` 一致時は `304` を返す |

## 決算速報・ランキング・スクリーナー・PTS

| エンドポイント | 応答の要点 | 詳細仕様 |
|---|---|---|
| `GET /api/earnings?date=YYYY-MM-DD&today=YYYY-MM-DD` | 指定日の実績開示と発表予定を `{kind, actuals, scheduled, ready?}` で返す。REIT（`product_category` が `'013'`・`'022'`）は除外する | [決算速報](earnings.md) |
| `GET /api/earnings/detail/{code}?disclosure_no=…` | 1 銘柄の累計表（過去 4 期 + 当期の同一四半期 + 中間予想 + 通期予想 + 前期通期実績）、3 か月表（直近 12 四半期の Q 単独）、通期予想の修正履歴 `revisions`、中間予想の有無 `notes.half_forecast` を返す。`disclosure_no` が無ければ、最新の決算短信（開示日・年度末・四半期・時刻・開示番号の新しい順。同じ日に出た 2Q と 1Q の訂正なら 2Q）を開く。選んだ開示が同じ期の 2 本目の決算短信（訂正）なら、変わっていない空欄の実績と予想を元の値で埋める（KAB-576）。実績が無ければ `404` を返す | [決算速報](earnings.md) |
| `GET /api/earnings/calendar?month=YYYY-MM&today=YYYY-MM-DD` | 月内の各日付に対し `{kind, n, pending?}` を疎なマップで返す。件数は決算短信本体の銘柄数（distinct）で、REIT は除外する | [決算速報](earnings.md) |
| `GET /api/ranking/earnings-perf?month=YYYY-MM` | 指定月に初回決算短信を出した銘柄を、次の決算の前営業日終値までの騰落率（`return_to_next`）降順で返す | [ランキング](ranking.md) |
| `GET /api/screener?stage=2&rs_min=70&sort=rs&limit=100` | `indicators` の最新営業日における内国株券（`'011'`）をステージと RS ランク下限で絞り、`{date, results}` を返す（`indicators` が空なら `[]`） | [画面設計（スクリーナー）](gen/screener.md) |
| `GET /api/pts?date=YYYY-MM-DD&ranking=night_up&at=…` | 株探の夜間 PTS ランキング（`night_up`・`night_down`・`night_volume`、それ以外は `400`）1 回分と翌営業日始値とのギャップを返す | [PTS](pts.md) |

### `GET /api/earnings` と `detail` の主なフィールド規則

| フィールド | 規則 |
|---|---|
| `kind` / `ready` | 過去日は `actual`、未来日は `scheduled`、当日で開示済みと未発表の両方があるときは `mixed` を返す。`today` 省略時はサーバー当日を使う。発表予定台帳が取得窓を埋める前の未来日には `ready: false` を付ける |
| `actuals` | 決算短信本体のみを 1 開示 1 行で返す（予想修正のみの開示と、損益が初回と同一の再提出は除外し、損益が変わった再提出には `restated: true` を付ける）。金額（`single`・`cumulative`）は円単位で、売上総利益は `xbrl_financials` から補う。`forecast_revision` は、その短信の通期営業利益予想が同一事業年度の直前の時点の予想（開示を古い順に畳んだ値。決算短信の空欄は未定、営業利益の取り下げの印の後は予想なし、`1Q`・`3Q` 型の業績予想修正と同日の予想修正のみの開示は使わない）から `±0.1%` 以上動いたとき `{direction: "up" \| "down", pct}`、それ以外（直前の予想が無い・`0`、今回の予想が空を含む）は `null` |
| `scheduled` | 未発表銘柄を 1 銘柄 1 行 `{code, name, sector33, pts, periods: [{fq, fye, label}], latest}` で返す。`latest` は直近の実績開示（無ければ `null`） |
| `pts` | `actuals`・`scheduled` ともに、そのセッション日の最終 PTS スナップショットにあれば `{price, chg_pct, base_close, as_of}`、無ければ `null` を付ける |
| `revisions`（`detail`） | 選択した開示日までの通期予想の推移（`kind` は `forecast_initial`・`forecast`・`forecast_revised`・`forecast_withdrawn`・`actual_fy`、前回と同値は `state: "flat"`）。各行の `values` はその時点で畳んだ予想で、予想修正で出し直していない項目は前の値のまま。`forecast_withdrawn` は予想の取り下げの行で、取り下げた項目の `values` は `null`、`growth` は `null`。損益をすべて取り下げた後に予想が戻った行は `forecast`（`growth` は `null`）。既定のデータ源は `jquants`（`?rev_source=jquants\|xbrl` で切替可。`xbrl` は取り下げの印を見ないので `forecast_withdrawn` は出ない） |
| `notes.half_forecast`（`detail`） | 1Q と本決算（来期予想）における中間予想の状態を `given`・`fy_only`・`none`・`unknown` で返す。2Q・3Q では `null` を返す |

## 株価・銘柄詳細・大量保有

| エンドポイント | 応答の要点 | 詳細仕様 |
|---|---|---|
| `GET /api/stocks/search?q=…` | 銘柄名（NFKC 正規化とひらがな→カタカナ変換を含む部分一致）とコード前方一致で最大 20 件返す。対象は株券（`'011'`・`'021'`）のみで、ETF・REIT・出資証券は出さない（`product_category` 未投入 DB では全銘柄） | [銘柄](stock.md) |
| `GET /api/chart/{code}` | 日足と業績階段データを列配列 `{code, name, t, o, h, l, c, v, tv, mc, earnings_dates, fund}` で返す。`o`〜`c` と `v` は調整後（欠損時は調整前）、`tv` は売買代金（円）、`mc` は時価総額（百万円）、`fund` は `{t, eps, op_q, op_ttm}`。株価が無ければ `404` | [銘柄](stock.md) |
| `GET /api/topix` | TOPIX の日足終値を列配列 `{t, c}` で返す | [銘柄](stock.md) |
| `GET /api/earnings-performance/{code}` | 指定銘柄の決算発表後リターンと対 TOPIX 超過リターン（`earnings_events`）を返す | [銘柄](stock.md) |
| `GET /api/stock/{code}/overview` | 銘柄ヘッダーと概要タブ用の株価・時価総額・次回決算予定日・バリュエーション指標（予想 PER、実績 PBR、予想配当利回り、実績 ROE、自己資本比率）を返す | [銘柄](stock.md) |
| `GET /api/stock/{code}/financials?statement=BS&periods=8&consolidated=auto` | 有報・短信 XBRL・短信 PDF を統合した財務 3 表（`BS`・`PL`・`CF`、最大 20 期）を科目×決算期で返す。会計基準が変わった銘柄は旧基準分を `earlier` に分けて返す | [銘柄](stock.md)、[XBRL 規則](../jobs/xbrl.md) |
| `GET /api/stock/{code}/filings` | TDnet の決算短信・予想修正 XBRL と、PDF から変換した過去短信の一覧を返す | [銘柄](stock.md) |
| `GET /api/stock/filings/{filing_id}/documents/{seq}` | 短信の iXBRL 原文からスクリプトや外部通信要素を除去したサニタイズ済み HTML（`text/html`、CSP 付き）を返す | [銘柄](stock.md) |
| `GET /api/stock/pdf/{disclosure_no}` | IR BANK の PDF から変換した短信 HTML（`tdnet_pdf_documents.status = 'ok'` の行のみ、それ以外は `404`）を同じ CSP ヘッダー付きで返す | [銘柄](stock.md) |
| `GET /api/stock/{code}/decks` | 銘柄の決算説明資料一覧（最大 200 件、新しい順）と読み取り状態（`done`・`submitted`・`failed`・`null`）を返す | [銘柄](stock.md) |
| `GET /api/stock/{code}/decks/{key}` | 決算説明資料 1 冊の抽出メタデータとページ別の要約・数値表 `{document, extract, pages}` を返す（原本 PDF は返さない） | [銘柄](stock.md) |
| `GET /api/stock/{code}/holders` | 銘柄別の大量保有者一覧（保有割合推移つき）と最新の自己株買い状況 `treasury` を返す | [銘柄](stock.md) |
| `GET /api/holders/search?q=` / `GET /api/holders/activity` / `GET /api/holders/{edinet}` | 大量保有報告書の提出者検索、直近提出一覧、提出者別の保有銘柄一覧を返す（株券以外の発行体は除外する） | [大量保有報告書](holders.md) |

## サプライ

| エンドポイント | 応答の要点 | 詳細仕様 |
|---|---|---|
| `GET /api/supply/graph` | 素材ノードと工程エッジの定義（`config/supply/supply_chain.json`）を返す | [サプライ](supply.md) |
| `GET /api/supply/companies` | 素材ごとの関連上場企業マスタ（`config/supply/supply_companies.json`）を返す | [サプライ](supply.md) |
| `GET /api/supply/monthly/{material_id}` | 指定素材の月次統計系列（生産・出荷・在庫・輸出入と見掛け消費）を返す。未登録の `material_id` は `404` を返す | [サプライ](supply.md) |
| `GET /api/supply/countries/{material_id}?flow=import` | 指定素材の相手国別貿易統計（`flow` は `import` または `export`、それ以外は `400`）を返す | [サプライ](supply.md) |

## 月次（`/api/v1/tables`）

月次 API の画面側の使い方は [月次](monthly.md)、行名の正規化と保存名凍結は [月次の行名](../jobs/monthly_labels.md)、取込規則は [月次の取込規則](../jobs/monthly.md) を参照する。

### 読み取り API

| エンドポイント | 応答の要点 |
|---|---|
| `GET /api/v1/tables` | 月次表を持つ銘柄一覧をコード昇順で返す。各要素は `{code, name, years, market, sector33, industry, last_ingested_at, is_new, ingested_today, all_store_yoy, existing_store_yoy}` で、`industry` 未設定時は `"その他"` を返す |
| `GET /api/v1/tables/{code}` | 指定銘柄に存在する暦年の一覧を返す |
| `GET /api/v1/tables/{code}/{year}` | 指定年の月次表を返す（1 年を通じて値が無い行は除外し、表が無ければ `404`）。各行は `{element, unit, label, values, tree?}` で、`values` は `1`〜`12` 月の値（欠損は `null`、`0` は実ゼロ）。`config/monthly/monthly_row_order.json` に定義があればその木、無ければ自動構築した木（`tree_auto: true`）から `tree` と `tree_metrics` を付与する |
| `GET /api/v1/tables/{code}/{year}/doc` | `PUT /doc` 用の編集元文書 `{code, year, version, elements}` を `sort_order` 順で返す（値が空の行も含む）。`version` は行名・`unit`・並び・値のハッシュである |

### 書き込み API（loopback 限定）

| エンドポイント | 動作 |
|---|---|
| `POST /api/v1/tables/{code}/ingest` | 資料の列順と `as_of` 時点の決算期から暦年を割り当てて一括投入する（`dry_run: true` は書かずに割当結果とセルを返し、`only_months` は `YYYY-MM` で対象月を絞る）。銘柄エイリアスで同じ保存名に寄る行は 1 行に統合する |
| `POST /api/v1/tables/{code}/{year}/elements` | 月次行を登録する（`unit` 必須、表が無ければ作成）。トークン正規化と銘柄エイリアス適用後に保存名と完全一致する既存行があれば冪等に `200`、新規作成時は `201` を返す |
| `PUT /api/v1/tables/{code}/{year}/elements` | 既存行の `unit` を変更し、新しい `unit` の正規名へ改名する（既に同名行があれば値を統合する） |
| `DELETE /api/v1/tables/{code}/{year}/elements` | 値が 1 件も入っていない空の行を削除する（表自体は残す） |
| `PUT /api/v1/tables/{code}/{year}/values` | 単一セル `{element, month, value}` を更新または削除（`value: null`）する |
| `PUT /api/v1/tables/{code}/{year}/values/batch` | 1 行の複数月 `values: [{month, value}, ...]` を検証後に 1 トランザクションで更新する |
| `PUT /api/v1/tables/{code}/{year}/doc` | 文書全体 `{version, elements, allow_structure_change?, dry_run?}` を 1 トランザクションで反映する。行名には正規化やエイリアスを掛けず、`was` 指定で改名、省略行は削除、新規名は末尾追加として扱う |
| `PUT /api/v1/tables/{code}/industry` | 銘柄の月次業種 `{industry}`（1〜50 文字）を更新する |

### 月次 API のエラー応答

月次 API のエラーはすべて `{"error": {"code": "...", "message": "..."}}` 形式で返す。主なステータスとエラーコードは以下のとおりである。

| HTTP | `error.code` | 発生条件 |
|---|---|---|
| `400` | `UNIT_REQUIRED` / `INVALID_PARAM` | `unit` の省略・不正値、月や数値の型不正、正規化後 50 文字超の行名 |
| `400` | `PRIOR_PERIOD_ELEMENT` | 前期・前年同月などの実数行を登録しようとしたとき |
| `400` | `FUTURE_COLUMN` | `POST /ingest` で割り当てた年月が `as_of` より未来になるとき |
| `403` | `FORBIDDEN` | プロキシ経由（loopback 以外）から書き込み API を呼んだとき |
| `404` | `NOT_IN_MASTER` / `TABLE_NOT_FOUND` | `stocks` マスタに銘柄が無いとき、または対象の月次表・行が存在しないとき |
| `409` | `SIMILAR_ELEMENT` / `UNIT_CONFLICT` | 類似行が既にあるとき、または既存行と `unit` が異なるとき（`force: true` で上書き可） |
| `409` | `YOY_NUMBER_CONFLICT` | 同じ指標の実数行と前年比行が別名になるとき（両方を同一 `POST /ingest` に含めるか `force: true` で作成可） |
| `409` | `ROW_MERGE_CONFLICT` / `FOLD_CONFLICT` | エイリアス統合または `unit` 変更による行統合で、同じ月に異なる非 NULL 値や異なる `unit` が衝突したとき |
| `409` | `ELEMENT_HAS_VALUES` | 値が残っている行を `DELETE /elements` で消そうとしたとき |
| `409` | `VERSION_MISMATCH` / `STRUCTURE_CHANGED` | `PUT /doc` で `version` が一致しないとき、または `allow_structure_change: true` なしで行の追加・削除・改名・`unit` 変更を行ったとき |
