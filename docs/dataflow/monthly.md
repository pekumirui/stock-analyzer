# データの流れ: 月次

読み方と作り方は [DATA_FLOW.md](../DATA_FLOW.md)。全体の表と影響の調べ方は [ビューア](viewer.html)。

図の見方: 左がバッチ・API、右が表。実線の矢印は書き込み、点線は読み取りだけ。灰色の点線の枠はほかの機能の表。`stocks` はほぼ全部が読み取るので、読み取りの線は省く（表には出す。線が残らないバッチ・API は図から外す）。

## バッチ

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["monthly/ir_monthly_html_ingest.py"]:::job
  n1["monthly/ir_monthly_json_ingest.py"]:::job
  n2["monthly/ir_monthly_xls_ingest.py"]:::job
  n3["monthly/monthly_names.py"]:::job
  n4["monthly/tdnet_monthly_pdf_ingest.py"]:::job
  n5["monthly/tdnet_monthly_pdf_sidecar.py"]:::job
  n6[("monthly_element_sources")]:::tbl
  n7[("monthly_elements")]:::tbl
  n8[("monthly_issuers")]:::tbl
  n9[("monthly_tables")]:::tbl
  n10[("monthly_values")]:::tbl
  n11[("statements")]:::ext
  n0 --> n6
  n0 --> n7
  n0 --> n8
  n0 --> n9
  n0 --> n10
  n0 -.- n11
  n1 --> n6
  n1 --> n7
  n1 --> n8
  n1 --> n9
  n1 --> n10
  n1 -.- n11
  n2 --> n6
  n2 --> n7
  n2 --> n8
  n2 --> n9
  n2 --> n10
  n2 -.- n11
  n3 --> n6
  n3 --> n7
  n3 --> n10
  n4 --> n6
  n4 --> n7
  n4 --> n8
  n4 --> n9
  n4 --> n10
  n4 -.- n11
  n5 -.- n7
  n5 -.- n9
  n5 -.- n10
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| バッチ | 書き込み | 読み取りだけ | 呼ぶ API |
|---|---|---|---|
| `jobs/monthly/ir_monthly_html_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `GET /api/v1/tables/{code}`<br>`POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/ir_monthly_html_r2.py` |  | `stocks` |  |
| `jobs/monthly/ir_monthly_json_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/ir_monthly_r2.py` |  | `stocks` |  |
| `jobs/monthly/ir_monthly_xls_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `GET /api/v1/tables/{code}/{year}`<br>`POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/monthly_names.py` | `monthly_element_sources`, `monthly_elements`, `monthly_values` |  |  |
| `jobs/monthly/tdnet_monthly_pdf_ingest.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values` | `statements`, `stocks` | `GET /api/v1/tables/{code}`<br>`POST /api/v1/tables/{code}/ingest` |
| `jobs/monthly/tdnet_monthly_pdf_sidecar.py` |  | `monthly_elements`, `monthly_tables`, `monthly_values` | `GET /api/v1/tables/{code}/{year}` |
| `jobs/monthly/tdnet_monthly_r2.py` |  | `stocks` |  |

## API

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["DELETE /api/v1/tables/{code}/{year}/elements"]:::api
  n1["GET /api/v1/tables"]:::api
  n2["GET /api/v1/tables/{code}"]:::api
  n3["GET /api/v1/tables/{code}/{year}"]:::api
  n4["GET /api/v1/tables/{code}/{year}/doc"]:::api
  n5["POST /api/v1/tables/{code}/ingest"]:::api
  n6["POST /api/v1/tables/{code}/{year}/elements"]:::api
  n7["PUT /api/v1/tables/{code}/industry"]:::api
  n8["PUT /api/v1/tables/{code}/{year}/doc"]:::api
  n9["PUT /api/v1/tables/{code}/{year}/elements"]:::api
  n10["PUT /api/v1/tables/{code}/{year}/values"]:::api
  n11["PUT /api/v1/tables/{code}/{year}/values/batch"]:::api
  n12[("data_versions")]:::ext
  n13[("monthly_element_sources")]:::tbl
  n14[("monthly_elements")]:::tbl
  n15[("monthly_issuers")]:::tbl
  n16[("monthly_tables")]:::tbl
  n17[("monthly_values")]:::tbl
  n18[("statements")]:::ext
  n0 --> n13
  n0 --> n14
  n0 -.- n16
  n0 -.- n17
  n1 -.- n12
  n1 -.- n14
  n1 -.- n15
  n1 -.- n16
  n1 -.- n17
  n2 -.- n16
  n3 -.- n14
  n3 -.- n16
  n3 -.- n17
  n4 -.- n14
  n4 -.- n16
  n4 -.- n17
  n5 --> n13
  n5 --> n14
  n5 --> n15
  n5 --> n16
  n5 --> n17
  n5 -.- n18
  n6 --> n13
  n6 --> n14
  n6 --> n16
  n7 --> n15
  n7 -.- n16
  n8 --> n13
  n8 --> n14
  n8 -.- n16
  n8 --> n17
  n9 --> n13
  n9 --> n14
  n9 -.- n16
  n9 -.- n17
  n10 -.- n14
  n10 -.- n16
  n10 --> n17
  n11 -.- n14
  n11 -.- n16
  n11 --> n17
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| API | 書き込み | 読み取りだけ |
|---|---|---|
| `DELETE /api/v1/tables/{code}/{year}/elements` | `monthly_element_sources`, `monthly_elements` | `monthly_tables`, `monthly_values`, `stocks` |
| `GET /api/v1/tables` |  | `data_versions`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `stocks` |
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

## テーブル

ほかの機能のバッチ・API も含めて、その表を読み書きするバッチ・API。

### monthly_element_sources

- 書き込み: `jobs/golden_db.py（共通・運用）`, `jobs/migrate.py（共通・運用）`, `jobs/monthly/ir_monthly_html_ingest.py`, `jobs/monthly/ir_monthly_json_ingest.py`, `jobs/monthly/ir_monthly_xls_ingest.py`, `jobs/monthly/monthly_names.py`, `jobs/monthly/tdnet_monthly_pdf_ingest.py`, `DELETE /api/v1/tables/{code}/{year}/elements`, `POST /api/v1/tables/{code}/ingest`, `POST /api/v1/tables/{code}/{year}/elements`, `PUT /api/v1/tables/{code}/{year}/doc`, `PUT /api/v1/tables/{code}/{year}/elements`
- 読み取りだけ: （観測なし）

### monthly_elements

- 書き込み: `jobs/golden_db.py（共通・運用）`, `jobs/migrate.py（共通・運用）`, `jobs/monthly/ir_monthly_html_ingest.py`, `jobs/monthly/ir_monthly_json_ingest.py`, `jobs/monthly/ir_monthly_xls_ingest.py`, `jobs/monthly/monthly_names.py`, `jobs/monthly/tdnet_monthly_pdf_ingest.py`, `DELETE /api/v1/tables/{code}/{year}/elements`, `POST /api/v1/tables/{code}/ingest`, `POST /api/v1/tables/{code}/{year}/elements`, `PUT /api/v1/tables/{code}/{year}/doc`, `PUT /api/v1/tables/{code}/{year}/elements`
- 読み取りだけ: `jobs/monthly/tdnet_monthly_pdf_sidecar.py`, `GET /api/v1/tables`, `GET /api/v1/tables/{code}/{year}`, `GET /api/v1/tables/{code}/{year}/doc`, `PUT /api/v1/tables/{code}/{year}/values`, `PUT /api/v1/tables/{code}/{year}/values/batch`

### monthly_issuers

- 書き込み: `jobs/golden_db.py（共通・運用）`, `jobs/migrate.py（共通・運用）`, `jobs/monthly/ir_monthly_html_ingest.py`, `jobs/monthly/ir_monthly_json_ingest.py`, `jobs/monthly/ir_monthly_xls_ingest.py`, `jobs/monthly/tdnet_monthly_pdf_ingest.py`, `POST /api/v1/tables/{code}/ingest`, `PUT /api/v1/tables/{code}/industry`
- 読み取りだけ: `GET /api/v1/tables`

### monthly_tables

- 書き込み: `jobs/golden_db.py（共通・運用）`, `jobs/migrate.py（共通・運用）`, `jobs/monthly/ir_monthly_html_ingest.py`, `jobs/monthly/ir_monthly_json_ingest.py`, `jobs/monthly/ir_monthly_xls_ingest.py`, `jobs/monthly/tdnet_monthly_pdf_ingest.py`, `POST /api/v1/tables/{code}/ingest`, `POST /api/v1/tables/{code}/{year}/elements`
- 読み取りだけ: `jobs/monthly/tdnet_monthly_pdf_sidecar.py`, `DELETE /api/v1/tables/{code}/{year}/elements`, `GET /api/v1/tables`, `GET /api/v1/tables/{code}`, `GET /api/v1/tables/{code}/{year}`, `GET /api/v1/tables/{code}/{year}/doc`, `PUT /api/v1/tables/{code}/industry`, `PUT /api/v1/tables/{code}/{year}/doc`, `PUT /api/v1/tables/{code}/{year}/elements`, `PUT /api/v1/tables/{code}/{year}/values`, `PUT /api/v1/tables/{code}/{year}/values/batch`

### monthly_values

- 書き込み: `jobs/golden_db.py（共通・運用）`, `jobs/migrate.py（共通・運用）`, `jobs/monthly/ir_monthly_html_ingest.py`, `jobs/monthly/ir_monthly_json_ingest.py`, `jobs/monthly/ir_monthly_xls_ingest.py`, `jobs/monthly/monthly_names.py`, `jobs/monthly/tdnet_monthly_pdf_ingest.py`, `POST /api/v1/tables/{code}/ingest`, `PUT /api/v1/tables/{code}/{year}/doc`, `PUT /api/v1/tables/{code}/{year}/values`, `PUT /api/v1/tables/{code}/{year}/values/batch`
- 読み取りだけ: `jobs/monthly/tdnet_monthly_pdf_sidecar.py`, `DELETE /api/v1/tables/{code}/{year}/elements`, `GET /api/v1/tables`, `GET /api/v1/tables/{code}/{year}`, `GET /api/v1/tables/{code}/{year}/doc`, `PUT /api/v1/tables/{code}/{year}/elements`
