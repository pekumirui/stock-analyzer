# データの流れ: 株価・決算

**自動生成。手で直さない。** 読み方と作り方は [DATA_FLOW.md](../DATA_FLOW.md)。全体の表と影響の調べ方は [ビューア](viewer.html)。

図の見方: 左が入口、右が表。実線の矢印は書く、点線は読むだけ。灰色の点線の枠はほかの機能の表。`stocks` はほぼ全部が読むので、読む線は省く（表には出す。線が残らない入口は図から外す）。

## バッチ

```mermaid
flowchart LR
  n0["market/check_demo.py"]:::job
  n1["market/pts_snapshot.py"]:::job
  n2["market/run_batch.py"]:::job
  n3[("daily_prices")]:::tbl
  n4[("earnings_date_fetches")]:::tbl
  n5[("earnings_date_pubs")]:::tbl
  n6[("earnings_events")]:::tbl
  n7[("indicators")]:::tbl
  n8[("pts_snapshots")]:::tbl
  n9[("statements")]:::tbl
  n10[("stocks")]:::tbl
  n11[("topix")]:::tbl
  n0 -.- n9
  n1 --> n8
  n2 --> n3
  n2 --> n4
  n2 --> n5
  n2 --> n6
  n2 --> n7
  n2 --> n9
  n2 --> n10
  n2 --> n11
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| バッチ | 書く | 読むだけ |
|---|---|---|
| `jobs/market/check_demo.py` |  | `statements`, `stocks` |
| `jobs/market/pts_snapshot.py` | `pts_snapshots` |  |
| `jobs/market/run_batch.py` | `daily_prices`, `earnings_date_fetches`, `earnings_date_pubs`, `earnings_events`, `indicators`, `statements`, `stocks`, `topix` |  |

## API

```mermaid
flowchart LR
  n0["GET /api/chart/{code}"]:::api
  n1["GET /api/earnings"]:::api
  n2["GET /api/earnings-performance/{code}"]:::api
  n3["GET /api/earnings/calendar"]:::api
  n4["GET /api/pts"]:::api
  n5["GET /api/ranking/earnings-perf"]:::api
  n6["GET /api/screener"]:::api
  n7["GET /api/stock/{code}/overview"]:::api
  n8[("daily_prices")]:::tbl
  n9[("earnings_date_fetches")]:::tbl
  n10[("earnings_date_pubs")]:::tbl
  n11[("earnings_events")]:::tbl
  n12[("indicators")]:::tbl
  n13[("pts_snapshots")]:::tbl
  n14[("statements")]:::tbl
  n15[("topix")]:::tbl
  n16[("xbrl_filings")]:::ext
  n17[("xbrl_financials")]:::ext
  n18[("xbrl_std_items")]:::ext
  n0 -.- n8
  n0 -.- n11
  n0 -.- n14
  n1 -.- n9
  n1 -.- n10
  n1 -.- n14
  n1 -.- n16
  n1 -.- n17
  n1 -.- n18
  n2 -.- n11
  n3 -.- n10
  n3 -.- n14
  n4 -.- n8
  n4 -.- n13
  n5 -.- n8
  n5 -.- n11
  n5 -.- n15
  n6 -.- n12
  n7 -.- n8
  n7 -.- n10
  n7 -.- n14
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| API | 書く | 読むだけ |
|---|---|---|
| `GET /api/chart/{code}` |  | `daily_prices`, `earnings_events`, `statements`, `stocks` |
| `GET /api/earnings` |  | `earnings_date_fetches`, `earnings_date_pubs`, `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/earnings-performance/{code}` |  | `earnings_events` |
| `GET /api/earnings/calendar` |  | `earnings_date_pubs`, `statements`, `stocks` |
| `GET /api/pts` |  | `daily_prices`, `pts_snapshots` |
| `GET /api/ranking/earnings-perf` |  | `daily_prices`, `earnings_events`, `stocks`, `topix` |
| `GET /api/screener` |  | `indicators`, `stocks` |
| `GET /api/stock/{code}/overview` |  | `daily_prices`, `earnings_date_pubs`, `statements`, `stocks` |
| `GET /api/stocks/search` |  | `stocks` |

## テーブル

ほかの機能のバッチ・API も含めて、その表を書く・読む入口。

### daily_prices

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`, `jobs/ops/rebuild_daily_prices.py（共通・運用）`
- 読むだけ: `GET /api/chart/{code}`, `GET /api/holders/{edinet}（EDINET・大量保有）`, `GET /api/pts`, `GET /api/ranking/earnings-perf`, `GET /api/stock/{code}/overview`

### earnings_date_fetches

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`
- 読むだけ: `GET /api/earnings`

### earnings_date_pubs

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`
- 読むだけ: `GET /api/earnings`, `GET /api/earnings/calendar`, `GET /api/stock/{code}/overview`

### earnings_events

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`
- 読むだけ: `GET /api/chart/{code}`, `GET /api/earnings-performance/{code}`, `GET /api/ranking/earnings-perf`

### fx_rates

- 書く: `jobs/fx_rates.py（共通・運用）`, `jobs/golden_db.py（共通・運用）`
- 読むだけ: （観測なし）

### indicators

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`
- 読むだけ: `GET /api/screener`

### pts_snapshots

- 書く: `jobs/market/pts_snapshot.py`
- 読むだけ: `GET /api/pts`

### statements

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`
- 読むだけ: `jobs/market/check_demo.py`, `jobs/monthly/ir_monthly_html_ingest.py（月次）`, `jobs/monthly/ir_monthly_json_ingest.py（月次）`, `jobs/monthly/ir_monthly_xls_ingest.py（月次）`, `jobs/monthly/tdnet_monthly_pdf_ingest.py（月次）`, `jobs/xbrl/xbrl_parse.py（XBRL・決算書類）`, `GET /api/chart/{code}`, `GET /api/earnings`, `GET /api/earnings/calendar`, `GET /api/earnings/detail/{code}（XBRL・決算書類）`, `GET /api/stock/{code}/overview`, `POST /api/v1/tables/{code}/ingest（月次）`

### stocks

- 書く: `jobs/edinet/edinet_codes.py（EDINET・大量保有）`, `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`
- 読むだけ: `jobs/edinet/edinet_buybacks.py（EDINET・大量保有）`, `jobs/edinet/edinet_documents.py（EDINET・大量保有）`, `jobs/edinet/edinet_large_holdings.py（EDINET・大量保有）`, `jobs/edinet/edinet_tender_offers.py（EDINET・大量保有）`, `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/market/check_demo.py`, `jobs/migrate.py（共通・運用）`, `jobs/monthly/ir_monthly_html_ingest.py（月次）`, `jobs/monthly/ir_monthly_html_r2.py（月次）`, `jobs/monthly/ir_monthly_json_ingest.py（月次）`, `jobs/monthly/ir_monthly_r2.py（月次）`, `jobs/monthly/ir_monthly_xls_ingest.py（月次）`, `jobs/monthly/tdnet_monthly_pdf_ingest.py（月次）`, `jobs/monthly/tdnet_monthly_r2.py（月次）`, `jobs/xbrl/tdnet_xbrl_r2.py（XBRL・決算書類）`, `jobs/xbrl/xbrl_parse.py（XBRL・決算書類）`, `DELETE /api/v1/tables/{code}/{year}/elements（月次）`, `GET /api/chart/{code}`, `GET /api/earnings`, `GET /api/earnings/calendar`, `GET /api/earnings/detail/{code}（XBRL・決算書類）`, `GET /api/holders/activity（EDINET・大量保有）`, `GET /api/holders/{edinet}（EDINET・大量保有）`, `GET /api/ranking/earnings-perf`, `GET /api/screener`, `GET /api/stock/{code}/filings（XBRL・決算書類）`, `GET /api/stock/{code}/holders（EDINET・大量保有）`, `GET /api/stock/{code}/overview`, `GET /api/stocks/search`, `GET /api/v1/tables（月次）`, `POST /api/v1/tables/{code}/ingest（月次）`, `POST /api/v1/tables/{code}/{year}/elements（月次）`, `PUT /api/v1/tables/{code}/industry（月次）`, `PUT /api/v1/tables/{code}/{year}/doc（月次）`, `PUT /api/v1/tables/{code}/{year}/elements（月次）`, `PUT /api/v1/tables/{code}/{year}/values（月次）`, `PUT /api/v1/tables/{code}/{year}/values/batch（月次）`

### topix

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/market/run_batch.py`
- 読むだけ: `GET /api/ranking/earnings-perf`
