# ジョブ設計: 共通・運用

読み方と作り方は [JOBS.md](../../JOBS.md)。表との線は [データの流れ](../../dataflow/common.md)。

## fx_rates

`jobs/fx_rates.py`

- 起動: 毎日 08:00
- 書く: `fx_rates`
- 読むだけ: （観測なし）

| 引数 | 種類 | 既定 | 説明 |
|---|---|---|---|
| `--backfill` | フラグ |  | 1999-01-04 から再取得（UPSERT・冪等） |
| `--from` |  |  | YYYY-MM-DD |
| `--to` |  |  | YYYY-MM-DD |

## golden_db

`jobs/golden_db.py`

- 起動: cron に無い
- 書く: `daily_prices`, `earnings_date_fetches`, `earnings_date_pubs`, `earnings_events`, `fx_rates`, `indicators`, `material_monthly`, `material_trade_country`, `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `statements`, `stocks`, `topix`
- 読むだけ: （観測なし）

引数: なし

## migrate

`jobs/migrate.py`

- 起動: cron に無い
- 書く: `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `xbrl_elements`, `xbrl_financials`
- 読むだけ: `stocks`

引数: なし

## ops/backup_data

`jobs/ops/backup_data.py`

- 起動: 毎日 03:30
- 書く: （観測なし）
- 読むだけ: （観測なし）

| 引数 | 種類 | 既定 | 説明 |
|---|---|---|---|
| `--only` | KINDS、append |  | copy only this kind |

## ops/backup_db

`jobs/ops/backup_db.py`

- 起動: 毎日 05:00
- 書く: （観測なし）
- 読むだけ: （観測なし）

| 引数 | 種類 | 既定 | 説明 |
|---|---|---|---|
| `--db` |  | `str(default_db_path())` | source SQLite path |
| `--dest` |  | `str(DEFAULT_DEST)` | backup directory |
| `--no-offsite` | フラグ |  | skip OneDrive copy (keep the new gzip and any older local files) |

## ops/migrate_r2_share

`jobs/ops/migrate_r2_share.py`

- 起動: cron に無い
- 書く: （観測なし）
- 読むだけ: （観測なし）

| 引数 | 種類 | 既定 | 説明 |
|---|---|---|---|
| `--src` |  | `None` | source tree (default: FUSE r2_share_root()) |
| `--dest` |  | `None` | destination tree (default: share_root()) |
| `--include-cache` | フラグ |  | also copy xbrl/_cache |
| `--force` | フラグ |  | replace dest files whose sha256 differs |
| `--list-mismatches` | フラグ |  | print mismatches and exit without copying |

## ops/rebuild_daily_prices

`jobs/ops/rebuild_daily_prices.py`

- 起動: cron に無い
- 書く: `daily_prices`
- 読むだけ: （観測なし）

| 引数 | 種類 | 既定 | 説明 |
|---|---|---|---|
| `--yes` | フラグ |  | 作り直す（無ければ見積もりだけ） |

## ops/vps_packages_r2

`jobs/ops/vps_packages_r2.py`

- 起動: 毎日 06:40
- 書く: （観測なし）
- 読むだけ: （観測なし）

| 引数 | 種類 | 既定 | 説明 |
|---|---|---|---|
| `--dest` |  | `DEFAULT_DEST` | destination directory |
