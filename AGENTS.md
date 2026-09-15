# stock-analyzer

個人用の日本株分析ツール。ここには実装時の約束だけ書く。詳細は正本へ。同じ文を二箇所に置かない。

| 何 | 正本 |
|---|---|
| 設計・スキーマ・取込・API | `DESIGN.md` |
| 画面（タブ別） | `docs/DESIGN_<tab>.md`。書き方と共通見た目は `docs/SCREEN_SPEC.md` |
| 月次の行名 | `docs/DESIGN_monthly_labels.md` |
| XBRL の設計と検証 | `docs/DESIGN_xbrl.md` / `docs/XBRL_VERIFY.md` |
| VPS・MCP・R2 の運用 | `deploy/README.md` |
| 利用者向け手順・コマンド | `README.md` |

## スタック

- Python 3.10+ / FastAPI / SQLAlchemy 2 / pandas
- フロントは `static/index.html` + `static/css/` + `static/js/` の vanilla JS。フレームワークを足さない
- DB はローカル SQLite (`stock.db`)。本番切替は `DATABASE_URL` のみ
- 仮想環境は `.venv`。Windows は `.\setup.ps1` / `.\run.ps1`、Linux は `./setup.sh` / `./run.sh`
- Python は `.venv\Scripts\python.exe`（Windows）/ `.venv/bin/python`（Linux）。システムの python で依存を入れない
- 新しい依存やレイヤを増やさない。ロジックは下の表のファイルに足す

## モジュール分担

| 場所 | 役割 |
|---|---|
| `app/config.py` | 環境変数・閾値・プラン上限・ベース URL。ハードコードせずここに集約 |
| `app/models.py` | SQLAlchemy スキーマ |
| `app/jquants.py` | J-Quants API V2 クライアント（認証・ページング・レート制限） |
| `app/ingest.py` | API → DB 取込（UPSERT・冪等） |
| `app/analytics.py` | MA / RS / ステージ / Q単独 / 決算後リターン |
| `app/api.py` | REST (`/api/*`) |
| `app/monthly_ingest.py` | PDF 月次列 → 暦年。行名正規化と aliases |
| `app/main.py` | FastAPI エントリ + SPA 配信 |
| `app/xbrl/` | TDnet 短信 iXBRL の parse / YAML マッピング / normalize。UI には出さない |
| `app/xbrlp/` | kawasin73 gist の vendor。改変しない |
| `scripts/run_batch.py` | 日次バッチ / バックフィル |
| `scripts/check_demo.py` | スキーマ検査 + 旧デモ取り残しガード。`batch` / `backfill` の前に走る |
| `scripts/*_r2.py` | TDnet / IR → R2 mount。下の「R2 バッチ」 |
| `deploy/log_mcp.py` | VPS 上のログ MCP（本番のみ） |
| `tools/` | オフライン検証。月次 PDF 指紋は `fmtdiff.py`（pdfplumber。ingest は従来どおり pdftotext）。XBRL 一致率は `xbrl_match_rate.py` |

## J-Quants（必須）

- **V2 のみ**。V1（メール/パスワード → idToken）は使わない
- 認証は `JQUANTS_API_KEY` を `x-api-key` に載せるだけ。トークン更新は不要
- エンドポイントは `/v2/equities/master`, `/v2/equities/bars/daily`, `/v2/indices/bars/daily/topix`, `/v2/fins/summary`, `/v2/markets/calendar`
- レスポンスは `{"data": [...], "pagination_key": "..."}`。カラムは短縮形 (`O`/`C`/`AdjC` 等)
- 取込は **日付指定で全銘柄を1リクエスト**。銘柄ループで全日を取りに行かない
- Light は 60 req/分。`/fins/*` は別枠 60 req/分。`jquants.py` のトークンバケットを通す。直叩きしない
- 429 は既存の指数バックオフ（5→10→20→40→60秒、最大6回）に乗せる
- 仕様確認は `j-quants-doc-mcp`

## データルール

- 分析・チャート・RS・ステージは **調整済価格** (`adj_*`)。生値は表示・保存用
- 銘柄コードは 5 桁文字列（例 `"72030"`, `"262A0"`）。パターンは `^[0-9][0-9A-Z]{3}[0-9]$`。実コードは 1-9 始まり。表示は 4 桁 (7203 / 262A)
- 合成デモデータは投入しない。`scripts/seed_demo.py` は置かない
- 旧デモの `0` 始まりコードが残っていると RS ランクが狂う。`check_demo.py` のガードを外さない。残っていたら `reset`
- 取込は UPSERT で冪等。何度走らせても壊れないこと
- Q単独は保存しない。`analytics.quarter_summary` で累計差から都度算出
- `Statement.raw_json` は元レスポンス全項目を残す
- `indicators` のステージは 1/2/3/4。判定不能は `NULL`

### 月次

取込・暦年割当・API・業種は `DESIGN.md` §3 / §5 と `docs/DESIGN_monthly.md`。ここは守る点だけ。

- `stocks` に無い code は書かない。ingest / POST / PUT は先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`
- FK は付けない。`PRAGMA foreign_keys` も有効化しない
- `MonthlyElement.unit` は `number` / `yoy_index` / `yoy_pct`。見出しから付ける（値の大きさから推測しない）。保存値は原文のまま
- 行名は `canonicalize_element_label`。銘柄固有は `app/issuer_aliases.json`。新しい例外は `docs/DESIGN_monthly_labels.md` に箇条書きで足す
- `scripts/test_monthly_ingest.py` に銘柄分岐は足さない
- 月次業種は `monthly_issuers.industry`（既定 `その他`）。`stocks.sector33` とは別。ingest は `last_ingested_at` だけ更新し industry を触らない

## 検証の目安

- API: `/api/stocks/search?q=トヨタ` や `/api/screener`
- backfill 後に UI の決算・チャート・スクリーナーが空でないこと
- UI を実際に動かして確かめるなら `.cursor/skills/verify-stock-analyzer/SKILL.md`

## Git

- `main` 上では実装しない。最初のファイル編集の前に `git checkout -b <type>/<短い名前>`（`feat` / `fix` / `chore`）
- `.env` / `stock.db` / `.venv` / `rclone.conf` / `*.pdf` はコミットしない。既存の `.env` は上書きしない
- 秘密情報（API キー、`MCP_TOKEN`）をコミットしない・ログに出さない
- `main` へマージすると GitHub Actions がテスト → タグ/Release → VPS デプロイする。手元で `main` に直接 push しない
- コミット先頭が `feat:` なら minor、`BREAKING CHANGE` / `type!:` なら major、それ以外は patch
- VPS 初期設定は `deploy/provision.sh`（冪等）

## 本番（VPS / MCP / R2）

ツール一覧・OAuth・Secrets・ログの場所は `deploy/README.md`。ここは守る点だけ。

- MCP に任意シェル・restart・任意 SQL は無い。足さない
- 月次ツールは `http://127.0.0.1:8000/api/v1/tables` のみ（nginx Basic は使わない）
- 月次の新規登録は `monthly_ingest`。year は渡さない。`monthly_put_values` の year は暦年で訂正専用
- `monthly_delete_element` は値の無い行だけ（残っていれば 409）。表は残す
- アプリ本体 (`app/*`) にログ API を足さない。MCP が死んだときの退避路は Actions の **Debug VPS**
- R2 bucket `share` は VPS で `/home/stock/r2-share` に rclone mount。`rclone.conf` はリポジトリに置かない

### R2 バッチ

保存先は `/home/stock/r2-share/` 配下。cron は JST。

| スクリプト | 保存先 | cron | 振る舞いの正本 |
|---|---|---|---|
| `scripts/tdnet_monthly_r2.py N` | `monthly/{code}/YYYYMMDD_CODE_….pdf` | 18:00 / 21:00（N=1）。初回は Actions の **TDnet monthly backfill** | `DESIGN.md` §3 |
| `scripts/ir_monthly_r2.py` | `monthly/{code}/….pdf` / `.xlsx` | 10:00 / 16:00 | `DESIGN.md` §3、対象は `config/ir_monthly.json` の issuers |
| `scripts/ir_monthly_html_r2.py` | `monthly/{code}/…_月次.html` + `.ingest.json` | 10:20 / 16:20 | `DESIGN.md` §3、対象は `config/ir_monthly.json` の html |
| `scripts/tdnet_xbrl_r2.py N` | `xbrl/{code4}/` | 12–23 時 毎時 00 分（N=1） | `docs/DESIGN_xbrl.md` |
| `scripts/xbrl_parse.py` → `scripts/xbrl_normalize.py` | `xbrl_*` テーブル | 毎時 10 分 / 20 分 | `docs/DESIGN_xbrl.md` |

- 月次の `{code}` は TDnet の発行体コードそのまま（9279, 262A）。J-Quants の 5 桁にしない。stocks 照合だけ末尾 0 を足す
- `stocks` に無いコードは取らない。XBRL だけは `_unmatched/` へ置く
- 既存ファイルは消さない。IR は同じ URL の上書きを日付付きで残し、中身の sha256 が同じなら skip
- HTML 月次は `.ingest.json` を書くだけ。自動 POST しない。パーサー修正後は `--reparse`
- 各バッチの cron は分ける。1 本にまとめない
- XBRL は決算 UI / `statements` を触らない。マッピングは `config/xbrl/` だけ。エージェントが独断で足さない
- XBRL 検証は別 DB。本番 `stock.db` に書かない（`docs/XBRL_VERIFY.md`）

## 実装方針

- 既存スタイルに合わせる。型ヒントは付ける。コメントは非自明な制約だけ
- UI はスマホでも使えること。共通見た目は `docs/SCREEN_SPEC.md`
- 新規タブは `static/js/<tab>.js` を追加し、`index.html` に section と script を 1 行足す。画面仕様は `docs/DESIGN_<tab>.md`
- 再読込は直近タブを復元する（`static/js/tabs.js`）。新しい例外は `docs/SCREEN_SPEC.md` へ
- 設計と実装が食い違うときは、実装を直すか正本を更新する。放置しない
- 米国株・IRBank・通知は将来拡張。今は日本株 + J-Quants に閉じる
