# データの流れ（バッチ・API × テーブル）

**このファイルと `docs/dataflow/` は自動生成。手で直さない。** 作り直しは `python -m pytest tests --dataflow=write`。CI は `--dataflow=check` で実装と合うかを見る（作り方は `tools/dataflow/`）。

テスト中に SQLite へ実際に流れた SQL を、呼んだバッチ・API に紐づけて集めたもの。

- 「書く」は INSERT / UPDATE / DELETE をした表（読んでもいる表を含む）。「読むだけ」は SELECT だけの表
- テストが通った道だけが載る。テストの無い分岐の読み書きは載らないので、表は**下限**として読む
- バッチは `jobs/` のうち `__main__` を持つファイル（銘柄ごとの `html_issuers` は除く）。`__main__` から関数の参照をたどって届く `app/` の読み書きも含む。別のプロセスで続けて走るもの（crontab で並べたジョブ）は別の行
- バッチが HTTP で API を呼ぶとき（月次の取込は loopback の API に POST する）は「呼ぶ API」に出し、その API の読み書きも足す
- API はルートごと
- 数えないもの: DDL（`CREATE` / `ALTER` など）、`init_db` のスキーマ直し、テストが下ごしらえで `upsert` などの汎用の関数を直に呼んだ分
- SQLAlchemy を通さず `sqlite3` で開く接続（`backup_db` など）も数える

## 見るところ

- 機能ごとのページ（図と表）: 下の表から。入口（バッチ・API）は `jobs/<機能>/` のフォルダと、書く表の多い機能で分ける
- 全体の表・テーブルを変えたときの影響: [ビューア](dataflow/viewer.html)（wiki で開く。行か列を選ぶと、つながる入口とテーブルだけを図にする）
- エージェントは機能ごとのページか、この下の表を読む。HTML は人が見る用

| 機能 | バッチ | API | テーブル |
|---|---|---|---|
| [株価・決算](dataflow/market.md) | 6 | 9 | `briefing_documents`, `daily_prices`, `earnings_date_fetches`, `earnings_date_pubs`, `earnings_events`, `fx_rates`, `homepages`, `indicators`, `pts_snapshots`, `statements`, `stocks`, `tanshin_covers`, `topix` |
| [月次](dataflow/monthly.md) | 9 | 12 | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` |
| [XBRL・決算書類](dataflow/xbrl.md) | 3 | 4 | `tdnet_pdf_documents`, `xbrl_contexts`, `xbrl_documents`, `xbrl_element_mappings`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings`, `xbrl_financials`, `xbrl_job_runs`, `xbrl_major_shareholders`, `xbrl_segments`, `xbrl_shareholder_filings`, `xbrl_shareholding_by_category`, `xbrl_std_items` |
| [EDINET・大量保有](dataflow/edinet.md) | 8 | 4 | `edinet_archives`, `edinet_buyback_resolutions`, `edinet_buybacks`, `edinet_documents`, `edinet_filers`, `edinet_list_days`, `edinet_tender_offer_docs`, `edinet_tender_offers`, `large_holding_reports`, `large_holdings` |
| [サプライ](dataflow/supply.md) | 1 | 2 | `material_monthly`, `material_trade_country` |
| [共通・運用](dataflow/common.md) | 4 | 0 |  |

## 設計の点検

表は下限なので、ここに出たものがすぐに誤りとは限らない（テストの無い道で書く・読むかもしれない）。

書く入口が観測されなかった表（読まれるだけ）:

- `tdnet_pdf_documents`

画面の API から読まれない表（バッチの中だけで使う表）:

- `briefing_documents`
- `fx_rates`
- `homepages`
- `tanshin_covers`
- `xbrl_contexts`
- `xbrl_element_mappings`
- `xbrl_elements`
- `xbrl_facts`
- `xbrl_job_runs`
- `xbrl_major_shareholders`
- `xbrl_segments`
- `xbrl_shareholder_filings`
- `xbrl_shareholding_by_category`
- `edinet_archives`
- `edinet_documents`
- `edinet_filers`
- `edinet_list_days`
- `edinet_tender_offer_docs`
- `edinet_tender_offers`

`app/models.py` にあるのに、どの入口の読み書きにも出なかった表:

- （無し）

## SQL を観測しなかった入口

テストが通っていないか、DB を使わない（ファイルの取得・バックアップなど）。どちらかはこの表からは分からない。

- `jobs/market/intraday_1m.py`
- `jobs/ops/backup_data.py`
- `jobs/ops/backup_db.py`
- `jobs/ops/migrate_r2_share.py`
- `jobs/ops/vps_packages_r2.py`

- `GET /api/supply/companies`
- `GET /api/supply/graph`
- `GET /api/topix`

## モデルに無いテーブル

`app/models.py` に無いのに SQL に出た名前。

- `daily_prices__old`
- `dbstat`
- `xbrl_financials__old`
