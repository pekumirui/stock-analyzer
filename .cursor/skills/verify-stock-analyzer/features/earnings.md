# 決算速報

The default tab lists financial-statement disclosures for a calendar day, then shows that issuer's cumulative, 3-month, and full-year forecast revision (修正履歴) tables. The month grid is a header popover (`#cal-pop`), not a permanently open panel.

## Sub-features

- `earnings-cal` paints `#cal` inside `#cal-pop` for the current month. Each occupied day is `{kind, n, pending?}`. Filings use `.cnt`. Future schedules use `.cnt.sched`. Today with names still scheduled adds `.cnt.pend` (`+N`).
- `earnings-day` fills `#earn-list` after a day click (`selectDate`). Past empty shows `開示なし`. Future empty inside a complete publication ledger shows `予定なし`. Future empty before that ledger is complete shows `予定未取得`. A quiet today shows `予定なし`. A failed fetch shows `取得できませんでした`.
- `earnings-list` draws `#earn-tbl` (`tableHtml`). Header row 1: `時刻` / `決算期` / `銘柄` / `3か月 / QoQ` / `累計 / YoY`; row 2: 売上 / 営利 / 経利 / 純利 twice. Each filing row has 11 cells. A metric cell shows the amount in 百万円 (`single` for 3か月, `cumulative` for 累計; a loss is `.loss`) with the rate under it in `.g`. `決算期` is `fy_label` without `FY` (`26.8`) with the period `.qb` chip under it (`FY` reads `4Q`; a schedule row with several periods lists the chips side by side). `銘柄` is the first 5 characters of the name with the 4-digit code under it in `.g`. A restated filing shows `.qb.qb-rev` `訂正` under the time. There is no Q column and no `.badge.rev`. 時刻 / 決算期 / 銘柄 are sticky on the left (`.pin0`–`.pin2`, offsets `--pin0`–`--pin2` measured by `pinCols`).
- `earnings-row` selects a `data-no` filing (auto-selects the first row when the list loads) and loads `#cum-tbl` (default) and the embedded price chart `#e-chart` (`.a-chart`). A scheduled issuer is `#earn-tbl tr[data-code]` in the same table (time and QoQ/YoY are `–`). That click loads the price chart only and replaces `#cum-tbl` / `#q-tbl` / `#rev-tbl` with `まだ開示されていません`.
- `earnings-sector` fills `#earn-groups` from the day's rows (`renderGroups`): `すべて` then one `.mchip[data-sector]` per `sector33` (`未分類` when null), most issuers first (an issuer with two periods counts once), label without a trailing `業`, count in `.n`. A chip click (`setSector`) filters both filing and schedule rows and reselects the first visible row if the selected one is hidden. Every `selectDate` resets it to すべて.
- `earnings-search` types into `#earn-sym` and fills `#earn-suggest` from `GET /api/stocks/search?q=` (all issuers; hiragana is also matched as katakana). Enter takes the first hit; a click takes that row. `pickEarnStock` calls `GET /api/earnings/detail/{code}` without `disclosure_no`, moves the calendar to its `disclosed_date`, and `selectDate(disclosed_date, want)` selects that filing row (or that issuer's row). If the day list has no matching row it still loads that filing's detail (`loadDetail`). With no filing the detail call fails and only the price chart loads (`loadSched`).
- `earnings-table` draws `#cum-tbl` / `#q-tbl` (`renderFin`). The 決算期 cell is `range` with no `期`: 累計 rows are the fiscal year end (`26.8`), 3か月 rows the quarter-end month (`25.08`). A `.qb` badge sits under it in `.g`: `1Q`–`4Q` on 3-month rows, `中予` / `通予` on forecasts, `通期` on the prior-FY row. There is no 営利率 column; the margin is `.opm` `(x.x%)` right of 営業益. A FY/4Q filing's 累計 ends with next year's `forecast_h` / `forecast_fy` and has no `actual_fy_prev` row.
- `earnings-qtab` switches `.ftab[data-fin="q"]` to `#q-wrap` and `.ftab[data-fin="rev"]` to `#rev-wrap`; the other two wraps get `hidden`.
- `earnings-rev` draws `#rev-tbl` (`renderRev`) from the detail `revisions`: per fiscal year, oldest year first (`revision_history_years` 4), 期初予想 → each disclosure whose 売上高・営業益・経常益・最終益 changed → 実績. Columns 決算期 / 発表日 / 売上高 / 営業益 / 経常益 / 最終益 / EPS (発表日 right after 決算期, #759). The badge under 決算期 is `.qb-init` `期初` (or `予想` when the first forecast is not within 100 days of the prior year end), `.qb-mod` `修正`, `.qb-act` `実績`. The rate under each value is the change from the row above: `↑+x%` / `↓-x%` / `→` when equal; 期初 rows have none. Rows get `.act` (実績), `.sep` (first row of a year), `.now` (same `disclosed_date` as the selected filing). After a FY filing the next year's 期初 is the last row. No rows → `修正履歴なし`. The API source is `EARNINGS_PARAMS["revision_source"]` (`jquants`, from `statements`); `?rev_source=xbrl` reads `xbrl_financials` and anything else is 422. The SPA never sends `rev_source`.
- `earnings-events` shows `.evt-toggles` under `#e-chart` (checkbox `input[data-evt="earnings"]`, label 決算発表). Overlay markers come from the chart payload `earnings_dates` when the box is on. The selected filing’s `disclosed_date` is always marked on `#e-chart` even when the box is off (`renderPriceChart` `opt.mark`).

## How to get to it (user POV)

- Open `{url}/app` with no hash and no `ui.route` (or after clearing them). 決算速報 is the fallback tab; boot still runs `selectDate(todayISO())` before `restoreRoute()`, so today's list loads even if another tab is restored. With a clean route: nav is `.active` on 決算速報 and `#earn-title` reads `{date}の決算発表`.
- Click `nav button[data-tab="earnings"]` from any other tab.
- Click `#cal-btn` to open `#cal-pop`. Use `#cal-title` ◀/▶ (`calMove`) to change month when needed. Click a day cell `#cal .d` that shows `.cnt` (accent for filings, muted `.cnt.sched` for a future schedule, `.cnt.pend` for `+N` still scheduled today). The popover closes.
- Click a filing row `#earn-tbl tr[data-no]`, or a scheduled row `#earn-tbl tr[data-code]`.
- Type in `#earn-sym` and pick a suggestion, or click a `#earn-groups` chip.
- Click 累計 / 3か月 / 修正履歴 in `.fin-tabs`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `{url}/app` is loaded.
- `#cal-pop` starts `hidden`. Open it with `#cal-btn`. Close with Esc, `#cal-backdrop`, or a day click.
- Schedule paths (`.cnt.sched`, `.cnt.pend`, `tr[data-code]`, `予定なし` on a future day) need the ledger tables `earnings_date_pubs` / `earnings_date_fetches`. `jobs/market/run_batch.py` fills them through `app/market/earnings_schedule.py` `sync` (J-Quants earnings-date). With an empty ledger, every future day is `ready: false` / `予定未取得` and `scheduled` stays `[]`. Rows in `earnings_date_pubs` alone are enough for `.cnt.sched` and `tr[data-code]`. `予定なし` on a future day also needs `earnings_date_fetches` to hold every day from today − `EARNINGS_DATE_LOOKBACK_DAYS` through today (`_ledger_ready`). Until today's fetch lands, future days carry `ready: false` even when `scheduled` is non-empty, and empty future days still read `予定未取得`. `.cnt.pend` needs today's filings in `stock.db` as well. Report each missing path as `verified-unreachable` with its prerequisite.

- **Tab.** Click `nav button[data-tab="earnings"]`. `#tab-earnings` is displayed; `#tab-screener` is not.
- **Month payload.** Run `helpers/capture.py earnings`. `artifacts/earnings/calendar.json` is a `{YYYY-MM-DD: {kind, n, pending?}}` object with at least one key. `_pick_earnings_date` ranks `cell.n`.
- **Day list.** `artifacts/earnings/body.json` is `{kind, actuals, scheduled}`. A filing day has non-empty `actuals`. Each actual has `code` (no leading `0`), `name`, `disclosure_no`, `yoy`, `qoq`, `single`, `cumulative`, `sector33`. A future schedule day has non-empty `scheduled` (`code`, `name`, `periods`, `sector33`) and `#earn-title` reads `{date}の決算発表予定`. A past or today filing title reads `{date}の決算発表`.
- **Detail.** `GET /api/earnings/detail/{code}?disclosure_no={disclosure_no}` for the first filing row returns tables the UI puts in `#cum-tbl` (SPA always sends `disclosure_no` from `tr[data-no]`). The same selection also `GET /api/chart/{code}?tf=D&ma=50,150,200` into `#e-chart`.
- **List.** On a filing day (2026-10-15, 133 rows on the box DB), `#earn-tbl thead tr:first-child th` reads `時刻` / `決算期` / `銘柄` / `3か月 / QoQ` / `累計 / YoY` and every row has 11 cells. Row 1 is `10:00` / `26.8` over `4Q` / `ウエストホ` over `1407`; its first metric cell is `27,310` with `+92.9%` in `.g`. 43 rows have the `4Q` chip. 2026-10-13 has 3 `訂正` chips, all in `td:first-child .g`, and no `.badge.rev`. The first three cells are `position: sticky`.
- **修正履歴.** Select 1407 on 2026-10-15 (the first row) and click `.ftab[data-fin="rev"]`: `#rev-wrap` shows and `#cum-wrap` / `#q-wrap` are `hidden`. `#rev-tbl` has 9 rows: 24.8 期初 (`23/10/13`, 64,655) / 修正 (`↓-22.1%`) / 実績 (`→`), 25.8 期初 / 修正 (`↓-20.2%`) / 実績 (`↑+5.0%`), 26.8 期初 / 実績 (`↑+4.0%`, `.now`), 27.8 期初 (`.now`). With more rows than the wrap (3116 トヨタ紡織 on 2026-07-31: 13 rows; the wrap is about 475px high), scroll `#rev-wrap` down: `#rev-tbl thead th:first-child` stays at the top and `elementFromPoint` there hits it (z-index 3 over the body's 2, #759).
- **Scheduled row.** Click `#earn-tbl tr[data-code]`. Time and the eight QoQ/YoY cells read `–`. The SPA calls `GET /api/chart/{code}?tf=D&ma=50,150,200` and does not call `/api/earnings/detail`. `#cum-tbl`, `#q-tbl`, and `#rev-tbl` read `まだ開示されていません`. On a mixed today both kinds share `#earn-tbl` (filings first). Arrow keys walk filing rows, then schedule rows.
- **Sector chips.** On a filing day, `#earn-groups` starts with `すべて.on`. Click a sector chip: it gets `.on`, `#earn-tbl` keeps only rows of that `sector33`, and the selected row is one of them. Pick another day: すべて is on again.
- **Search.** Type `とよた` or `トヨタ` into `#earn-sym`: `#earn-suggest` lists `3116 トヨタ紡織` then `7203 トヨタ自動車` (Enter takes the first). Click 7203: `#earn-title` moves to its latest `disclosed_date` (`GET /api/earnings/detail/72030` with no `disclosure_no`), and that filing row is `.sel` and scrolled into view.
- **Table.** For a FY filing, `#cum-tbl` 決算期 cells read like `26.8` with no `期` and no `actual_fy_prev` row; when the filing has next-year forecasts the last rows carry `中予` / `通予` (30480 ビックカメラ FY26.8 → `27.8`). 営業益 cells end with `.opm` `(x.x%)`.
- **3か月.** Click `.ftab[data-fin="q"]`. `#q-wrap` is not `hidden`; `#cum-wrap` is.
- **イベント.** `.evt-toggles` under `#e-chart` is in the markup and filled at boot (`fillEventToggles`). After a row loads `#e-chart`, one 「決」 mark is already on the selected `disclosed_date`. Checking 決算発表 adds the rest from `earnings_dates` (same layer as the chart tab; chart tab has no selected-filing `mark`).
- **Proof.** Keep `calendar.json`, `body.json`, and (if CDP) `artifacts/earnings/ui.png` showing `#earn-tbl` (or a chosen day's list) plus header `h1` `株式分析`. Prefer a calendar day with `.cnt` — today's empty `予定なし` is not a list proof. The month grid must not occupy the first row; the list is at the top of `#tab-earnings`.

## Gotchas

- The API query param is `date`, not `date_`. Calendar query is `month=YYYY-MM`.
- Days without `.cnt` still call `selectDate`. Past empty is `開示なし`. Future empty is `予定なし` once the publication ledger covers the lookback, and `予定未取得` before that. A quiet today is `予定なし`. None of those is a list proof.
- A thrown day fetch shows `取得できませんでした` and does not use the empty copies.
- `#earn-count` reads `N件` for one kind. A mixed today reads `N件・未発表 M`.
- `selectDate` does not refetch the month. `/api/earnings/calendar` runs from `calOpen` / `calMove` (`renderCal`) and once at boot after the first list (`calPrefetch`). `renderCal` draws a cached month first, then redraws when the fetch for the still-shown month lands.
- There is no `#q-chips` and no 営利率 column any more.
- `.ftab` also matches the PTS ranking buttons (`#pts-ranking`). Select 決算 tabs with `.ftab[data-fin]`.
- 修正履歴 is only as long as `statements` history. The box DB has `xbrl_financials` empty, so `?rev_source=xbrl` returns `revisions: []` there.
- Clicking the day's first row right after `selectDate` repeats the auto-selected request; a doubled `/api/earnings/detail` in the network log is that, not a bug.
- REIT (`product_category` 013/022) are excluded from this tab; an empty day can be real.
- Narrow viewports keep `#earn-tbl` and scroll horizontally. Do not look for `.card`.
- `renderCal` uses the machine local month, not JST. Capture `--month` if today is a month boundary.
- There is no `#cal-panel` / `▼決算カレンダー` section. `#cal` exists only inside the popover.
- A prior `ui.route` / `#chart` / `#monthly` / `#screener` can make reload land off 決算速報 — clear route when proving the default tab.
