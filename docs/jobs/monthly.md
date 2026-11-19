# 取込と保存の規則: 月次

ジョブの起動時刻・引数・読み書きする表は [ジョブ設計](gen/monthly.md)（自動生成）。行名は [月次の行名](monthly_labels.md)。ここには規則だけ書く。

## 月次表の保存

月次一覧の版（name = monthly_list）を上げるのは monthly_tables / monthly_elements /
monthly_values / monthly_issuers / stocks の INSERT・UPDATE・DELETE（行ごと）。
API・cron・MCP・手書きの SQL のどれでも上がる。トリガーは init_db と jobs/migrate.py が作る。
行がまだ無いあいだの版は 0。last_ingested_at は版に使わない（更新しない書き込みがある）。
一覧の is_new は `last_ingested_at` の JST 暦日が今日を含む直近 3 日（当日・前日・前々日）。`ingested_today` は JST の今日。どちらも日付で変わるので、使い回しのキーは (version, JST の今日)。
結果はワーカーのメモリに置く。別プロセスの書き込みは、次の読みで版が変わって作り直す。

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
number で表示単位がある指標は項目名の末尾に `（単位）` を付け、セルは四捨五入した数字だけにする。対応は [`docs/jobs/monthly_labels.md`](monthly_labels.md)。GET の JSON と保存名は単位なし。

**行名。** 正本は [`docs/jobs/monthly_labels.md`](monthly_labels.md)。
ingest / POST は `canonicalize_element_label` してから既存行を探す。GET は保存名。SPA は `fmtMonthlyElement`。

ingest が新しい行を作るとき、同じ (code, year) に `strip_yoy_fragments` 後の名前が同じで unit が yoy と number で違う既存行があり、その既存行が今回の `rows` に無いなら 409 `YOY_NUMBER_CONFLICT`。`force=true` で作る。同じ POST に両方あるときは作る（実数と前年比の併存）。既にある行の更新は対象外。`POST .../elements` はこの検査をしない。

## 月次ファイルの取得と投入

**月次ファイルの保存（`jobs/*_r2.py`）。** 保存先はリポジトリの `data/monthly/{code}/`。`SHARE_ROOT` を置くと、その直下の `monthly/{code}/` になる。cron と運用は `deploy/README.md`。共通の約束は次のとおり。

- `{code}` は TDnet の発行体コードそのまま（9279 / 262A）。J-Quants 5 桁にしない。stocks 照合だけ display_code + 末尾 0（9282→92820, 262A→262A0）
- stocks に無いコードはダウンロードしない。DB が読めない／map が空なら警告のみで継続
- 既存ファイルは消さない。IR（PDF/xlsx/HTML）は同じ URL の上書き公開を日付付きで残し、中身の sha256 が同じなら skip
- 3 本のバッチ（TDnet PDF / IR PDF・xlsx / IR HTML）の cron は分ける
- 日付・HTTP・銘柄マスタ・ファイル名の共通処理は `jobs/_r2common.py`。銘柄マスタは `app.db` の読み取り専用エンジン（`sqlite_readonly_engine`）で読み、DB が無い・読めないときは空のまま続ける。`http_get` は TDnet も IR も同じ。識別 UA はジョブごとで、403 のときだけブラウザ UA に一度切り替え、中間証明書の欠落は AIA で補う。429 / 408 / 5xx は Retry-After があればそれに従い（最大 60 秒）、無ければ指数バックオフ＋jitter（5→10→20→40→60 秒、最大 6 回）。打ち切ったら例外を上げる。発行体ログは原則 `fail issuer {code} fetch:`（IR HTML の 429 だけ下の例外）
- 新しいファイルは `data/` に書く。rclone FUSE（`/home/stock/r2-share`）の切断プロトコルは、dest がまだそのマウント上にあるときに働く。`data/` の dest は `/proc/mounts` を見ない。マウントが切れたら各社 `fail` を並べない。開始時と処理中に dest を probe し、ECONNABORTED / ENOTCONN / ESTALE / ルート消失なら `RestartSec` と同じ 5 秒を挟んで再試行し、駄目なら `R2 マウント切断` の 1 行で exit≠0。正常時の行は変えない。マウントの再起動は `deploy/r2-share.service` と `r2-share-watch.timer`。デプロイはハッシュが変わったときだけ `r2-share.service` を restart する（`deploy/README.md`）

TDnet 月次 PDF（`jobs/monthly/tdnet_monthly_r2.py`）:

- TDnet の開示一覧（`jobs/_tdnet.py`）の表題を `LIST_KEYWORDS` で絞る。`月次` が無い売上速報・○月度・受注速報・売上推移・業績速報なども取る。`is_monthly_title` が四半期短信・自己株式・新株予約権の月間行使・業績予想を落とす。`月度` 単体では取らず、売上/受注/IRニュースなどのヒントが要る。日次 cron と backfill は同じ判定
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
