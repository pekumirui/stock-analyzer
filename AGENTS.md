# stock-analyzer

個人用の日本株分析ツール（J-Quants V2 の株価・決算、月次、サプライ、XBRL）。ここには実装時の約束だけ書く。詳細は正本へ。コードを読めば分かることは書かない。

| 何 | 正本 |
|---|---|
| 設計・スキーマ・取込・API | `DESIGN.md` |
| 画面（タブ別） | `docs/DESIGN_<tab>.md`。書き方と共通見た目は `docs/SCREEN_SPEC.md` |
| 月次の行名 | `docs/DESIGN_monthly_labels.md` |
| XBRL の設計と検証 | `docs/DESIGN_xbrl.md` / `docs/XBRL_VERIFY.md` |
| SQLite バックアップ | `docs/DESIGN_backup.md` |
| VPS・MCP・R2 の運用 | `deploy/README.md` |
| 利用者向け手順・コマンド | `README.md` |

## 環境構築

- Python 3.10+ / FastAPI / SQLAlchemy 2 / pandas。フロントは `static/` の vanilla JS。DB はローカル SQLite (`stock.db`)
- 仮想環境は `.venv`。Windows は `.\setup.ps1` / `.\run.ps1`、Linux は `./setup.sh` / `./run.sh`。システムの python で依存を入れない
- クラウドのエージェント（ベンダー問わず）は `bash .cursor/install.sh`（冪等）で `.venv` / `pdftotext` / `rclone` と、`JQUANTS_API_KEY` があれば `stock.db` を用意する。SPA は `bash .cursor/start-web.sh` が 8000 で配信する。8000 を止めない・`reset` しない
- 秘密情報は各ベンダーの Secrets / 環境変数で渡す: `JQUANTS_API_KEY` / `ESTAT_APP_ID` / `VPS_SSH_KEY` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`。無い変数の機能は使えないものとして進める。チャットで鍵を求めない
- Claude Code の自動メモリは `.claude/memory/`。環境は消えるので、メモリを足したら作業ブランチに一緒にコミットする

## モジュール分担

| 場所 | 役割 |
|---|---|
| `app/` | アプリ本体。直下は機能をまたぐ共通部分、機能ごとに `market/` / `monthly/` / `supply/` / `xbrl/` と `api/`。`app/xbrlp/` は vendor なので改変しない |
| `static/` | SPA（`index.html` + `css/` + `js/<tab>.js`） |
| `jobs/` | バッチ・cron。機能ごとのフォルダ、直下は共通 |
| `config/` | 機能ごとの JSON / YAML 設定と `pyproject.toml`（Ruff / Bandit） |
| `tests/` | 機能ごとのフォルダ。新しい `test_*.py` は `tests/ci_smoke.py` の `SUITES` に登録する |
| `tools/` | オフライン検証と手元ヘルパー |
| `analyzer/` | `stock.db` を読むだけのオフライン分析。約束は `analyzer/README.md` |
| `mcp/` | VPS 上のログ MCP（別プロセス）。`__init__.py` は置かない（公式 SDK の `mcp` と衝突） |
| `deploy/` | VPS のプロビジョン・cron・nginx・運用手順 |
| `docs/` | 画面仕様と個別設計の正本 |
| `data/` | XBRL 検証のゴールデンデータ |
| `.cursor/` | 環境構築スクリプトと手順書（`skills/*/SKILL.md`）。Cursor 専用ではない |
| `.github/` | CI/CD とメンテ用 Actions |

## Git

- `main` 上では実装しない。最初のファイル編集の前に `git checkout -b <type>/<短い名前>`（`feat` / `fix` / `chore`）
- `.env` / `stock.db` / `.venv` / `rclone.conf` / `*.pdf` はコミットしない。既存の `.env` は上書きしない
- 秘密情報（API キー、`MCP_TOKEN`）をコミットしない・ログに出さない
- `main` へマージすると GitHub Actions がテスト → タグ/Release → VPS デプロイする。手元で `main` に直接 push しない
- コミット先頭が `feat:` なら minor、`BREAKING CHANGE` / `type!:` なら major、それ以外は patch
