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
- クラウドのエージェント（ベンダー問わず）は `bash .cursor/install.sh`（冪等）で `.venv` / `pdftotext` / `rclone` と `stock.db` を用意する。`stock.db` は既定でゴールデンデータ（`tests/golden/db/` の実データの切り出し）から作る（数秒）。全銘柄の実データが要るときだけ `STOCK_DB=full`（backfill 2。約40分。`JQUANTS_API_KEY` が要る）SPA は `bash .cursor/start-web.sh` が 8000 で配信する。8000 を止めない・`reset` しない
- 秘密情報は各ベンダーの Secrets / 環境変数で渡す: `JQUANTS_API_KEY` / `ESTAT_APP_ID` / `VPS_SSH_KEY` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`。無い変数の機能は使えないものとして進める。チャットで鍵を求めない
- Claude Code の自動メモリは `.claude/memory/`。環境は消えるので、メモリを足したら作業ブランチに一緒にコミットする

## モジュール分担

| 場所 | 役割 |
|---|---|
| `app/` | アプリ本体。直下は機能をまたぐ共通部分、機能ごとに `market/` / `monthly/` / `supply/` / `xbrl/` と `api/`。`app/xbrlp/` は vendor なので改変しない |
| `static/` | SPA（`index.html` + `css/` + `js/<tab>.js`） |
| `jobs/` | バッチ・cron。機能ごとのフォルダ、直下は共通 |
| `config/` | 機能ごとの JSON / YAML 設定と `pyproject.toml`（Ruff / Bandit） |
| `tests/` | 機能ごとのフォルダ。`python -m pytest tests` が `test_*.py` を全部集める（登録は要らない）。DB はファイルごとに空から（`tests/conftest.py`） |
| `tools/` | オフライン検証と手元ヘルパー |
| `analyzer/` | `stock.db` を読むだけのオフライン分析。約束は `analyzer/README.md` |
| `mcp/` | VPS 上のログ MCP（別プロセス）。`__init__.py` は置かない（公式 SDK の `mcp` と衝突） |
| `deploy/` | VPS のプロビジョン・cron・nginx・運用手順 |
| `docs/` | 画面仕様と個別設計の正本 |
| `data/` | XBRL 検証のゴールデンデータ |
| `.cursor/` | 環境構築スクリプトと手順書（`skills/*/SKILL.md`）。Cursor 専用ではない |
| `.github/` | CI/CD とメンテ用 Actions |

## 作業の進め方

1. 作業前に Linear（チーム `Kabukpi` / キー `KAB`）で issue を起票する。依頼に既存の issue ID があればそれを使う
2. 採番された ID でブランチを切る

| プレフィックス | 用途 |
|---|---|
| `feature/<ID>` | 新規機能開発 |
| `improve/<ID>` | 既存機能拡張 |
| `bug/<ID>` | バグ修正 |
| `chore/<ID>` | ドキュメント修正などの雑務 |

例: `feature/KAB-1`

- issue にはエージェント自身のベンダーのラベル（`agent:claude` / `agent:cursor` / `agent:codex`）を付ける。無いベンダーなら `agent:<ベンダー名>` を作って付ける
- Linear につながらない環境では起票せず、止まって人に確認する

## 禁止事項

1. `main` 上で実装しない
2. `.env` / `stock.db` / `.venv` / `rclone.conf` / `*.pdf` をコミットしない
