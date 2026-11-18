# スキーマ: EDINET・大量保有

**自動生成。手で直さない。** 正は `app/models.py`。作り直しは `python -m tools.schema.render`。全体は [SCHEMA.md](../SCHEMA.md)。

図は主キーと外部キーの列だけ。線は外部キー（列名つき）。ほかの機能の表は主キーだけ出す。

```mermaid
erDiagram
  edinet_archives {
    String doc_id PK
    Integer file_type PK
  }
  edinet_buyback_resolutions {
    String doc_id PK
    Integer resolution_no PK
  }
  edinet_buybacks {
    String doc_id PK
  }
  edinet_documents {
    Date file_date PK
    Integer seq_number PK
  }
  edinet_filers {
    String edinet_code PK
  }
  edinet_list_days {
    Date file_date PK
  }
  edinet_tender_offer_docs {
    String doc_id PK
  }
  edinet_tender_offers {
    String tender_offer_id PK
  }
  large_holding_reports {
    String doc_id PK
  }
  large_holdings {
    String doc_id PK
    Integer holder_no PK
  }
```

## テーブル

### edinet_archives

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK |  |
| `file_type` | INTEGER |  | PK |  |
| `doc_type_code` | VARCHAR(3) | 可 |  |  |
| `rel_path` | TEXT |  |  |  |
| `status` | VARCHAR(8) |  |  |  |
| `bytes` | INTEGER | 可 |  |  |
| `sha256` | VARCHAR(64) | 可 |  |  |
| `saved_at` | DATETIME |  |  |  |

### edinet_buyback_resolutions

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK |  |
| `resolution_no` | INTEGER |  | PK |  |
| `kind` | VARCHAR |  |  |  |
| `body` | VARCHAR | 可 |  |  |
| `resolution_date` | DATE | 可 |  |  |
| `acquire_from` | DATE | 可 |  |  |
| `acquire_to` | DATE | 可 |  |  |
| `limit_shares` | BIGINT | 可 |  |  |
| `limit_amount` | BIGINT | 可 |  |  |
| `month_shares` | BIGINT | 可 |  |  |
| `month_amount` | BIGINT | 可 |  |  |
| `cumulative_shares` | BIGINT | 可 |  |  |
| `cumulative_amount` | BIGINT | 可 |  |  |
| `progress_shares` | FLOAT | 可 |  |  |
| `progress_amount` | FLOAT | 可 |  |  |
| `label` | TEXT | 可 |  |  |

### edinet_buybacks

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK |  |
| `file_date` | DATE |  |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |
| `doc_type_code` | VARCHAR(3) |  |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |
| `code` | VARCHAR(5) | 可 |  |  |
| `edinet_code` | VARCHAR(6) | 可 |  |  |
| `filer_name` | VARCHAR | 可 |  |  |
| `report_month` | VARCHAR(7) | 可 |  |  |
| `period_start` | DATE | 可 |  |  |
| `period_end` | DATE | 可 |  |  |
| `issued_shares` | BIGINT | 可 |  |  |
| `treasury_shares` | BIGINT | 可 |  |  |
| `resolution_count` | INTEGER | 可 |  |  |
| `month_shares` | BIGINT | 可 |  |  |
| `month_amount` | BIGINT | 可 |  |  |
| `is_amendment` | BOOLEAN |  |  | `False` |
| `amends_doc_id` | VARCHAR(8) | 可 |  |  |
| `original_doc_id` | VARCHAR(8) |  |  |  |
| `is_latest` | BOOLEAN |  |  | `False` |
| `withdrawn` | BOOLEAN |  |  | `False` |
| `parse_status` | VARCHAR |  |  |  |
| `parse_error` | TEXT | 可 |  |  |
| `fetched_at` | DATETIME |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_buybacks_code` | code, report_month |  |
| `ix_edinet_buybacks_edinet_code` | edinet_code |  |
| `ix_edinet_buybacks_original_doc_id` | original_doc_id |  |

### edinet_documents

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `file_date` | DATE |  | PK |  |
| `seq_number` | INTEGER |  | PK |  |
| `doc_id` | VARCHAR(8) |  |  |  |
| `code` | VARCHAR(5) | 可 |  |  |
| `edinet_code` | VARCHAR(6) | 可 |  |  |
| `sec_code` | VARCHAR(5) | 可 |  |  |
| `jcn` | VARCHAR(13) | 可 |  |  |
| `filer_name` | VARCHAR | 可 |  |  |
| `fund_code` | VARCHAR(6) | 可 |  |  |
| `ordinance_code` | VARCHAR(3) | 可 |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |
| `doc_type_code` | VARCHAR(3) | 可 |  |  |
| `period_start` | DATE | 可 |  |  |
| `period_end` | DATE | 可 |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |
| `doc_description` | TEXT | 可 |  |  |
| `issuer_edinet_code` | VARCHAR(6) | 可 |  |  |
| `subject_edinet_code` | VARCHAR(6) | 可 |  |  |
| `subsidiary_edinet_codes` | TEXT | 可 |  |  |
| `current_report_reasons` | TEXT | 可 |  |  |
| `parent_doc_id` | VARCHAR(8) | 可 |  |  |
| `ope_datetime` | DATETIME | 可 |  |  |
| `withdrawal_status` | VARCHAR(1) |  |  |  |
| `doc_info_edit_status` | VARCHAR(1) |  |  |  |
| `disclosure_status` | VARCHAR(1) |  |  |  |
| `xbrl_flag` | BOOLEAN |  |  |  |
| `pdf_flag` | BOOLEAN |  |  |  |
| `attach_doc_flag` | BOOLEAN |  |  |  |
| `english_doc_flag` | BOOLEAN |  |  |  |
| `csv_flag` | BOOLEAN |  |  |  |
| `legal_status` | VARCHAR(1) |  |  |  |
| `fetched_at` | DATETIME |  |  |  |

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

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `edinet_code` | VARCHAR(6) |  | PK |  |
| `sec_code` | VARCHAR(5) | 可 |  |  |
| `filer_name` | VARCHAR | 可 |  |  |
| `filer_name_en` | VARCHAR | 可 |  |  |
| `filer_type` | VARCHAR | 可 |  |  |
| `listed` | VARCHAR | 可 |  |  |
| `is_consolidated` | VARCHAR | 可 |  |  |
| `capital` | VARCHAR | 可 |  |  |
| `fiscal_year_end` | VARCHAR | 可 |  |  |
| `industry` | VARCHAR | 可 |  |  |
| `jcn` | VARCHAR(13) | 可 |  |  |
| `list_date` | DATE | 可 |  |  |
| `updated_at` | DATETIME |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_filers_sec_code` | sec_code |  |

### edinet_list_days

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `file_date` | DATE |  | PK |  |
| `count` | INTEGER |  |  |  |
| `max_seq` | INTEGER | 可 |  |  |
| `process_datetime` | DATETIME | 可 |  |  |
| `finalized` | BOOLEAN |  |  |  |
| `fetched_at` | DATETIME |  |  |  |

### edinet_tender_offer_docs

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK |  |
| `tender_offer_id` | VARCHAR(8) |  |  |  |
| `file_date` | DATE |  |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |
| `doc_type_code` | VARCHAR(3) |  |  |  |
| `ordinance_code` | VARCHAR(3) | 可 |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |
| `title` | TEXT | 可 |  |  |
| `filer_edinet_code` | VARCHAR(6) | 可 |  |  |
| `filer_name` | VARCHAR | 可 |  |  |
| `subject_edinet_code` | VARCHAR(6) | 可 |  |  |
| `parent_doc_id` | VARCHAR(8) | 可 |  |  |
| `withdrawn` | BOOLEAN |  |  | `False` |
| `period_start` | DATE | 可 |  |  |
| `period_end` | DATE | 可 |  |  |
| `price` | FLOAT | 可 |  |  |
| `planned_shares` | BIGINT | 可 |  |  |
| `min_shares` | BIGINT | 可 |  |  |
| `max_shares` | BIGINT | 可 |  |  |
| `holding_ratio_after` | FLOAT | 可 |  |  |
| `target_name` | VARCHAR | 可 |  |  |
| `opinion` | VARCHAR | 可 |  |  |
| `recommendation` | VARCHAR | 可 |  |  |
| `opinion_text` | TEXT | 可 |  |  |
| `success` | BOOLEAN | 可 |  |  |
| `tendered_shares` | BIGINT | 可 |  |  |
| `purchased_shares` | BIGINT | 可 |  |  |
| `parse_status` | VARCHAR |  |  |  |
| `parse_error` | TEXT | 可 |  |  |
| `fetched_at` | DATETIME |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_tender_offer_docs_tender_offer_id` | tender_offer_id |  |

### edinet_tender_offers

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `tender_offer_id` | VARCHAR(8) |  | PK |  |
| `is_issuer` | BOOLEAN |  |  | `False` |
| `offeror_edinet_code` | VARCHAR(6) | 可 |  |  |
| `offeror_name` | VARCHAR | 可 |  |  |
| `offeror_code` | VARCHAR(5) | 可 |  |  |
| `target_edinet_code` | VARCHAR(6) | 可 |  |  |
| `target_name` | VARCHAR | 可 |  |  |
| `target_code` | VARCHAR(5) | 可 |  |  |
| `announced_date` | DATE | 可 |  |  |
| `period_start` | DATE | 可 |  |  |
| `period_end` | DATE | 可 |  |  |
| `price` | FLOAT | 可 |  |  |
| `price_initial` | FLOAT | 可 |  |  |
| `planned_shares` | BIGINT | 可 |  |  |
| `min_shares` | BIGINT | 可 |  |  |
| `max_shares` | BIGINT | 可 |  |  |
| `holding_ratio_after` | FLOAT | 可 |  |  |
| `opinion` | VARCHAR | 可 |  |  |
| `recommendation` | VARCHAR | 可 |  |  |
| `status` | VARCHAR |  |  |  |
| `tendered_shares` | BIGINT | 可 |  |  |
| `purchased_shares` | BIGINT | 可 |  |  |
| `result_date` | DATE | 可 |  |  |
| `latest_doc_id` | VARCHAR(8) | 可 |  |  |
| `doc_count` | INTEGER | 可 |  |  |
| `updated_at` | DATETIME |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_edinet_tender_offers_offeror_edinet_code` | offeror_edinet_code |  |
| `ix_edinet_tender_offers_target_code` | target_code |  |
| `ix_edinet_tender_offers_target_edinet_code` | target_edinet_code |  |

### large_holding_reports

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK |  |
| `file_date` | DATE |  |  |  |
| `submit_datetime` | DATETIME | 可 |  |  |
| `doc_type_code` | VARCHAR(3) |  |  |  |
| `form_code` | VARCHAR(6) | 可 |  |  |
| `title` | TEXT | 可 |  |  |
| `filer_edinet_code` | VARCHAR(6) | 可 |  |  |
| `filer_name` | VARCHAR | 可 |  |  |
| `issuer_edinet_code` | VARCHAR(6) | 可 |  |  |
| `issuer_sec_code` | VARCHAR(5) | 可 |  |  |
| `issuer_name` | VARCHAR | 可 |  |  |
| `code` | VARCHAR(5) | 可 |  |  |
| `obligation_date` | DATE | 可 |  |  |
| `filing_date` | DATE | 可 |  |  |
| `reason` | TEXT | 可 |  |  |
| `arrangement` | VARCHAR | 可 |  |  |
| `holder_count` | INTEGER | 可 |  |  |
| `shares_held` | BIGINT | 可 |  |  |
| `outstanding_shares` | BIGINT | 可 |  |  |
| `holding_ratio` | FLOAT | 可 |  |  |
| `holding_ratio_prev` | FLOAT | 可 |  |  |
| `joint_holders_text` | TEXT | 可 |  |  |
| `is_amendment` | BOOLEAN |  |  | `False` |
| `amends_doc_id` | VARCHAR(8) | 可 |  |  |
| `original_doc_id` | VARCHAR(8) |  |  |  |
| `is_latest` | BOOLEAN |  |  | `False` |
| `withdrawn` | BOOLEAN |  |  | `False` |
| `parse_status` | VARCHAR |  |  |  |
| `parse_error` | TEXT | 可 |  |  |
| `fetched_at` | DATETIME |  |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_large_holding_reports_code` | code, obligation_date |  |
| `ix_large_holding_reports_file_date` | file_date |  |
| `ix_large_holding_reports_filer_edinet_code` | filer_edinet_code |  |
| `ix_large_holding_reports_latest_file` | is_latest, file_date |  |
| `ix_large_holding_reports_original_doc_id` | original_doc_id |  |

### large_holdings

| 列 | 型 | NULL | キー | 既定 |
|---|---|---|---|---|
| `doc_id` | VARCHAR(8) |  | PK |  |
| `holder_no` | INTEGER |  | PK |  |
| `edinet_code` | VARCHAR(6) | 可 |  |  |
| `name` | VARCHAR | 可 |  |  |
| `holder_type` | VARCHAR | 可 |  |  |
| `shares_held` | BIGINT | 可 |  |  |
| `outstanding_shares` | BIGINT | 可 |  |  |
| `holding_ratio` | FLOAT | 可 |  |  |
| `holding_ratio_prev` | FLOAT | 可 |  |  |
| `base_date` | DATE | 可 |  |  |
| `purpose` | TEXT | 可 |  |  |
| `important_proposal` | TEXT | 可 |  |  |
| `own_funds` | BIGINT | 可 |  |  |
| `borrowings` | BIGINT | 可 |  |  |
| `other_funds` | BIGINT | 可 |  |  |
| `total_funds` | BIGINT | 可 |  |  |

| 索引・一意 | 列 | 一意 |
|---|---|---|
| `ix_large_holdings_edinet_code` | edinet_code |  |
