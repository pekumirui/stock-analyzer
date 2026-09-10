# 月次

The monthly tab lists ingested issuer monthly-sales tables with industry chips, then opens a Detail view (series select, 3-year clustered chart, year-stacked tables). It is not a TDnet day calendar.

## Sub-features

- `monthly-list` fills `#monthly-groups` (新着 + industry chips) and `#monthly-tbl` from `GET /api/v1/tables`.
- `monthly-detail` opens `#monthly-detail` for a row `data-code` via `GET /api/v1/tables/{code}` then parallel `GET /api/v1/tables/{code}/{year}` for the latest calendar year and up to two prior years.
- `monthly-series` switches `#monthly-series` (default: 全店売上 yoy row when present, else first series) and redraws `#monthly-chart`.
- `monthly-back` returns to TOP via `#monthly-back` while keeping the selected chip.

## How to get to it (user POV)

- Click `nav button[data-tab="monthly"]`.
- Click an industry / 新着 chip in `#monthly-groups`. The 新着 chip is always rendered; if no issuer has `is_new`, selection falls back to the first industry chip but 新着 stays visible. Industry chips appear only for industries present in the list.
- Click a company-name cell (`.name`) in `#monthly-tbl` to open Detail.
- Change `#monthly-series` or click a `tr[data-series]` in the year tables.
- Click `#monthly-back` to restore TOP + chip.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `GET /api/v1/tables` returns a non-empty array (`helpers/capture.py monthly` must not exit 1). If empty, monthly ingest never ran — report that, do not fake rows.

- **Tab.** Click `nav button[data-tab="monthly"]`. `#tab-monthly` is displayed; `#monthly-top` is shown and `#monthly-detail` is `display:none`.
- **List.** `helpers/capture.py monthly` writes `artifacts/monthly/body.json` from `GET /api/v1/tables`. Assert a JSON array; each item has `code` (not `0`-prefix), `industry`, `is_new`, `all_store_yoy` / `existing_store_yoy`. `#monthly-tbl` shows 会社名 / 市場 / 業種 / 月次業種 / 全店(前年比) / 既存店(前年比) (no standalone code column).
- **Detail.** Pass `--code` from the list. Capture also writes years + one year payload. UI: click a `.name` cell → `#monthly-detail` visible, `#monthly-chart` has an SVG, `#monthly-table-wrap` stacks `table.monthly` newest-year-first.
- **Proof.** Keep `body.json` (list) and, when `--code` was used, the year artifact(s). If CDP: `ui.png` of Detail or list with header `h1` `株式分析`.
- Do **not** call `/api/monthly*`, `#mon-cal`, or `#mon-to-chart` — those are not on this tip.

## Gotchas

- Empty `/api/v1/tables` renders「登録された月次データはありません」— that is empty ingest, not a render bug.
- Detail with no years shows「データなし」.
- NEW badge uses `ingested_today` (JST today); 新着 chip window is `MONTHLY_NEW_DAYS` (7) via `is_new`.
- YoY chart/cell values add +100 only for `yoy_pct` (not `yoy_index`), with a 100 horizontal line; year bar colors cycle `MONTHLY_YEAR_COLORS[year % 4]`.
- Row order is 全店 → 既存店 → FC店 → named segments (`monthlyRowOrder`); 全社 counts as 全店. Within each block 売上高 → 客数 → 客単価 → 店舗数. yoy row labels end with `(前年比)` via `fmtMonthlyElement`.
- SPA only reads `/api/v1/tables*`. Write routes (`POST …/ingest`, `PUT …/industry`, values/elements) are MCP/ingest — do not use them in verify.
- Narrow viewports (`@media (max-width:640px)`) drop the left sticky columns; do not require desktop sticky layout.
