# 画面設計

- 画面は `static/js/screens.js` の `ROUTES` と `SCREEN_LABELS`、画面遷移は `static/js/*.js` の画面を移る関数の呼び出しとリンクの `href`、画面が呼ぶ API は `static/js/*.js` の `/api/…` の文字列、API の引数は FastAPI の OpenAPI、読み書きする表は `docs/dataflow/entries.json` から拾う
- API の応答の形は載らない（ルートが応答の型を宣言していない）。形は各タブの手書きの画面仕様にある
- 読み書きする表はテストが通った道だけなので下限。読み方は [DATA_FLOW.md](DATA_FLOW.md)

## タブごとのページ

タブの画面、画面が呼ぶ API と表の図、API の引数。API は呼んでいる JS のファイル名の頭（`stock-price.js` なら銘柄）でタブに分ける。

| タブ | 画面 | JS | 呼ぶ API |
|---|---|---|---|
| [決算速報](screen/gen/earnings.md) | 決算速報 | `earnings.js` | 7 |
| [銘柄](screen/gen/stock.md) | 銘柄、概要、株価、業績詳細、ニュース、ニュース詳細、株主、説明資料の中身 | `stock-deck.js`, `stock-financials.js`, `stock-holders.js`, `stock-price.js`, `stock.js` | 9 |
| [スクリーナー](screen/gen/screener.md) | スクリーナー | `screener.js` | 1 |
| [月次](screen/gen/monthly.md) | 月次、月次詳細 | `monthly.js` | 3 |
| [サプライ](screen/gen/supply.md) | サプライ | `supply.js` | 4 |
| [ランキング](screen/gen/ranking.md) | ランキング、PTS | `pts.js`, `ranking.js` | 2 |
| [大量保有報告書](screen/gen/holders.md) | 大量保有報告書、保有者 | `holders.js` | 3 |
| [共通](screen/gen/common.md) |  | `chart-core.js` | 2 |

## 画面遷移

枠はタブ、箱は画面（名前とハッシュ）。矢印は、元の画面の JS が先の画面を開く呼び出し（`gotoChart`, `gotoHolder`, `gotoHolderFromStock`, `openHolder`, `showMonthlyDetail`, `openStockNewsDeck`）か、先の画面のハッシュへのリンクを持っていること。元の画面は呼び出しのある JS のファイル名で決める（`stock-holders.js` なら株主、`pts.js` なら PTS、それ以外はタブの最初の画面）。タブの切り替えと、銘柄の中のタブの切り替えはどこからでもできるので線にしない。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  subgraph earnings["決算速報"]
    s0["決算速報<br>#earnings"]
  end
  subgraph stock["銘柄"]
    s1["銘柄<br>#stock"]
    s2["概要<br>#stock/:code/summary"]
    s3["株価<br>#stock/:code/price"]
    s4["業績詳細<br>#stock/:code/financials"]
    s5["ニュース<br>#stock/:code/news"]
    s6["ニュース詳細<br>#stock/:code/news/:newsId"]
    s7["株主<br>#stock/:code/holders"]
    s8["説明資料の中身<br>#stock/:code/decks/:deckKey"]
  end
  subgraph screener["スクリーナー"]
    s9["スクリーナー<br>#screener"]
  end
  subgraph monthly["月次"]
    s10["月次<br>#monthly"]
    s11["月次詳細<br>#monthly/:code"]
  end
  subgraph supply["サプライ"]
    s12["サプライ<br>#supply/:id?"]
  end
  subgraph ranking["ランキング"]
    s13["ランキング<br>#ranking"]
    s14["PTS<br>#ranking/pts"]
  end
  subgraph holders["大量保有報告書"]
    s15["大量保有報告書<br>#holders"]
    s16["保有者<br>#holders/:edinet"]
  end
  s0 --> s3
  s15 --> s16
  s15 --> s3
  s10 --> s11
  s13 --> s3
  s14 --> s3
  s9 --> s3
  s1 --> s8
  s7 --> s16
  s12 --> s3
```

| 元 | 先 | 呼び出しのある JS |
|---|---|---|
| 決算速報（`earnings`） | 株価（`stock-price`） | `earnings.js` |
| 大量保有報告書（`holders`） | 保有者（`holders-detail`） | `holders.js` |
| 大量保有報告書（`holders`） | 株価（`stock-price`） | `holders.js` |
| 月次（`monthly`） | 月次詳細（`monthly-detail`） | `monthly-public.js`, `monthly.js` |
| ランキング（`ranking`） | 株価（`stock-price`） | `ranking.js` |
| PTS（`ranking-pts`） | 株価（`stock-price`） | `pts.js` |
| スクリーナー（`screener`） | 株価（`stock-price`） | `screener.js` |
| 銘柄（`stock`） | 説明資料の中身（`stock-deck-detail`） | `stock.js` |
| 株主（`stock-holders`） | 保有者（`holders-detail`） | `stock-holders.js` |
| サプライ（`supply`） | 株価（`stock-price`） | `supply.js` |

## 画面の一覧

| 画面 | id | タブ | ハッシュ |
|---|---|---|---|
| 決算速報 | `earnings` | 決算速報 | `#earnings` |
| 銘柄 | `stock` | 銘柄 | `#stock` |
| 概要 | `stock-summary` | 銘柄 | `#stock/:code`<br>`#stock/:code/summary` |
| 株価 | `stock-price` | 銘柄 | `#stock/:code/price` |
| 業績詳細 | `stock-financials` | 銘柄 | `#stock/:code/financials` |
| ニュース | `stock-news` | 銘柄 | `#stock/:code/news` |
| ニュース詳細 | `stock-news-detail` | 銘柄 | `#stock/:code/news/:newsId` |
| 株主 | `stock-holders` | 銘柄 | `#stock/:code/holders` |
| 説明資料の中身 | `stock-deck-detail` | 銘柄 | `#stock/:code/decks/:deckKey` |
| スクリーナー | `screener` | スクリーナー | `#screener` |
| 月次 | `monthly` | 月次 | `#monthly` |
| 月次詳細 | `monthly-detail` | 月次 | `#monthly/:code` |
| サプライ | `supply` | サプライ | `#supply`<br>`#supply/:id?` |
| ランキング | `ranking` | ランキング | `#ranking` |
| PTS | `ranking-pts` | ランキング | `#ranking/pts` |
| 大量保有報告書 | `holders` | 大量保有報告書 | `#holders` |
| 保有者 | `holders-detail` | 大量保有報告書 | `#holders/:edinet` |

## 画面の JS が呼ばない API

ジョブや MCP、リンクの URL から使う。

| API | 呼ぶジョブ |
|---|---|
| `DELETE /api/v1/tables/{code}/{year}/elements` |  |
| `GET /api/stock/filings/{filing_id}/documents/{seq}` |  |
| `GET /api/stock/pdf/{disclosure_no}` |  |
| `GET /api/v1/tables/publications` |  |
| `GET /api/v1/tables/{code}/{year}/doc` |  |
| `POST /api/v1/tables/{code}/ingest` | `jobs/monthly/ir_monthly_html_ingest.py`, `jobs/monthly/ir_monthly_json_ingest.py`, `jobs/monthly/ir_monthly_xls_ingest.py`, `jobs/monthly/tdnet_monthly_pdf_ingest.py` |
| `POST /api/v1/tables/{code}/{year}/elements` |  |
| `PUT /api/v1/tables/publications/{code}/{year}/{month}` |  |
| `PUT /api/v1/tables/{code}/industry` |  |
| `PUT /api/v1/tables/{code}/{year}/doc` |  |
| `PUT /api/v1/tables/{code}/{year}/elements` |  |
| `PUT /api/v1/tables/{code}/{year}/values` |  |
| `PUT /api/v1/tables/{code}/{year}/values/batch` |  |
