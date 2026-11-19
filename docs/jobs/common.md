# 取込と保存の規則: 共通・運用

ジョブの起動時刻・引数・読み書きする表は [ジョブ設計](gen/common.md)（自動生成）。バックアップは [SQLite バックアップ](../DESIGN_backup.md)。ここには規則だけ書く。

**為替（`fx_rates`）。** Frankfurter v1（`https://api.frankfurter.dev/v1`、ECB、基準通貨 EUR、キー不要。旧 `api.frankfurter.app` は 301）。UI は出さない。

- 保存は対 EUR だけ。USD/JPY などは `EURJPY / EURUSD`。ヘルパーは `app.fx.cross_rate`
- 時系列エンドポイント `/{from}..{to}` を約 1 年チャンクで取る。UPSERT で冪等
- 初回は `python jobs/fx_rates.py --backfill`（1999-01-04〜）。以後は最終保存日以降の差分（その日を含めて取り直す）
- 日次 cron は 08:00 JST（ECB 公表 16:00 CET の翌朝）。`deploy/crontab` → provision が反映
- 休日・非公表日は指定日以前の直近営業日（`as_of`）。期間 MCP は公表日だけ返す
- MCP: `fx_rate`（指定日）/ `fx_rates`（期間）

**VPS パッケージ一覧（`jobs/ops/vps_packages_r2.py`）。** 読み取り専用。`data/inventory/` へ書く。中身と cron は [`deploy/README.md`](../../deploy/README.md)。
