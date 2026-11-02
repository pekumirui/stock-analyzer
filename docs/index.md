# 株式分析ツール wiki

`docs/` の設計メモと、リポジトリ内の `AGENTS.md`。元のファイルは動かしていない。

## 画面

- [書き方と共通見た目](SCREEN_SPEC.md)
- [決算速報](DESIGN_earnings.md)
- [銘柄](DESIGN_stock.md)
- [ランキング](DESIGN_ranking.md)
- [PTS](DESIGN_pts.md)
- [月次](DESIGN_monthly.md) / [月次の行名](DESIGN_monthly_labels.md)
- [サプライ](DESIGN_supply.md)
- [大量保有報告書](DESIGN_holders.md)

## 取込

- [データの流れ（バッチ・API × テーブル）](DATA_FLOW.md)（自動生成）
- [XBRL](DESIGN_xbrl.md) / [XBRL 検証](XBRL_VERIFY.md)
- [EDINET](DESIGN_edinet.md)
- [SQLite バックアップ](DESIGN_backup.md)

## 約束

- [AGENTS.md](../AGENTS.md)（ルート）
- [app/api/AGENTS.md](../app/api/AGENTS.md)
- [static/AGENTS.md](../static/AGENTS.md)

スキーマと取込の全体はリポジトリ直下の `DESIGN.md`（この wiki には載せない）。

## この wiki

手順は [wiki-setup.md](wiki-setup.md)。
