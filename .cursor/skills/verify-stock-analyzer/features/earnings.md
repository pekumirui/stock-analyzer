# 決算速報

The default tab lists financial-statement disclosures for a calendar day, then shows that issuer's cumulative and 3-month tables. The month grid is a header popover (`#cal-pop`), not a permanently open panel.

## Sub-features

- `earnings-cal` paints `#cal` inside `#cal-pop` for the current month. Each occupied day is `{kind, n, pending?}`. Filings use `.cnt`. Future schedules use `.cnt.sched`. Today with names still scheduled adds `.cnt.pend` (`+N`).
- `earnings-day` fills `#earn-list` after a day click (`selectDate`). Past empty shows `開示なし`. Future empty inside a complete publication ledger shows `予定なし`. Future empty before that ledger is complete shows `予定未取得`. A quiet today shows `予定なし`. A failed fetch shows `取得できませんでした`.
- `earnings-row` selects a `data-no` filing (auto-selects the first row when the list loads) and loads `#cum-tbl` (default), display-only `#q-chips`, and the embedded price chart `#e-chart` (`.a-chart`). A scheduled issuer is `#earn-tbl tr[data-code]` in the same table (time and QoQ/YoY are `–`). That click loads the price chart only and replaces `#cum-tbl` / `#q-tbl` with `まだ開示されていません`.
- `earnings-qtab` switches `.ftab[data-fin="q"]` to `#q-tbl`.
- `earnings-events` shows `.evt-toggles` under `#e-chart` (checkbox `input[data-evt="earnings"]`, label 決算発表). Overlay markers come from the chart payload `earnings_dates` when the box is on. The selected filing’s `disclosed_date` is always marked on `#e-chart` even when the box is off (`renderPriceChart` `opt.mark`).

## How to get to it (user POV)

- Open `{url}/app` with no hash and no `ui.route` (or after clearing them). 決算速報 is the fallback tab; boot still runs `selectDate(todayISO())` before `restoreRoute()`, so today's list loads even if another tab is restored. With a clean route: nav is `.active` on 決算速報 and `#earn-title` reads `{date}の決算発表`.
- Click `nav button[data-tab="earnings"]` from any other tab.
- Click `#cal-btn` to open `#cal-pop`. Use `#cal-title` ◀/▶ (`calMove`) to change month when needed. Click a day cell `#cal .d` that shows `.cnt` (accent for filings, muted `.cnt.sched` for a future schedule, `.cnt.pend` for `+N` still scheduled today). The popover closes.
- Click a filing row `#earn-tbl tr[data-no]`, or a scheduled row `#earn-tbl tr[data-code]`.
- Click 累計 / 3か月 in `.fin-tabs`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `{url}/app` is loaded.
- `#cal-pop` starts `hidden`. Open it with `#cal-btn`. Close with Esc, `#cal-backdrop`, or a day click.
- Schedule paths (`.cnt.sched`, `.cnt.pend`, `tr[data-code]`, `予定なし` on a future day) need the ledger tables `earnings_date_pubs` / `earnings_date_fetches`. `jobs/run_batch.py` fills them through `app/earnings_schedule.py` `sync` (J-Quants earnings-date). With an empty ledger, every future day is `ready: false` / `予定未取得` and `scheduled` stays `[]`. Report those paths as `verified-unreachable` with that prerequisite.

- **Tab.** Click `nav button[data-tab="earnings"]`. `#tab-earnings` is displayed; `#tab-screener` is not.
- **Month payload.** Run `helpers/capture.py earnings`. `artifacts/earnings/calendar.json` is a `{YYYY-MM-DD: {kind, n, pending?}}` object with at least one key. `_pick_earnings_date` ranks `cell.n`.
- **Day list.** `artifacts/earnings/body.json` is `{kind, actuals, scheduled}`. A filing day has non-empty `actuals`. Each actual has `code` (no leading `0`), `name`, `disclosure_no`, `yoy`, `qoq`. A future schedule day has non-empty `scheduled` (`code`, `name`, `periods`) and `#earn-title` reads `{date}の決算発表予定`. A past or today filing title reads `{date}の決算発表`.
- **Detail.** `GET /api/earnings/detail/{code}?disclosure_no={disclosure_no}` for the first filing row returns tables the UI puts in `#cum-tbl` (SPA always sends `disclosure_no` from `tr[data-no]`). The same selection also `GET /api/chart/{code}?tf=D&ma=50,150,200` into `#e-chart` and fills `#q-chips` (1Q–4Q display only).
- **Scheduled row.** Click `#earn-tbl tr[data-code]`. Time and the eight QoQ/YoY cells read `–`. The SPA calls `GET /api/chart/{code}?tf=D&ma=50,150,200` and does not call `/api/earnings/detail`. `#cum-tbl` and `#q-tbl` read `まだ開示されていません`. On a mixed today both kinds share `#earn-tbl` (filings first). Arrow keys walk filing rows, then schedule rows.
- **3か月.** Click `.ftab[data-fin="q"]`. `#q-wrap` is not `hidden`; `#cum-wrap` is.
- **イベント.** `.evt-toggles` under `#e-chart` is in the markup and filled at boot (`fillEventToggles`). After a row loads `#e-chart`, one 「決」 mark is already on the selected `disclosed_date`. Checking 決算発表 adds the rest from `earnings_dates` (same layer as the chart tab; chart tab has no selected-filing `mark`).
- **Proof.** Keep `calendar.json`, `body.json`, and (if CDP) `artifacts/earnings/ui.png` showing `#earn-tbl` (or a chosen day's list) plus header `h1` `株式分析`. Prefer a calendar day with `.cnt` — today's empty `予定なし` is not a list proof. The month grid must not occupy the first row; the list is at the top of `#tab-earnings`.

## Gotchas

- The API query param is `date`, not `date_`. Calendar query is `month=YYYY-MM`.
- Days without `.cnt` still call `selectDate`. Past empty is `開示なし`. Future empty is `予定なし` once the publication ledger covers the lookback, and `予定未取得` before that. A quiet today is `予定なし`. None of those is a list proof.
- A thrown day fetch shows `取得できませんでした` and does not use the empty copies.
- `#earn-count` reads `N件` for one kind. A mixed today reads `N件・未発表 M`.
- `selectDate` does not refetch the month. `/api/earnings/calendar` runs only from `calOpen` / `calMove` (`renderCal`).
- REIT (`product_category` 013/022) are excluded from this tab; an empty day can be real.
- Narrow viewports keep `#earn-tbl` and scroll horizontally. Do not look for `.card`.
- `renderCal` uses the machine local month, not JST. Capture `--month` if today is a month boundary.
- There is no `#cal-panel` / `▼決算カレンダー` section. `#cal` exists only inside the popover.
- A prior `ui.route` / `#chart` / `#monthly` / `#screener` can make reload land off 決算速報 — clear route when proving the default tab.
