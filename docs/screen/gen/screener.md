# 画面設計: スクリーナー

読み方と作り方は [SCREENS.md](../../SCREENS.md)。

## 画面

| 画面 | id | ハッシュ |
|---|---|---|
| スクリーナー | `screener` | `#screener` |

## 呼ぶ API と表

左から JS のファイル、API、表。API から表への実線は書く、点線は読むだけ。`stocks` を読むだけの線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["screener.js"]:::js
  n1["GET /api/screener"]:::api
  n2[("indicators")]:::tbl
  n0 --> n1
  n1 -.- n2
  classDef js fill:#f3e8ff,stroke:#7048e8
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
```

| API | 呼ぶ JS | 書く | 読むだけ |
|---|---|---|---|
| `GET /api/screener` | `screener.js` |  | `indicators`, `stocks` |

## API の引数

### GET /api/screener

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `stage` | query | integer |  |  |
| `rs_min` | query | integer |  | `0` |
| `sort` | query | string |  | `rs` |
| `limit` | query | integer |  | `100` |
