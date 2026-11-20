# API の内容

API の一覧・引数・読み書きする表は [画面設計](../SCREENS.md)（自動生成）。ここには応答の形と約束だけ書く。

## API の一覧

| Method/Path | 内容 |
|---|---|
| `GET /api/earnings?date=2026-08-14&today=2026-10-02` | `{kind, actuals, scheduled}`。`actuals` は開示1件が1行で、QoQ(Q単独の前四半期比) と YoY(累計の前年同期比) を指標ごとに返す（売上・売上総利益・営業利益・経常利益・純利益・EPS。売上総利益は `xbrl_financials` から。KAB-278）。金額は `single`（Q単独。算出できなければ null）と `cumulative`（累計）で、円。決算短信本体のみ (予想修正のみの開示、および損益が初回と同一の再提出は除外。損益が変わった再提出は `restated` を立てて残す)。通期営利予想が直前開示から ±0.1% 以上動いていれば `forecast_revision: {direction: "up"|"down", pct}`、無ければ null。`scheduled` は `{code, name, periods:[{fq, fye, label}], latest}` で銘柄1行。`latest` は直近の実績開示（`disclosure_no` / `period` / `fy_label` / `single` / `cumulative` / `qoq` / `yoy` / `progress` / `forecast_fy`）。実績が無ければ null。どちらの行にも `sector33`（マスタに無ければ null）と `pts`（`pts_snapshots` でその日のセッションに最後に取った値 `{price, chg_pct, base_close, as_of}`。3 つのランキングのどれにも無ければ null）。`kind` は `actual` / `scheduled` / `mixed`。過去は `actual` で `scheduled` は空。今日は開示と、そのコードが今日の開示に無い未発表。未来は予定だけ。`today` を省略するとサーバの今日。台帳が lookback を埋める前の未来には `ready: false`（空なら画面は 予定未取得）。`product_category` が REIT (`013`/`022`) の銘柄は出さない |
| `GET /api/earnings/detail/{code}?disclosure_no=…` | 選択銘柄の累計テーブル (過去4期+当期の同一四半期 + 中間予想 + 通期予想 + 前期通期実績。本決算は予想が `raw_json` の来期予想 `NxF*` で、前期通期は出さない) と3か月決算テーブル (直近12四半期のQ単独)。各行に会計期末 (`26.11`) または Q単独の四半期末 (`25.08`) と発表日を付ける。同一銘柄が同日に複数開示する日があるため `disclosure_no` で一意に引く (`?date=` でも可)。どちらも無ければ最新の実績開示（決算速報の銘柄検索が使う）。実績が無ければ 404。`revisions` は通期予想の修正履歴 (`kind` = `forecast_initial` / `forecast` / `forecast_revised` / `actual_fy`、`growth` は前の行からの修正率で同値は `state: flat`)。選んだ開示の日までの開示から作る。データ源は `revision_source`（既定 `EARNINGS_PARAMS["revision_source"]` = `jquants`）で、`?rev_source=jquants|xbrl` で上書きできる。`jquants` は `statements` の `fy_*` と本決算の `NxF*`（期初）と本決算の実績。`xbrl` は `xbrl_financials` の `forecast`/`FY`（連結優先、レンジは中点）と `annual` の実績。組み立ては `app/revisions.py`、データ源は `app/market/revisions.py` / `app/xbrl/revisions.py`。`notes.half_forecast` は 1Q と本決算（来期）の中間予想の状態で、`given`（あり）/ `fy_only`（`raw_json` の `FSales2Q` などが空で通期予想だけ）/ `none`（中間も通期も無い）/ `unknown`（`raw_json` に項目が無い）。2Q・3Q は `null` |
| `GET /api/earnings/calendar?month=2026-08&today=2026-10-02` | 日付 → `{kind, n}`。`pending` は今日かつ未発表がいるときだけ。件数は銘柄の distinct。疎。一覧と同じく本体のみを数え、REIT も除外する。`today` を省略するとサーバの今日 |
| `GET /api/chart/{code}` | 日足を列ごとの配列で返す `{code, name, t, o, h, l, c, v, tv, mc, earnings_dates, fund}`。`t` は日付、`o`〜`c` は調整後の四本値（調整後が無い日は生の値）、`v` は調整後の出来高、`tv` は売買代金（円）、`mc` は時価総額（百万円。無い日は null）。`fund` は業績の階段 `{t, eps, op_q, op_ttm}`（`t` は使い始める日、`eps` は調整後の予想EPS、営業利益は円。値は次の点まで続く。作り方は `docs/screen/stock.md`「業績の数字の作り方」）。週足・月足・移動平均はブラウザで作る（`docs/screen/stock.md`「株価」）。`ETag` は中身のハッシュで、`If-None-Match` が合えば 304。行が無ければ 404 |
| `GET /api/topix` | TOPIX の終値 `{t, c}`。対TOPIX 相対に使う。`ETag` は同じ |
| `GET /api/earnings-performance/{code}` | 決算日からの対TOPIXリターン |
| `GET /api/ranking/earnings-perf?month=YYYY-MM` | 決算月ごとの決算発表後パフォーマンス（次の決算の前日終値まで）のランキング。`docs/screen/ranking.md` |
| `GET /api/screener?stage=2&rs_min=70&sort=rs` | ステージフィルタ |
| `GET /api/pts?date=2026-10-16&ranking=night_up&at=2026-10-16 23:30:00` | PTS ランキング 1 回分と翌営業日の始値とのギャップ。`ranking` は `night_up` / `night_down` / `night_volume`（ほかは 400）。`date` 省略で最新のセッション、`at`（`fetched_at`）省略かつ不一致で最新の取得。形は `docs/screen/pts.md` |
| `GET /api/stocks/search?q=トヨタ` | 銘柄検索。名前の部分一致（q を NFKC とひらがな→カタカナに寄せたものでも引く）とコードの前方一致。最大 20 件。株（`product_category` が `011` / `021`）だけで、ETF・ETN・REIT・出資証券は出さない。ProdCat がまだ 1 件も入っていない DB では全銘柄 |
| `GET /api/holders/search?q=` / `GET /api/holders/activity` / `GET /api/holders/{edinet}` | 大量保有の保有者検索・提出日（または検索）の一覧・保有者ページ。発行体が株でない報告は出さない。`docs/screen/holders.md` |
| `GET /api/stock/{code}/holders` | 銘柄の株主タブ。`docs/screen/stock.md` |
| `GET /api/stock/{code}/financials?statement=BS&periods=8&consolidated=auto` | 財務3表（BS / PL / CF）を有報・短信 XBRL・短信 PDF をまたいで科目×決算期に並べる。セルごとに円の値と出どころ。形は `docs/screen/stock.md`、組み立ては `docs/jobs/xbrl.md`「財務3表」 |
| `GET /api/supply/graph` | サプライタブの素材と流れ（`config/supply/supply_chain.json`）。形は `docs/screen/supply.md` |
| `GET /api/supply/monthly/{material_id}` | 素材の月次量（`material_monthly`）と見掛け消費。形は `docs/screen/supply.md`。素材が無ければ 404 |
| `POST /api/v1/tables/{code}/{year}/elements` | 月次表の要素登録。`unit` 必須。表が無ければ作成。保存名をトークン正規化（yoy は末尾 `(前年比)`）と銘柄エイリアスのあと、保存名と完全一致する行を探す（既存行は作り直さない。`docs/jobs/monthly_labels.md`「同一行・保存名の凍結」）。完全一致は冪等。類似は 409 (force=true で強制)。unit の食い違いは 409 `UNIT_CONFLICT` (force=true で上書き)。`stocks` に無ければ 4xx `NOT_IN_MASTER` |
| `POST /api/v1/tables/{code}/ingest` | PDF 列順 + as_of 時点の最新 `fiscal_year_end` から暦年を割り当てて登録。year は受け取らない。`as_of` と各行の `unit` は必須。`dry_run: true` は書かずに mapping と書くはずのセルを返す。`only_months` は `YYYY-MM` で列を絞る。マスタ無しでは 4xx `NOT_IN_MASTER`。mapping の年月が as_of より後なら 400 `FUTURE_COLUMN`。新規行で、`strip_yoy_fragments` 後の名前が同じ yoy と number の既存行が今回の `rows` に無いときは 409 `YOY_NUMBER_CONFLICT`（`force=true` で作成。両方を同じ POST に含めれば通る）。銘柄エイリアスで同じ保存名に寄る行（別表の同じ数字）は 1 行にまとめる。同じ列で値か unit が食い違えば 409 `ROW_MERGE_CONFLICT`（後勝ちで上書きしない） |
| `GET /api/v1/tables` | 月次表がある銘柄一覧 (code 順)。各要素は `{code, name, years, market, sector33, industry, last_ingested_at, is_new, ingested_today, all_store_yoy, existing_store_yoy}`。name/market/sector33 は stocks を LEFT JOIN (無ければ null)。industry は無ければ `その他`。years は昇順。表が無ければ `[]` |
| `PUT /api/v1/tables/{code}/industry` | 月次業種。body `{industry}`。stocks 無ければ 4xx `NOT_IN_MASTER`。月次表が無ければ 4xx `TABLE_NOT_FOUND` |
| `GET /api/v1/tables/{code}/{year}` | 月次表。要素は登録順。各行は `{element, unit, label, values}`。`element` は保存名。`label` は GET 派生（display / group / kinds / 7層）。`values` は 1〜12。無い月は null（0 は実ゼロ）。1年通して値のない行は出さない。全銘柄で親子関係を足す。`config/monthly/monthly_row_order.json` に `tree` があればそれ、無ければ自動の木（`tree_auto: true`）。各行に `tree: {path, leaf, metric}`（根から自分の節までの名前の列、節の中での行名、指標名。指標に振り分けていなければ null。自動の木は全行に指標が付き、3 年を通した並び `order` も付く）、表に `tree`（節 `{name, heading, part, elements, children}` の木。形は config、自動なら表示する 3 年で決まり年で変えない。`elements` はその年の保存名）と `tree_metrics`（指標名の並び。横持ちの一番上の行の順。自動の木は既存 → 全店 → 店舗 の見る順。自動の木は指標どうしの親子 `tree_metric_parent` も付く）を足す。config の木でどの節にも当たらない行に `tree` は付かない（自動の木は全行に付く）。書式は `docs/screen/monthly.md`「親子関係」。表が無ければ 404 |
| `GET /api/v1/tables/{code}` | その銘柄の年度一覧 |
| `DELETE /api/v1/tables/{code}/{year}/elements` | 空の月次行を消す。body `{element}`。値が残っていれば 409 `ELEMENT_HAS_VALUES`。表は残す。未登録は 404。マスタ無しは 4xx `NOT_IN_MASTER` |
| `PUT /api/v1/tables/{code}/{year}/values/batch` | 複数月を検証してから一括 commit。マスタ無しは 4xx `NOT_IN_MASTER` |
| `GET /api/v1/tables/{code}/{year}/doc` | `PUT /doc` の元になる文書。`{code, year, version, elements}`。行は `sort_order` 順で、値の無い行も出す。各行 `{element, unit, values, tree?}`（`values` は `"1"`〜`"12"`、`tree` は木の path）。`version` は行名・unit・並び・値のハッシュ。公開の nginx の口には載せない（Access の後ろ） |
| `PUT /api/v1/tables/{code}/{year}/doc` | 文書を丸ごと返して 1 トランザクションで書く。body `{version, elements, allow_structure_change?, dry_run?}`。行名は渡したまま（正規化・エイリアスを掛けない）。`was` で改名（値はサーバーが移す）、文書に無い行は削除、`was` の無い新しい名前は作成（`unit` 必須、末尾に並ぶ）。`values` を渡した行は 12 か月を置き換え（無い月・null は削除）、省いた行は値をそのまま。`version` が違えば 409 `VERSION_MISMATCH`。改名・作成・削除・unit 変更は `allow_structure_change=true` のときだけ（無ければ 409 `STRUCTURE_CHANGED`）。`dry_run=true` は書かずに `changes` を返す。config の木に入らない行は `warnings` の `NOT_IN_TREE` |

月次 API のエラーは `{"error": {"code": "...", "message": "..."}}`。閲覧 GET は公開。書き込みは loopback のみ（nginx 経由は 403）。本番の個人ツールは `/app` と Cloudflare Access（`deploy/README.md` の「Cloudflare」）。

## 画面ごとの文書

### ヘッダーとパンくずの置き場所

見た目の正本は `docs/screen/SPEC.md`（共通見た目 9・10）。置き場所だけここにも書く。

- `nav#crumbs` はヘッダーに置く。`/app` は `#menu-btn` の右（PC はタブ行の下）。公開月次はサイト名の右。`main` には置かない
- パンくずがある画面はヘッダーのページ名（`#cur-tab`）を出さない。無い画面はいまどおりページ名
- 区切りは ` > `。幅に収まらないときは折り返さず横スクロールし、先頭を省略して末尾（いまの画面）が見えるようにする

### 決算速報の画面

画面は `docs/screen/earnings.md`。書き方は `docs/screen/SPEC.md`。

### 銘柄タブの画面

画面は `docs/screen/stock.md`。書き方は `docs/screen/SPEC.md`。原文の保存は `docs/jobs/xbrl.md`「短信の原文 HTML」。

### 大量保有報告書タブの画面

画面は `docs/screen/holders.md`。書き方は `docs/screen/SPEC.md`。データは `docs/jobs/edinet.md`「大量保有報告書」。

### ランキングタブの画面

画面は `docs/screen/ranking.md`。書き方は `docs/screen/SPEC.md`。PTS は同タブの中で、画面は `docs/screen/pts.md`。

### 月次タブの画面

画面は `docs/screen/monthly.md`。書き方は `docs/screen/SPEC.md`。

### サプライタブの画面

画面とマスタの形は `docs/screen/supply.md`。書き方は `docs/screen/SPEC.md`。
