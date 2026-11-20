# 取込と保存の規則: 共通・運用

ジョブの起動時刻・引数・読み書きする表は [ジョブ設計](gen/common.md)（自動生成）。バックアップは [SQLite バックアップ](../DESIGN_backup.md)。ここには規則だけ書く。

## 為替（`fx_rates`）

ジョブ: [fx_rates](gen/common.md#fx_rates)（`jobs/fx_rates.py`）

Frankfurter v1（`https://api.frankfurter.dev/v1`、ECB、基準通貨 EUR、キー不要。旧 `api.frankfurter.app` は 301）。UI は出さない。

- 保存は対 EUR だけ。USD/JPY などは `EURJPY / EURUSD`。ヘルパーは `app.fx.cross_rate`
- 時系列エンドポイント `/{from}..{to}` を約 1 年チャンクで取る。UPSERT で冪等
- 日次 cron は ECB 公表 16:00 CET の翌朝に走る。`deploy/crontab` → provision が反映
- MCP: `fx_rate`（指定日）/ `fx_rates`（期間）

| 実行 | 取る期間 |
|---|---|
| 初回 `python jobs/fx_rates.py --backfill` | 1999-01-04〜 |
| 以後 | 最終保存日以降の差分（その日を含めて取り直す） |

| 問い合わせ | 返す値 |
|---|---|
| 休日・非公表日を指定 | 指定日以前の直近営業日（`as_of`） |
| 期間 MCP | 公表日だけ |

## VPS パッケージ一覧（`vps_packages_r2.py`）

ジョブ: [ops/vps_packages_r2](gen/common.md#opsvps_packages_r2)（`jobs/ops/vps_packages_r2.py`）

読み取り専用。`data/inventory/` へ書く。中身と cron は [`deploy/README.md`](../../deploy/README.md)。
