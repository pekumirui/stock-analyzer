# 画面設計: 共通

**自動生成。手で直さない。** 読み方と作り方は [SCREENS.md](../../SCREENS.md)。

## 呼ぶ API と表

左から JS のファイル、API、表。API から表への実線は書く、点線は読むだけ。`stocks` を読むだけの線は省く。

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["chart-core.js"]:::js
  n1["GET /api/chart/{code}"]:::api
  n2["GET /api/topix"]:::api
  n3[("daily_prices")]:::tbl
  n4[("earnings_events")]:::tbl
  n5[("statements")]:::tbl
  n0 --> n1
  n1 -.- n3
  n1 -.- n4
  n1 -.- n5
  n0 --> n2
  classDef js fill:#f3e8ff,stroke:#7048e8
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
```

| API | 呼ぶ JS | 書く | 読むだけ |
|---|---|---|---|
| `GET /api/chart/{code}` | `chart-core.js` |  | `daily_prices`, `earnings_events`, `statements`, `stocks` |
| `GET /api/topix` | `chart-core.js` |  |  |

## API の引数

### GET /api/chart/{code}

| 引数 | 場所 | 型 | 必須 | 既定 |
|---|---|---|---|---|
| `code` | path | string | ○ |  |

### GET /api/topix

引数: なし
