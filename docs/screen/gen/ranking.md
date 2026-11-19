# 画面設計: ランキング

**自動生成。手で直さない。** 読み方と作り方は [SCREENS.md](../../SCREENS.md)。

## 画面

| 画面 | id | ハッシュ |
|---|---|---|
| ランキング | `ranking` | `#ranking` |
| PTS | `ranking-pts` | `#ranking/pts` |

## 呼ぶ API と表

左から JS のファイル、API、表。API から表への実線は書く、点線は読むだけ。`stocks` を読むだけの線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["pts.js"]:::js
  n1["ranking.js"]:::js
  n2["GET /api/pts"]:::api
  n3["GET /api/ranking/earnings-perf"]:::api
  n4[("daily_prices")]:::tbl
  n5[("earnings_events")]:::tbl
  n6[("pts_snapshots")]:::tbl
  n7[("topix")]:::tbl
  n0 --> n2
  n2 -.- n4
  n2 -.- n6
  n1 --> n3
  n3 -.- n4
  n3 -.- n5
  n3 -.- n7
  classDef js fill:#f3e8ff,stroke:#7048e8
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
```

| API | 呼ぶ JS | 書く | 読むだけ |
|---|---|---|---|
| `GET /api/pts` | `pts.js` |  | `daily_prices`, `pts_snapshots` |
| `GET /api/ranking/earnings-perf` | `ranking.js` |  | `daily_prices`, `earnings_events`, `stocks`, `topix` |

## API の引数

### GET /api/pts

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `date` | query | string（date） |  |  |
| `ranking` | query | string |  | `night_up` |
| `at` | query | string（date-time） |  |  |

### GET /api/ranking/earnings-perf

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `month` | query | string |  |  |
