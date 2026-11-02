# データの流れ（バッチ・API × テーブル）

**このファイルは自動生成。手で直さない。** 作り直しは `python -m pytest tests --dataflow=write`。CI は `--dataflow=check` で実装と合うかを見る（作り方は `tools/dataflow/plugin.py`）。

テスト中に SQLite へ実際に流れた SQL を、呼んだバッチ・API に紐づけて集めたもの。

- 「書く」は INSERT / UPDATE / DELETE をした表（読んでもいる表を含む）。「読むだけ」は SELECT だけの表
- テストが通った道だけが載る。テストの無い分岐の読み書きは載らないので、表は**下限**として読む
- バッチは `jobs/` のうち `__main__` を持つファイル（銘柄ごとの `html_issuers` は除く）。`__main__` から関数の参照をたどって届く `app/` の読み書きも含む。別のプロセスで続けて走るもの（crontab で並べたジョブ）は別の行
- バッチが HTTP で API を呼ぶとき（月次の取込は loopback の API に POST する）は「呼ぶ API」に出し、その API の読み書きも足す
- API はルートごと
- 数えないもの: DDL（`CREATE` / `ALTER` など）、`init_db` のスキーマ直し、テストが下ごしらえで `upsert` などの汎用の関数を直に呼んだ分

## バッチ

| バッチ | 書く | 読むだけ | 呼ぶ API |
|---|---|---|---|
| `jobs/edinet/edinet_archive.py` | `edinet_archives` | `edinet_buybacks`, `edinet_documents`, `edinet_tender_offer_docs`, `large_holding_reports`, `xbrl_filings` |  |
| `jobs/edinet/edinet_buybacks.py` | `edinet_archives`, `edinet_buyback_resolutions`, `edinet_buybacks` | `edinet_documents`, `edinet_filers`, `stocks` |  |
| `jobs/edinet/edinet_codes.py` | `edinet_documents`, `edinet_filers`, `stocks` |  |  |
| `jobs/edinet/edinet_documents.py` | `edinet_documents`, `edinet_list_days` | `edinet_filers`, `stocks` |  |
| `jobs/edinet/edinet_large_holdings.py` | `edinet_archives`, `large_holding_reports`, `large_holdings` | `edinet_documents`, `edinet_filers`, `stocks` |  |
| `jobs/edinet/edinet_shareholders.py` | `xbrl_major_shareholders`, `xbrl_shareholder_filings`, `xbrl_shareholding_by_category` | `edinet_documents`, `xbrl_contexts`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings` |  |
| `jobs/edinet/edinet_tender_offers.py` | `edinet_archives`, `edinet_tender_offer_docs`, `edinet_tender_offers` | `edinet_documents`, `edinet_filers`, `stocks` |  |
| `jobs/edinet/edinet_xbrl.py` | `edinet_archives`, `xbrl_contexts`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings`, `xbrl_financials`, `xbrl_job_runs`, `xbrl_major_shareholders`, `xbrl_segments`, `xbrl_shareholder_filings`, `xbrl_shareholding_by_category` | `edinet_documents`, `stocks`, `xbrl_element_mappings`, `xbrl_std_items` |  |
| `jobs/fx_rates.py` | `fx_rates` |  |  |
| `jobs/golden_db.py` | `daily_prices`, `earnings_date_fetches`, `earnings_date_pubs`, `earnings_events`, `fx_rates`, `indicators`, `material_monthly`, `material_trade_country`, `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `statements`, `stocks`, `topix` |  |  |
| `jobs/market/check_demo.py` |  | `statements`, `stocks` |  |
| `jobs/market/pts_snapshot.py` | `pts_snapshots` |  |  |
| `jobs/market/run_batch.py` | `daily_prices`, `earnings_date_fetches`, `earnings_date_pubs`, `earnings_events`, `indicators`, `statements`, `stocks`, `topix` |  |  |
| `jobs/migrate.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `xbrl_elements`, `xbrl_financials` | `stocks` |  |
| `jobs/monthly/ir_monthly_html_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `GET /api/v1/tables/{code}`<br>`POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/ir_monthly_html_r2.py` |  | `stocks` |  |
| `jobs/monthly/ir_monthly_json_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/ir_monthly_r2.py` |  | `stocks` |  |
| `jobs/monthly/ir_monthly_xls_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `GET /api/v1/tables/{code}/{year}`<br>`POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/monthly_names.py` | `monthly_element_sources`, `monthly_elements`, `monthly_values` |  |  |
| `jobs/monthly/tdnet_monthly_pdf_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `GET /api/v1/tables/{code}`<br>`POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/tdnet_monthly_pdf_sidecar.py` |  | `monthly_elements`, `monthly_tables`, `monthly_values` | `GET /api/v1/tables/{code}/{year}` |
| `jobs/monthly/tdnet_monthly_r2.py` |  | `stocks` |  |
| `jobs/ops/rebuild_daily_prices.py` | `daily_prices` |  |  |
| `jobs/supply/material_stats.py` | `material_monthly`, `material_trade_country` |  |  |
| `jobs/xbrl/tdnet_xbrl_r2.py` |  | `stocks` |  |
| `jobs/xbrl/xbrl_normalize.py` | `xbrl_element_mappings`, `xbrl_financials`, `xbrl_job_runs`, `xbrl_major_shareholders`, `xbrl_segments`, `xbrl_shareholder_filings`, `xbrl_shareholding_by_category`, `xbrl_std_items` | `edinet_documents`, `xbrl_contexts`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings` |  |
| `jobs/xbrl/xbrl_parse.py` | `xbrl_contexts`, `xbrl_documents`, `xbrl_element_mappings`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings`, `xbrl_financials`, `xbrl_job_runs`, `xbrl_major_shareholders`, `xbrl_segments`, `xbrl_shareholder_filings`, `xbrl_shareholding_by_category`, `xbrl_std_items` | `statements`, `stocks`, `tdnet_pdf_documents` |  |

## API

| API | 書く | 読むだけ |
|---|---|---|
| `DELETE /api/v1/tables/{code}/{year}/elements` | `monthly_element_sources`, `monthly_elements` | `monthly_tables`, `monthly_values`, `stocks` |
| `GET /api/chart/{code}` |  | `daily_prices`, `earnings_events`, `statements`, `stocks` |
| `GET /api/earnings` |  | `earnings_date_fetches`, `earnings_date_pubs`, `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/earnings-performance/{code}` |  | `earnings_events` |
| `GET /api/earnings/calendar` |  | `earnings_date_pubs`, `statements`, `stocks` |
| `GET /api/earnings/detail/{code}` |  | `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/holders/activity` |  | `large_holding_reports`, `large_holdings`, `stocks` |
| `GET /api/holders/search` |  | `large_holding_reports`, `large_holdings` |
| `GET /api/holders/{edinet}` |  | `daily_prices`, `large_holding_reports`, `large_holdings`, `stocks` |
| `GET /api/pts` |  | `daily_prices`, `pts_snapshots` |
| `GET /api/ranking/earnings-perf` |  | `daily_prices`, `earnings_events`, `stocks`, `topix` |
| `GET /api/screener` |  | `indicators`, `stocks` |
| `GET /api/stock/filings/{filing_id}/documents/{seq}` |  | `xbrl_documents` |
| `GET /api/stock/pdf/{disclosure_no}` |  | `tdnet_pdf_documents` |
| `GET /api/stock/{code}/filings` |  | `stocks`, `tdnet_pdf_documents`, `xbrl_documents`, `xbrl_filings` |
| `GET /api/stock/{code}/holders` |  | `edinet_buyback_resolutions`, `edinet_buybacks`, `large_holding_reports`, `large_holdings`, `stocks` |
| `GET /api/stock/{code}/overview` |  | `daily_prices`, `earnings_date_pubs`, `statements`, `stocks` |
| `GET /api/stocks/search` |  | `stocks` |
| `GET /api/supply/countries/{material_id}` |  | `material_trade_country` |
| `GET /api/supply/monthly/{material_id}` |  | `material_monthly` |
| `GET /api/v1/tables` |  | `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `stocks` |
| `GET /api/v1/tables/{code}` |  | `monthly_tables` |
| `GET /api/v1/tables/{code}/{year}` |  | `monthly_elements`, `monthly_tables`, `monthly_values` |
| `GET /api/v1/tables/{code}/{year}/doc` |  | `monthly_elements`, `monthly_tables`, `monthly_values` |
| `POST /api/v1/tables/{code}/ingest` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` |
| `POST /api/v1/tables/{code}/{year}/elements` | `monthly_element_sources`, `monthly_elements`, `monthly_tables` | `stocks` |
| `PUT /api/v1/tables/{code}/industry` | `monthly_issuers` | `monthly_tables`, `stocks` |
| `PUT /api/v1/tables/{code}/{year}/doc` | `monthly_element_sources`, `monthly_elements`, `monthly_values` | `monthly_tables`, `stocks` |
| `PUT /api/v1/tables/{code}/{year}/elements` | `monthly_element_sources`, `monthly_elements` | `monthly_tables`, `monthly_values`, `stocks` |
| `PUT /api/v1/tables/{code}/{year}/values` | `monthly_values` | `monthly_elements`, `monthly_tables`, `stocks` |
| `PUT /api/v1/tables/{code}/{year}/values/batch` | `monthly_values` | `monthly_elements`, `monthly_tables`, `stocks` |

## テーブルから引く

| テーブル | 書く | 読む |
|---|---|---|
| `daily_prices` | `jobs/golden_db.py`<br>`jobs/market/run_batch.py`<br>`jobs/ops/rebuild_daily_prices.py` | `GET /api/chart/{code}`<br>`GET /api/holders/{edinet}`<br>`GET /api/pts`<br>`GET /api/ranking/earnings-perf`<br>`GET /api/stock/{code}/overview`<br>`jobs/golden_db.py`<br>`jobs/market/run_batch.py` |
| `earnings_date_fetches` | `jobs/golden_db.py`<br>`jobs/market/run_batch.py` | `GET /api/earnings`<br>`jobs/market/run_batch.py` |
| `earnings_date_pubs` | `jobs/golden_db.py`<br>`jobs/market/run_batch.py` | `GET /api/earnings`<br>`GET /api/earnings/calendar`<br>`GET /api/stock/{code}/overview` |
| `earnings_events` | `jobs/golden_db.py`<br>`jobs/market/run_batch.py` | `GET /api/chart/{code}`<br>`GET /api/earnings-performance/{code}`<br>`GET /api/ranking/earnings-perf`<br>`jobs/golden_db.py` |
| `edinet_archives` | `jobs/edinet/edinet_archive.py`<br>`jobs/edinet/edinet_buybacks.py`<br>`jobs/edinet/edinet_large_holdings.py`<br>`jobs/edinet/edinet_tender_offers.py`<br>`jobs/edinet/edinet_xbrl.py` | `jobs/edinet/edinet_archive.py`<br>`jobs/edinet/edinet_buybacks.py`<br>`jobs/edinet/edinet_large_holdings.py`<br>`jobs/edinet/edinet_tender_offers.py`<br>`jobs/edinet/edinet_xbrl.py` |
| `edinet_buyback_resolutions` | `jobs/edinet/edinet_buybacks.py` | `GET /api/stock/{code}/holders` |
| `edinet_buybacks` | `jobs/edinet/edinet_buybacks.py` | `GET /api/stock/{code}/holders`<br>`jobs/edinet/edinet_archive.py`<br>`jobs/edinet/edinet_buybacks.py` |
| `edinet_documents` | `jobs/edinet/edinet_codes.py`<br>`jobs/edinet/edinet_documents.py` | `jobs/edinet/edinet_archive.py`<br>`jobs/edinet/edinet_buybacks.py`<br>`jobs/edinet/edinet_documents.py`<br>`jobs/edinet/edinet_large_holdings.py`<br>`jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_tender_offers.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py` |
| `edinet_filers` | `jobs/edinet/edinet_codes.py` | `jobs/edinet/edinet_buybacks.py`<br>`jobs/edinet/edinet_codes.py`<br>`jobs/edinet/edinet_documents.py`<br>`jobs/edinet/edinet_large_holdings.py`<br>`jobs/edinet/edinet_tender_offers.py` |
| `edinet_list_days` | `jobs/edinet/edinet_documents.py` | `jobs/edinet/edinet_documents.py` |
| `edinet_tender_offer_docs` | `jobs/edinet/edinet_tender_offers.py` | `jobs/edinet/edinet_archive.py`<br>`jobs/edinet/edinet_tender_offers.py` |
| `edinet_tender_offers` | `jobs/edinet/edinet_tender_offers.py` |  |
| `fx_rates` | `jobs/fx_rates.py`<br>`jobs/golden_db.py` | `jobs/fx_rates.py` |
| `indicators` | `jobs/golden_db.py`<br>`jobs/market/run_batch.py` | `GET /api/screener`<br>`jobs/golden_db.py`<br>`jobs/market/run_batch.py` |
| `large_holding_reports` | `jobs/edinet/edinet_large_holdings.py` | `GET /api/holders/activity`<br>`GET /api/holders/search`<br>`GET /api/holders/{edinet}`<br>`GET /api/stock/{code}/holders`<br>`jobs/edinet/edinet_archive.py`<br>`jobs/edinet/edinet_large_holdings.py` |
| `large_holdings` | `jobs/edinet/edinet_large_holdings.py` | `GET /api/holders/activity`<br>`GET /api/holders/search`<br>`GET /api/holders/{edinet}`<br>`GET /api/stock/{code}/holders` |
| `material_monthly` | `jobs/golden_db.py`<br>`jobs/supply/material_stats.py` | `GET /api/supply/monthly/{material_id}`<br>`jobs/supply/material_stats.py` |
| `material_trade_country` | `jobs/golden_db.py`<br>`jobs/supply/material_stats.py` | `GET /api/supply/countries/{material_id}`<br>`jobs/supply/material_stats.py` |
| `monthly_element_sources` | `DELETE /api/v1/tables/{code}/{year}/elements`<br>`POST /api/v1/tables/{code}/ingest`<br>`POST /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`PUT /api/v1/tables/{code}/{year}/elements`<br>`jobs/golden_db.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/monthly_names.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py` | `POST /api/v1/tables/{code}/ingest`<br>`POST /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`jobs/golden_db.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py` |
| `monthly_elements` | `DELETE /api/v1/tables/{code}/{year}/elements`<br>`POST /api/v1/tables/{code}/ingest`<br>`POST /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`PUT /api/v1/tables/{code}/{year}/elements`<br>`jobs/golden_db.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/monthly_names.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py` | `DELETE /api/v1/tables/{code}/{year}/elements`<br>`GET /api/v1/tables`<br>`GET /api/v1/tables/{code}/{year}`<br>`GET /api/v1/tables/{code}/{year}/doc`<br>`POST /api/v1/tables/{code}/ingest`<br>`POST /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`PUT /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/values`<br>`PUT /api/v1/tables/{code}/{year}/values/batch`<br>`jobs/golden_db.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/monthly_names.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_sidecar.py` |
| `monthly_issuers` | `POST /api/v1/tables/{code}/ingest`<br>`PUT /api/v1/tables/{code}/industry`<br>`jobs/golden_db.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py` | `GET /api/v1/tables`<br>`POST /api/v1/tables/{code}/ingest`<br>`PUT /api/v1/tables/{code}/industry`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py` |
| `monthly_tables` | `POST /api/v1/tables/{code}/ingest`<br>`POST /api/v1/tables/{code}/{year}/elements`<br>`jobs/golden_db.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py` | `DELETE /api/v1/tables/{code}/{year}/elements`<br>`GET /api/v1/tables`<br>`GET /api/v1/tables/{code}`<br>`GET /api/v1/tables/{code}/{year}`<br>`GET /api/v1/tables/{code}/{year}/doc`<br>`POST /api/v1/tables/{code}/ingest`<br>`POST /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/industry`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`PUT /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/values`<br>`PUT /api/v1/tables/{code}/{year}/values/batch`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_sidecar.py` |
| `monthly_values` | `POST /api/v1/tables/{code}/ingest`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`PUT /api/v1/tables/{code}/{year}/values`<br>`PUT /api/v1/tables/{code}/{year}/values/batch`<br>`jobs/golden_db.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/monthly_names.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py` | `DELETE /api/v1/tables/{code}/{year}/elements`<br>`GET /api/v1/tables`<br>`GET /api/v1/tables/{code}/{year}`<br>`GET /api/v1/tables/{code}/{year}/doc`<br>`POST /api/v1/tables/{code}/ingest`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`PUT /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/values`<br>`PUT /api/v1/tables/{code}/{year}/values/batch`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/monthly_names.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_sidecar.py` |
| `pts_snapshots` | `jobs/market/pts_snapshot.py` | `GET /api/pts`<br>`jobs/market/pts_snapshot.py` |
| `statements` | `jobs/golden_db.py`<br>`jobs/market/run_batch.py` | `GET /api/chart/{code}`<br>`GET /api/earnings`<br>`GET /api/earnings/calendar`<br>`GET /api/earnings/detail/{code}`<br>`GET /api/stock/{code}/overview`<br>`POST /api/v1/tables/{code}/ingest`<br>`jobs/golden_db.py`<br>`jobs/market/check_demo.py`<br>`jobs/market/run_batch.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `stocks` | `jobs/edinet/edinet_codes.py`<br>`jobs/golden_db.py`<br>`jobs/market/run_batch.py` | `DELETE /api/v1/tables/{code}/{year}/elements`<br>`GET /api/chart/{code}`<br>`GET /api/earnings`<br>`GET /api/earnings/calendar`<br>`GET /api/earnings/detail/{code}`<br>`GET /api/holders/activity`<br>`GET /api/holders/{edinet}`<br>`GET /api/ranking/earnings-perf`<br>`GET /api/screener`<br>`GET /api/stock/{code}/filings`<br>`GET /api/stock/{code}/holders`<br>`GET /api/stock/{code}/overview`<br>`GET /api/stocks/search`<br>`GET /api/v1/tables`<br>`POST /api/v1/tables/{code}/ingest`<br>`POST /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/industry`<br>`PUT /api/v1/tables/{code}/{year}/doc`<br>`PUT /api/v1/tables/{code}/{year}/elements`<br>`PUT /api/v1/tables/{code}/{year}/values`<br>`PUT /api/v1/tables/{code}/{year}/values/batch`<br>`jobs/edinet/edinet_buybacks.py`<br>`jobs/edinet/edinet_codes.py`<br>`jobs/edinet/edinet_documents.py`<br>`jobs/edinet/edinet_large_holdings.py`<br>`jobs/edinet/edinet_tender_offers.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/golden_db.py`<br>`jobs/market/check_demo.py`<br>`jobs/market/run_batch.py`<br>`jobs/migrate.py`<br>`jobs/monthly/ir_monthly_html_ingest.py`<br>`jobs/monthly/ir_monthly_html_r2.py`<br>`jobs/monthly/ir_monthly_json_ingest.py`<br>`jobs/monthly/ir_monthly_r2.py`<br>`jobs/monthly/ir_monthly_xls_ingest.py`<br>`jobs/monthly/tdnet_monthly_pdf_ingest.py`<br>`jobs/monthly/tdnet_monthly_r2.py`<br>`jobs/xbrl/tdnet_xbrl_r2.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `tdnet_pdf_documents` |  | `GET /api/stock/pdf/{disclosure_no}`<br>`GET /api/stock/{code}/filings`<br>`jobs/xbrl/xbrl_parse.py` |
| `topix` | `jobs/golden_db.py`<br>`jobs/market/run_batch.py` | `GET /api/ranking/earnings-perf`<br>`jobs/golden_db.py`<br>`jobs/market/run_batch.py` |
| `xbrl_contexts` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_parse.py` | `jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `xbrl_documents` | `jobs/xbrl/xbrl_parse.py` | `GET /api/stock/filings/{filing_id}/documents/{seq}`<br>`GET /api/stock/{code}/filings`<br>`jobs/xbrl/xbrl_parse.py` |
| `xbrl_element_mappings` | `jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `xbrl_elements` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/migrate.py`<br>`jobs/xbrl/xbrl_parse.py` | `jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/migrate.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `xbrl_facts` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_parse.py` | `jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `xbrl_filings` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_parse.py` | `GET /api/earnings`<br>`GET /api/earnings/detail/{code}`<br>`GET /api/stock/{code}/filings`<br>`jobs/edinet/edinet_archive.py`<br>`jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `xbrl_financials` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/migrate.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` | `GET /api/earnings`<br>`GET /api/earnings/detail/{code}`<br>`jobs/xbrl/xbrl_normalize.py` |
| `xbrl_job_runs` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |
| `xbrl_major_shareholders` | `jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |  |
| `xbrl_segments` | `jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` | `jobs/xbrl/xbrl_normalize.py` |
| `xbrl_shareholder_filings` | `jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` | `jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py` |
| `xbrl_shareholding_by_category` | `jobs/edinet/edinet_shareholders.py`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |  |
| `xbrl_std_items` | `jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` | `GET /api/earnings`<br>`GET /api/earnings/detail/{code}`<br>`jobs/edinet/edinet_xbrl.py`<br>`jobs/xbrl/xbrl_normalize.py`<br>`jobs/xbrl/xbrl_parse.py` |

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
