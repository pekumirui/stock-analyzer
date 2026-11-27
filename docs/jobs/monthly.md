# 取込と保存の規則: 月次

月次 KPI の表構造・単位・保存規則と、TDnet および各社 IR ページからのファイル取得・自動投入の仕様を定義する。ジョブの起動時刻・引数・読み書きするテーブルは自動生成の [ジョブ設計](gen/monthly.md) を参照する。行名の正規化・保存名の凍結・発行体別の親子木は [月次の行名](monthly_labels.md) を正本とし、画面の表示仕様は [月次](../screen/monthly.md) を参照する。

## 月次表の保存

### 月次一覧の版

月次一覧キャッシュの版番号（`cache_versions` テーブルの `name = 'monthly_list'`）は、対象テーブルに行単位の `INSERT`・`UPDATE`・`DELETE` が発生した時点で SQLite トリガーがインクリメントする。トリガーは `init_db` と `jobs/migrate.py` が作成し、API・cron・MCP・直接の SQL 実行のいずれの経路でも発火する。

版番号の更新対象とキャッシュ運用の規則は以下のとおりである。

- 監視対象のテーブルは `monthly_tables`・`monthly_elements`・`monthly_values`・`monthly_issuers`・`stocks` の 5 つである。
- 行がまだ無い初期状態では版番号を `0` とする。値が変わらない再投入など `last_ingested_at` を更新しない書き込みもあるため、`last_ingested_at` は版の判定に使わない。
- 一覧の `is_new` は `last_ingested_at` の JST 暦日が当日・前日・前々日の直近 3 日間に入るときに真となる。`ingested_today` は `last_ingested_at` の JST 暦日が当日のときに真となる。
- `is_new` と `ingested_today` はどちらも暦日の切り替えで変化するため、ワーカーのメモリキャッシュは `(version, JST の今日)` をキーにして使い回す。別プロセスが DB に書き込んだ場合は、次の読み取り時に版番号の変化を検知してキャッシュを再構築する。
- `monthly_publications` はこの版の監視対象に入れない。公表日の埋め戻しで一覧キャッシュを進めない。一覧と株価チャートは応答のたびにこの表を 1 回読み、直近の公表日はキャッシュした一覧へ後から載せる。

### 公表日（`monthly_publications`）

1 か月の最初の公表を、銘柄 × 暦年 × 月に 1 行で持つ。セルごとには持たない。同じ月をあとから訂正しても日付は動かさない。その月の値が全部消えたら行も消す。`as_of` は列の暦年割当にだけ使い、公表日にはしない。`monthly_element_sources.first_seen_at` は取り込み時刻であり、公表日にはしない。

| 列 | 意味 |
|---|---|
| `published_on` | JST の公表日。分からないときは `NULL`。月末や取り込み日を入れない |
| `published_time` | `HH:MM:SS`。出どころに時刻があるときだけ。無ければ `NULL` |
| `basis` | `tdnet`（開示日）/ `ir_page`（ページ・URL・表計算・JSON に書いてある暦日）/ `first_seen`（ページに日付が無く、バッチがその月を最初に見た日。観測の上限）/ `unknown` |
| `unknown_reason` | `basis` が `unknown` のときだけ。`carried_history` / `manual` / `no_source` / `no_page_date` / `unspecified` |
| `confidence` | `exact`（題名の年月かページの暦日からその月だと特定できた）/ `estimated`（題名から月が読めず、月末から 45 日以内の開示、または取得日という上限）/ `unknown`（日付なし） |
| `source_ref` | ファイル名、開示番号、URL |

同じ月に複数の候補が来たときは、`exact` が `estimated` に勝ち、`estimated` が `unknown` に勝つ。確かさが同じなら、より早い日付が勝ち、同じ日なら `tdnet`、`ir_page`、`first_seen` の順、さらに時刻がある方が勝つ。`exact` を `estimated` や `unknown` に戻さない。

| 経路 | 記録 |
|---|---|
| TDnet の月次 PDF | ファイル名の `YYYYMMDD` を `tdnet`。時刻は隣の `.pdf.meta.json` の `pubdate` がその日と一致するときだけ。`via` は `ir_html` のまま |
| IR HTML | ページの「更新・公表・発表・開示・掲載・確定・現在・時点」の隣の暦日が `as_of` と同じ年なら `ir_page`。`as_of` が月末でなければその日を `ir_page`。`as_of` が月末ならファイル名の日付を `first_seen`。それも無ければ `unknown` / `no_page_date` |
| IR の表計算 | 明示の日付、URL の日付、セルの日付は `ir_page`。最終月の月末へのフォールバックは、ファイル名があれば `first_seen`、無ければ `unknown` / `no_page_date` |
| IR の JSON | 明示の日付か `last_updated` は `ir_page`。月の 1 日へのプレースホルダは、ライブ取得のときだけその日の `first_seen`。保存した JSON の再生では `unknown` / `no_page_date` |
| API・MCP が `publication` を付けない | `unknown` / `unspecified` |
| `PUT` の値・文書で新しい月ができる | `unknown` / `manual` |

1 回の投入で複数の月が初めて値を持つときは、いちばん新しい月だけにその資料の日付を付ける。それより前の月は `unknown` / `carried_history` にする。後の資料が古い月を載せているだけでは、その古い月の日付にしない。

過去分は `python tools/monthly/backfill_publications.py` が dry-run で数える。`--write` で `monthly_publications` に書く。本番の DB には向けない。`monthly_element_sources.first_seen_at` は見ない。

| 出どころ | 月の決め方 | `confidence` |
|---|---|---|
| TDnet PDF | 先頭の `YYYYMMDD` が開示日。ファイル名の `○年○月度`・`○月期○月度`・月だけの `○月度`、または `○年○月`（`○月期` でも `○月○日` でもない）が読めればその月。読めなければ PDF 本文（先頭 2 ページ。数字と「月」「度」のあいだの空白は無視）の `○年○月度`・`○月期○月度`・`○月度`。月だけのときは開示日以前のいちばん新しいその月。1 ファイル 1 月。本文の `○年○月`（`月度` でない）は開示日の行と取り違えるので使わない | `exact` |
| 題名にも本文にも `月度` が無く、ファイル名も月を言わない TDnet PDF | 同じファイルの開示日が、その月の月末の翌日から 45 日以内にあるとき。入る月が複数ならいちばん新しい月。`月度` があるのに読めない題名は推定しない | `estimated` |
| `statements` の表題 | PDF のファイル名と同じ。`○年○月度` / `○月期○月度` / `○月度` / `○年○月` が読めたときだけ | `exact` |
| IR HTML | ページの更新・公表などの隣の暦日、または月末でない `as_of` は `ir_page`。`as_of` が表の最終月の月末だけのときは公表日にしない。ファイル名は取得日なので、2026-09 以降に取得した 2026-09 以降の月だけ `first_seen`。それより前の IR だけの月は戻さない | ページの暦日は `exact`。取得日は `estimated` |

戻せない月は `unknown` / `no_source`。後の PDF が古い月を題名に含んでいても、その月の最初の開示より遅い日付では上書きしない。

埋め戻しのあと、`exact` でない月は MCP の `monthly_publications` で読む。調べた開示日は `monthly_set_publication` で、値を再取り込みせずにその月へ書く。`date` は開示日、`source` は `tdnet_pdf` か `ir_page`、`doc_id` は開示番号、`url` は IR BANK や IR アーカイブの URL。既に `exact` の行は `override: true` のときだけ上書きする。

### PDF 取込（`POST /api/v1/tables/{code}/ingest`）

月次テーブルの `year` は常に暦年（1〜12 月）で保持する。新規取込 API はクライアントから `year` を受け取らず、PDF や HTML の左から右への月並び `months` と開示日 `as_of` からサーバ側で各列の暦年を決定する。年を指定する `PUT` 系エンドポイントは既存セルの訂正専用である。

#### 列のレイアウトと暦年の割り当て

`months` には表の左から右の順で 1〜12 の整数配列を渡す。サーバは `as_of` 以前に開示された最新の `statements.fiscal_year_end`（同日に複数あれば期末日が新しい方）から事業年度の期末月 `fy_end_month` を引き、右端列から左へ向かって列ごとに暦年を割り当てる。過去に決算期変更があっても `as_of` 時点の期末月 1 つだけを使い、該当する決算行が 1 件も無ければ `400 NEED_FY_END` を返す。Genky DrugStores の 13 列（6 月から翌年 6 月）のように同じ月番号が 2 回現れる表でも、列インデックスごとに異なる暦年が付く。

`months` の先頭月に応じてレイアウト種別 `layout` を以下の 3 通りに分類する。列の並び自体を理由にリクエストを拒否することはない。

| `months` の並び | `layout` | 年アンカーの決め方 |
|---|---|---|
| 先頭が `1` で右へ月番号が単調増加する | `calendar` | 右端列の月と `as_of` から暦年アンカーを決める |
| 会計年度開始月（`fy_end_month` の翌月）で始まる | `fiscal` | `fy_end_month` と `as_of` から会計年度の期末年を求め、右端列の暦年を決める |
| 上記以外 | `partial` | 右端列の月と `as_of` から暦年アンカーを決める |

会計年度レイアウト（`fiscal`）における期末月前後の年割り当てと未来列の検査規則を以下に示す。

| 条件 | 扱い |
|---|---|
| `fiscal` で右端列の月が `fy_end_month` 以下（通期 12 か月表など）かつ `as_of` が期末月より後 | 完了した会計年度に割り当てる（12 月決算の翌年 1 月開示は前年、3 月決算の同年 4 月以降の開示は当年が期末年になる） |
| `fiscal` で右端列の月が `fy_end_month` より大きく（期首側の月で終わる進行中の表）かつ `as_of` の月が `fy_end_month` より後 | 進行中の新会計年度の期首年（`as_of.year`）に割り当てる |
| `fiscal` で `as_of` の月が `fy_end_month` 以前（当期会計年度の期末前） | 年度末までの空欄列や残月が含まれていても `FUTURE_COLUMN` にしない |
| 上記以外で `mapping` に `as_of` の年月より後の列が含まれる | `400 FUTURE_COLUMN` で拒否する |

#### 値の渡し方と応答形式

`months` に重複月があるかどうかで、リクエストの `values` とレスポンスの `mapping` の形式が切り替わる。

| `months` の状態 | リクエストの `values` | 応答の `mapping` |
|---|---|---|
| 重複が無い | 月番号をキーとするオブジェクト（`{"4": 102.1, ...}`）または列順の配列 | 月番号文字列から暦年へのオブジェクト（`{"4": 2025, ...}`） |
| 重複がある | `months` と同じ長さの配列、または 0 始まりの列番号文字列をキーとするオブジェクト（`{"0": 101.5, ...}`）。1〜12 の月番号キーは曖昧なため `400 INVALID_PARAM` で拒否する | 列順のオブジェクト配列（`[{"month": 6, "year": 2025}, ...]`） |

#### リクエストパラメータとエラー応答

`POST /api/v1/tables/{code}/ingest` が受け取る主要パラメータは以下のとおりである。

| パラメータ | 扱い |
|---|---|
| `as_of` | `YYYY-MM-DD` 形式の必須文字列である。省略または `null` は `400 AS_OF_REQUIRED`、形式不正は `400 INVALID_PARAM` になり、サーバの現在日付で補完しない |
| `dry_run` | `true` のときは `fy_end_month`・`layout`・`mapping` と更新予定の `updated` だけを返し、DB への書き込みも新着更新も行わない |
| `only_months` | `YYYY-MM` 形式の配列である。全列で `mapping` を確定したあと、指定した暦年月の列だけを DB に書き込む。省略時は全列を書き込み、`mapping` に当たらない指定月は無視する |
| `via` | 取込経路を表す文字列で、`api`・`ir_html`・`ir_xls`・`ir_json` のいずれかを受け付ける。省略時は `api` とし、それ以外の値は `400 INVALID_PARAM` にする。本登録時は送られてきた元の行名と保存名の対応を `monthly_element_sources` に記録する（`dry_run: true` では記録しない） |

バリデーションや既存行との競合で返る主なエラーステータスは以下のとおりである。

| ステータスとコード | 発生条件 |
|---|---|
| `404 NOT_IN_MASTER` | 指定した銘柄コードが `stocks` テーブルに存在しない |
| `400 AS_OF_REQUIRED` | `as_of` が省略されているか `null` である |
| `400 NEED_FY_END` | `as_of` 以前に開示された `statements.fiscal_year_end` が 1 件も無い |
| `400 FUTURE_COLUMN` | 当期会計年度の期末前である `fiscal` 表を除き、`as_of` より未来の年月列が含まれる |
| `400 UNIT_REQUIRED` | 行の `unit` が省略されているか、`number`・`yoy_index`・`yoy_pct` 以外である |
| `400 PRIOR_PERIOD_ELEMENT` | 前期・前年同月・前年同期・前年の実数行が含まれる。詳細は [月次の行名](monthly_labels.md)「入力の拒否条件」を参照する |
| `409 ROW_MERGE_CONFLICT` | 正規化・エイリアス適用後に同じ保存名へ寄った複数行の間で `unit` または同列の値が食い違う |
| `409 UNIT_CONFLICT` | 既存行と異なる `unit` を `force: true` なしで送った |
| `409 ELEMENT_SIMILAR_EXISTS` | 空白・全半角正規化後に同名となる別表記の既存行があり、`force: true` が無い |
| `409 YOY_NUMBER_CONFLICT` | 実数と前年比の片方だけが既存行にある状態で、もう片方を単独で新規作成しようとし、`force: true` が無い |

### 銘柄マスタとの関係

月次データの書き込みは銘柄マスタ `stocks` に存在する 5 桁コードだけに限定する。

- 新規取込（`ingest`）、要素追加（`POST .../elements`）、値・文書の訂正 `PUT`、業種設定（`PUT .../industry`）はすべて最初に `db.get(Stock, code)` を検査し、存在しなければ `404 NOT_IN_MASTER` を返す。
- 既存テーブルへ後から外部キー制約を足せないため、`stocks` への FK は張らず `PRAGMA foreign_keys` も有効化しない。
- デプロイ時に走る `jobs/migrate.py`（`app/monthly/maintenance.run_deploy_maintenance`）が、`stocks` に存在しないコードの月次行を `monthly_publications` → `monthly_values` → `monthly_element_sources` → `monthly_elements` → `monthly_tables` → `monthly_issuers` の順で冪等に `DELETE` する。
- アプリ起動時や各バッチの `init_db` ではこの削除を行わない（名証単独上場の `30320`・`30660` などを起動処理で触らないため）。

### unit

行の単位 `unit` は数値の大小から推測せず、原資料の見出しや単位表記に従って入力側で指定する。

- 入力が受け付ける値は `number`・`yoy_index`・`yoy_pct` の 3 種類であり、DB に保存する値は `number` と `yoy_index` の 2 種類である（`yoy_pct` は保存時に `yoy_index` へ変換し、`yoy_pct` のままでは書き込まない）。
- `ingest` の各行と `POST .../elements` では `unit` が必須であり、省略や `null` は `400 UNIT_REQUIRED` になる。
- `GET` 時に既存行の `unit` が `NULL` であった場合は `number` として返す。
- `PUT .../values` および `PUT .../values/batch` は既存セルの訂正用であり、保存形（前年比なら 100 基準の指数）をそのまま送る。サーバ側での `+100` 変換や `unit` 変更は行わない。

入力 `unit` ごとの判定基準と保存形は以下のとおりである。

| 入力 `unit` | 原資料の見出し・表記 | DB の保存形と画面表示 |
|---|---|---|
| `yoy_index` | 前年比 100 基準（`単位:%` のみで符号なし 100 前後の数値が並ぶ表など） | `yoy_index` のまま保存し（例: `125.8`）、画面にも `100` をフラットとしてそのまま表示する |
| `yoy_pct` | `▲`・`△` 付きの変化率、または見出しに `増減比`・`伸び率`・`増減率` がある表 | `unit` を `yoy_index` にし、値に `100` を足して保存する（例: `-7.1` は `92.9`） |
| `number` | 店舗数・受注高・会員数などの実数・件数・率 | `number` のまま保存する |

前年比のセルは `▲` や `+X.X%` に戻さず保存値（100 基準の指数）のまま表示する。`number` のうち表示単位が定義されている指標だけは、画面の項目名末尾に `（単位）` を補い、セルには指定桁で四捨五入した数値だけを出す。対象トークンと桁数の対応表は [月次の行名](monthly_labels.md)「表示単位」を参照する。`GET` の JSON 応答と DB の保存名には表示単位を付けない。

### 行名

行名の正規化と保存名凍結の正本は [月次の行名](monthly_labels.md) である。`ingest` と `POST .../elements` は入力された行名を `canonicalize_element_label` で正規化し、銘柄別エイリアスを適用してから完全一致する既存行を探す。`GET` は DB の保存名と派生ラベル情報 `label` を返し、SPA 側の `fmtMonthlyElement` は再パースを行わない。

`ingest` がまだ存在しない行を新規作成する際は、実数（`number`）と前年比（`yoy_index`）の取り違えを防ぐため以下の衝突検査を行う。

| 条件 | 扱い |
|---|---|
| 同じ `(code, year)` に `strip_yoy_fragments` 後の基底名が同じで `unit`（実数と前年比）が異なる既存行があり、その既存行が今回のリクエスト `rows` に含まれていない | `409 YOY_NUMBER_CONFLICT` を返す |
| 上記の条件で当該行に `force: true` が指定されている | 新規行を作成する |
| 同一リクエストの `rows` に実数行と前年比行の両方が含まれている | 両方とも作成する（実数と前年比の併存） |
| 既に DB にある保存名の行を更新する | 衝突検査の対象外とし、そのまま更新する |
| `POST .../elements` で単一行を追加する | この衝突検査は行わない |

## 月次ファイルの取得と投入

### 共通の約束（`jobs/*_r2.py`）

月次ファイルの既定の保存先はリポジトリ配下の `data/monthly/{code}/` である。環境変数 `SHARE_ROOT` を設定した場合は `{SHARE_ROOT}/monthly/{code}/` になる。cron のスケジュールと運用手順は `deploy/README.md` を参照する。

3 本の取得バッチ（TDnet PDF・IR 表計算と PDF・IR HTML）は別々の cron として動かし、日付・HTTP・銘柄マスタ照合・ファイル名生成の共通処理を `jobs/_r2common.py` に集約する。保存先とマスタ照合の共通規則は以下のとおりである。

- ディレクトリ名 `{code}` には TDnet の発行体コード（`9279`・`262A` など 4 桁表記）をそのまま使い、J-Quants の 5 桁コードにはしない。`stocks` テーブルと照合するときだけ `app/xbrl/codes.py` の `jq_lookup_code` を使う（`9282` は `92820`、`262A` は `262A0`。空の入力は `""`）。
- 銘柄マスタは `app.db` の読み取り専用エンジン（`sqlite_readonly_engine`）で読む。`stocks` に存在しないコードはダウンロードしないが、DB ファイルが無い場合や読めない場合は警告だけを出して空マップのまま処理を継続する。
- 既存の保存済みファイルは削除しない。IR ページの PDF・xlsx・HTML のように同じ URL で上書き公開される資料はファイル名に日付を付けて残し、既存ファイルと `sha256` が一致するときは保存をスキップする。

#### HTTP 取得と再試行（`http_get`）

TDnet と各社 IR ページの HTTP 取得はどちらも `jobs/_r2common.py` の `http_get`（`http_get_result`）を使う。識別用の `User-Agent` はジョブごとに設定し、応答ステータスと通信エラーに応じて以下の順序で再試行する。

| 応答・事象 | 扱い |
|---|---|
| HTTP `403` | そのリクエストに限りブラウザ互換の `User-Agent` に切り替えて 1 回だけ再試行する |
| SSL 検証エラー（中間証明書の欠落・付け違い） | TLS 検証を無効化せず、リーフ証明書の AIA（CA Issuers）から中間証明書を取得して検証し直す（`7544` スリーエフなど） |
| 通信エラー（`URLError`・`TimeoutError`・`OSError`、証明書検証エラーを除く） | `1.5` 秒待って同じ `User-Agent` で 1 回再試行し、なお失敗したらブラウザ互換 `User-Agent` でさらに 1 回試す |
| HTTP `429`・`408`・`5xx`（`Retry-After` ヘッダあり） | 指定秒数（最大 `60` 秒）待って再試行する。初回を含め最大 6 回試行する |
| HTTP `429`・`408`・`5xx`（`Retry-After` ヘッダなし） | 基準 `5` → `10` → `20` → `40` → `60` 秒に `0.75〜1.25` 倍の jitter を掛けた指数バックオフで最大 5 回スリープし（初回を含め最大 6 回試行）、解消しなければ例外を送出する |
| 再試行の上限に達して打ち切った場合 | 例外を送出する。ログ形式はジョブごとに異なり、`ir_monthly_html_r2.py` は `fail issuer {code} fetch:`（429 は後述の `rate-limited`）、`ir_monthly_r2.py` は一覧が `fail issuer {code} {index_url}:`・個別ファイルが `fail {rel} url={url}:`、`tdnet_monthly_r2.py` は `fail {rel} id={pdf_id}:` を出力する |

#### 残置 R2 マウントの切断検知

現在の既定保存先はローカルの `data/` であり、`data/` 配下を指しているときは `/proc/mounts` を検査しない。保存先 `dest` が残置の rclone FUSE マウント（`/home/stock/r2-share`）上にあるときだけ、マウント切断時に全銘柄分の `fail` ログが並ぶのを防ぐため開始時と処理中に `dest` を probe する。

| probe の結果 | 扱い |
|---|---|
| `ECONNABORTED`・`ENOTCONN`・`ESTALE` またはマウントルートの消失 | `deploy/r2-share.service` の `RestartSec` と同じ `5` 秒を挟んで 1 回再試行する |
| 再試行後も回復しない | `R2 マウント切断` の 1 行だけを出力して終了コード非ゼロで終了する |
| 正常に応答する | 通常の取得処理を続ける |

マウント自体の自動復旧は `deploy/r2-share.service` と `r2-share-watch.timer` が担い、デプロイ時はユニットまたは設定のハッシュが変わったときだけ `r2-share.service` を再起動する（詳細は `deploy/README.md` を参照）。

### TDnet 月次 PDF（`tdnet_monthly_r2.py`）

取得ジョブは [`monthly/tdnet_monthly_r2`](gen/monthly.md#monthlytdnet_monthly_r2)（`jobs/monthly/tdnet_monthly_r2.py`）である。TDnet の適時開示一覧（`jobs/_tdnet.py`）から表題を `is_monthly_title`（`LIST_KEYWORDS` と除外語）で絞り込み、対象の PDF を保存する。日次 cron と期間指定のバックフィルは同じ判定関数を使う。

表題と銘柄による採否規則は以下のとおりである。

| 表題・銘柄の条件 | 扱い |
|---|---|
| `月次` の語が無くても `売上速報`・`○月度`（売上等のヒント付き）・`受注速報`・`売上推移`・`業績速報` などに該当する表題 | 取得対象にする |
| 四半期決算短信・自己株式の取得状況・新株予約権の月間行使状況・業績予想の修正 | `is_monthly_title` で除外する |
| `月度` 単体で売上・受注・IR ニュースなどのヒント語が無い表題 | 取得対象にしない |
| インフラファンド | ダウンロード時に除外する。J-Quants の商品区分は通常の REIT と同じ `013`・`022` しか無いため、銘柄名に「インフラ」を含むものだけを除外する（マスタ未取得時は TDnet 社名の「インフラ投資法人」で判定する） |
| 通常の REIT | 取得対象に残す（決算速報で REIT 全体を除外する規則とは異なる） |
| `MONTHLY_SKIP_CODES`（4 桁の `display_code`）に含まれる銘柄 | 取得対象から外す。`8985` ジャパン・ホテル・リート投資法人、`8798` アドバンスクリエイト（グラフ画像のみ）、`8739` スパークス・グループ（運用資産残高のストック表のみ）が該当する |
| `stocks` に存在しないコード（名証単独の `3032`・`3066` など） | `unknown-stock` としてスキップし、東証マスタにも追加しない |

### TDnet 月次 PDF の自動投入（sidecar と ingest）

担当ジョブは [`monthly/tdnet_monthly_pdf_sidecar`](gen/monthly.md#monthlytdnet_monthly_pdf_sidecar)（`jobs/monthly/tdnet_monthly_pdf_sidecar.py`）と [`monthly/tdnet_monthly_pdf_ingest`](gen/monthly.md#monthlytdnet_monthly_pdf_ingest)（`jobs/monthly/tdnet_monthly_pdf_ingest.py`）である。TDnet 月次 PDF には銘柄ごとの専用パーサや書式定義ファイルを持たない。代わりに、ファイル名日付の前暦月（開示月）の値が既に DB に入っている過去の PDF（最大 3 本までさかのぼる）と DB の保存値を突き合わせ、DB の各行を再現できる PDF 上の行位置を自動学習して今月分の sidecar を生成する。

#### 学習と PDF 読み取りの規則

学習と PDF 表抽出の規則は以下のとおりである。

- 各行の `unit` は DB の既存行から引き継ぐ。DB が 100 基準の指数（`yoy_index`）で PDF 側の数値が増減率（`+100` で一致）である場合は、sidecar 側の入力 `unit` を `yoy_pct` に設定する。
- 前回までの DB の値を正解として学習するため、過去に手作業で入れ違えた行があればそのまま引き継ぐ。一方、前月速報値から確報値への訂正は、翌月の sidecar が表の全列を送り直すことで自動的に追いつく。
- PDF の文字と座標は `pdftotext -tsv` で抽出する。月見出しの行と列位置がそろう数値行の表（横組み）と、月が行ラベルに並ぶ表（縦組み）の両方を対象とし、`上期計`・`累計` などの集計列は捨てる。
- 列見出しに年（`25年4月` など）が書かれていればその年を採用し、年が無ければ行ごとに値が入っている右端の月から左へ向かって暦年を割り当てる。

#### 自動生成の安全弁（書き出し条件）

sidecar の生成対象は各銘柄の最新 PDF 1 本だけである。誤った表やレイアウト変更を自動投入しないよう、以下のいずれかに該当する場合は sidecar を書かずに手作業（`manual`）へ回す。

| 条件 | 扱い |
|---|---|
| DB の最新月がその PDF の開示月より新しい（開示月自体には値が無い） | 書かない（投入済み） |
| 開示月に値が既にあり、その開示月の PDF が 1 本だけ | 書かない（投入済み） |
| 同じ開示月の PDF が 2 本以上あり、最新 PDF の行がすべて投入済み | 書かない（投入済み） |
| 同じ開示月の PDF が 2 本以上あり、最新 PDF に未投入の行がある | 書かない（`manual`。sidecar は書かない。ログは `手動投入が必要`） |
| 前回の開示月に値がある DB の行を 1 行でも再現できない | 書かない（`manual`） |
| 使用する表までの見出し骨格（数値・日付をマスクした文字列）または行の並びが前回 PDF と異なる | 書かない（`manual`） |
| 前回は無かった行または表（表の出現順を含む。見出しと行名が同じ新設ブロックも含む）に今月の値がある | 書かない（`manual`） |
| DB に値がある過去月との不一致数が、一致した月数の 5 分の 1 を超える（一致 5 か月につき 1 か月までの速報修正は許容する） | 書かない（`manual`） |
| DB にまだ無い月の実数値（件数・金額）が、直近 12 か月の DB 値の中央値から 5 倍を超えて外れる（中央値との絶対差が `10` 以下の小数は除く） | 書かない（`manual`） |
| 前回 PDF では同値だった複数の候補行が、今回の PDF では異なる値に分かれた | 書かない（`manual`） |
| 上記のいずれにも該当しない | sidecar を書き出す |

#### sidecar ファイルと DB 投入

sidecar ファイルの形式と DB 投入の手順は以下のとおりである。

- sidecar は PDF と同じディレクトリに `<PDFベース名>.pdf-ingest.json` として保存する。IR HTML 用の `.ingest.json` とは拡張子を分け、HTML 側の投入ジョブが拾わないようにする。
- JSON の構造は `POST /api/v1/tables/{code}/ingest` のリクエストボディに準拠し、`year` は持たない。`null` を送ると既存セルを消してしまうため、`values` には値がある月だけを入れる（月番号が重複する表では 0 始まりの列番号キーを使う）。検査用に各列の暦年月 `columns` と学習元の PDF 名 `learned_from` を併記する。
- DB への投入は `jobs/monthly/tdnet_monthly_pdf_ingest.py` が担う。銘柄ごとに最新の sidecar だけを対象とし、`sha256` 台帳（`/var/log/app/pdf_ingest_ledger.json`）で送信済みをスキップし、各行に `force` を付けない点は HTML の投入ジョブと同じである。
- 本投入の前に必ず `dry_run: true` で API を呼び、サーバが返した `mapping` が sidecar の `columns` と一致しない場合は本投入を中止する。

### IR 月次 PDF・xlsx・xls（`ir_monthly_r2.py` と `ir_monthly_xls_ingest.py`）

担当ジョブは [`monthly/ir_monthly_r2`](gen/monthly.md#monthlyir_monthly_r2)（`jobs/monthly/ir_monthly_r2.py`）と [`monthly/ir_monthly_xls_ingest`](gen/monthly.md#monthlyir_monthly_xls_ingest)（`jobs/monthly/ir_monthly_xls_ingest.py`）である。TDnet に月次開示を出さない発行体のうち、IR ページの URL を確認済みの銘柄を `config/monthly/ir_monthly.json` の `issuers` に登録して取得する。同一ページに複数のファイル形式が並んでいる場合は以下の優先順位で保存対象を選ぶ。

| ページ上のファイル構成 | 取得するファイル |
|---|---|
| 表計算ファイル（`xlsx`・`xls`）と `pdf` の両方がある | 表計算ファイルだけを取得する |
| `xlsx` がある | `xlsx` を取得する |
| `xlsx` が無く `xls` がある | `xls` を取得する（大東建託 `1878` の `history_*.xls` など） |

AWS ELB 等が識別用 `User-Agent` に `403` を返すサイトや、中間証明書が欠落しているサイトは、「共通の約束」に示した `http_get` のフォールバックと AIA 補完でそのまま取得する。

取得した表計算ファイルの DB 投入は `jobs/monthly/ir_monthly_xls_ingest.py` が担う。

- 対象は大東建託（`1878`、`history.xls`）とユナイテッドアローズ（`7606`、`xlsx`）であり、シート・列・行の読み取り位置は `config/monthly/ir_monthly_xls.json` で宣言する。汎用 xlsx パーサや銘柄別 Python クラスは作らず、HTML 投入ジョブの cron にも混ぜない。
- 取得ジョブの直後に `--all --post` で起動し、設定ファイルにある全銘柄について `data/monthly/<code>/` 内の最新ファイル（ファイル名の日付順）を読み、`via="ir_xls"` で loopback の `POST /api/v1/tables/{code}/ingest` に送る（`year` は送らない）。
- 毎回シートの全列を送るため、前月速報値の訂正（大東建託の前月空室数など）も次回実行で自動的に反映される。値が変わらないセルはサーバ側で書き換えず、`last_ingested_at` も動かない。
- 銘柄ごとに `post` または `fail` を 1 行出力し、最終行に `ir_monthly_xls_ingest done posted=... skipped=... failed=...` を出す。1 銘柄が失敗しても残りの銘柄の処理を続け、失敗が 1 件でもあれば非ゼロの終了コードで終了する（KAB-117）。

### IR 月次 JSON（`ir_monthly_json_ingest.py`）

担当ジョブは [`monthly/ir_monthly_json_ingest`](gen/monthly.md#monthlyir_monthly_json_ingest)（`jobs/monthly/ir_monthly_json_ingest.py`）である。スギホールディングス（`7649`）が公開している CloudFront 上の月次 JSON API を取得し、`.ingest.json` を書き出したうえで loopback の `POST /api/v1/tables/{code}/ingest` へ投入する（`via="ir_json"`、`year` は送らない）。

- `deploy/crontab` では HTML 投入ジョブと同じ時刻に `deploy/cron-run.sh` 経由で実行する。銘柄別クラスは作らず、HTML パーサのレジストリにも混ぜない。
- 取得先 URL・決算期末月・丸め桁数・フィールドと行名の対応は `config/monthly/ir_monthly_json.json` に定義する。
- JSON 内の `year` はページ HTML の `data-year` と同じ暦年（例: 2027 年 2 月期の 3 月度は `year=2026`・`month=3`）であり、当年度のデータだけを取り込み、過年度の表は入れない。
- 伸び率の値には `100` を足して `yoy_index` に変換し、`digits` 指定に従って小数第 1 位に丸める。
- `null` で既存セルを消さないよう、`values` には値がある月だけを `{月: 値}` で詰める。同値のセルはサーバ側で書き換えない。
- 最終行には成否にかかわらず `ir_monthly_json_ingest done posted=... skipped=... failed=...` を出力し、`failed > 0` のときは終了コード `1` で終了する。

### IR 月次 HTML（`ir_monthly_html_r2.py` と `ir_monthly_html_ingest.py`）

担当ジョブは [`monthly/ir_monthly_html_r2`](gen/monthly.md#monthlyir_monthly_html_r2)（`jobs/monthly/ir_monthly_html_r2.py`）と [`monthly/ir_monthly_html_ingest`](gen/monthly.md#monthlyir_monthly_html_ingest)（`jobs/monthly/ir_monthly_html_ingest.py`）である。

#### HTML の取得と sidecar 生成（`ir_monthly_html_r2.py`）

取得対象は `config/monthly/ir_monthly.json` の `html` セクションで定義する。年度別サブページに分かれるサイトは `follow_re` で最新年度のリンクを辿り、`source="eir"` の銘柄は Pronexus E-IR の JSONP から月次アイテムの `text` を取り出して `.html` として保存する（`sha256` もその HTML 文字列に対して計算する）。

原本の `.html` の隣に派生ファイルの `.ingest.json`（`POST /api/v1/tables/{code}/ingest` と同形式）を書き出すだけで、取得ジョブ自身は API への自動 POST を行わない。

HTTP 失敗時のログと終了コードの扱いは以下のとおりである。再試行回数の上限は共通の `http_get` から増やさない。

| 失敗の種類 | ログ出力 | サマリの `failed=` と終了コード |
|---|---|---|
| `http_get` が再試行後に打ち切った HTTP `429` | `skip issuer {code} rate-limited:` | `failed` に数えない |
| `429` を出した同一ホストの後続銘柄 | `skip issuer {code} host-cooled {host}` | `failed` に数えない |
| HTTP `404`・パース失敗・ファイル入出力エラーなど | `fail issuer {code} ...` | `failed` に数え、`failed > 0` なら終了コード `1` にする |

`config/monthly/ir_monthly.json` の `parse` フラグによってパーサの有無を切り替える。

| `parse` の設定 | 扱い |
|---|---|
| `parse: true` | `jobs/monthly/html_issuers/` 配下の `i<code>.py`（`PARSERS` レジストリに自動登録）または共通パーサ `jobs/monthly/html_monthly.py` で `.ingest.json` を生成する |
| `parse: false` | パーサモジュールを持たず、原本の `.html` だけを保存して `.ingest.json` は書かない（画像や本文のみのページに使う） |

パーサのロジックを修正したときは `--reparse` で保存済み HTML から `.ingest.json` を再生成する。また通常実行で HTML の `sha256` が前回と同じためダウンロードをスキップした場合でも、現行パーサの出力が保存済みの `.ingest.json` と異なればその場で `.ingest.json` を書き直し、古い sidecar が残らないようにする。

#### DB への投入（`ir_monthly_html_ingest.py`）

生成された `.ingest.json` の DB 投入は別スクリプトの `jobs/monthly/ir_monthly_html_ingest.py` が取得ジョブの後に実行する。

- 銘柄ごとにファイル名の日付が最も新しい `.ingest.json` 1 本を選び、`via="ir_html"` で loopback へ POST する（`year` は送らない）。
- 台帳は `dest` 配下ではなく `/var/log/app/html_ingest_ledger.json` に置き、ファイルの `sha256` が前回成功分と同じなら POST をスキップする。
- 自動投入では各行に `force` を付けない。同名で `unit` が異なる行や類似行名がある場合は `409` で止め、人が内容を確認したうえで `--force --only <code>` を付けて手動投入する。
- `--months YYYY-MM,...` を指定したときは `only_months` 付きの 1 回の POST として送り、部分投入であるため台帳は更新しない。

#### `unit` と `as_of` の決定規則

HTML パーサにおける `unit` 判定のコンテキストには、ページ全体の見出しに加えて当該 `<table>` 直上のセクション見出しとページ内の `(単位：%)`・`単位:%` 表記を含める。見出しとセル内容からの判定優先順位は以下のとおりである。

| 見出し・セルの条件 | 採用する `unit` |
|---|---|
| 見出しに `増減比`・`伸び率` がある（ツルハ・サガミなど） | ページ内に `単位:%` があっても `yoy_pct` を優先する |
| ページに単位表記が無いが実態が 100 基準の指数である銘柄 | 銘柄モジュールで `yoy_index` を明示宣言する |
| 見出しが `売上伸び率（単位：％）` でもセルが符号なし 100 基準の銘柄（`3148` クリエイトSD など） | 銘柄モジュールで `force_unit="yoy_index"` を宣言し、`+100` 変換を防ぐ |
| 店舗数・拠点数・営業所数・出退店の行 | ページ見出しが前年比であっても件数なので `number` にする |

`as_of` は次の優先順で最初に取得できた日付を採用し、いずれでも決まらなければ `.ingest.json` を書き出さない。

1. HTML 本文の開示日・更新日・月度見出し（`parse_as_of_text`）を採用する。ただし、表のセル自体に `YYYY年M月` などの年が明記されており（`grid_has_cell_years`）、本文の「現在」表示などと年が食い違う場合は、手順 2 の表側の年月末日を優先する（`6537` ＷＡＳＨハウスなど）。
2. 表の値が入っている最終列の年月末日（セル内の年、または表直前の期見出し・`YYYY年M月期`・`YYYY年度`・`YYYY年M月〜YYYY年M月` から算出）を採用する。
3. 呼び出し側から渡されたヒント日付（E-IR JSONP の `date` フィールドなど）を採用する。

#### 表の選択と行の組み立て規則

共通パーサにおける表の選択と行の組み立て規則は以下のとおりである。

- 複数年度の表が並ぶアーカイブページでは会計年度が新しい表を優先し、同じ期末なら値が入っている最終月が新しい表を選ぶ（12 か月すべて埋まっている過年度表を最終月の大きさで誤選択しない）。
- 1 つの表の中に複数年の月次が縦横に並ぶアーカイブ（`2791` 大黒天物産）では、同じ月番号は先に出現した新しい年の値だけを残す。`YYYY年（令和N年）度` のような年度帯の行は行名に入れない。
- 同一ページに店舗数表と売上表が別々に置かれている場合、月軸とセルの年が一致すれば同一の payload に結合する。
- ページ全体の見出しが「売上高前年同月比」であっても、客数・客単価・店舗数・拠点数・営業所数・開店・閉店・出店・退店・開設・撤退の行には `売上高前年同月比` を補わない。
- 数値を持たない `既存店`・`全店` の見出し帯行は、直後に続く指標行のスコープ接頭辞として引き継ぐ。
- 1 つの表の中で上期と下期の月見出しが上下に続く書式（`3038` 神戸物産の単体月次など）では、後半の月列も同じ行に結合する。ソースに無い `全店` は補わない。
- JSON に `year` は書かず、`△1.9`・`▲1.9` は `-1.9` に変換する。`累計`・`通期計`・`上期計` の列は `months` に入れず、末尾の空列は落として値のある最終月で切る（途中の欠損月は空のまま残す）。

#### 銘柄ごとの HTML パーサモジュール

`jobs/monthly/html_issuers/` の `i*.py` は `__init__.py` の `_load_parsers` が全部 `PARSERS` に載せる（51 本）。下表は共通規則と違う抽出を持つ銘柄だけであり、載っていない銘柄は共通パーサか各モジュールの `parse` に従う。正規化後の保存名やエイリアスとの対応は [月次の行名](monthly_labels.md) を参照する。

| 銘柄（4 桁コード） | モジュール | 抽出・結合規則 |
|---|---|---|
| 大黒天物産（`2791`） | `i2791.py` | 複数年が並ぶ表から最新年度の行ブロックだけを採り、`※ YYYY年（令和N年）度` の年度帯を除外する |
| ロック・フィールド（`2910`） | `i2910.py` | `既存店売上` と `全店売上` の 2 表を結合し、業態行に見出しのスコープを補う。最下行の `合計` は会社計の `既存店 売上高前年同月比`・`全店 売上高前年同月比` にする |
| アルペン（`3028`） | `i3028.py` | 売上高前年比表に同伴の出退店数表を結合し、合計列の `開店`・`閉店`・`店数` を `出店`・`退店`・`店舗数`（`number`）として取り込む。業態別の出退店行は落とす |
| 神戸物産（`3038`） | `i3038.py` | 先頭行が `売上高` の単体表を主表とし、売上総利益・営業利益・経常利益の表は採らない。同ページの `店舗仕入れ前年対比` は月軸が揃うときに `仕入れ` セグメントとして結合し、脚注の「商品出荷」は行名にしない |
| きちりHD（`3082`） | `i3082.py` | 全社表（`全店` と `既存店全店`）と業態別表を結合する |
| クリエイトSD（`3148`） | `i3148.py` | 見出しは `売上伸び率（単位：％）` だがセルが符号なし 100 基準のため、`force_unit="yoy_index"` を宣言する |
| USMH（`3222`） | `i3222.py` | 持株会社計を表す `U.S.M.H` 列の接頭辞を外して会社全体の行にし、マルエツ・カスミ等の事業会社列は社名接頭辞を残す |
| ベガコーポレーション（`3542`） | `i3542.py` | LOWYA 事業の単月表から `当期`（実数）と `対前年同月比（%）` を取り込み、前期実績行を落とす |
| ソフトウェア・サービス（`3733`） | `i3733.py` | 単体の売上高・受注高・受注残高の実数行と各前年同月比行を組にして取り込む |
| ＷＡＳＨハウス（`6537`） | `i6537.py` | 年ごとに並ぶ店舗数表と売上高前年同月比表のペアを同じセル年どうしで同一 payload に結合する |
| ヒマラヤ（`7514`） | `i7514.py` | 店舗数表の `月末` 行を `店舗数`（`number`）に改名してから売上表へ結合する |
| ワタミ（`7522`） | `i7522.py` | ページ内の国内外食・海外・宅食の各表を抽出し、`海外事業`・`宅食事業` の見出しを続く四半期表へ引き継ぐ。同じ `as_of` を持つ表だけを結合し、宅食のお届け数（千食）・営業所数・開設数は `number` に固定する |
| ハークスレイ（`7561`） | `i7561.py` | 同一ページにある `ほっかほっか亭` と `店舗流通ネット` の各表にブランド接頭辞を付与して結合する |
| ＳＲＳホールディングス（`8163`） | `i8163.py` | 全店・既存店の前年比表と M&A 除外の注記行を取り込む |
| ロイヤルHD（`8179`） | `i8179.py` | `ロイヤルホスト` と `てんや` を見出し接頭辞とし、上期表と下期表を `tile_segment_series` で同一ブランド系列に結合する。前年比は `force_unit="yoy_index"` に固定する（件数の `number` 行は変更しない） |
| フジ（`8278`） | `i8278.py` | 改装店舗の面積一覧表を除外し、営業速報の表に総店舗数だけを結合する |
| ＮＳＤ（`9759`） | `i9759.py` | `売上高（百万円）` を持つ当期の単月表だけを採り、12 か月埋まった前期表と差額の `前期比（百万円）` 行を落とす。`増減率` 行は `売上高(前期比)` として出し、共通正規化で `売上高(前年比)` に揃える |
| ニトリHD（`9843`） | `i9843.py` | 表見出しの単位ラベル `売上（%）` が行名に残らないよう除去して全店・既存店の前年比を取り込む |
| ミニストップ（`9946`） | `i9946.py` | `全店売上` の表から `売上高合計` だけを残し、直後の表が同じ行構成（売上高合計前年比・CVS 前年比・店内加工 FF 前年比）で月が続く（3〜8 月の次に 9〜2 月）ときは同じ行に結合する。`1店1日当り` と内訳の CVS・店内加工 FF は採らない |
| サンドラッグ（`9989`） | `i9989.py` | 日英併記のグループ・ドラッグストア・ディスカウントストア各表の売上前年比を `yoy_pct`（セルに `%` が無くても変化率）、店舗数・出退店を `number` として取り込む |
| やまや（`9994`） | `i9994.py` | 既存店表と全店表を結合し、卸計は除外する。`当 年` を `売上高` に変え、`前 年` と累計は落とし、`unit` を `yoy_index` に固定する |
