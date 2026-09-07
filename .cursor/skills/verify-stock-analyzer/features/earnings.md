# 決算速報

The default tab lists financial-statement disclosures for a calendar day, then shows that issuer's cumulative and 3-month tables. The month grid is a header popover (`#cal-pop`), not a permanently open panel.

## Sub-features

- `earnings-cal` paints `#cal` inside `#cal-pop` for the current month with per-day `.cnt`.
- `earnings-day` fills `#earn-list` after a day click (`selectDate`); an empty day shows `開示なし`.
- `earnings-row` selects a `data-no` (auto-selects the first row when the list loads) and loads `#cum-tbl` (default), display-only `#q-chips`, and the embedded price chart `#e-chart` (`.a-chart`).
- `earnings-qtab` switches `.ftab[data-fin="q"]` to `#q-tbl`.
- `earnings-events` shows `.evt-toggles` under `#e-chart` (checkbox `input[data-evt="earnings"]`, label 決算発表); markers come from the chart payload `earnings_dates`.

## How to get to it (user POV)

- Open `{url}/`. The 決算速報 nav button is already `.active`. Today's list is already loaded; `#earn-title` reads `{date}の決算発表`.
- Click `nav button[data-tab="earnings"]` from any other tab.
- Click `#cal-btn` to open `#cal-pop`. Click a day cell `#cal .d` that shows `.cnt`. The popover closes.
- Click a row `#earn-tbl tr[data-no]`.
- Click 累計 / 3か月 in `.fin-tabs`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `{url}/` is loaded.
- `#cal-pop` starts `hidden`. Open it with `#cal-btn`. Close with Esc, `#cal-backdrop`, or a day click.

- **Tab.** Click `nav button[data-tab="earnings"]`. `#tab-earnings` is displayed; `#tab-screener` is not.
- **Month payload.** Run `helpers/capture.py earnings`. `artifacts/earnings/calendar.json` is a `{YYYY-MM-DD: count}` object with at least one key.
- **Day list.** `artifacts/earnings/body.json` is a non-empty array. Each row has `code` (no leading `0`), `name`, `disclosure_no`, `yoy`, `qoq`. `#earn-title` reads `{date}の決算発表`.
- **Detail.** `GET /api/earnings/detail/{code}?disclosure_no={disclosure_no}` for the first row returns tables the UI puts in `#cum-tbl` (SPA always sends `disclosure_no` from `tr[data-no]`). The same selection also `GET /api/chart/{code}?tf=D&ma=50,150,200` into `#e-chart` and fills `#q-chips` (1Q–4Q display only).
- **3か月.** Click `.ftab[data-fin="q"]`. `#q-wrap` is not `hidden`; `#cum-wrap` is.
- **イベント.** `.evt-toggles` under `#e-chart` is present after a row is selected; toggling 決算発表 uses `earnings_dates` from the chart payload (same layer as the chart tab).
- **Proof.** Keep `calendar.json`, `body.json`, and (if CDP) `artifacts/earnings/ui.png` showing `#earn-tbl` (or a chosen day's list) plus header `h1` `株式分析`. Prefer a calendar day with `.cnt` — today's empty `開示なし` is not a list proof. The month grid must not occupy the first row; the list is at the top of `#tab-earnings`.

## Gotchas

- The API query param is `date`, not `date_`. Calendar query is `month=YYYY-MM`.
- Days without `.cnt` still call `selectDate` and show `開示なし`; that is not a list proof.
- REIT (`product_category` 013/022) are excluded from this tab; an empty day can be real.
- Narrow viewports keep `#earn-tbl` and scroll horizontally. Do not look for `.card`.
- `renderCal` uses the machine local month, not JST. Capture `--month` if today is a month boundary.
- There is no `#cal-panel` / `▼決算カレンダー` section. `#cal` exists only inside the popover.
