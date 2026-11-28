# 画面設計: 銘柄

読み方と作り方は [SCREENS.md](../../SCREENS.md)。

## 画面

| 画面 | id | ハッシュ |
|---|---|---|
| 銘柄 | `stock` | `#stock` |
| 概要 | `stock-summary` | `#stock/:code`<br>`#stock/:code/summary` |
| 株価 | `stock-price` | `#stock/:code/price` |
| 業績詳細 | `stock-financials` | `#stock/:code/financials` |
| ニュース | `stock-news` | `#stock/:code/news` |
| ニュース詳細 | `stock-news-detail` | `#stock/:code/news/:newsId` |
| 株主 | `stock-holders` | `#stock/:code/holders` |
| 説明資料の中身 | `stock-deck-detail` | `#stock/:code/decks/:deckKey` |

## 呼ぶ API と表

左から JS のファイル、API、表。API から表への実線は書き込み、点線は読み取りだけ。`stocks` を読み取るだけの線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["stock-deck.js"]:::js
  n1["stock-financials.js"]:::js
  n2["stock-holders.js"]:::js
  n3["stock-price.js"]:::js
  n4["stock.js"]:::js
  n5["GET /api/earnings-performance/{code}"]:::api
  n6["GET /api/earnings/detail/{code}"]:::api
  n7["GET /api/stock/{code}/decks"]:::api
  n8["GET /api/stock/{code}/decks/{key}"]:::api
  n9["GET /api/stock/{code}/filings"]:::api
  n10["GET /api/stock/{code}/financials"]:::api
  n11["GET /api/stock/{code}/holders"]:::api
  n12["GET /api/stock/{code}/overview"]:::api
  n13["GET /api/stocks/search"]:::api
  n14[("briefing_documents")]:::tbl
  n15[("briefing_extracts")]:::tbl
  n16[("briefing_facts")]:::tbl
  n17[("briefing_pages")]:::tbl
  n18[("daily_prices")]:::tbl
  n19[("earnings_date_pubs")]:::tbl
  n20[("earnings_events")]:::tbl
  n21[("edinet_buyback_resolutions")]:::tbl
  n22[("edinet_buybacks")]:::tbl
  n23[("large_holding_reports")]:::tbl
  n24[("large_holdings")]:::tbl
  n25[("statements")]:::tbl
  n26[("tdnet_pdf_documents")]:::tbl
  n27[("xbrl_contexts")]:::tbl
  n28[("xbrl_documents")]:::tbl
  n29[("xbrl_elements")]:::tbl
  n30[("xbrl_facts")]:::tbl
  n31[("xbrl_filings")]:::tbl
  n32[("xbrl_financials")]:::tbl
  n33[("xbrl_presentation_roles")]:::tbl
  n34[("xbrl_std_items")]:::tbl
  n3 --> n5
  n5 -.- n20
  n4 --> n6
  n6 -.- n18
  n6 -.- n25
  n6 -.- n31
  n6 -.- n32
  n6 -.- n34
  n4 --> n7
  n7 -.- n14
  n7 -.- n15
  n0 --> n8
  n8 -.- n14
  n8 -.- n15
  n8 -.- n16
  n8 -.- n17
  n4 --> n9
  n9 -.- n26
  n9 -.- n28
  n9 -.- n31
  n1 --> n10
  n10 -.- n27
  n10 -.- n29
  n10 -.- n30
  n10 -.- n31
  n10 -.- n33
  n2 --> n11
  n11 -.- n21
  n11 -.- n22
  n11 -.- n23
  n11 -.- n24
  n4 --> n12
  n12 -.- n18
  n12 -.- n19
  n12 -.- n25
  n4 --> n13
  classDef js fill:#f3e8ff,stroke:#7048e8
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
```

| API | 呼ぶ JS | 書き込み | 読み取りだけ |
|---|---|---|---|
| `GET /api/earnings-performance/{code}` | `stock-price.js` |  | `earnings_events` |
| `GET /api/earnings/detail/{code}` | `stock.js` |  | `daily_prices`, `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/stock/{code}/decks` | `stock.js` |  | `briefing_documents`, `briefing_extracts`, `stocks` |
| `GET /api/stock/{code}/decks/{key}` | `stock-deck.js` |  | `briefing_documents`, `briefing_extracts`, `briefing_facts`, `briefing_pages` |
| `GET /api/stock/{code}/filings` | `stock.js` |  | `stocks`, `tdnet_pdf_documents`, `xbrl_documents`, `xbrl_filings` |
| `GET /api/stock/{code}/financials` | `stock-financials.js` |  | `stocks`, `xbrl_contexts`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings`, `xbrl_presentation_roles` |
| `GET /api/stock/{code}/holders` | `stock-holders.js` |  | `edinet_buyback_resolutions`, `edinet_buybacks`, `large_holding_reports`, `large_holdings`, `stocks` |
| `GET /api/stock/{code}/overview` | `stock.js` |  | `daily_prices`, `earnings_date_pubs`, `statements`, `stocks` |
| `GET /api/stocks/search` | `stock.js` |  | `stocks` |

## API の引数

### GET /api/earnings-performance/{code}

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |

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

### GET /api/stock/{code}/financials

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |
| `statement` | query | string（`^(BS\|PL\|CF)$`） |  | `BS` |
| `periods` | query | integer |  | `8` |
| `consolidated` | query | string（`^(auto\|true\|false)$`） |  | `auto` |

### GET /api/stock/{code}/holders

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |
| `today` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |

### GET /api/stock/{code}/overview

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |
| `today` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |

### GET /api/stocks/search

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `q` | query | string | ○ |  |
