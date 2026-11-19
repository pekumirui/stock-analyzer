# データの流れ: サプライ

**自動生成。手で直さない。** 読み方と作り方は [DATA_FLOW.md](../DATA_FLOW.md)。全体の表と影響の調べ方は [ビューア](viewer.html)。

図の見方: 左が入口、右が表。実線の矢印は書く、点線は読むだけ。灰色の点線の枠はほかの機能の表。`stocks` はほぼ全部が読むので、読む線は省く（表には出す。線が残らない入口は図から外す）。

## バッチ

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["supply/material_stats.py"]:::job
  n1[("material_monthly")]:::tbl
  n2[("material_trade_country")]:::tbl
  n0 --> n1
  n0 --> n2
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| バッチ | 書く | 読むだけ |
|---|---|---|
| `jobs/supply/material_stats.py` | `material_monthly`, `material_trade_country` |  |

## API

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["GET /api/supply/countries/{material_id}"]:::api
  n1["GET /api/supply/monthly/{material_id}"]:::api
  n2[("material_monthly")]:::tbl
  n3[("material_trade_country")]:::tbl
  n0 -.- n3
  n1 -.- n2
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| API | 書く | 読むだけ |
|---|---|---|
| `GET /api/supply/countries/{material_id}` |  | `material_trade_country` |
| `GET /api/supply/monthly/{material_id}` |  | `material_monthly` |

## テーブル

ほかの機能のバッチ・API も含めて、その表を書く・読む入口。

### material_monthly

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/supply/material_stats.py`
- 読むだけ: `GET /api/supply/monthly/{material_id}`

### material_trade_country

- 書く: `jobs/golden_db.py（共通・運用）`, `jobs/supply/material_stats.py`
- 読むだけ: `GET /api/supply/countries/{material_id}`
