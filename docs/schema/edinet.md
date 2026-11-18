# スキーマ: EDINET・大量保有

**自動生成。手で直さない。** 正は `app/models.py`。作り直しは `python -m tools.schema.render`。全体は [SCHEMA.md](../SCHEMA.md)。

図は主キーと参照の列だけ。実線は外部キー、点線はモデルで宣言しただけの参照（DB の制約は無い）。親の側が `}|` の線は、親の列が一意でない（同じ値の行が親に何行もある）。ほかの機能の表は主キーと指される列だけ出す。

```mermaid
---
config:
  layout: elk
---
erDiagram
  direction LR
  edinet_archives {
    String doc_id PK, FK
    Integer file_type PK
  }
  edinet_buyback_resolutions {
    String doc_id PK, FK
    Integer resolution_no PK
  }
  edinet_buybacks {
    String doc_id PK, FK
    String code FK
    String edinet_code FK
    String amends_doc_id FK
    String original_doc_id FK
  }
  edinet_documents {
    Date file_date PK, FK
    Integer seq_number PK
    String doc_id 
    String code FK
    String edinet_code FK
    String sec_code FK
    String issuer_edinet_code FK
    String subject_edinet_code FK
    String parent_doc_id FK
  }
  edinet_filers {
    String edinet_code PK
    String sec_code FK
  }
  edinet_list_days {
    Date file_date PK
  }
  edinet_tender_offer_docs {
    String doc_id PK, FK
    String tender_offer_id FK
    String filer_edinet_code FK
    String subject_edinet_code FK
    String parent_doc_id FK
  }
  edinet_tender_offers {
    String tender_offer_id PK, FK
    String offeror_edinet_code FK
    String offeror_code FK
    String target_edinet_code FK
    String target_code FK
    String latest_doc_id FK
  }
  large_holding_reports {
    String doc_id PK, FK
    String filer_edinet_code FK
    String issuer_edinet_code FK
    String issuer_sec_code FK
    String code FK
    String amends_doc_id FK
    String original_doc_id FK
  }
  large_holdings {
    String doc_id PK, FK
    Integer holder_no PK
    String edinet_code FK
  }
  stocks {
    String code PK
  }
  edinet_buybacks |o..o{ edinet_buybacks : amends_doc_id
  edinet_buybacks ||..o{ edinet_buyback_resolutions : doc_id
  edinet_buybacks ||..o{ edinet_buybacks : original_doc_id
  edinet_documents }o..o{ edinet_documents : parent_doc_id
  edinet_documents }|..o{ edinet_archives : doc_id
  edinet_documents }|..o{ edinet_buybacks : doc_id
  edinet_documents }|..o{ edinet_tender_offer_docs : doc_id
  edinet_documents }|..o{ large_holding_reports : doc_id
  edinet_filers |o..o{ edinet_buybacks : edinet_code
  edinet_filers |o..o{ edinet_documents : edinet_code
  edinet_filers |o..o{ edinet_documents : issuer_edinet_code
  edinet_filers |o..o{ edinet_documents : subject_edinet_code
  edinet_filers |o..o{ edinet_tender_offer_docs : filer_edinet_code
  edinet_filers |o..o{ edinet_tender_offer_docs : subject_edinet_code
  edinet_filers |o..o{ edinet_tender_offers : offeror_edinet_code
  edinet_filers |o..o{ edinet_tender_offers : target_edinet_code
  edinet_filers |o..o{ large_holding_reports : filer_edinet_code
  edinet_filers |o..o{ large_holding_reports : issuer_edinet_code
  edinet_filers |o..o{ large_holdings : edinet_code
  edinet_list_days ||..o{ edinet_documents : file_date
  edinet_tender_offer_docs |o..o{ edinet_tender_offer_docs : parent_doc_id
  edinet_tender_offer_docs |o..o{ edinet_tender_offers : latest_doc_id
  edinet_tender_offer_docs ||..o{ edinet_tender_offers : tender_offer_id
  edinet_tender_offers ||..o{ edinet_tender_offer_docs : tender_offer_id
  large_holding_reports |o..o{ large_holding_reports : amends_doc_id
  large_holding_reports ||..o{ large_holding_reports : original_doc_id
  large_holding_reports ||..o{ large_holdings : doc_id
  stocks |o..o{ edinet_buybacks : code
  stocks |o..o{ edinet_documents : code
  stocks |o..o{ edinet_documents : sec_code
  stocks |o..o{ edinet_filers : sec_code
  stocks |o..o{ edinet_tender_offers : offeror_code
  stocks |o..o{ edinet_tender_offers : target_code
  stocks |o..o{ large_holding_reports : code
  stocks |o..o{ large_holding_reports : issuer_sec_code
```

## テーブル

### edinet_archives

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK / → `edinet_documents.doc_id`（制約なし） |  |  |
| `file_type` | INTEGER |  | PK |  |  |
| `doc_type_code` | VARCHAR(3) | 可 |  |  |  |
| `rel_path` | TEXT |  |  |  |  |
| `status` | VARCHAR(8) |  |  |  |  |
| `bytes` | INTEGER | 可 |  |  |  |
| `sha256` | VARCHAR(64) | 可 |  |  |  |
| `saved_at` | DATETIME |  |  |  |  |

### edinet_buyback_resolutions

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK / → `edinet_buybacks.doc_id`（制約なし） |  |  |
| `resolution_no` | INTEGER |  | PK |  |  |
| `kind` | VARCHAR |  |  | `board` / `shareholders` |  |
| `body` | VARCHAR | 可 |  |  |  |
| `resolution_date` | DATE | 可 |  |  |  |
| `acquire_from` | DATE | 可 |  |  |  |
| `acquire_to` | DATE | 可 |  |  |  |
| `limit_shares` | BIGINT | 可 |  |  |  |
| `limit_amount` | BIGINT | 可 |  |  |  |
| `month_shares` | BIGINT | 可 |  |  |  |
| `month_amount` | BIGINT | 可 |  |  |  |
| `cumulative_shares` | BIGINT | 可 |  |  |  |
| `cumulative_amount` | BIGINT | 可 |  |  |  |
| `progress_shares` | FLOAT | 可 |  |  |  |
| `progress_amount` | FLOAT | 可 |  |  |  |
| `label` | TEXT | 可 |  |  |  |

### edinet_buybacks

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK / → `edinet_documents.doc_id`（制約なし） |  |  |
| `file_date` | DATE |  |  |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |  |
| `doc_type_code` | VARCHAR(3) |  |  |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |  |
| `code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `filer_name` | VARCHAR | 可 |  |  |  |
| `report_month` | VARCHAR(7) | 可 |  |  |  |
| `period_start` | DATE | 可 |  |  |  |
| `period_end` | DATE | 可 |  |  |  |
| `issued_shares` | BIGINT | 可 |  |  |  |
| `treasury_shares` | BIGINT | 可 |  |  |  |
| `resolution_count` | INTEGER | 可 |  |  |  |
| `month_shares` | BIGINT | 可 |  |  |  |
| `month_amount` | BIGINT | 可 |  |  |  |
| `is_amendment` | BOOLEAN |  |  |  | `False` |
| `amends_doc_id` | VARCHAR(8) | 可 | → `edinet_buybacks.doc_id`（制約なし） |  |  |
| `original_doc_id` | VARCHAR(8) |  | → `edinet_buybacks.doc_id`（制約なし） |  |  |
| `is_latest` | BOOLEAN |  |  |  | `False` |
| `withdrawn` | BOOLEAN |  |  |  | `False` |
| `parse_status` | VARCHAR |  |  | `parsed` / `meta` / `failed` |  |
| `parse_error` | TEXT | 可 |  |  |  |
| `fetched_at` | DATETIME |  |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_buybacks_code` | code, report_month |  |
| `ix_edinet_buybacks_edinet_code` | edinet_code |  |
| `ix_edinet_buybacks_original_doc_id` | original_doc_id |  |

### edinet_documents

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `file_date` | DATE |  | PK / → `edinet_list_days.file_date`（制約なし） |  |  |
| `seq_number` | INTEGER |  | PK |  |  |
| `doc_id` | VARCHAR(8) |  |  |  |  |
| `code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `sec_code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `jcn` | VARCHAR(13) | 可 |  |  |  |
| `filer_name` | VARCHAR | 可 |  |  |  |
| `fund_code` | VARCHAR(6) | 可 |  |  |  |
| `ordinance_code` | VARCHAR(3) | 可 |  |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |  |
| `doc_type_code` | VARCHAR(3) | 可 |  |  |  |
| `period_start` | DATE | 可 |  |  |  |
| `period_end` | DATE | 可 |  |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |  |
| `doc_description` | TEXT | 可 |  |  |  |
| `issuer_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `subject_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `subsidiary_edinet_codes` | TEXT | 可 |  |  |  |
| `current_report_reasons` | TEXT | 可 |  |  |  |
| `parent_doc_id` | VARCHAR(8) | 可 | → `edinet_documents.doc_id`（制約なし） |  |  |
| `ope_datetime` | DATETIME | 可 |  |  |  |
| `withdrawal_status` | VARCHAR(1) |  |  |  |  |
| `doc_info_edit_status` | VARCHAR(1) |  |  |  |  |
| `disclosure_status` | VARCHAR(1) |  |  |  |  |
| `xbrl_flag` | BOOLEAN |  |  |  |  |
| `pdf_flag` | BOOLEAN |  |  |  |  |
| `attach_doc_flag` | BOOLEAN |  |  |  |  |
| `english_doc_flag` | BOOLEAN |  |  |  |  |
| `csv_flag` | BOOLEAN |  |  |  |  |
| `legal_status` | VARCHAR(1) |  |  |  |  |
| `fetched_at` | DATETIME |  |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_documents_code` | code |  |
| `ix_edinet_documents_code_type` | code, doc_type_code, submit_datetime |  |
| `ix_edinet_documents_doc_id` | doc_id |  |
| `ix_edinet_documents_doc_type_code` | doc_type_code |  |
| `ix_edinet_documents_edinet_code` | edinet_code |  |
| `ix_edinet_documents_issuer_edinet_code` | issuer_edinet_code |  |
| `ix_edinet_documents_subject_edinet_code` | subject_edinet_code |  |

### edinet_filers

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `edinet_code` | VARCHAR(6) |  | PK |  |  |
| `sec_code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `filer_name` | VARCHAR | 可 |  |  |  |
| `filer_name_en` | VARCHAR | 可 |  |  |  |
| `filer_type` | VARCHAR | 可 |  |  |  |
| `listed` | VARCHAR | 可 |  |  |  |
| `is_consolidated` | VARCHAR | 可 |  |  |  |
| `capital` | VARCHAR | 可 |  |  |  |
| `fiscal_year_end` | VARCHAR | 可 |  |  |  |
| `industry` | VARCHAR | 可 |  |  |  |
| `jcn` | VARCHAR(13) | 可 |  |  |  |
| `list_date` | DATE | 可 |  |  |  |
| `updated_at` | DATETIME |  |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_filers_sec_code` | sec_code |  |

### edinet_list_days

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `file_date` | DATE |  | PK |  |  |
| `count` | INTEGER |  |  |  |  |
| `max_seq` | INTEGER | 可 |  |  |  |
| `process_datetime` | DATETIME | 可 |  |  |  |
| `finalized` | BOOLEAN |  |  |  |  |
| `fetched_at` | DATETIME |  |  |  |  |

### edinet_tender_offer_docs

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK / → `edinet_documents.doc_id`（制約なし） |  |  |
| `tender_offer_id` | VARCHAR(8) |  | → `edinet_tender_offers.tender_offer_id`（制約なし） |  |  |
| `file_date` | DATE |  |  |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |  |
| `doc_type_code` | VARCHAR(3) |  |  |  |  |
| `ordinance_code` | VARCHAR(3) | 可 |  |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |  |
| `title` | TEXT | 可 |  |  |  |
| `filer_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `filer_name` | VARCHAR | 可 |  |  |  |
| `subject_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `parent_doc_id` | VARCHAR(8) | 可 | → `edinet_tender_offer_docs.doc_id`（制約なし） |  |  |
| `withdrawn` | BOOLEAN |  |  |  | `False` |
| `period_start` | DATE | 可 |  |  |  |
| `period_end` | DATE | 可 |  |  |  |
| `price` | FLOAT | 可 |  |  |  |
| `planned_shares` | BIGINT | 可 |  |  |  |
| `min_shares` | BIGINT | 可 |  |  |  |
| `max_shares` | BIGINT | 可 |  |  |  |
| `holding_ratio_after` | FLOAT | 可 |  |  |  |
| `target_name` | VARCHAR | 可 |  |  |  |
| `opinion` | VARCHAR | 可 |  |  |  |
| `recommendation` | VARCHAR | 可 |  |  |  |
| `opinion_text` | TEXT | 可 |  |  |  |
| `success` | BOOLEAN | 可 |  |  |  |
| `tendered_shares` | BIGINT | 可 |  |  |  |
| `purchased_shares` | BIGINT | 可 |  |  |  |
| `parse_status` | VARCHAR |  |  | `parsed` / `meta` / `failed` |  |
| `parse_error` | TEXT | 可 |  |  |  |
| `fetched_at` | DATETIME |  |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_tender_offer_docs_tender_offer_id` | tender_offer_id |  |

### edinet_tender_offers

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `tender_offer_id` | VARCHAR(8) |  | PK / → `edinet_tender_offer_docs.doc_id`（制約なし） |  |  |
| `is_issuer` | BOOLEAN |  |  |  | `False` |
| `offeror_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `offeror_name` | VARCHAR | 可 |  |  |  |
| `offeror_code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `target_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `target_name` | VARCHAR | 可 |  |  |  |
| `target_code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `announced_date` | DATE | 可 |  |  |  |
| `period_start` | DATE | 可 |  |  |  |
| `period_end` | DATE | 可 |  |  |  |
| `price` | FLOAT | 可 |  |  |  |
| `price_initial` | FLOAT | 可 |  |  |  |
| `planned_shares` | BIGINT | 可 |  |  |  |
| `min_shares` | BIGINT | 可 |  |  |  |
| `max_shares` | BIGINT | 可 |  |  |  |
| `holding_ratio_after` | FLOAT | 可 |  |  |  |
| `opinion` | VARCHAR | 可 |  | `support` / `oppose` / `neutral` / `reserved` |  |
| `recommendation` | VARCHAR | 可 |  | `recommend` / `leave` / `not_recommend` |  |
| `status` | VARCHAR |  |  | `open` / `succeeded` / `failed` / `withdrawn` |  |
| `tendered_shares` | BIGINT | 可 |  |  |  |
| `purchased_shares` | BIGINT | 可 |  |  |  |
| `result_date` | DATE | 可 |  |  |  |
| `latest_doc_id` | VARCHAR(8) | 可 | → `edinet_tender_offer_docs.doc_id`（制約なし） |  |  |
| `doc_count` | INTEGER | 可 |  |  |  |
| `updated_at` | DATETIME |  |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_tender_offers_offeror_edinet_code` | offeror_edinet_code |  |
| `ix_edinet_tender_offers_target_code` | target_code |  |
| `ix_edinet_tender_offers_target_edinet_code` | target_edinet_code |  |

### large_holding_reports

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK / → `edinet_documents.doc_id`（制約なし） |  |  |
| `file_date` | DATE |  |  |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |  |
| `doc_type_code` | VARCHAR(3) |  |  |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |  |
| `title` | TEXT | 可 |  |  |  |
| `filer_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `filer_name` | VARCHAR | 可 |  |  |  |
| `issuer_edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `issuer_sec_code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `issuer_name` | VARCHAR | 可 |  |  |  |
| `code` | VARCHAR(5) | 可 | → `stocks.code`（制約なし） |  |  |
| `obligation_date` | DATE | 可 |  |  |  |
| `filing_date` | DATE | 可 |  |  |  |
| `reason` | TEXT | 可 |  |  |  |
| `arrangement` | VARCHAR | 可 |  |  |  |
| `holder_count` | INTEGER | 可 |  |  |  |
| `shares_held` | BIGINT | 可 |  |  |  |
| `outstanding_shares` | BIGINT | 可 |  |  |  |
| `holding_ratio` | FLOAT | 可 |  |  |  |
| `holding_ratio_prev` | FLOAT | 可 |  |  |  |
| `joint_holders_text` | TEXT | 可 |  |  |  |
| `is_amendment` | BOOLEAN |  |  |  | `False` |
| `amends_doc_id` | VARCHAR(8) | 可 | → `large_holding_reports.doc_id`（制約なし） |  |  |
| `original_doc_id` | VARCHAR(8) |  | → `large_holding_reports.doc_id`（制約なし） |  |  |
| `is_latest` | BOOLEAN |  |  |  | `False` |
| `withdrawn` | BOOLEAN |  |  |  | `False` |
| `parse_status` | VARCHAR |  |  | `parsed` / `failed` |  |
| `parse_error` | TEXT | 可 |  |  |  |
| `fetched_at` | DATETIME |  |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_large_holding_reports_code` | code, obligation_date |  |
| `ix_large_holding_reports_file_date` | file_date |  |
| `ix_large_holding_reports_filer_edinet_code` | filer_edinet_code |  |
| `ix_large_holding_reports_latest_file` | is_latest, file_date |  |
| `ix_large_holding_reports_original_doc_id` | original_doc_id |  |

### large_holdings

| 列 | 型 | NULL | キー | 値 | 既定 |
|---|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK / → `large_holding_reports.doc_id`（制約なし） |  |  |
| `holder_no` | INTEGER |  | PK |  |  |
| `edinet_code` | VARCHAR(6) | 可 | → `edinet_filers.edinet_code`（制約なし） |  |  |
| `name` | VARCHAR | 可 |  |  |  |
| `holder_type` | VARCHAR | 可 |  |  |  |
| `shares_held` | BIGINT | 可 |  |  |  |
| `outstanding_shares` | BIGINT | 可 |  |  |  |
| `holding_ratio` | FLOAT | 可 |  |  |  |
| `holding_ratio_prev` | FLOAT | 可 |  |  |  |
| `base_date` | DATE | 可 |  |  |  |
| `purpose` | TEXT | 可 |  |  |  |
| `important_proposal` | TEXT | 可 |  |  |  |
| `own_funds` | BIGINT | 可 |  |  |  |
| `borrowings` | BIGINT | 可 |  |  |  |
| `other_funds` | BIGINT | 可 |  |  |  |
| `total_funds` | BIGINT | 可 |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_large_holdings_edinet_code` | edinet_code |  |
