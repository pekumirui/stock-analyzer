# ジョブ設計: サプライ

読み方と作り方は [JOBS.md](../../JOBS.md)。表との線は [データの流れ](../../dataflow/supply.md)。

## supply/material_stats

`jobs/supply/material_stats.py`

- 起動: 月曜 07:10（`--source estat`）
- 起動: 毎日 07:15（`--source meti --only seidou --ledger /var/log/app/material_stats_ledger.json`）
- 起動: 毎日 07:45（`--source meti --only sekiyu --ledger /var/log/app/material_stats_ledger.json`）
- 書く: `material_monthly`, `material_trade_country`
- 読むだけ: （観測なし）

| 引数 | 種類 | 既定 | 説明 |
|---|---|---|---|
| `--years` |  |  | e-Stat の年。2025 または 2021-2026。省略時は前年と今年 |
| `--source` | meti / estat |  |  |
| `--only` | seidou / sekiyu / trade |  |  |
| `--refetch` | フラグ |  | 取り込み済みの年報・年間補正号・過去年の 12 月の確報も経産省から落とし直す |
| `--reread` | フラグ |  | 取り込み済みの年報なども読み直す。控えがあれば経産省には行かない |
| `--ledger` |  |  | 経産省 Excel の取得の台帳（JSON）。続けて取れないとき page する。cron は /var/log/app/material_stats_ledger.json |
| `--december` |  |  | 生産動態の過去年の 12 月の確報も読む（一覧ページに無い年）。2024 または 2023-2024。手で一度だけ |
