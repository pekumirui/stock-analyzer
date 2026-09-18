# 月次

The monthly tab lists ingested issuer monthly-sales tables with industry chips, then opens a Detail view (series select, 3-year clustered chart, year-stacked tables). It is not a TDnet day calendar.

## Sub-features

- `monthly-list` fills `#monthly-groups` (新着 + industry chips) and `#monthly-tbl` from `GET /api/v1/tables`.
- `monthly-detail` opens `#monthly-detail` for a row `data-code` via `GET /api/v1/tables/{code}` then parallel `GET /api/v1/tables/{code}/{year}` for the latest calendar year and up to two prior years.
- `monthly-series` switches `#monthly-series` (default: unsegmented 全店+売上高 yoy when present, else any `all_store` yoy including 全社, else first series) and redraws `#monthly-chart`.
- `monthly-back` returns to TOP via `#monthly-back` while keeping the selected chip.
- `monthly-route` persists `#monthly` or `#monthly/{code}` in `location.hash` + `localStorage` `ui.route`; reload/`restoreRoute` reopens TOP or Detail.

## How to get to it (user POV)

- Click `nav button[data-tab="monthly"]`.
- Click an industry / 新着 chip in `#monthly-groups`. The 新着 chip is always rendered; if no issuer has `is_new`, selection falls back to the first industry chip but 新着 stays visible. Industry chips appear only for industries present in the list.
- Click a company-name cell (`.name`) in `#monthly-tbl` to open Detail.
- Change `#monthly-series` or click a `tr[data-series]` in the year tables.
- Click `#monthly-back` to restore TOP + chip.
- Reload with `#monthly` or `#monthly/{code}` (or the same path in `ui.route`) to restore TOP or Detail.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `GET /api/v1/tables` returns a non-empty array (`helpers/capture.py monthly` must not exit 1). If empty, monthly ingest never ran — report that, do not fake rows.

- **Tab.** Click `nav button[data-tab="monthly"]`. `#tab-monthly` is displayed; `#monthly-top` is shown and `#monthly-detail` is `display:none`. List host is `#monthly-list` (holds empty copy or `#monthly-tbl`).
- **List.** `helpers/capture.py monthly` writes `artifacts/monthly/body.json` from `GET /api/v1/tables`. Assert a JSON array; each item has `code` (not `0`-prefix), `industry`, `is_new`, `all_store_yoy` / `existing_store_yoy` (nulls are fine). `#monthly-tbl` shows 会社名 / 市場 / 業種 / 月次業種 / 全店(前年比) / 既存店(前年比) (no standalone code column); null YoY cells show `-`.
- **Detail.** Pass `--code` from the list. Capture also writes years + one year payload. UI: click a `.name` cell → `#monthly-detail` visible, `#monthly-chart` has an SVG, `#monthly-table-wrap` stacks `table.monthly` newest-year-first.
- **Proof.** Keep `body.json` (list) and, when `--code` was used, the year artifact(s). If CDP: `ui.png` of Detail or list with header `h1` `株式分析`.
- **Route restore.** After Detail, confirm `location.hash` is `#monthly/{code}`. Reload → Detail for that code (list loads first). `#monthly-back` writes `#monthly`.
- Do **not** call `/api/monthly*`, `#mon-cal`, or `#mon-to-chart` — those are not on this tip.

## Gotchas

- Empty `/api/v1/tables` renders「登録された月次データはありません」— that is empty ingest, or a Cloud Agent build that skipped VPS monthly copy. Not a render bug.
- Detail with no years shows「データなし」.
- NEW badge uses `ingested_today` (JST today); 新着 chip window is `MONTHLY_NEW_DAYS` (7) via `is_new`.
- Stored YoY is `yoy_index` (100=flat). Display is the stored number. Chart has a 100 horizontal line; year bar colors cycle `MONTHLY_YEAR_COLORS[year % 4]`.
- Row order (`monthlyRowOrder`): unsegmented rows first, then named segments (first-seen). Issuer `segment_tail` from `/static/monthly_row_order.json` (loaded by `loadMonthlyRowOrderConfig` before Detail tables) ranks those segment names after other named segments (e.g. 76830 `内アパレル`). Inside each group: country → scope 全店 (0) → 全社 (1) → 既存 (2) → other (3) (`monthlyRowScopeRank`; 全社 is its own bucket, not folded into 全店) → channel 直営+FC → 直営 → FC → unlabeled → metric slot 売上高 → 客数 → 客単価 → 出店 → 退店 → 純増/新設/改装/業態転換 → 店舗数 → その他 (`MONTHLY_METRIC_RANK`). Scope-less 店舗数 / 合計店舗数 and 出店/退店 (no 全店・既存・全社 token) sort in the other-scope bucket (rank 3), not under 全店. Ingest/UI unglue maps `実店舗数` → `実店舗 店舗数` (metric 店舗数, leftover 実店舗). yoy row labels end with `(前年比)` via `fmtMonthlyElement`.
- Series default (`pickDefaultMonthlySeries`): prefer unsegmented 全店+売上高 yoy; else any row whose `monthlySeriesKind` is `all_store` (全店 / 全社, including non-売上); else the first ordered row.
- Missing values render as `-` (not a blank cell): list 全店/既存店 YoY via `fmtMonthlyYoyList`, Detail month cells via `fmtMonthlyCell` / server `format_monthly_cell`. Year JSON from `GET /api/v1/tables/{code}/{year}` always has month keys `"1"`…`"12"` with absent months as `null` (0 is a real zero).
- SPA only reads `/api/v1/tables*`. Write routes (`POST …/ingest`, `PUT …/industry`, values/elements) are MCP/ingest — do not use them in verify.
- Narrow viewports (`@media (max-width:640px)`) drop the left sticky columns; do not require desktop sticky layout.
- Deep link codes must match `^[0-9][0-9A-Z]{3}[0-9]$` (`tabs.js` `CODE_RE`); invalid rest is ignored and TOP opens.
