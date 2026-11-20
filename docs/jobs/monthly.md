# 取込と保存の規則: 月次

ジョブの起動時刻・引数・読み書きする表は [ジョブ設計](gen/monthly.md)（自動生成）。行名は [月次の行名](monthly_labels.md)。ここには規則だけ書く。各節の頭の「ジョブ」は、生成ページの同じ名前の節へのリンク。

## 月次表の保存

### 月次一覧の版

月次一覧の版（name = monthly_list）を上げるのは monthly_tables / monthly_elements /
monthly_values / monthly_issuers / stocks の INSERT・UPDATE・DELETE（行ごと）。

- API・cron・MCP・手書きの SQL のどれでも上がる。トリガーは init_db と jobs/migrate.py が作る
- 行がまだ無いあいだの版は 0。last_ingested_at は版に使わない（更新しない書き込みがある）
- 一覧の is_new は `last_ingested_at` の JST 暦日が今日を含む直近 3 日（当日・前日・前々日）。`ingested_today` は JST の今日
- is_new と `ingested_today` はどちらも日付で変わるので、使い回しのキーは (version, JST の今日)
- 結果はワーカーのメモリに置く。別プロセスの書き込みは、次の読みで版が変わって作り直す

### PDF 取込（`POST /api/v1/tables/{code}/ingest`）

月次の year は暦年。クライアントは year を渡さない。年付き PUT は訂正用。

列の年の決め方:

- `months` は PDF 左→右
- 年は最終列の月と、開示日 `as_of` 時点の最新 `statements.fiscal_year_end` から右→左へ列ごとに決める
- 同じ月番号が2回あっても列が違えば年が違う（Genky 型 13 列の Jun…Jun など）
- 決算期変更があっても as_of 時点の1か月だけを使う

| `months` の並び | layout |
|---|---|
| 先頭が1で、右へ月が増え続ける | calendar |
| 会計年度開始月（期末月の翌月）で始まる | fiscal |
| それ以外 | partial。列の並びでは拒否しない |

| 場合 | 扱い |
|---|---|
| fiscal で、期末月より後の as_of（新FY先頭月に限らない） | 完了した FY に割り当てる |
| mapping の年月が as_of より後 | 400 `FUTURE_COLUMN` |
| fiscal で、as_of の月が期末月以前（当期FYの期末前） | 未来の列があっても `FUTURE_COLUMN` にしない |

値と応答の形:

| `months` | `values` | 応答 `mapping` |
|---|---|---|
| 重複が無い | `{月: 値}` | `{月: 年}` |
| 重複がある | 列順の配列（または列番号キー `"0"`…） | 列順 `[{month, year}, …]` |

パラメータ:

| パラメータ | 扱い |
|---|---|
| `as_of` | YYYY-MM-DD 必須。省略・null は 400 `AS_OF_REQUIRED`。サーバ日付は使わない |
| `dry_run: true` | `fy_end_month` / `layout` / `mapping` と、書くはずのセル（`updated`）を返す。DB には書かない。新着にもならない |
| `only_months` | `YYYY-MM` の配列。mapping を決めたあと、その暦年の列だけ書く。省略時は全列。当たらない月は書かない |
| `via` | 経路（`api` / `ir_html` / `ir_xls` / `ir_json`）。省略は `api`。それ以外は 400。書いた行ごとに元の行名を `monthly_element_sources` に残す（dry_run では残さない） |

### 銘柄マスタとの関係

- 書き込みは `stocks` に行がある code だけ。ingest / POST elements / 訂正 PUT / industry PUT は先に `db.get(Stock, code)`。無ければ 404 `NOT_IN_MASTER`
- FK は付けない（既存テーブルへ ADD できない）。`PRAGMA foreign_keys` も有効化しない
- デプロイごとの `jobs/migrate.py`（`app/monthly/maintenance.run_deploy_maintenance`）が `stocks` に無い月次行を DELETE する。冪等
- アプリ起動・各ジョブの `init_db` は触らない（本番の名証単独 30320 / 30660 など）
- 消す順は `monthly_values` → `monthly_element_sources` → `monthly_elements` → `monthly_tables` → `monthly_issuers`

### unit

PDF 見出しが入力タグを決める。値の大きさから推測しない。

- 入力は `number` / `yoy_index` / `yoy_pct`。保存は `number` / `yoy_index`。`yoy_pct` は書かない
- ingest の各行と `POST .../elements` は `unit` 必須。省略・null は 400 `UNIT_REQUIRED`
- GET の NULL は number として出す
- PUT values は訂正で、保存形（指数）を送る。unit も `+100` もしない

| 入力 unit | 見出し | 保存 |
|---|---|---|
| `yoy_index` | 前年比 100 基準（`単位:%` のみ等） | `yoy_index` のまま（125.8）。表示もそのまま。100=フラット |
| `yoy_pct` | ▲/△ 付き変化率、または見出しの 増減比/伸び率 | `yoy_index`、`value+100`（-7.1 → 92.9） |
| `number` | 店舗数など件数 | `number` のまま |

前年比の表の表示は保存値のまま（指数 100=フラット）。▲ や +X.X% にはしない。
number で表示単位がある指標は項目名の末尾に `（単位）` を付け、セルは四捨五入した数字だけにする。対応は [`docs/jobs/monthly_labels.md`](monthly_labels.md)。GET の JSON と保存名は単位なし。

### 行名

正本は [`docs/jobs/monthly_labels.md`](monthly_labels.md)。
ingest / POST は `canonicalize_element_label` してから既存行を探す。GET は保存名。SPA は `fmtMonthlyElement`。

ingest が新しい行を作るときの、実数と前年比の衝突:

| 場合 | 扱い |
|---|---|
| 同じ (code, year) に `strip_yoy_fragments` 後の名前が同じで unit が yoy と number で違う既存行があり、その既存行が今回の `rows` に無い | 409 `YOY_NUMBER_CONFLICT` |
| 上で `force=true` | 作る |
| 同じ POST に両方ある | 作る（実数と前年比の併存） |
| 既にある行の更新 | 対象外 |
| `POST .../elements` | この検査をしない |

## 月次ファイルの取得と投入

### 共通の約束（`jobs/*_r2.py`）

月次ファイルの保存先はリポジトリの `data/monthly/{code}/`。`SHARE_ROOT` を置くと、その直下の `monthly/{code}/` になる。cron と運用は `deploy/README.md`。

- `{code}` は TDnet の発行体コードそのまま（9279 / 262A）。J-Quants 5 桁にしない。stocks 照合だけ display_code + 末尾 0（9282→92820, 262A→262A0）
- stocks に無いコードはダウンロードしない。DB が読めない／map が空なら警告のみで継続
- 既存ファイルは消さない。IR（PDF/xlsx/HTML）は同じ URL の上書き公開を日付付きで残し、中身の sha256 が同じなら skip
- 3 本のバッチ（TDnet PDF / IR PDF・xlsx / IR HTML）の cron は分ける
- 日付・HTTP・銘柄マスタ・ファイル名の共通処理は `jobs/_r2common.py`
- 銘柄マスタは `app.db` の読み取り専用エンジン（`sqlite_readonly_engine`）で読み、DB が無い・読めないときは空のまま続ける

`http_get` は TDnet も IR も同じ。識別 UA はジョブごと。

| 応答 | 扱い |
|---|---|
| 403 | そのときだけブラウザ UA に一度切り替える |
| 中間証明書の欠落 | AIA で補う |
| 429 / 408 / 5xx で Retry-After あり | それに従う（最大 60 秒） |
| 429 / 408 / 5xx で Retry-After 無し | 指数バックオフ＋jitter（5→10→20→40→60 秒、最大 6 回） |
| 打ち切った | 例外を上げる。発行体ログは原則 `fail issuer {code} fetch:`（IR HTML の 429 だけ下の例外） |

R2 マウント:

- 新しいファイルは `data/` に書く。`data/` の dest は `/proc/mounts` を見ない
- rclone FUSE（`/home/stock/r2-share`）の切断プロトコルは、dest がまだそのマウント上にあるときに働く
- マウントが切れたら各社 `fail` を並べない。開始時と処理中に dest を probe する

| probe の結果 | 扱い |
|---|---|
| ECONNABORTED / ENOTCONN / ESTALE / ルート消失 | `RestartSec` と同じ 5 秒を挟んで再試行 |
| 再試行しても駄目 | `R2 マウント切断` の 1 行で exit≠0 |
| 正常 | 行は変えない |

- マウントの再起動は `deploy/r2-share.service` と `r2-share-watch.timer`。デプロイはハッシュが変わったときだけ `r2-share.service` を restart する（`deploy/README.md`）

### TDnet 月次 PDF（`tdnet_monthly_r2.py`）

ジョブ: [monthly/tdnet_monthly_r2](gen/monthly.md#monthlytdnet_monthly_r2)（`jobs/monthly/tdnet_monthly_r2.py`）

TDnet の開示一覧（`jobs/_tdnet.py`）の表題を `LIST_KEYWORDS` で絞る。日次 cron と backfill は同じ判定。

| 表題・銘柄 | 扱い |
|---|---|
| `月次` が無い売上速報・○月度・受注速報・売上推移・業績速報など | 取る |
| 四半期短信・自己株式・新株予約権の月間行使・業績予想 | `is_monthly_title` が落とす |
| `月度` 単体 | 取らない。売上/受注/IRニュースなどのヒントが要る |
| インフラファンド | ダウンロード時に除外する。商品区分は REIT と同じ `013`/`022` しか無いので、名称に「インフラ」を含むものだけ落とす（マスタ欠落時は TDnet 社名の「インフラ投資法人」） |
| 通常の REIT | 残す。決算速報の REIT 全除外とは別 |
| `MONTHLY_SKIP_CODES`（display_code の4桁）に入った銘柄 | 保存しない。8985 ジャパン・ホテル・リート投資法人、8798 アドバンスクリエイト（グラフ画像のみ）、8739 スパークス（運用資産残高のストック表）は取得対象外 |
| stocks に無いコード（名証単独の 3032 / 3066 など） | `unknown-stock` で skip。東証マスタには載せない |

### TDnet 月次 PDF の自動投入（sidecar → ingest）

ジョブ: [monthly/tdnet_monthly_pdf_sidecar](gen/monthly.md#monthlytdnet_monthly_pdf_sidecar)（`jobs/monthly/tdnet_monthly_pdf_sidecar.py`）→ [monthly/tdnet_monthly_pdf_ingest](gen/monthly.md#monthlytdnet_monthly_pdf_ingest)（`jobs/monthly/tdnet_monthly_pdf_ingest.py`）

銘柄ごとの書式は宣言しない。DB に開示月（ファイル名日付の前暦月）の値がある前回の PDF（最大 3 本さかのぼる）から、DB の各行を再現する PDF の行を探して学ぶ。

- unit は DB から。DB が指数で PDF が増減率なら `yoy_pct`（+100 で一致）
- 前回の DB の値を正とするので、過去に人が入れ違えた行はそのまま引き継ぐ。速報の訂正は翌月の sidecar が全列を書き直すので追いつく

PDF の読み方:

- PDF は `pdftotext -tsv` の語の座標で読む
- 読む表は、月見出しの行と列がそろう数値の行（横組み）と、月が行ラベルの表（縦組み）。上期計・累計などの列は捨てる
- 見出しに年（`25年4月`）があれば使い、無ければ行ごとに値のある右端の月から左へ暦年を振る

書くかどうか。書くのは各 code の最新 PDF だけ:

| 条件 | 扱い |
|---|---|
| DB の最新月がその開示月より古くない | 書かない（投入済み） |
| 開示月に値のある DB の行を全部は再現できない | 書かない（`manual`） |
| 使う表までの見出しの骨格（数値・日付を伏せる）か使う行の並びが前回と違う | 書かない（`manual`） |
| 前回無かった行・表に今月の値がある（表の序数込み。見出しと行名が既存と同じ新ブロックも含む） | 書かない（`manual`） |
| DB に既にある月との食い違いが、一致した月の数の 5 分の 1 より多い（一致 5 か月に 1 つまでは許す） | 書かない（`manual`） |
| DB にまだ無い月の件数・金額が、直近 12 か月の DB の値の中央値から 5 倍を超えて外れる（中央値との差が 10 以下は除く） | 書かない（`manual`） |
| 前回同じ値だった候補行が今回は違う | 書かない（`manual`） |
| 上のどれにも当たらない | 書く |

sidecar と投入:

- sidecar は PDF の隣の `….pdf-ingest.json`（HTML の `.ingest.json` と別の名前。HTML の ingest は拾わない）
- 形は ingest と同じで year は無い。values は値のある月だけ（null はセルを消すので送らない。月が重複すれば列番号キー）。`columns` に列ごとの暦年月、`learned_from` に学んだ PDF を残す
- DB 投入は `jobs/monthly/tdnet_monthly_pdf_ingest.py`。latest-per-code・sha256 ledger・行に `force` を付けない点は HTML の ingest と同じ
- POST の前に `dry_run` し、mapping が `columns` と違えば POST しない
- ledger は `/var/log/app/pdf_ingest_ledger.json`

### IR 月次 PDF/xlsx/xls（`ir_monthly_r2.py` → `ir_monthly_xls_ingest.py`）

ジョブ: [monthly/ir_monthly_r2](gen/monthly.md#monthlyir_monthly_r2)（`jobs/monthly/ir_monthly_r2.py`）→ [monthly/ir_monthly_xls_ingest](gen/monthly.md#monthlyir_monthly_xls_ingest)（`jobs/monthly/ir_monthly_xls_ingest.py`）

TDnet に月次が無い発行体が対象。`config/monthly/ir_monthly.json` の issuers（URL 確認済みだけ載せる）。

| ページにあるファイル | 取るもの |
|---|---|
| 表計算と pdf の両方 | 表計算だけ |
| xlsx あり | xlsx |
| xlsx 無し・xls あり | xls（大東建託 `history_*.xls`） |

- AWS ELB が識別 UA を 403 する発行体は、同じ `http_get` がブラウザ UA に一度だけフォールバックする
- 中間証明書が欠ける／付け違う発行体は、同じ `http_get` がリーフの AIA (CA Issuers) から中間を補って検証し直す。検証は切らない（7544 スリーエフ）

表計算の投入:

- 大東建託 (1878) の `history.xls`、ユナイテッドアローズ (7606) の xlsx は `jobs/monthly/ir_monthly_xls_ingest.py` が扱う
- `config/monthly/ir_monthly_xls.json` の列・行指定で ingest JSON を作り loopback POST する（`via=ir_xls`）。year は送らない
- 汎用 xlsx パーサは無い。銘柄クラスは作らない。HTML ingest の cron には混ぜない
- cron は取得の後に `--all --post`。config の全銘柄について `data/monthly/<code>/` の最新ファイル（ファイル名の日付）を送る
- 毎回全列を送るので、速報の訂正（1878 の前月の空室数など）も次の回で追いつく。同じ値のセルはサーバが書き換えない（`last_ingested_at` も動かない）
- 銘柄ごとに `post` / `fail` を 1 行、最終行 `ir_monthly_xls_ingest done posted= skipped= failed=`
- 1 銘柄の失敗で残りを止めず、失敗があれば exit≠0（KAB-117）

### IR 月次 JSON（`ir_monthly_json_ingest.py`）

ジョブ: [monthly/ir_monthly_json_ingest](gen/monthly.md#monthlyir_monthly_json_ingest)（`jobs/monthly/ir_monthly_json_ingest.py`）

- スギHD (7649) の公開 CloudFront JSON。E-IR と同じく GET して ingest JSON を書き、loopback `POST /api/v1/tables/{code}/ingest` する（`via=ir_json`。year は送らない）
- crontab は HTML ingest と同じ時刻（`deploy/cron-run.sh`）。銘柄クラスは作らない。HTML ingest のレジストリには混ぜない
- JSON の `year` は HTML `data-year`（暦年。2027年2月期の3月は year=2026 month=3）。今年度だけ。過去年度の表は入れない
- 伸び率は +100 して `yoy_index`、`digits` で小数1桁。フィールドと行名は `config/monthly/ir_monthly_json.json`
- `values` は値のある月だけ（`{月: 値}`。null は既存セルを消すので送らない）。同じ値のセルはサーバが書き換えない
- 失敗時は最終行 `failed=K` で exit≠0

### IR 月次 HTML（`ir_monthly_html_r2.py` → `ir_monthly_html_ingest.py`）

ジョブ: [monthly/ir_monthly_html_r2](gen/monthly.md#monthlyir_monthly_html_r2)（`jobs/monthly/ir_monthly_html_r2.py`）→ [monthly/ir_monthly_html_ingest](gen/monthly.md#monthlyir_monthly_html_ingest)（`jobs/monthly/ir_monthly_html_ingest.py`）

#### 取得

- 対象は `config/monthly/ir_monthly.json` の html。セレクタは json（`follow_re` / `source=eir` / `parse`）
- 年度別ページは `follow_re` で最新年を辿る
- E-IR は Pronexus JSONP の月次 item `text` を `.html` として保存する（sha256 はその HTML）
- `.html`（原本）と `.ingest.json`（派生。`POST /api/v1/tables/{code}/ingest` と同じ形）を隣に置く。自動 POST しない

| 失敗 | ログ | summary の `failed=` と exit=1 |
|---|---|---|
| `http_get` が打ち切った 429 | `failed` にせず `skip issuer {code} rate-limited:` | 数えない |
| 同一ホストの残り | `skip issuer {code} host-cooled {host}` | 数えない |
| 404・パース・I/O など | — | 数える（`failed>0`） |

再試行回数は増やさない。

| `parse` | 扱い |
|---|---|
| `parse: true` | `jobs/monthly/html_issuers` の `parse`（`PARSERS`。`i<code>.py` を置けば載る）。各モジュールが表・行・単位を宣言する |
| `parse: false` | モジュールを作らない。画像・本文のみのページはこれにする（HTML は残し JSON は書かない） |

- パーサーを直したら保存済み HTML を `--reparse`
- HTML が同じでも、現行パーサの `.ingest.json` が保存済みと違えば hash skip 時に書き直す（古い sidecar を latest-per-code に残さない）

#### 投入

DB 投入は別スクリプト `jobs/monthly/ir_monthly_html_ingest.py`。

- code ごとにファイル名日付が最新の `.ingest.json` を loopback へ POST する。year は送らない
- sha256 が前回成功分と同じなら POST しない
- 行に `force` は付けない（同名で unit が違う行・類似名は 409 で止め、人が `--force --only` で入れる）
- `--months` は暦年の `YYYY-MM` を `only_months` として 1 回 POST する（年はサーバの mapping。部分投入は ledger を更新しない）
- cron は producer の後
- ledger は dest ではなく `/var/log/app/html_ingest_ledger.json`

#### unit と as_of

- unit 判定の context は page 見出しに加え、当該 table 直上の section とページ内の `(単位：%)` / `単位:%` を含む
- unit は見出しから（[unit](#unit) の表）

| 見出し・セル | unit |
|---|---|
| 見出しに 増減比/伸び率 がある（ツルハ・サガミ） | `単位:%` より `yoy_pct` を優先 |
| ページに単位表示が無く指数な銘柄 | モジュールで `yoy_index` を宣言する |
| 見出しが伸び率でもセルが符号なし100基準の銘柄（3148 クリエイトSD） | モジュールで `yoy_index` を宣言する |
| 店舗数・拠点数・出退店の行 | 件数なので `number` |

`as_of` は次の順で最初に取れたもの。取れなければ JSON は書かない。

1. 開示日
2. 表の最終値の年月（セルの年 or 表直前の期見出し / `YYYY年M月期` / `YYYY年度` / `YYYY年M月～YYYY年M月`）

#### 表の選び方と行の作り方

- 複数年度のアーカイブは期が新しい表を優先。同じ期末なら最終値の新しい表を優先。12か月埋まっている表は最終値では選ばない
- 1表に複数年が並ぶアーカイブ（大黒天物産）は同月の先勝ち（新しい方）。`YYYY年（令和N年）度` 年度帯は見出しに入れない
- 同ページの店舗数表と売上表は、月軸と表のセル年が同じとき同一 payload に足す。行に `YYYY年M月` がある表の as_of はページ全体の「現在」よりセルの年を優先（6537）
- ページ見出しが売上の前年比でも、客数・客単価・店舗数・拠点数・営業所数・開店/閉店/出店/退店/開設/撤退の行には `売上高前年同月比` を足さない
- 数値の無い 既存店/全店 の帯行は、直後の指標行に付ける
- 1表に上期と下期の月見出しが続くとき（神戸物産の単体月次）は後半の月も同じ行に足す。行名に全店は付けない
- year は書かない。`△1.9` は `-1.9`。`累計` / `通期計` / `上期計` は months に入れない
- HTML ingest JSON の `months` は末尾の空列を落とす（値のある最終月で終わる）。途中の欠月は残す

#### 銘柄ごとのモジュール

| 銘柄 | モジュール | 規則 |
|---|---|---|
| ミニストップ (9946) | `jobs/monthly/html_issuers/i9946.py` | `全店売上` を選び、`売上高合計` だけ残す。選んだ表の次の表が同じ行（売上高合計前年比 / CVS前年比 / 店内加工FF前年比）で月が続く（3〜8 → 9〜2）ときは同じ行に足す。`1店1日当り` と CVS / 店内加工FF は採らない |
| やまや (9994) | `jobs/monthly/html_issuers/i9994.py` | 既存店と全店をマージし、卸計は選ばない。`当 年`→`売上高`、`前 年` は落とす。unit は `yoy_index` |
| ロック・フィールド (2910) | `jobs/monthly/html_issuers/i2910.py` | `既存店売上` / `全店売上` をマージし、業態名の行に見出しのスコープを付ける（付けないと既存店の数値が裸の `売上高(前年比)` になる）。表の最下行 `合計` は会社の `既存店/全店 売上高前年同月比`（canonicalize で根の `既存/全店 売上高(前年比)`） |
| フジ (8278) | `jobs/monthly/html_issuers/i8278.py` | 改装の店舗面積リストは選ばず、営業速報に総店舗数だけ足す |
| クリエイトSD (3148) | `jobs/monthly/html_issuers/i3148.py` | 見出しが伸び率でもモジュールが `yoy_index` を宣言する |
| 神戸物産 (3038) | `jobs/monthly/html_issuers/i3038.py` | 主表は先頭行が「売上高」の表。売上総利益・営業利益・経常利益の表は選ばない。同じページの「店舗仕入れ前年対比」は月が揃うとき足す。セグメントは仕入れ。脚注の商品出荷は行名にしない |
