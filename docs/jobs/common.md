# 取込と保存の規則: 共通・運用

機能をまたぐ共通データ（為替レート）、デプロイ時のスキーマ追随、および VPS パッケージ一覧出力の規則を定める。各ジョブの起動時刻・引数・読み書きするテーブルは [ジョブ設計: 共通・運用](gen/common.md)（自動生成）、本番 DB と `data/` のバックアップ手順は [本番 SQLite バックアップ](../DESIGN_backup.md)、ゴールデン DB の構成は [全体像](../OVERVIEW.md)「ゴールデンデータの DB」を参照する。

## 為替レート（`fx_rates`）

対象ジョブは `jobs/fx_rates.py` であり、共通の取得・換算処理は `app/fx.py` に置く（起動時刻と引数一覧は [ジョブ設計: 共通・運用](gen/common.md) を参照する）。

Frankfurter v1（`https://api.frankfurter.dev/v1`、欧州中央銀行 ECB 公表値、基準通貨 `EUR`、API キー不要。旧 `api.frankfurter.app` は `301` リダイレクト）から日次為替レートを取得し、`fx_rates` テーブルへ UPSERT する。専用の画面は持たず、MCP ツール（`fx_rate`・`fx_rates`）と内部集計から参照する。

取得・保存・再試行の規則は以下のとおりである。

| 項目 | 規則 |
|---|---|
| 保存形式とクロス換算 | DB には `EUR` 以外の各通貨（`quote`）の対 `EUR` レートのみを保存し、`EUR` 自身の行は持たない。`USD/JPY` などの任意ペアは `app.fx.cross_rate` で `EURJPY / EURUSD` として算出する（同一通貨ペアは `1.0`） |
| エンドポイントと分割幅 | `FX_CHUNK_DAYS`（既定 `366` 日）単位に期間を区切り、単日は `/{date}`、複数日は時系列エンドポイント `/{from}..{to}` を呼んで冪等に UPSERT する |
| エラー再試行 | HTTP `429` は `5`〜`60` 秒待機、HTTP `500`・`502`・`503`・`504` は最大 `30` 秒の指数バックオフでそれぞれ最大 `6` 回再試行し、HTTP `404` は空応答として扱う |
| 日次スケジュールの考え方 | ECB の公表時刻（`16:00 CET` 前後）の翌朝 JST に `deploy/crontab` から日次実行する |

実行時の引数に応じた取得開始日・終了日の決まり方は以下のとおりである（終了日の上限は常に実行当日）。

| 実行条件 | 取得する期間 |
|---|---|
| 引数なし（`fx_rates` が空の初回） | `1999-01-04`（`FX_EARLIEST`）から当日まで取得する |
| 引数なし（既存行がある定常実行） | `fx_rates` の最終保存日から当日まで取得する（最終保存日当日を含めて取り直す） |
| `--backfill` 指定 | 既存行の有無にかかわらず `1999-01-04` から当日まで再取得する |
| `--from`・`--to` 指定 | 指定した開始日（下限 `1999-01-04`）から終了日（省略時は当日）まで取得する |

MCP の参照ツール（`fx_rate` と `fx_rates`）における休日・非公表日の扱いは以下のとおりである。

| 問い合わせ手段 | 返す値 |
|---|---|
| `fx_rate`（単日指定） | 指定日（`1999-01-04` 以降）以前で、必要な通貨がすべて揃う直近公表日（`as_of`）のレートからクロスレートを計算して返す。`requested_date` と `as_of` が異なる場合は `holiday_fallback: true` を付ける |
| `fx_rates`（期間指定） | 期間内（`from`〜`to`）で必要な通貨が揃う公表日のみを返す（休日は直近営業日で埋めない）。`interval`（`day`・`week`・`month`、週・月は期間内の最終公表日）と先頭の期間要約（始値・終値・変化率・高値・安値）を付ける |

## スキーマ追随とデプロイ時メンテナンス（`migrate.py`）

対象ジョブは `jobs/migrate.py` であり、DDL ヘルパーは `app/db.py`、月次データの保守処理は `app/monthly/maintenance.py` に置く（定義は [ジョブ設計: 共通・運用](gen/common.md) を参照する）。

デプロイ時に `deploy/update.sh` が uvicorn 再起動の前に 1 回だけ実行し、既存データを消さずにスキーマと索引を最新モデルへ追随させる（手動実行は `./run.sh migrate` または `.\run.ps1 migrate`）。複数ワーカーの uvicorn が起動時に同時に `CREATE INDEX` を発行して競合するのを防ぐ役割も担う。日次バッチや毎時の XBRL ジョブと重なって SQLite がロックされた場合（`database is locked`）は、`5` 秒から最大 `60` 秒までの指数バックオフで最大 `10` 回再試行する。

1 回の実行で行う処理の順序は以下のとおりであり、すべて冪等に動作する。

| 順序 | 処理 | 内容 |
|---|---|---|
| 1 | テーブル・トリガー・ビューの作成 | `init_db()` で未作成のテーブル、索引、`data_versions` の版管理トリガー、および財務 3 表用ビュー（`v_xbrl_statement_values`）を作成する |
| 2 | 索引の追加・整理と統計更新 | `ensure_indexes()` で既存テーブルに不足している索引を追加し、置き換え済みの旧索引（`idx_xbrl_financials_lookup`）を削除したうえで、変更があったテーブルに `ANALYZE` を実行する |
| 3 | 不足列の追加 | `migrate_columns()` でモデル定義に対して既存テーブルに不足している列を検出し、`ALTER TABLE ADD COLUMN` で追加する（既存行の追加列は `NULL` または `server_default` になるため、株価・財務の値が必要な場合は別途バックフィルする） |
| 4 | XBRL 既存行の整合補正 | `xbrl_financials` の一意制約を予想・レンジ列（`value_kind`・`range_side`・`target_period`）込みの構成へ付け替え、未設定の `source_layer` を要素の名前空間から埋め、`xbrl_elements.is_custom` を現在の標準名前空間判定で更新する |
| 5 | 月次データのデプロイ保守 | `run_deploy_maintenance()` で既知の `unit` 補正、保存名ドリフト行数のログ出力、`stocks` に存在しない孤立銘柄の月次行削除、および `monthly_element_sources` が未記録の既存行への `legacy` 補完を行う |
| 6 | 最終検証 | `ensure_data_versions()` を再確認したうえで `init_db(strict=True)` を実行し、不足列が残っていないことを検証する |

## VPS パッケージ一覧（`vps_packages_r2.py`）

対象ジョブは `jobs/ops/vps_packages_r2.py` である（起動時刻と引数は [ジョブ設計: 共通・運用](gen/common.md)、運用上の位置づけは [`deploy/README.md`](../../deploy/README.md) を参照する）。

セキュリティ照合用にホスト上のパッケージと OS 情報を採取して `data/inventory/`（`share_path("inventory")`、`--dest` で変更可）へ書き出す読み取り専用ジョブである。DB と外部ネットワークには一切アクセスせず、出力先のマウントが切れている場合は `fail_if_mount_down` で非ゼロ終了する。

出力ファイルと収録内容の規則は以下のとおりである。

| 項目 | 規則 |
|---|---|
| 出力ファイル | 最新版の `data/inventory/vps-packages.txt`（毎回上書き）と、日次履歴の `data/inventory/history/vps-packages-YYYYMMDD.txt`（同日の再実行は上書き）の 2 ファイルを `.part` 一時ファイル経由のアトミック置換で書く |
| 収録セクション | 先頭に JST の生成時刻（`generated-at`）と `hostname` を書き、続けて `.venv` の `pip freeze`、`dpkg-query -W`（全パッケージに加え、`nginx`・`openssh-server`・`openssl`・`certbot`・`python3`・`fluent-bit`・`fuse3`・`ufw` が未導入なら `<パッケージ名>\tnot installed` を必ず補う）、`uname -r`、`/etc/os-release`、`rclone version` の 5 区画を出力する |
| 未導入コマンドと秘匿処理 | コマンド未導入や実行失敗時はジョブ自体を失敗させず該当区画に `not installed` と書く。URL 内の認証情報（`://user:pass@`）は `://<redacted>@` に置換し、API キー・秘密鍵・Webhook 変数名を含む行は `<redacted>` に伏せる |
