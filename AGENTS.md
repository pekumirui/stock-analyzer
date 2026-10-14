# stock-analyzer

| 概要 | 正本 |
|---|---|
| 設計・スキーマ・取込・API | `DESIGN.md` |
| 画面（タブ別） | `docs/DESIGN_<tab>.md`。書き方と共通見た目は `docs/SCREEN_SPEC.md` |
| 月次の行名 | `docs/DESIGN_monthly_labels.md` |
| XBRL の設計と検証 | `docs/DESIGN_xbrl.md` / `docs/XBRL_VERIFY.md` |
| SQLite バックアップ | `docs/DESIGN_backup.md` |
| VPS・MCP・R2 の運用 | `deploy/README.md` |
| 利用者向け手順・コマンド | `README.md` |

## スタック

- Python 3.10+ / FastAPI / SQLAlchemy 2 / pandas
- フロントは `static/index.html` + `static/css/` + `static/js/` の vanilla JS
- DB はローカル SQLite (`stock.db`)。本番切替は `DATABASE_URL` のみ
- 仮想環境は `.venv`。Windows は `.\setup.ps1` / `.\run.ps1`、Linux は `./setup.sh` / `./run.sh`
- Python は `.venv\Scripts\python.exe`（Windows）/ `.venv/bin/python`（Linux）。システムの python で依存を入れない

## 検証環境構築方法
-`bash .cursor/install.sh`を実行する。
- SPA は `bash .cursor/start-web.sh` が 8000 で配信する。
- 秘密情報は各ベンダーの Secrets / 環境変数で渡す: `JQUANTS_API_KEY` / `ESTAT_APP_ID` / `VPS_SSH_KEY` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY`。
- VPS のログ・DB 件数・月次 API は MCP（`https://kabukpi.com/mcp`、OAuth）。どのベンダーからでも同じ。任意シェルを MCP に足さない
- VPS シェルは `VPS_SSH_KEY` があるときだけ `ssh stock-vps`（`install.sh` → `.cursor/setup-vps-ssh.sh`）。無ければ SSH は人がローカル PC から行う。R2 も `R2_*` が無ければ読めない

## モジュール分担

| 場所 | 役割 |
|---|---|
| `app/config.py` | 環境変数・閾値・プラン上限・ベース URL。ハードコードせずここに集約 |
| `app/models.py` | SQLAlchemy スキーマ |
| `app/db.py` | セッションと migrate |
| `app/jquants.py` | J-Quants API V2 クライアント（認証・ページング・レート制限） |
| `app/ingest.py` | API → DB 取込（UPSERT・冪等） |
| `app/analytics.py` | MA / RS / ステージ / Q単独 / 決算後リターン |
| `app/fx.py` | Frankfurter ECB 対EUR レートとクロス換算 |
| `app/api.py` | REST (`/api/*`) |
| `app/monthly_ingest.py` | PDF 月次列 → 暦年。行名正規化と aliases |
| `app/monthly_tree.py` | 月次の親子関係。`monthly_row_order.json` の `tree`、無ければゲートと内訳から作る自動の木を行に割り当てる。year GET の `tree` |
| `app/supply_chain.py` | サプライタブの素材マスタ（`config/supply_chain.json`）と企業マスタ（`config/supply_companies.json`）の読込と検査。月次量の API 形と見掛け消費 |
| `app/material_stats.py` | 素材の月次量（`material_monthly`）の共通部分。対応表 `config/material_stats.json`、品名 → 素材の合計、revision（速報 < 確報 < 年報）を下げない UPSERT |
| `app/estat.py` | e-Stat API クライアント。生産動態統計の年報と貿易統計 |
| `app/meti.py` | 経産省の統計 Excel。生産動態統計（速報・確報・年間補正号）と資源・エネルギー統計の石油（速報・確報・年報） |
| `app/main.py` | FastAPI エントリ + SPA 配信 |
| `app/assets.py` | 配信する HTML の `/static` URL に中身のハッシュ（`?v=`）を付ける |
| `app/xbrl/` | TDnet 短信 iXBRL の parse / YAML マッピング / normalize。UI には出さない |
| `app/xbrlp/` | kawasin73 gist の vendor。改変しない |
| `jobs/run_batch.py` | 日次バッチ / バックフィル |
| `jobs/backup_db.py` | 本番 SQLite の日次 gzip バックアップ（ローカルのみ。手順は `docs/DESIGN_backup.md`） |
| `jobs/material_stats.py` | 統計 → `material_monthly`。`--source meti --only seidou|sekiyu`（毎日、時刻をずらす）/ `--source estat`（毎週。前年と今年、`--years`） |
| `jobs/fx_rates.py` | Frankfurter 為替の日次差分 / `--backfill`（1999-01-04〜） |
| `jobs/check_demo.py` | スキーマ検査 + 旧デモ取り残しガード。`batch` / `backfill` の前に走る |
| `jobs/*_r2.py` | TDnet / IR → R2 mount。下の「R2 バッチ」 |
| `jobs/_r2common.py` | R2 バッチの共通処理（日付・HTTP・銘柄マスタ・ファイル名）。`app` は import しない |
| `jobs/html_issuers/` | `parse: true` の HTML 月次。各 `i<code>.py` が表・行・単位を宣言する。`parse: false` はモジュールを作らない |
| `config/` | `ir_monthly.json`、`issuer_aliases.json`、`issuer_metrics.json`、`monthly_vocab.json`（行名の共通語彙）、`monthly_row_order.json`、`supply_chain.json`（サプライタブの素材と流れ）、`supply_companies.json`（サプライタブの企業・生産能力）、`material_stats.json`（統計の品目 → 素材）、XBRL YAML、Ruff/Bandit は `pyproject.toml` |
| `tests/` | `test_*.py` と `ci_smoke.py`。新しい `test_*.py` は `ci_smoke.SUITES` に登録する（漏れは ci_smoke が落とす） |
| `analyzer/` | `stock.db` を読むだけのオフライン分析（1 問 1 フォルダ、結果は各 `README.md`）。約束と一覧は `analyzer/README.md`。`app` からは使わない |
| `deploy/log_mcp.py` | VPS 上のログ MCP（本番のみ） |
| `tools/` | オフライン検証と手元ヘルパー。月次 PDF 指紋は `fmtdiff.py`（pdfplumber。ingest は従来どおり pdftotext）。TDnet 月次の未投入一覧は `tdnet_monthly_pending.py`（読み取り専用。使い方は `deploy/README.md`）。XBRL 一致率は `xbrl_match_rate.py`。e-Stat の表探しは `estat_fetch.py`。`check_api.py` と `read_xls.py` |

## データルール

- 分析・チャート・RS・ステージは **調整済価格** (`adj_*`)。生値は表示・保存用
- 銘柄コードは 5 桁文字列（例 `"72030"`, `"262A0"`）。パターンは `^[0-9][0-9A-Z]{3}[0-9]$`。実コードは 1-9 始まり。表示は 4 桁 (7203 / 262A)
- 合成デモデータは投入しない。`seed_demo.py` は置かない
- 旧デモの `0` 始まりコードが残っていると RS ランクが狂う。`check_demo.py` のガードを外さない。残っていたら `reset`
- 取込は UPSERT で冪等。何度走らせても壊れないこと
- Q単独は保存しない。`analytics.quarter_summary` で累計差から都度算出
- `Statement.raw_json` は元レスポンス全項目を残す
- `indicators` のステージは 1/2/3/4。判定不能は `NULL`

### 月次

取込・暦年割当・API・業種は `DESIGN.md` §3 / §5 と `docs/DESIGN_monthly.md`。ここは守る点だけ。

- `stocks` に無い code は書かない。ingest / POST / PUT は先に `db.get(Stock, code)`。無ければ 4xx `NOT_IN_MASTER`
- FK は付けない。`PRAGMA foreign_keys` も有効化しない
- `MonthlyElement.unit` は保存 `number` / `yoy_index`。入力タグは `yoy_pct` も可（見出し。値の大きさから推測しない）。ingest は `yoy_pct` を `yoy_index` と `value+100` にして書く。PUT values は保存形の訂正で `+100` しない
- 行名は `canonicalize_element_label`。銘柄固有は `config/issuer_aliases.json`（改名）と `config/issuer_metrics.json`（件数指標。層だけで保存名は変えない）。新しい例外は `docs/DESIGN_monthly_labels.md` に箇条書きで足す
- 保存名は起動時・取り込み時に作り直さない（凍結）。正規化や alias を変えたら Actions の **Monthly names** で plan → apply
- `tests/test_monthly_ingest.py` に銘柄分岐は足さない
- 月次業種は `monthly_issuers.industry`（既定 `その他`）。`stocks.sector33` とは別。ingest は `last_ingested_at` だけ更新し industry を触らない
- HTML 月次の穴埋め・archive 投入は `.cursor/skills/monthly-html-ops-ingest/SKILL.md`。ウェーブ完了後のペアレビューは `.cursor/skills/monthly-html-pair-review/SKILL.md`（親が Task で起動。自己証明しない）
- 決算予想エージェント実験は `.cursor/skills/agent-forecast/SKILL.md`（`/agent-forecast`）。手順の正本は `analyzer/agent_forecast/README.md`

## 検証の目安

- 静的解析: `.\run.ps1 lint` / `./run.sh lint`（Ruff + Bandit。設定は `config/pyproject.toml`）
- API: `/api/stocks/search?q=トヨタ` や `/api/screener`
- backfill 後に UI の決算・チャート・スクリーナーが空でないこと
- UI を実際に動かして確かめるなら `.cursor/skills/verify-stock-analyzer/SKILL.md`
- 画面録画は作らない


## 本番（VPS / MCP / R2）

ツール一覧・OAuth・Secrets・ログの場所は `deploy/README.md`。ここは守る点だけ。

- MCP に任意シェル・restart・任意 SQL は無い。足さない
- 月次ツールは `http://127.0.0.1:8000/api/v1/tables` のみ（nginx Basic は使わない）
- 月次 GET は公開（`/monthly`）。書き込みは loopback。nginx 経由の POST/PUT/DELETE は 403。個人 SPA は `/app` + Basic
- 月次の新規登録は `monthly_ingest`。year は渡さない。`monthly_put_values` の year は暦年で訂正専用
- `monthly_delete_element` は値の無い行だけ（残っていれば 409）。表は残す
- 行名の付け替え・まとめての訂正は `monthly_get_doc` → `monthly_put_doc`。先に `dry_run` で `changes` を見る
- アプリ本体 (`app/*`) にログ API を足さない。MCP が死んだときの退避路は Actions の **Debug VPS**
- R2 bucket `share` は VPS で `/home/stock/r2-share` に rclone mount。`rclone.conf` はリポジトリに置かない。マウント切断時のジョブ打ち切りと watch timer は `DESIGN.md` §3 / `deploy/README.md`
- クラウドのエージェントから R2 を読むときは `deploy/README.md` の「クラウドのエージェントから読む」。読み取りのみ。取得先は `/tmp`

### R2 バッチ

保存先は `/home/stock/r2-share/` 配下。cron は JST。

| スクリプト | 保存先 | cron | 振る舞いの正本 |
|---|---|---|---|
| `jobs/tdnet_monthly_r2.py N` | `monthly/{code}/YYYYMMDD_CODE_….pdf` | 18:00 / 21:00（N=1）。初回は Actions の **TDnet monthly backfill** | `DESIGN.md` §3 |
| `jobs/tdnet_monthly_pdf_sidecar.py` | `monthly/{code}/….pdf-ingest.json`（前回投入分と同じ書式の PDF だけ） | 18:20 / 21:20 | `DESIGN.md` §3。自動 POST しない |
| `jobs/tdnet_monthly_pdf_ingest.py` | （R2 は読まない。loopback POST） | 18:30 / 21:30 | `DESIGN.md` §3。`--ledger` は `/var/log/app/pdf_ingest_ledger.json` |
| `jobs/ir_monthly_r2.py` | `monthly/{code}/….pdf` / `.xlsx` / `.xls` | 10:00 / 16:00 | `DESIGN.md` §3、対象は `config/ir_monthly.json` の issuers |
| `jobs/ir_monthly_html_r2.py` | `monthly/{code}/…_月次.html` + `.ingest.json` | 10:20 / 16:20 | `DESIGN.md` §3、対象は `config/ir_monthly.json` の html。自動 POST しない |
| `jobs/ir_monthly_html_ingest.py` | （R2 は読まない。loopback POST） | 10:30 / 16:30 | `DESIGN.md` §3。`--ledger` は `/var/log/app/html_ingest_ledger.json` |
| `jobs/tdnet_xbrl_r2.py N` | `xbrl/{code4}/` | 12–23 時 毎時 00 分（N=1） | `docs/DESIGN_xbrl.md` |
| `jobs/xbrl_parse.py` → `jobs/xbrl_normalize.py` | `xbrl_*` テーブル | 毎時 10 分 / 20 分 | `docs/DESIGN_xbrl.md` |
| `jobs/material_stats.py --source meti` | `stats/meti/{seidou,sekiyu}/…xlsx`（経産省 Excel の控え） | 07:15 / 07:45（取込のついで） | `DESIGN.md` §3 |
| `jobs/vps_packages_r2.py` | `inventory/vps-packages.txt` と `inventory/history/vps-packages-YYYYMMDD.txt` | 06:40 | `deploy/README.md` |

- 月次の `{code}` は TDnet の発行体コードそのまま（9279, 262A）。J-Quants の 5 桁にしない。stocks 照合だけ末尾 0 を足す
- `stocks` に無いコードは取らない。XBRL だけは `_unmatched/` へ置く
- 既存ファイルは消さない。IR は同じ URL の上書きを日付付きで残し、中身の sha256 が同じなら skip
- HTML 月次は `.ingest.json` を書くだけ。自動 POST しない。パーサー修正後は `--reparse`。DB 投入は別 cron の `jobs/ir_monthly_html_ingest.py`（loopback、latest-per-code、sha256 ledger）
- 各バッチの cron は分ける。1 本にまとめない
- cron の出力は `deploy/cron-run.sh` が `/var/log/app/<stem>.log` に書く。行頭は JST。crontab にリダイレクトは書かない。古い `batch.log` は rotate まで残す。Actions の backfill はワークフローのログ（`cron-run.sh` を通さない）
- XBRL は決算 UI / `statements` を触らない。マッピングは `config/xbrl/` だけ。エージェントが独断で足さない
- XBRL 検証は別 DB。本番 `stock.db` に書かない（`docs/XBRL_VERIFY.md`）
