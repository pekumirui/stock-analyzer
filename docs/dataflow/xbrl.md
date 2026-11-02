# データの流れ: XBRL・決算書類

**自動生成。手で直さない。** 読み方と作り方は [DATA_FLOW.md](../DATA_FLOW.md)。全体の表と影響の調べ方は [ビューア](viewer.html)。

図の見方: 左が入口、右が表。実線の矢印は書く、点線は読むだけ。灰色の点線の枠はほかの機能の表。`stocks` はほぼ全部が読むので、読む線は省く（表には出す。線が残らない入口は図から外す）。

## バッチ

```mermaid
flowchart LR
  n0["xbrl/xbrl_normalize.py"]:::job
  n1["xbrl/xbrl_parse.py"]:::job
  n2[("edinet_documents")]:::ext
  n3[("statements")]:::ext
  n4[("tdnet_pdf_documents")]:::tbl
  n5[("xbrl_contexts")]:::tbl
  n6[("xbrl_documents")]:::tbl
  n7[("xbrl_element_mappings")]:::tbl
  n8[("xbrl_elements")]:::tbl
  n9[("xbrl_facts")]:::tbl
  n10[("xbrl_filings")]:::tbl
  n11[("xbrl_financials")]:::tbl
  n12[("xbrl_job_runs")]:::tbl
  n13[("xbrl_major_shareholders")]:::tbl
  n14[("xbrl_segments")]:::tbl
  n15[("xbrl_shareholder_filings")]:::tbl
  n16[("xbrl_shareholding_by_category")]:::tbl
  n17[("xbrl_std_items")]:::tbl
  n0 -.- n2
  n0 -.- n5
  n0 --> n7
  n0 -.- n8
  n0 -.- n9
  n0 -.- n10
  n0 --> n11
  n0 --> n12
  n0 --> n13
  n0 --> n14
  n0 --> n15
  n0 --> n16
  n0 --> n17
  n1 -.- n3
  n1 -.- n4
  n1 --> n5
  n1 --> n6
  n1 --> n7
  n1 --> n8
  n1 --> n9
  n1 --> n10
  n1 --> n11
  n1 --> n12
  n1 --> n13
  n1 --> n14
  n1 --> n15
  n1 --> n16
  n1 --> n17
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| バッチ | 書く | 読むだけ |
|---|---|---|
| `jobs/xbrl/tdnet_xbrl_r2.py` |  | `stocks` |
| `jobs/xbrl/xbrl_normalize.py` | `xbrl_element_mappings`, `xbrl_financials`, `xbrl_job_runs`, `xbrl_major_shareholders`, `xbrl_segments`, `xbrl_shareholder_filings`, `xbrl_shareholding_by_category`, `xbrl_std_items` | `edinet_documents`, `xbrl_contexts`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings` |
| `jobs/xbrl/xbrl_parse.py` | `xbrl_contexts`, `xbrl_documents`, `xbrl_element_mappings`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings`, `xbrl_financials`, `xbrl_job_runs`, `xbrl_major_shareholders`, `xbrl_segments`, `xbrl_shareholder_filings`, `xbrl_shareholding_by_category`, `xbrl_std_items` | `statements`, `stocks`, `tdnet_pdf_documents` |

## API

```mermaid
flowchart LR
  n0["GET /api/earnings/detail/{code}"]:::api
  n1["GET /api/stock/filings/{filing_id}/documents/{seq}"]:::api
  n2["GET /api/stock/pdf/{disclosure_no}"]:::api
  n3["GET /api/stock/{code}/filings"]:::api
  n4[("statements")]:::ext
  n5[("tdnet_pdf_documents")]:::tbl
  n6[("xbrl_documents")]:::tbl
  n7[("xbrl_filings")]:::tbl
  n8[("xbrl_financials")]:::tbl
  n9[("xbrl_std_items")]:::tbl
  n0 -.- n4
  n0 -.- n7
  n0 -.- n8
  n0 -.- n9
  n1 -.- n6
  n2 -.- n5
  n3 -.- n5
  n3 -.- n6
  n3 -.- n7
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| API | 書く | 読むだけ |
|---|---|---|
| `GET /api/earnings/detail/{code}` |  | `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/stock/filings/{filing_id}/documents/{seq}` |  | `xbrl_documents` |
| `GET /api/stock/pdf/{disclosure_no}` |  | `tdnet_pdf_documents` |
| `GET /api/stock/{code}/filings` |  | `stocks`, `tdnet_pdf_documents`, `xbrl_documents`, `xbrl_filings` |

## テーブル

ほかの機能のバッチ・API も含めて、その表を書く・読む入口。

### tdnet_pdf_documents

- 書く: （観測なし）
- 読むだけ: `jobs/market/homepages.py（株価・決算）`, `jobs/xbrl/xbrl_parse.py`, `GET /api/stock/pdf/{disclosure_no}`, `GET /api/stock/{code}/filings`

### xbrl_contexts

- 書く: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `jobs/edinet/edinet_shareholders.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_normalize.py`

### xbrl_documents

- 書く: `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `GET /api/stock/filings/{filing_id}/documents/{seq}`, `GET /api/stock/{code}/filings`

### xbrl_element_mappings

- 書く: `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`

### xbrl_elements

- 書く: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/migrate.py（共通・運用）`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `jobs/edinet/edinet_shareholders.py（EDINET・大量保有）`, `jobs/market/homepages.py（株価・決算）`, `jobs/xbrl/xbrl_normalize.py`

### xbrl_facts

- 書く: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `jobs/edinet/edinet_shareholders.py（EDINET・大量保有）`, `jobs/market/homepages.py（株価・決算）`, `jobs/xbrl/xbrl_normalize.py`

### xbrl_filings

- 書く: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `jobs/edinet/edinet_archive.py（EDINET・大量保有）`, `jobs/edinet/edinet_shareholders.py（EDINET・大量保有）`, `jobs/market/homepages.py（株価・決算）`, `jobs/xbrl/xbrl_normalize.py`, `GET /api/earnings（株価・決算）`, `GET /api/earnings/detail/{code}`, `GET /api/stock/{code}/filings`

### xbrl_financials

- 書く: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/migrate.py（共通・運用）`, `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `GET /api/earnings（株価・決算）`, `GET /api/earnings/detail/{code}`

### xbrl_job_runs

- 書く: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: （観測なし）

### xbrl_major_shareholders

- 書く: `jobs/edinet/edinet_shareholders.py（EDINET・大量保有）`, `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: （観測なし）

### xbrl_segments

- 書く: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: （観測なし）

### xbrl_shareholder_filings

- 書く: `jobs/edinet/edinet_shareholders.py（EDINET・大量保有）`, `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: （観測なし）

### xbrl_shareholding_by_category

- 書く: `jobs/edinet/edinet_shareholders.py（EDINET・大量保有）`, `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: （観測なし）

### xbrl_std_items

- 書く: `jobs/xbrl/xbrl_normalize.py`, `jobs/xbrl/xbrl_parse.py`
- 読むだけ: `jobs/edinet/edinet_xbrl.py（EDINET・大量保有）`, `GET /api/earnings（株価・決算）`, `GET /api/earnings/detail/{code}`
