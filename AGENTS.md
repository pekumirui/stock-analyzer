# stock-analyzer

個人用の日本株分析ツール。設計の正本は `DESIGN.md`。README は利用者向け手順。ここには実装時の約束だけ書く。

## スタック

- Python 3.10+ / FastAPI / SQLAlchemy 2 / pandas
- フロントは `static/index.html` の単一 SPA（lightweight-charts + vanilla JS）。フレームワークを足さない
- DB はローカル SQLite (`stock.db`)。本番切替は `DATABASE_URL` のみ
- Windows 前提。仮想環境は `.venv`。実行は `.\run.ps1`、初回は `.\setup.ps1`

## モジュール分担

| 場所 | 役割 |
|---|---|
| `app/config.py` | 環境変数・ステージ判定閾値。閾値はここに集約 |
| `app/models.py` | SQLAlchemy スキーマ |
| `app/jquants.py` | J-Quants API V2 クライアント（認証・ページング・レート制限） |
| `app/ingest.py` | API → DB 取込（UPSERT・冪等） |
| `app/analytics.py` | MA / RS / ステージ / Q単独 / 決算後リターン |
| `app/api.py` | REST (`/api/*`) |
| `app/main.py` | FastAPI エントリ + SPA 配信 |
| `scripts/run_batch.py` | 日次バッチ / バックフィル |
| `scripts/seed_demo.py` | デモデータ |
| `scripts/check_demo.py` | 本番投入前ガード |
| `deploy/log_mcp.py` | VPS 上の読み取り専用ログ MCP（本番のみ） |

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
- 銘柄コードは 5 桁文字列（例 `"72030"`）
- デモ銘柄は必ず `"0"` 始まり。実コードは 1-9 始まりなので衝突しない
- **デモと実データを混ぜない。** `batch` / `backfill` の前に `scripts/check_demo.py` が走る。デモが残っていたら `.\run.ps1 reset` してから本番投入
- 取込は UPSERT で冪等。何度走らせても壊れないこと
- Q単独は保存しない。`analytics.quarter_summary` で累計差から都度算出
- `Statement.raw_json` は元レスポンス全項目を残す（将来項目追加用）
- `indicators` のステージは 1/2/3/4。判定不能は `NULL`

## コマンド

```powershell
.\setup.ps1              # .venv + 依存
.\run.ps1 seed           # デモ投入（API不要）
.\run.ps1 serve          # http://localhost:8000
.\run.ps1 reset          # stock.db 削除（本番投入前）
.\run.ps1 batch          # 日次（要 .env）
.\run.ps1 backfill       # 初回 5 年（要 .env）
```

Python は `.venv\Scripts\python.exe` を使う。システムの python で依存を入れない。

検証の目安:

- API: `http://localhost:8000/api/stocks/search?q=デモ` や `/api/screener`
- デモ投入後に UI の決算・チャート・スクリーナーが空でないこと
- バッチ変更時はデモ混在ガードがまだ効くこと

## Git（ソースを編集するとき）

- `main` 上では実装しない。最初のファイル編集の前に `git checkout -b <type>/<短い名前>`（`feat` / `fix` / `chore`）
- `.env` / `stock.db` / `.venv` はコミットしない
- `main` へマージすると GitHub Actions がテスト → タグ/Release → VPS デプロイする。手元で `main` に直接 push しない
- VPS 初期設定は `deploy/provision.sh`（冪等）
- コミット先頭が `feat:` なら minor、`BREAKING CHANGE` / `type!:` なら major、それ以外は patch

## 本番ログ MCP

- VPS 上の `stock-analyzer-mcp` が `127.0.0.1:8010/mcp` を待ち、nginx が `https://<domain>/mcp` に出す
- ツールは `service_status` / `app_logs` / `batch_logs` / `nginx_logs` のみ。任意シェルも restart もない
- 認証は `MCP_TOKEN`（Bearer または `?token=`）。GitHub secret と同値。値をログに出さない
- Grok カスタムコネクタは OAuth（PKCE、クライアントシークレットなし）。Client ID は `stock-analyzer`。許可画面で `MCP_TOKEN` を入れる
- アプリ本体 (`app/*`) にログ API を足さない。Debug VPS ワークフローは MCP が死んだときの退避路

## 実装方針

- 既存スタイルに合わせる。型ヒントは付ける。コメントは非自明な制約だけ（レート制限、調整済、デモ混在禁止など）
- 閾値・プラン上限・ベース URL は `config.py`。ハードコードしない
- UI はスマホでも使えること。`static/index.html` を分割しない（現状の単一ファイル方針）
- 秘密情報（`.env`, API キー）をコミットしない・ログに出さない
- DESIGN.md と実装が食い違うときは、実装を直すか DESIGN を更新する。放置しない
- 米国株・IRBank・通知は将来拡張。今は日本株 + J-Quants に閉じる
