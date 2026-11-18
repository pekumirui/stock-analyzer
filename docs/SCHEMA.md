# DB スキーマ（テーブル・列・ER 図）

**このファイルと `docs/schema/` は自動生成。手で直さない。** 正は `app/models.py`。作り直しは `python -m tools.schema.render`。

合わなければ `tests/nonfunctional/test_schema_doc.py` が落ちる。

| 機能 | テーブル数 | テーブル |
|---|---|---|
| [株価・決算](schema/market.md) | 18 | `briefing_documents` `briefing_extracts` `briefing_facts` `briefing_irbank_scans` `briefing_pages` `daily_prices` `data_versions` `earnings_date_fetches` `earnings_date_pubs` `earnings_events` `fx_rates` `homepages` `indicators` `pts_snapshots` `statements` `stocks` `tanshin_covers` `topix` |
| [月次](schema/monthly.md) | 5 | `monthly_element_sources` `monthly_elements` `monthly_issuers` `monthly_tables` `monthly_values` |
| [XBRL・決算書類](schema/xbrl.md) | 16 | `tdnet_pdf_documents` `xbrl_contexts` `xbrl_documents` `xbrl_element_mappings` `xbrl_elements` `xbrl_facts` `xbrl_filings` `xbrl_financials` `xbrl_job_runs` `xbrl_major_shareholders` `xbrl_pdf_matches` `xbrl_presentation_roles` `xbrl_segments` `xbrl_shareholder_filings` `xbrl_shareholding_by_category` `xbrl_std_items` |
| [EDINET・大量保有](schema/edinet.md) | 10 | `edinet_archives` `edinet_buyback_resolutions` `edinet_buybacks` `edinet_documents` `edinet_filers` `edinet_list_days` `edinet_tender_offer_docs` `edinet_tender_offers` `large_holding_reports` `large_holdings` |
| [サプライ](schema/supply.md) | 2 | `material_monthly` `material_trade_country` |
