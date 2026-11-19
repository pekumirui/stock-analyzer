# データの流れ: 共通・運用

**自動生成。手で直さない。** 読み方と作り方は [DATA_FLOW.md](../DATA_FLOW.md)。全体の表と影響の調べ方は [ビューア](viewer.html)。

図の見方: 左が入口、右が表。実線の矢印は書く、点線は読むだけ。灰色の点線の枠はほかの機能の表。`stocks` はほぼ全部が読むので、読む線は省く（表には出す。線が残らない入口は図から外す）。

## バッチ

```mermaid
---
config:
  layout: elk
---
flowchart LR
  n0["fx_rates.py"]:::job
  n1["golden_db.py"]:::job
  n2["migrate.py"]:::job
  n3["ops/rebuild_daily_prices.py"]:::job
  n4[("daily_prices")]:::ext
  n5[("earnings_date_fetches")]:::ext
  n6[("earnings_date_pubs")]:::ext
  n7[("earnings_events")]:::ext
  n8[("fx_rates")]:::ext
  n9[("indicators")]:::ext
  n10[("material_monthly")]:::ext
  n11[("material_trade_country")]:::ext
  n12[("monthly_element_sources")]:::ext
  n13[("monthly_elements")]:::ext
  n14[("monthly_issuers")]:::ext
  n15[("monthly_tables")]:::ext
  n16[("monthly_values")]:::ext
  n17[("statements")]:::ext
  n18[("stocks")]:::ext
  n19[("topix")]:::ext
  n20[("xbrl_elements")]:::ext
  n21[("xbrl_financials")]:::ext
  n0 --> n8
  n1 --> n4
  n1 --> n5
  n1 --> n6
  n1 --> n7
  n1 --> n8
  n1 --> n9
  n1 --> n10
  n1 --> n11
  n1 --> n12
  n1 --> n13
  n1 --> n14
  n1 --> n15
  n1 --> n16
  n1 --> n17
  n1 --> n18
  n1 --> n19
  n2 --> n12
  n2 --> n13
  n2 --> n14
  n2 --> n15
  n2 --> n16
  n2 --> n20
  n2 --> n21
  n3 --> n4
  classDef job fill:#e3ecff,stroke:#3b5bdb
  classDef api fill:#e6f7ec,stroke:#2b8a3e
  classDef tbl fill:#fff4e0,stroke:#e8590c
  classDef ext fill:#f1f3f5,stroke:#868e96,stroke-dasharray:4 3
```

| バッチ | 書く | 読むだけ |
|---|---|---|
| `jobs/fx_rates.py` | `fx_rates` |  |
| `jobs/golden_db.py` | `daily_prices`, `earnings_date_fetches`, `earnings_date_pubs`, `earnings_events`, `fx_rates`, `indicators`, `material_monthly`, `material_trade_country`, `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `statements`, `stocks`, `topix` |  |
| `jobs/migrate.py` | `monthly_element_sources`, `monthly_elements`, `monthly_issuers`, `monthly_tables`, `monthly_values`, `xbrl_elements`, `xbrl_financials` | `stocks` |
| `jobs/ops/rebuild_daily_prices.py` | `daily_prices` |  |
