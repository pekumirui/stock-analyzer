# stock-analyzer

個人用の日本株分析ツール。設計の正本は `DESIGN.md`。画面は `docs/DESIGN_*.md`。README は利用者向け手順。ここには実装時の約束だけ書く。

## スタック

- Python 3.10+ / FastAPI / SQLAlchemy 2 / pandas
- フロントは `static/index.html` + `static/css/` + `static/js/` の vanilla JS。フレームワークを足さない
- DB はローカル SQLite (`stock.db`)。本番切替は `DATABASE_URL` のみ
- Windows と Linux。仮想環境は `.venv`。Windows は `.\setup.ps1` / `.\run.ps1`（python は `.venv\Scripts\python.exe`）。Linux は `./setup.sh` / `./run.sh`（python は `.venv/bin/python`）

## モジュール分担

| 場所 | 役割 |
|---|---|
| `app/config.py` | 環境変数・ステージ判定閾値。閾値はここに集約 |
| `app/models.py` | SQLAlchemy スキーマ |
| `app/jquants.py` | J-Quants API V2 クライアント（認証・ページング・レート制限） |
| `app/ingest.py` | API → DB 取込（UPSERT・冪等） |
| `app/analytics.py` | MA / RS / ステージ / Q単独 / 決算後リターン |
| `app/api.py` | REST (`/api/*`) |
| `app/monthly_ingest.py` | PDF 月次列順 + as_of 時点の最新 `fiscal_year_end` → 暦年（純関数。year は受け取らない。列ごと右→左。月重複の year-wrap 可）。layout は calendar / fiscal / partial（並びでは拒否しない）。unit は見出しから。行名は空白区切り `[国] [セグメント] [チャネル] [スコープ] [指標]`（`canonicalize_element_label`。yoy は前年比語を剥がして `(前年比)`）。銘柄固有の残りは `app/issuer_aliases.json`（キーも canonicalize 後。ingest は as_of < until、POST/PUT/migrate は year <= until.year。テストは `plan_issuer_alias_cases` が JSON から生成） |
| `app/main.py` | FastAPI エントリ + SPA 配信 |
| `scripts/run_batch.py` | 日次バッチ / バックフィル |
| `scripts/tdnet_monthly_r2.py` | TDnet 月次PDF → R2 mount (`/home/stock/r2-share/monthly/{code}/`) |
| `scripts/tdnet_xbrl_r2.py` | TDnet XBRL ZIP → R2 mount (`/home/stock/r2-share/xbrl/`) |
| `app/xbrl/` | TDnet 短信 iXBRL の parse / YAML マッピング / normalize。UI には出さない |
| `app/xbrlp/` | kawasin73 gist の vendor。改変しない |
| `scripts/check_demo.py` | スキーマ検査 + 旧デモ取り残しガード |
| `deploy/log_mcp.py` | VPS 上のログ MCP（本番のみ）。月次表ツールは localhost:8000 の FastAPI のみ |
| `tools/` | オフライン検証。月次PDF指紋は `fmtdiff.py`（pdfplumber。`--dir` は同一発行体の連続月だけ。DIR/*.pdf と DIR/<code>/*.pdf。空なら DIR/monthly を1段）。XBRL の IRBANK 一致率は `xbrl_match_rate.py`（stdlib+sqlite3。`docs/XBRL_VERIFY.md`）。ingest は従来どおり pdftotext |

新しい依存やレイヤを増やさない。ロジックは上のファイルに足す。

## J-Quants（必須）

- **V2 のみ**。V1（メール/パスワード → idToken）は使わない
- 認証は `JQUANTS_API_KEY` を `x-api-key` に載せるだけ。トークン更新は不要
- エンドポイントは `/v2/equities/master`, `/v2/equities/bars/daily`, `/v2/indices/bars/daily/topix`, `/v2/fins/summary`, `/v2/markets/calendar`
- レスポンスは `{"data": [...], "pagination_key": "..."}`。カラムは短縮形 (`O`/`C`/`AdjC` 等)
- 取込は **日付指定で全銘柄を1リクエスト**。銘柄ループで全日を取りに行かない
- Light は 60 req/分。`/fins/*` は別枠 60 req/分。`jquants.py` のトークンバケットを通す。直叩きしない
- 429 は既存の指数バックオフ（5→10→20→40→60秒、最大6回）に乗せる
- J-Quants の仕様確認は接続済みの `j-quants-doc-mcp` を使う

## データルール

- 分析・チャート・RS・ステージは **調整済価格** (`adj_*`)。生値は表示・保存用
- 銘柄コードは 5 桁文字列（例 `"72030"`, `"262A0"`）。パターンは `^[0-9][0-9A-Z]{3}[0-9]$`。表示の4桁 (262A / 7203) もある。実コードは 1-9 始まり
- 合成デモデータは投入しない。`scripts/seed_demo.py` は置かない
- 旧デモの `0` 始まりコードが残っていると RS ランクが狂う。`batch` / `backfill` の前に `scripts/check_demo.py` が走る。残っていたら `.\run.ps1 reset` または `./run.sh reset`
- 取込は UPSERT で冪等。何度走らせても壊れないこと
- Q単独は保存しない。`analytics.quarter_summary` で累計差から都度算出
- `Statement.raw_json` は元レスポンス全項目を残す（将来項目追加用）
- `indicators` のステージは 1/2/3/4。判定不能は `NULL`
- 月次 `MonthlyElement.unit` は `number` / `yoy_index` / `yoy_pct`。PDF 見出しから付ける（値の大きさから推測しない）。保存値は原文のまま（Gift 125.8 は 125.8、Intermestic -7.1 は -7.1）。表の表示は指数 100=フラット（`yoy_index` はそのまま、`yoy_pct` は `100+v`。▲ や +X.X% にはしない）
- 月次の行名: `canonicalize_element_label`。空白区切り `[国] [セグメント] [チャネル] [スコープ] [指標]`（無い層は出さない）。国は `日本` / `海外`（裸の `国内` だけ `日本`。複合の `国内リテール` 等はセグメント。`中国` もセグメント）。チャネルは `直営` / `FC` / `直営+FC`。スコープは `既存` / `全店`（`既存店`/`全店舗` は短く、`全社` は `全店`。`全店舗数` は `店舗数`）。指標は `売上高` / `客数` / `客単価` / `出店` / `退店` / `店舗数`。開店/新規は出店、閉店/閉鎖は退店。出店/退店に 全店/既存 は付けない（スコープ層は 売上高/客数/客単価）。スコープ空で 売上高/客数/客単価 なら `全店` を補う。`店舗数` と出店/退店には補わない。合計/総計/小計/計 と、店舗数の 月末/期末 は落とす。対（対前年比の残り）は落とす。指標が空なら 売上高（`LOWYA 対(前年比)` → `LOWYA 全店 売上高(前年比)`。ワッツの空指標 `直営 全店(前年比)` は触らない）。出店/退店は指標（店舗数にしない、セグメントにもしない。並びは 出店 → 退店 → 純増/新設/改装/業態転換 → 店舗数。純増等は名前を変えないが指標位置へ）。実店舗/EC はセグメント（`実店舗数` は `実店舗 店舗数`。店舗数 unglue で 実 に割らない）。yoy は PDF 由来の前年比語を剥がして `(前年比)` を1回（保存も表示も同じ。保存名は常に正規名）。number は前年比を付けない（number の `売上高` と yoy の `売上高(前年比)` は別行）。ingest / POST は正規化してから既存行を探す。正規化後に同じなら同一行。短い保存名は正規名へリネームする。`init_db` / migrate は number / yoy とも `canonicalize_element_label` へリネームする（同一年の重複がなくても。例: `全店 売上高前期比` → `全店 売上高(前年比)`）。正規名が既にあれば正規名を残し、値は非NULL優先で寄せて短い行を消す。SPA の行キーは表示名（`fmtMonthlyElement`、同じ規則）。銘柄固有の残りは `app/issuer_aliases.json`。`until` 未満の as_of（ingest）または `until.year` 以下の year（POST / PUT / 新規行 / migrate）で canonicalize 後の名前を rename する。JSON のキーも canonicalize してから載せる。マップに無い名前はそのまま。`scripts/test_monthly_ingest.py` に銘柄分岐は足さない。前期・前年同月・前年同期の実数行（直後が `比` でない）は canonicalize 前に 400 `PRIOR_PERIOD_ELEMENT`。ingest / POST elements のみ。保存名は NFKC しない。正規化後 50 文字超は 400
- 月次は `stocks` にある code だけ書く。ingest / POST elements / 訂正 PUT / industry PUT は先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`。マスタ無しでは ingest しない。FK は付けない（`PRAGMA foreign_keys` も有効化しない）。`init_db` / migrate は stocks に無い月次を values → elements → tables → issuers の順に DELETE（冪等）
- 月次業種は `monthly_issuers.industry`。既定は `その他`。`stocks.sector33` とは別。ingest は `last_ingested_at` を更新し industry は触らない

## コマンド

```powershell
.\setup.ps1              # .venv + 依存
.\run.ps1 serve          # http://localhost:8000
.\run.ps1 reset          # stock.db 削除
.\run.ps1 batch          # 日次（要 .env）
.\run.ps1 backfill       # 初回 5 年（要 .env）
```

```bash
./setup.sh               # .venv + 依存
./run.sh serve           # http://localhost:8000
./run.sh reset           # stock.db 削除
./run.sh batch           # 日次（要 .env）
./run.sh backfill        # 初回 5 年（要 .env）
```

Python は Windows では `.venv\Scripts\python.exe`、Linux では `.venv/bin/python` を使う。システムの python で依存を入れない。`.env` / `stock.db` / `.venv` はコミットしない。既存の `.env` は上書きしない。

検証の目安:

- API: `http://localhost:8000/api/stocks/search?q=トヨタ` や `/api/screener`
- backfill 後に UI の決算・チャート・スクリーナーが空でないこと
- バッチ変更時は 0 始まりコードの取り残しガードがまだ効くこと

## Git（ソースを編集するとき）

- `main` 上では実装しない。最初のファイル編集の前に `git checkout -b <type>/<短い名前>`（`feat` / `fix` / `chore`）
- `.env` / `stock.db` / `.venv` / `rclone.conf` / `*.pdf` はコミットしない
- `main` へマージすると GitHub Actions がテスト → タグ/Release → VPS デプロイする。手元で `main` に直接 push しない
- VPS 初期設定は `deploy/provision.sh`（冪等）
- コミット先頭が `feat:` なら minor、`BREAKING CHANGE` / `type!:` なら major、それ以外は patch

## 本番ログ MCP

- VPS 上の `stock-analyzer-mcp` が `127.0.0.1:8010/mcp` を待ち、nginx が `https://<domain>/mcp` に出す
- ツールは `service_status` / `app_logs` / `batch_logs` / `nginx_logs` / `db_stats` / `db_lookup` / `monthly_list` / `monthly_get` / `monthly_add_element` / `monthly_put_values` / `monthly_delete_element` / `monthly_set_industry` / `monthly_ingest`。任意シェルも restart も任意 SQL もない。月次ツールは `http://127.0.0.1:8000/api/v1/tables` のみ（nginx Basic は使わない）。`monthly_get` の `values` は 1〜12。無い月は null。0 は実ゼロ。1年通して値のない行は出さない
- 月次の新規登録は `monthly_ingest`（PDF 列順 + as_of 時点の最新 `fiscal_year_end` から暦年を決める。year は渡さない。月が重複する year-wrap は列順配列の `values`）。`monthly_put_values` の year は暦年で、訂正用。年付き put は訂正のみ。行の削除は `monthly_delete_element`（値が無い行だけ。残っていれば 409。表は残す）。月次業種は `monthly_set_industry`。書き込みは stocks にある code だけ（マスタ無しは `NOT_IN_MASTER`。FK は付けない）
- 月次の unit は `number` / `yoy_index` / `yoy_pct` の3種。エージェントは PDF 見出しから付ける（前年比 100基準 → `yoy_index`、▲付き変化率 → `yoy_pct`、店舗数 → `number`）。値の大きさから推測しない。保存値は PDF 原文のまま（125.8 は 125.8 のまま）。既存行の unit 変更は `PUT /api/v1/tables/{code}/{year}/elements`（`{element, unit}`）
- 認証は `MCP_TOKEN`（Bearer または OAuth で発行した access token）。GitHub secret と同値。値をログに出さない
- 本番ログは `/var/log/app/`（`stock-analyzer.log` / `batch.log` / `mcp.log`）。journal も残る。ローカルのバッチはアプリディレクトリの `batch.log`
- Cloudflare R2 bucket `share` は VPS 上で rclone user mount（`stock`、`loginctl enable-linger`）→ `/home/stock/r2-share`。認証は GitHub secrets `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` を `update.sh` が `~/.config/rclone/rclone.conf` に書く（リポジトリに置かない）
- `scripts/tdnet_monthly_r2.py N` が TDnet 月次PDF を `/home/stock/r2-share/monthly/{code}/YYYYMMDD_CODE_….pdf` へ保存。`{code}` は TDnet の発行体コードそのまま（9279, 262A）。J-Quants の5桁（92790, 262A0）にはしない。インフラファンドはダウンロード時にスキップ（`013`/`022` かつ名称に「インフラ」。マスタ欠落時は TDnet 社名の「インフラ投資法人」）。通常の REIT（8985 ジャパン・ホテル・リート等）は残す。stocks に無いコードはダウンロードしない（DB が読めない／map が空なら警告のみで継続）。stocks 照合だけ display_code + 末尾 0（9282→92820, 262A→262A0）。既存 PDF は消さない。cron 18:00/21:00 JST は N=1。初回は Actions の **TDnet monthly backfill**（days=30）。`*.pdf` と `rclone.conf` はコミットしない
- `scripts/tdnet_xbrl_r2.py N` が TDnet XBRL ZIP を `/home/stock/r2-share/xbrl/{code4}/` へ保存。マスタ無しとインフラはスキップせず `_unmatched/`。skip は lookback 内 manifest の `xbrl_id`。cron は 12–23 時 JST の毎時0分、N=1。
- XBRL のパース以降は `docs/DESIGN_xbrl.md`。`python scripts/xbrl_parse.py` が manifest → `xbrl_*` 生データ、`python scripts/xbrl_normalize.py` が YAML → `xbrl_financials`。cron は 12–23 時 JST の毎時10分 parse / 20分 normalize。normalize は未反映と再パース分だけ。決算 UI / `statements` は触らない。マッピングは `config/xbrl/` だけ。`_unmatched/` と iXBRL 無し ZIP は skipped。
- XBRL 検証DBと IRBANK ゴールデン一致率は `docs/XBRL_VERIFY.md`。計測は `tools/xbrl_match_rate.py`（ネットワーク無し）。stocks コピーは `tools/copy_stocks_for_verify.py`（prod `stock.db` には書かない）。
- Grok カスタムコネクタは OAuth（PKCE、クライアントシークレットなし）。Client ID は `stock-analyzer`。許可画面で `MCP_TOKEN` を入れる
- Cursor / Grok Bot の OAuth callback は `https://www.cursor.com/agents/mcp/oauth/callback`、デスクトップは `http://localhost:8787/callback`（localhost で許可済）、フォールバックは `cursor://anysphere.cursor-mcp/oauth/callback`
- アプリ本体 (`app/*`) にログ API を足さない。Debug VPS ワークフローは MCP が死んだときの退避路

## 実装方針

- 既存スタイルに合わせる。型ヒントは付ける。コメントは非自明な制約だけ（レート制限、調整済、旧デモ取り残し禁止など）
- 閾値・プラン上限・ベース URL は `config.py`。ハードコードしない
- UI はスマホでも使えること。
- UI の変更は該当ファイルだけ読む。例: 月次なら `static/js/monthly.js` と必要な CSS。`index.html` 全体と全 JS を読まない
- 新規タブを足すときは `static/js/<tab>.js` を追加し、`index.html` に section と script を1行足す
- キャンバスは白。カードを浮かさない。横幅はビューポート。密度優先。スマホとPCは同じ構造。詳細は `docs/SCREEN_SPEC.md` の共通見た目
- 月次タブの画面は `docs/DESIGN_monthly.md`。他タブの画面仕様を足すときは `docs/SCREEN_SPEC.md`
- 秘密情報（`.env`, API キー）をコミットしない・ログに出さない
- DESIGN.md と実装が食い違うときは、実装を直すか DESIGN を更新する。放置しない
- 米国株・IRBank・通知は将来拡張。今は日本株 + J-Quants に閉じる
