# 決算速報

The default tab lists financial-statement disclosures for a calendar day, then shows that issuer's cumulative and 3-month tables.

## Sub-features

- `earnings-cal` paints `#cal` for the current month with per-day `.cnt`.
- `earnings-day` fills `#earn-list` after a day click (`selectDate`).
- `earnings-row` loads `#cum-tbl` (default) for the selected `data-no`.
- `earnings-qtab` switches `.ftab[data-fin="q"]` to `#q-tbl`.

## How to get to it (user POV)

- Open `{url}/`. The 決算速報 nav button is already `.active`.
- Click `nav button[data-tab="earnings"]` from any other tab.
- Click a day cell `#cal .d` that shows `.cnt`.
- Click a row `#earn-tbl tr[data-no]` (wide) or `.card[data-no]` (narrow).
- Click 累計 / 3か月 in `.fin-tabs`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `{url}/` is loaded.
- `#cal-panel` may be collapsed under 1100px; open it (`<details>`) before clicking a day.

- **Tab.** Click `nav button[data-tab="earnings"]`. `#tab-earnings` is displayed; `#tab-screener` is not.
- **Month payload.** Run `helpers/capture.py earnings`. `artifacts/earnings/calendar.json` is a `{YYYY-MM-DD: count}` object with at least one key.
- **Day list.** `artifacts/earnings/body.json` is a non-empty array. Each row has `code` (no leading `0`), `name`, `disclosure_no`, `yoy`, `qoq`. `#earn-title` reads `{date} の決算発表`.
- **Detail.** `GET /api/earnings/detail/{code}` for the first row's `code` returns tables the UI puts in `#cum-tbl`.
- **3か月.** Click `.ftab[data-fin="q"]`. `#q-wrap` is not `hidden`; `#cum-wrap` is.
- **Proof.** Keep `calendar.json`, `body.json`, and (if CDP) `artifacts/earnings/ui.png` showing `#earn-tbl` or cards plus header `h1` `株式分析`.

## Gotchas

- The API query param is `date`, not `date_`. Calendar query is `month=YYYY-MM`.
- Days without `.cnt` still call `selectDate` and show `開示なし`; that is not a list proof.
- REIT (`product_category` 013/022) are excluded from this tab; an empty day can be real.
- Narrow viewports replace `#earn-tbl` with `.card`; assert `data-no` either way.
- `renderCal` uses the machine local month, not JST. Capture `--month` if today is a month boundary.
