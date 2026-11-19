# 画面設計: 銘柄

**自動生成。手で直さない。** 読み方と作り方は [SCREENS.md](../../SCREENS.md)。

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

左から JS のファイル、API、表。API から表への実線は書く、点線は読むだけ。`stocks` を読むだけの線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["stock-deck.js"]:::js
  n1["stock-financials.js"]:::js
  n2["stock-price.js"]:::js
  n3["stock.js"]:::js
  n4["GET /api/earnings-performance/{code}"]:::api
  n5["GET /api/earnings/detail/{code}"]:::api
  n6["GET /api/stock/{code}/decks"]:::api
  n7["GET /api/stock/{code}/decks/{key}"]:::api
  n8["GET /api/stock/{code}/filings"]:::api
  n9["GET /api/stock/{code}/financials"]:::api
  n10["GET /api/stock/{code}/overview"]:::api
  n11["GET /api/stocks/search"]:::api
  n12[("briefing_documents")]:::tbl
  n13[("briefing_extracts")]:::tbl
  n14[("briefing_facts")]:::tbl
  n15[("briefing_pages")]:::tbl
  n16[("daily_prices")]:::tbl
  n17[("earnings_date_pubs")]:::tbl
  n18[("earnings_events")]:::tbl
  n19[("statements")]:::tbl
  n20[("tdnet_pdf_documents")]:::tbl
  n21[("xbrl_contexts")]:::tbl
  n22[("xbrl_documents")]:::tbl
  n23[("xbrl_elements")]:::tbl
  n24[("xbrl_facts")]:::tbl
  n25[("xbrl_filings")]:::tbl
  n26[("xbrl_financials")]:::tbl
  n27[("xbrl_presentation_roles")]:::tbl
  n28[("xbrl_std_items")]:::tbl
  n2 --> n4
  n4 -.- n18
  n3 --> n5
  n5 -.- n16
  n5 -.- n19
  n5 -.- n25
  n5 -.- n26
  n5 -.- n28
  n3 --> n6
  n6 -.- n12
  n6 -.- n13
  n0 --> n7
  n7 -.- n12
  n7 -.- n13
  n7 -.- n14
  n7 -.- n15
  n3 --> n8
  n8 -.- n20
  n8 -.- n22
  n8 -.- n25
  n1 --> n9
  n9 -.- n21
  n9 -.- n23
  n9 -.- n24
  n9 -.- n25
  n9 -.- n27
  n3 --> n10
  n10 -.- n16
  n10 -.- n17
  n10 -.- n19
  n3 --> n11
  classDef js fill:#f3e8ff,stroke:#7048e8
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
```

| API | 呼ぶ JS | 書く | 読むだけ |
|---|---|---|---|
| `GET /api/earnings-performance/{code}` | `stock-price.js` |  | `earnings_events` |
| `GET /api/earnings/detail/{code}` | `stock.js` |  | `daily_prices`, `statements`, `stocks`, `xbrl_filings`, `xbrl_financials`, `xbrl_std_items` |
| `GET /api/stock/{code}/decks` | `stock.js` |  | `briefing_documents`, `briefing_extracts`, `stocks` |
| `GET /api/stock/{code}/decks/{key}` | `stock-deck.js` |  | `briefing_documents`, `briefing_extracts`, `briefing_facts`, `briefing_pages` |
| `GET /api/stock/{code}/filings` | `stock.js` |  | `stocks`, `tdnet_pdf_documents`, `xbrl_documents`, `xbrl_filings` |
| `GET /api/stock/{code}/financials` | `stock-financials.js` |  | `stocks`, `xbrl_contexts`, `xbrl_elements`, `xbrl_facts`, `xbrl_filings`, `xbrl_presentation_roles` |
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

### GET /api/stock/{code}/overview

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |
| `today` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |

### GET /api/stocks/search

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `q` | query | string | ○ |  |
