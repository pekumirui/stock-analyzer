# PTS

The PTS tab lists one night-session PTS ranking (株探, 75 names) for a session day and fetch time, and puts the next trading day's open beside it: 終値→翌始値, PTS→翌始値, and 追随率. Spec is `docs/DESIGN_pts.md`; code is `static/js/pts.js` + `app/api/pts.py`.

## Sub-features

- `pts-open` shows `#tab-pts` and calls `loadPts()` (`GET /api/pts?ranking=night_up`) when `#pts-tbl` is empty. `restoreRoute` on `#pts` always calls it.
- `pts-ranking` switches `#pts-ranking .ftab[data-ranking]` (`night_up` 値上がり default / `night_down` 値下がり / `night_volume` 出来高). The clicked button gets `.on`; the request keeps `date` from `#pts-date` and drops `at`, so it shows that pair's latest fetch.
- `pts-date` is `#pts-date` (session days, newest first, up to 60). Changing it reloads with the current ranking.
- `pts-at` is `#pts-at` (one option per fetch, `MM-DD HH:MM（HH:MM現在）`, newest first; `hidden` when there are no snapshots). Picking one sends `at=`.
- `pts-table` fills `#pts-tbl`: 順位 / 銘柄 / 市場 / 終値 / PTS / 終値比 / PTS出来高 / 翌始値 / 終値→翌始値 / PTS→翌始値 / 追随率. 銘柄 is name `.clicky` + 4-digit `<small>`. 追随率 = (翌始値 − 終値) / (PTS − 終値), `–` when PTS equals 終値 or there is no open. A row whose `next_date` differs from the table's adds `M/D` beside 翌始値.
- `pts-info` reads `{date} 終値比 / 翌営業日 {next_date}` (`未取込` when no row has an open yet). `#pts-summary` reads `翌始値あり N 銘柄: PTS→翌始値 平均 … / 追随率 中央値 …`, empty when `summary.n` is 0.
- `pts-to-chart` clicks a name → `gotoChart(code)` → 銘柄 › 株価, hash `#stock/{code}/price`.
- `pts-route` writes `#pts` to `location.hash` + `ui.route`; reload reopens the tab.

## How to get to it (user POV)

- Click `nav button[data-tab="pts"]` (label PTS). At ≤720px open `#menu-btn` first.
- Click 値上がり / 値下がり / 出来高 in `#pts-ranking`.
- Pick a session day in `#pts-date` or a fetch in `#pts-at`.
- Click a name in `#pts-tbl` to open 銘柄 › 株価.
- Reload with `#pts` (or `ui.route` `pts`).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Rows need `pts_snapshots`. Prod fills it from cron (`jobs/market/pts_snapshot.py`, weekdays 15:30–24:00 every 30 min). A box or Cloud Agent DB copy has none: `/api/pts` returns `dates: []`, `rows: []`, `summary: null`, and the tab reads 「PTS のデータがまだありません」. Record that `body.json` and report the rows as `verified-unreachable`, or run `jobs/market/pts_snapshot.py` against a DB copy this run owns (it fetches 株探, about 50 s). Never run it against a shared DB.
- 翌始値 needs `daily_prices` for the day after the session. Until the daily batch lands, every open is null, `#pts-info` ends with `未取込`, and `#pts-summary` is empty.

- **API.** `helpers/capture.py pts [--ranking …] [--date …]` writes `artifacts/pts/body.json` + `request.txt`. Assert `{dates, ranking, date, snapshots, fetched_at, as_of, rows, summary}`; each row has `rank`, `code` (5 chars, no leading `0`), `name`, `market`, `base_close`, `pts_price`, `pts_chg_pct`, `volume`, `next_date`, `next_open`, `open_gap_pct`, `open_vs_pts_pct`, `follow_ratio`. `GET /api/pts?ranking=bad` → 400.
- **Tab.** Click `nav button[data-tab="pts"]` → `#tab-pts` displayed, hash `#pts`, one `GET /api/pts?ranking=night_up`. With data: `#pts-tbl` has 11 header cells and 75 rows; `#pts-ranking` 値上がり is `.on`; `#pts-date` selects the newest day. On 2026-10-19 (copy filled at 10:01 JST): session 2026-10-16, `#pts-at` `10-19 10:01（06:02現在）`, row 1 `オンデック 7360` 711.0 → 861.0 `+21.1%`, info `2026-10-16 終値比 / 翌営業日 未取込`.
- **Ranking.** Click `.ftab[data-ranking="night_down"]` → `GET /api/pts?ranking=night_down&date=2026-10-16`, 値下がり is `.on`, row 1 has a negative 終値比.
- **To 株価.** Click a name → 銘柄 active with 株価 `.on`, hash `#stock/{code}/price`, `GET /api/stock/{code}/overview` then `GET /api/chart/{code}?tf=D&ma=50,150,200&rel=false` + `/api/earnings-performance/{code}` (2026-10-27: row 1 サークレイス → `#stock/50290/price`).
- **Route.** On `#pts`, reload → PTS active and `#pts-tbl` filled again.
- **Proof.** Keep `artifacts/pts/body.json`. If CDP: `ui.png` of `#pts-tbl` with the header.

## Gotchas

- `#pts-ranking` buttons are `.ftab` like 決算速報's 累計 / 3か月 / 修正履歴. Select by `[data-ranking]` or `[data-fin]`, not bare `.ftab`.
- Switching away and back does not reload when `#pts-tbl` already has HTML (`selectTab`). Reload or change a control to refetch.
- `session_date` is the day of the 通常取引 close the ranking compares against, not the fetch day. Before the evening session starts, 株探 still shows the previous session, so a Monday-morning fetch lands on Friday's session.
- `fetched_at` is JST without a zone (`app/market/pts.py`). The `at` query must match a `snapshots[].fetched_at` exactly; an unknown `at` falls back to the latest fetch.
- `%` cells use `pct()` (positive `.pos` red, negative `.neg` green).
