# 画面設計: 大量保有報告書

読み方と作り方は [SCREENS.md](../../SCREENS.md)。

## 画面

| 画面 | id | ハッシュ |
|---|---|---|
| 大量保有報告書 | `holders` | `#holders` |
| 保有者 | `holders-detail` | `#holders/:edinet` |

## 呼ぶ API と表

左から JS のファイル、API、表。API から表への実線は書く、点線は読むだけ。`stocks` を読むだけの線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["holders.js"]:::js
  n1["GET /api/holders/activity"]:::api
  n2["GET /api/holders/search"]:::api
  n3["GET /api/holders/{edinet}"]:::api
  n4["GET /api/stock/{code}/holders"]:::api
  n5[("daily_prices")]:::tbl
  n6[("edinet_buyback_resolutions")]:::tbl
  n7[("edinet_buybacks")]:::tbl
  n8[("large_holding_reports")]:::tbl
  n9[("large_holdings")]:::tbl
  n0 --> n1
  n1 -.- n8
  n1 -.- n9
  n0 --> n2
  n2 -.- n8
  n2 -.- n9
  n0 --> n3
  n3 -.- n5
  n3 -.- n8
  n3 -.- n9
  n0 --> n4
  n4 -.- n6
  n4 -.- n7
  n4 -.- n8
  n4 -.- n9
  classDef js fill:#f3e8ff,stroke:#7048e8
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
```

| API | 呼ぶ JS | 書く | 読むだけ |
|---|---|---|---|
| `GET /api/holders/activity` | `holders.js` |  | `large_holding_reports`, `large_holdings`, `stocks` |
| `GET /api/holders/search` | `holders.js` |  | `large_holding_reports`, `large_holdings`, `stocks` |
| `GET /api/holders/{edinet}` | `holders.js` |  | `daily_prices`, `large_holding_reports`, `large_holdings`, `stocks` |
| `GET /api/stock/{code}/holders` | `holders.js` |  | `edinet_buyback_resolutions`, `edinet_buybacks`, `large_holding_reports`, `large_holdings`, `stocks` |

## API の引数

### GET /api/holders/activity

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `limit` | query | integer |  | `80` |
| `offset` | query | integer |  | `0` |
| `file_date` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |
| `q` | query | string |  | `` |

### GET /api/holders/search

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `q` | query | string |  | `` |

### GET /api/holders/{edinet}

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `edinet` | path | string | ○ |  |
| `today` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |

### GET /api/stock/{code}/holders

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |
| `today` | query | string（`^\d{4}-\d{2}-\d{2}$`） |  |  |
