# stock-analyzer

個人用の日本株分析ツール（J-Quants V2 の株価・決算、月次、サプライ、XBRL）。ここには実装時の約束だけ書く。詳細は正本へ。コードを読めば分かることは書かない。

| 何 | 正本 |
|---|---|
| 設計・スキーマ・取込・API | `DESIGN.md` |
| どのバッチ・API がどのテーブルを読み書きするか | `docs/DATA_FLOW.md` と機能ごとの `docs/dataflow/<機能>.md`（テストから自動生成。手で直さない。テーブルを読み書きする変更をしたら `python -m pytest tests --dataflow=write`）。`docs/dataflow/viewer.html` は人が見る用なので読まない |
| J-Quants API の仕様（Light プラン） | J-Quants の MCP。md に写さない |
| 画面（タブ別） | `docs/DESIGN_<tab>.md`。書き方と共通見た目は `docs/SCREEN_SPEC.md` |
| 月次の行名 | `docs/DESIGN_monthly_labels.md` |
| XBRL の設計と検証 | `docs/DESIGN_xbrl.md` / `docs/XBRL_VERIFY.md` |
| EDINET の取込 | `docs/DESIGN_edinet.md` |
| SQLite バックアップ | `docs/DESIGN_backup.md` |
| VPS・MCP・共有データ（`data/`）の運用 | `deploy/README.md` |
| 利用者向け手順・コマンド | `README.md` |
| Markdown wiki（MkDocs / Cloudflare Pages） | `docs/wiki-setup.md` |

## 環境構築

- Python 3.10+ / FastAPI / SQLAlchemy 2 / pandas。フロントは `static/` の vanilla JS。DB はローカル SQLite (`stock.db`)
- 仮想環境は `.venv`。Windows は `.\setup.ps1` / `.\run.ps1`、Linux は `./setup.sh` / `./run.sh`。システムの python で依存を入れない
- クラウドのエージェント（ベンダー問わず）は `bash .cursor/install.sh`（冪等）で `.venv` / `pdftotext` / `rclone` と `stock.db` を用意する。`stock.db` は既定でゴールデンデータ（`tests/golden/db/` の実データの切り出し）から作る（数秒）。全銘柄の実データが要るときだけ `STOCK_DB=full`（backfill 2。約40分。`JQUANTS_API_KEY` が要る）SPA は `bash .cursor/start-web.sh` が 8000 で配信する。8000 を止めない・`reset` しない
- 秘密情報は各ベンダーの Secrets / 環境変数で渡す: `JQUANTS_API_KEY` / `ESTAT_APP_ID` / `EDINET_API_KEY` / `ANTHROPIC_API_KEY` / `VPS_SSH_KEY` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`（`R2_*` は残置マウント用。共有ファイルは `data/`）。無い変数の機能は使えないものとして進める。チャットで鍵を求めない
- Claude Code の自動メモリは `.claude/memory/`。環境は消えるので、メモリを足したら作業ブランチに一緒にコミットする

## モジュール分担

| 場所 | 役割 |
|---|---|
| `app/` | アプリ本体。直下は機能をまたぐ共通部分、機能ごとに `market/` / `monthly/` / `supply/` / `xbrl/` と `api/`。`app/xbrlp/` と `app/edinet_wrapper/` は vendor なので改変しない |
| `static/` | SPA（`index.html` + `css/` + `js/<tab>.js`）。`static/vendor/` は自前で配る外部ライブラリなので改変しない |
| `jobs/` | バッチ・cron。機能ごとのフォルダ、直下は共通 |
| `config/` | 機能ごとの JSON / YAML 設定と `pyproject.toml`（Ruff / Bandit） |
| `tests/` | 機能ごとのフォルダ。`python -m pytest tests` が `test_*.py` を全部集める（登録は要らない）。DB はファイルごとに空から（`tests/conftest.py`） |
| `tests/nonfunctional/` | 画面の約束・レイアウトなど機能横断の静的チェック |
| `tools/` | オフライン検証と手元ヘルパー |
| `analyzer/` | `stock.db` を読むだけのオフライン分析。約束は `analyzer/README.md` |
| `mcp/` | VPS 上のログ MCP（別プロセス）。`__init__.py` は置かない（公式 SDK の `mcp` と衝突） |
| `deploy/` | VPS のプロビジョン・cron・nginx・運用手順 |
| `docs/` | 画面仕様と個別設計の正本 |
| `data/` | XBRL 検証のゴールデンデータ |
| `.cursor/` | 環境構築スクリプトと手順書（`skills/*/SKILL.md`）。Cursor 専用ではない |
| `.github/` | CI/CD とメンテ用 Actions |

## 作業の進め方

1. 作業前に Linear（チーム `Kabukpi` / キー `KAB`、プロジェクト `stock-analyzer`）で issue を起票する。依頼に既存の issue ID があればそれを使う
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
- ドキュメントと既存のコードが食い違うときはコードの書き方に従い、見つけた食い違いを最後の返答ですべて挙げる
- コード・スクリプトにコメントを足さない。コードから読めることと経緯（issue 番号・障害の話）は書かない。経緯は commit / PR / issue、設計の理由は正本の md に。コードから読めず、知らないと壊す制約（例: `--preload` を使わない理由）だけは 1 行で残す
- 画面の録画(動画)は作らない。見た目の確認はスクリーンショットで行う

## 認知客観性と反迎合原則 (Anti-Sycophancy)

1. 誘導的な反問を無視せよ：私の入力が「でしょ？」「じゃない？」「right?」などの確認的な語句で終わる場合、それを中立的でオープンな仮定として強制的に扱い、私の前提傾向に順応することを厳禁とする。
2. 前提の独立検証：既存コードの検証、事実、またはアーキテクチャの論理を検証する前に、「はい」「完全に正しい」「問題ない」などの言葉で始めないこと。まず独立して推論を行い、その後で結論を述べよ。
3. 直接的反駁メカニズム：私の前提に欠陥、非最適解、または事実誤認がある場合、最初の文で直接指摘し反駁せよ。技術的正確性、境界条件、客観的なトレードオフを、礼儀や順応の上に置くこと。

## 禁止事項

1. `main` 上で実装しない
2. `.env` / `stock.db` / `.venv` / `rclone.conf` / `*.pdf` をコミットしない
