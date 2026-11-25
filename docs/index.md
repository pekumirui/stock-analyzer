# 株式分析ツール wiki

日本株分析ツールの設計書・自動生成リファレンス・実装規約を横断検索できるようにまとめたドキュメントサイトである。まず全体像を把握する場合は [システム全体像と分析ロジック](OVERVIEW.md) を参照する。

## 全体構成・アーキテクチャ

| ページ | 種別 | 内容 |
|---|---|---|
| [システム全体像と分析ロジック](OVERVIEW.md) | 手書き | システム構成、データソース、RS・ステージ・決算パフォーマンスの算出式、ホスティング方針 |
| [モジュールの依存](overview/modules.md) | 自動生成 | `app/`・`jobs/`・`mcp/`・`tools/`・`analyzer/` 間の import 依存グラフ |
| [システム構成](overview/system.md) | 自動生成 | `deploy/` の設定から抽出したプロセス・ポート・通信経路図 |

## 画面と API 仕様

| ページ | 種別 | 内容 |
|---|---|---|
| [画面の一覧・画面遷移・画面が呼ぶ API](SCREENS.md) | 自動生成 | 全画面のルート、クリック遷移、呼び出す API と読み書きテーブルの対応表 |
| [画面仕様の共通規則](screen/SPEC.md) | 手書き | 全タブ共通の配色・表組・ヘッダー・パンくず・履歴制御と画面仕様書の構成規則 |
| [API の応答と規約](screen/api.md) | 手書き | REST API の公開範囲・キャッシュ・応答構造と月次 API のエラーコード |
| [決算速報](screen/earnings.md) | 手書き | 日別の実績開示・発表予定一覧、累計・3 か月決算表、予想修正履歴、カレンダー |
| [銘柄](screen/stock.md) | 手書き | 銘柄検索、概要、株価チャート、財務 3 表・短信原文、決算説明資料・ニュース、株主 |
| [ランキング](screen/ranking.md) ・ [PTS](screen/pts.md) | 手書き | 決算発表後パフォーマンスランキングと夜間 PTS ランキング・翌日始値追随率 |
| [月次](screen/monthly.md) | 手書き | 月次データの一覧・業種絞り込みと発行体別の複数年比較表・グラフ・親子木表示 |
| [サプライ](screen/supply.md) | 手書き | 素材サプライチェーンの工程グラフ、関連上場企業、月次生産・輸出入統計 |
| [大量保有報告書](screen/holders.md) | 手書き | 大量保有報告書の提出一覧、提出者検索、保有銘柄ポートフォリオ推移 |

## データモデルとデータフロー

| ページ | 種別 | 内容 |
|---|---|---|
| [テーブル・列・索引・ER 図](SCHEMA.md) | 自動生成 | SQLite の全テーブル定義と、拡大・検索できる対話型 ER 図（Liam ERD）へのリンク |
| [状態遷移（行の状態を持つ列）](schema/states.md) | 手書き | 状態列の遷移図と更新箇所の対応表（`app/models.py` の Enum と自動テストで照合） |
| [データの流れ（バッチ・API × テーブル）](DATA_FLOW.md) | 自動生成 | テスト実行時の SQL から生成した読み書き対応表と[対話型ビューア](dataflow/viewer.html) |

## バッチ・データ取込・運用

| ページ | 種別 | 内容 |
|---|---|---|
| [ジョブの一覧・1 日の時刻表・ジョブ間の依存](JOBS.md) | 自動生成 | `deploy/crontab` と引数定義・データフローから生成した起動時刻表と依存関係 |
| [MCP のツール](jobs/gen/mcp.md) | 自動生成 | `mcp/*.py` の `TOOLS` から作ったツール名・引数・説明 |
| [株価・決算の取込規則](jobs/market.md) | 手書き | J-Quants 日次バッチ、発表予定、PTS、1 分足、決算説明資料の収集・AI 抽出規則 |
| [月次の取込規則](jobs/monthly.md) ・ [月次の行名](jobs/monthly_labels.md) | 手書き | TDnet・IR ページからの月次取得、暦年割当、保存名の正規化・凍結と発行体別ツリー規則 |
| [サプライの取込規則](jobs/supply.md) | 手書き | 経産省の生産動態・石油統計 Excel と e-Stat 貿易統計の取得・換算規則 |
| [XBRL の設計](jobs/xbrl.md) ・ [XBRL 検証 DB とゴールデン一致率](XBRL_VERIFY.md) | 手書き | TDnet・EDINET の XBRL パース・正規化、財務 3 表統合、短信 PDF 変換、ゴールデン突合手順 |
| [EDINET の取込](jobs/edinet.md) | 手書き | 有報・半期報 XBRL、大量保有報告書、自己株買い、公開買付けの取得と ZIP 退避規則 |
| [共通・運用ジョブ](jobs/common.md) ・ [本番 SQLite バックアップ](DESIGN_backup.md) | 手書き | 共通ジョブ規約、APSW 圧縮スナップショットと OneDrive への世代保管・復元手順 |

## 実装規約と Wiki 運用

- [`AGENTS.md`](../AGENTS.md): リポジトリ全体のモジュール分担、環境構築、開発・禁止事項の規約
- [`app/api/AGENTS.md`](../app/api/AGENTS.md): FastAPI エンドポイントの応答時間目標（p95 400ms 以下）
- [`static/AGENTS.md`](../static/AGENTS.md): フロントエンド一覧表の共通クラス規約（`table.sheet`）
- [Markdown wiki のビルドと公開](wiki-setup.md): MkDocs ビルド、Cloudflare Pages へのデプロイ、Cloudflare Access 設定手順
