# 月次

The monthly tab lists TDnet monthly-sales style disclosures by day, then one issuer's KPIs.

## Sub-features

- `monthly-cal` paints `#mon-cal` from `/api/monthly/calendar`.
- `monthly-day` fills `#mon-list` from `/api/monthly?date=`.
- `monthly-issuer` fills `#mon-kpi` from `/api/monthly/{code}`.
- `monthly-to-chart` uses `#mon-to-chart`.

## How to get to it (user POV)

- Click `nav button[data-tab="monthly"]`.
- Click a `#mon-cal .d` cell with `.cnt`.
- Click a row in `#mon-list`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Monthly tables are populated (`helpers/capture.py monthly` must not exit 1). If it fails, this feature is unreachable until `.\run.ps1 monthly-init` — report that, do not fake rows.

- **Tab.** Click `nav button[data-tab="monthly"]`. `#tab-monthly` is displayed. `renderMonCal` runs.
- **Calendar.** `artifacts/monthly/calendar.json` maps `YYYY-MM-DD` to counts ≥ 1.
- **Day list.** `artifacts/monthly/body.json` is a non-empty array of disclosures for the captured day (`code` not `0`-prefix).
- **Issuer.** `GET /api/monthly/{code}` for a list code returns KPI rows the UI shows in `#mon-kpi`.
- **Proof.** Keep `calendar.json` and `body.json`, plus (if CDP) `ui.png` of `#mon-list`.

## Gotchas

- Public TDnet PDFs vanish after ~30 days. An old `pdf_path` on disk can exist while a new date is empty.
- Foreign / REIT names are filtered out (`is_domestic_equity`). Calendar counts only kept rows.
- `#mon-cal-panel` is a `<details>` closed on narrow viewports.
- Capture `--month` when the local calendar month has no filings; pick a month that `calendar.json` showed keys for.
