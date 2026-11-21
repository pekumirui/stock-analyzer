# 画面設計: 決算速報

読み方と作り方は [SCREENS.md](../../SCREENS.md)。

## 画面

| 画面 | id | ハッシュ |
|---|---|---|
| 決算速報 | `earnings` | `#earnings` |

## 呼ぶ API と表

左から JS のファイル、API、表。API から表への実線は書き込み、点線は読み取りだけ。`stocks` を読み取るだけの線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["earnings.js"]:::js
  n1["GET /api/earnings"]:::api
  n2["GET /api/earnings/calendar"]:::api
  n3["GET /api/earnings/detail/{code}"]:::api
  n4["GET /api/stock/{code}/decks"]:::api
  n5["GET /api/stock/{code}/decks/{key}"]:::api
  n6["GET /api/stock/{code}/filings"]:::api
  n7["GET /api/stocks/search"]:::api
  n8[("briefing_documents")]:::tbl
  n9[("briefing_extracts")]:::tbl
  n10[("briefing_facts")]:::tbl
  n11[("briefing_pages")]:::tbl
  n12[("daily_prices")]:::tbl
  n13[("earnings_date_fetches")]:::tbl
  n14[("earnings_date_pubs")]:::tbl
  n15[("pts_snapshots")]:::tbl
  n16[("statements")]:::tbl
  n17[("tdnet_pdf_documents")]:::tbl
  n18[("xbrl_documents")]:::tbl
  n19[("xbrl_filings")]:::tbl
  n20[("xbrl_financials")]:::tbl
  n21[("xbrl_std_items")]:::tbl
  n0 --> n1
  n1 -.- n12
  n1 -.- n13
  n1 -.- n14
  n1 -.- n15
  n1 -.- n16
  n1 -.- n19
  n1 -.- n20
  n1 -.- n21
  n0 --> n2
  n2 -.- n14
  n2 -.- n16
  n0 --> n3
  n3 -.- n12
  n3 -.- n16
  n3 -.- n19
  n3 -.- n20
  n3 -.- n21
  n0 --> n4
  n4 -.- n8
  n4 -.- n9
  n0 --> n5
  n5 -.- n8
  n5 -.- n9
  n5 -.- n10
  n5 -.- n11
  n0 --> n6
  n6 -.- n17
  n6 -.- n18
  n6 -.- n19
  n0 --> n7
  classDef js fill:#f3e8ff,stroke:#7048e8
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
```

| API | 呼ぶ JS | 書き込み | 読み取りだけ |
|---|---|---|---|
| `GET /api/earnings` | `earnings.js` |  | `daily_prices`, `earnings_date_fetches`, `earnings_date_pubs`, `pts_snapshots`, `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/earnings/calendar` | `earnings.js` |  | `earnings_date_pubs`, `statements`, `stocks` |
| `GET /api/earnings/detail/{code}` | `earnings.js` |  | `daily_prices`, `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/stock/{code}/decks` | `earnings.js` |  | `briefing_documents`, `briefing_extracts`, `stocks` |
| `GET /api/stock/{code}/decks/{key}` | `earnings.js` |  | `briefing_documents`, `briefing_extracts`, `briefing_facts`, `briefing_pages` |
| `GET /api/stock/{code}/filings` | `earnings.js` |  | `stocks`, `tdnet_pdf_documents`, `xbrl_documents`, `xbrl_filings` |
| `GET /api/stocks/search` | `earnings.js` |  | `stocks` |

## API の引数

### GET /api/earnings

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `date` | query | string | ○ |  |
| `today` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |

### GET /api/earnings/calendar

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `month` | query | string（`^\d{4}-\d{2}$`） | ○ |  |
| `today` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |

### GET /api/earnings/detail/{code}

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |
| `date` | query | string |  |  |
| `disclosure_no` | query | string |  |  |
| `rev_source` | query | string（`^(jquants\|xbrl)$`） |  |  |

### GET /api/stock/{code}/decks

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |

### GET /api/stock/{code}/decks/{key}

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |
| `key` | path | string | ○ |  |

### GET /api/stock/{code}/filings

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |

### GET /api/stocks/search

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `q` | query | string | ○ |  |
