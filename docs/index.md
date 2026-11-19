# 株式分析ツール wiki

`docs/` の設計書と、リポジトリ内の `AGENTS.md`。全体像は [OVERVIEW.md](OVERVIEW.md)。

## 構成

- [モジュールの依存](overview/modules.md)（自動生成）
- [システム構成](overview/system.md)（自動生成）

## 画面設計

- [画面の一覧・画面遷移・画面が呼ぶ API](SCREENS.md)（自動生成）
- [書き方と共通見た目](screen/SPEC.md)
- [決算速報](screen/earnings.md)
- [銘柄](screen/stock.md)
- [ランキング](screen/ranking.md) / [PTS](screen/pts.md)
- [月次](screen/monthly.md)
- [サプライ](screen/supply.md)
- [大量保有報告書](screen/holders.md)
- [API の応答の形と約束](screen/api.md)

## データモデル

- [テーブル・列・索引・ER 図](SCHEMA.md)（自動生成）
- [状態遷移（行の状態を持つ列）](schema/states.md)（手書き。図の状態名は Enum と突き合わせるテストがある）
- [データの流れ（バッチ・API × テーブル）](DATA_FLOW.md)（自動生成。機能ごとの図と、全体の表の[ビューア](dataflow/viewer.html)）

## ジョブ設計

- [ジョブの一覧・1 日の時刻表・ジョブ間の依存](JOBS.md)（自動生成）
- 取込と保存の規則: [株価・決算](jobs/market.md) / [月次](jobs/monthly.md) / [月次の行名](jobs/monthly_labels.md) / [サプライ](jobs/supply.md) / [共通・運用](jobs/common.md)
- [XBRL](jobs/xbrl.md) / [XBRL 検証](XBRL_VERIFY.md)
- [EDINET](jobs/edinet.md)
- [SQLite バックアップ](DESIGN_backup.md)

## 約束

- [AGENTS.md](../AGENTS.md)（ルート）
- [app/api/AGENTS.md](../app/api/AGENTS.md)
- [static/AGENTS.md](../static/AGENTS.md)

## この wiki

手順は [wiki-setup.md](wiki-setup.md)。
