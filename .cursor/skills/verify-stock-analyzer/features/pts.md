# PTS

PTS lives inside the ランキング tab. It lists one night-session PTS ranking (株探, 75 names) for a session day and fetch time, and puts the next trading day's open beside it: 終値→翌始値, PTS→翌始値, and 追随率. Spec is `docs/screen/pts.md`; code is `static/js/pts.js` + `static/js/ranking.js` (`showRankingSub("pts")`) + `app/api/pts.py`. There is no header `PTS` tab (`data-tab="pts"` / `#tab-pts` are gone; KAB-265).

## Sub-features

- `pts-open` shows `#tab-ranking` with `#rk-subs [data-rksub="pts"]` `.on`, unhides `#rk-sub-pts`, and calls `loadPts()` (`GET /api/pts?ranking=night_up`) when `#pts-tbl` is empty. `restoreRoute` on `#ranking/pts` (and old `#pts`) calls it on boot / a reload (`force: !pop`). `popstate` skips the fetch when `#pts-tbl` already has HTML.
- `pts-ranking` switches `#pts-ranking .ftab[data-ranking]` (`night_up` 値上がり default / `night_down` 値下がり / `night_volume` 出来高). The clicked button gets `.on`; the request keeps `date` from `#pts-date` and drops `at`, so it shows that pair's latest fetch.
- `pts-date` is `#pts-date` (session days, newest first, up to 60). Changing it reloads with the current ranking.
- `pts-at` is `#pts-at` (one option per fetch, `MM-DD HH:MM（HH:MM現在）`, newest first; `hidden` when there are no snapshots). Picking one sends `at=`.
- `pts-table` fills `#pts-tbl.sheet`: 順位 / 銘柄 / 市場 / 終値 / PTS / 終値比 / PTS出来高 / 翌始値 / 終値→翌始値 / PTS→翌始値 / 追随率. 銘柄 is `shortName` (5 chars, `title` full) + `code.slice(0,4)` under it (same `table.sheet` look as ランキング). 追随率 = (翌始値 − 終値) / (PTS − 終値), `–` when PTS equals 終値 or there is no open. A row whose `next_date` differs from the table's adds `M/D` beside 翌始値.
- `pts-info` reads `{date} 終値比 / 翌営業日 {next_date}` (`未取込` when no row has an open yet). `#pts-summary` reads `翌始値あり N 銘柄: PTS→翌始値 平均 … / 追随率 中央値 …`, empty when `summary.n` is 0.
- `pts-to-chart` clicks a name → `gotoChart(code)` → 銘柄 › 株価, hash `#stock/{code}/price?from=ranking-pts`.
- `pts-route` writes `#ranking/pts` to `location.hash` + `ui.route` (path only). Opening PTS from 決算後パフォーマンス adds `?from=ranking`. Reload reopens ランキング › PTS. Old `#pts` (hash or `ui.route`) is rewritten to `#ranking/pts`.

## How to get to it (user POV)

- Click `nav button[data-tab="ranking"]` (label ランキング), then `#rk-subs .ftab[data-rksub="pts"]` (label PTS). At ≤720px open `#menu-btn` first.
- Click 値上がり / 値下がり / 出来高 in `#pts-ranking`.
- Pick a session day in `#pts-date` or a fetch in `#pts-at`.
- Click a name in `#pts-tbl` to open 銘柄 › 株価.
- Reload with `#ranking/pts` (or old `#pts` / `ui.route` `pts`).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Rows need `pts_snapshots`. Prod fills it from cron (`jobs/market/pts_snapshot.py`, weekdays 15:30–24:00 every 30 min). A box or Cloud Agent DB copy has none: `/api/pts` returns `dates: []`, `rows: []`, `summary: null`, and the pane reads 「PTS のデータがまだありません」. Record that `body.json` and report the rows as `verified-unreachable`, or run `jobs/market/pts_snapshot.py` against a DB copy this run owns (it fetches 株探, about 50 s). Never run it against a shared DB.
- 翌始値 needs `daily_prices` for the day after the session. Until the daily batch lands, every open is null, `#pts-info` ends with `未取込`, and `#pts-summary` is empty.

- **API.** `helpers/capture.py pts [--ranking …] [--date …]` writes `artifacts/pts/body.json` + `request.txt`. Assert `{dates, ranking, date, snapshots, fetched_at, as_of, rows, summary}`; each row has `rank`, `code` (5 chars, no leading `0`), `name`, `market`, `base_close`, `pts_price`, `pts_chg_pct`, `volume`, `next_date`, `next_open`, `open_gap_pct`, `open_vs_pts_pct`, `follow_ratio`. `GET /api/pts?ranking=bad` → 400.
- **Tab.** Click `nav button[data-tab="ranking"]` then `#rk-subs .ftab[data-rksub="pts"]` → `#tab-ranking` displayed, `#rk-sub-pts` visible, `#rk-sub-perf` hidden, hash `#ranking/pts?from=ranking` (`ui.route` `ranking/pts`), one `GET /api/pts?ranking=night_up`. Header has no `data-tab="pts"`. With data: `#pts-tbl` has 11 header cells and 75 rows; `#pts-ranking` 値上がり is `.on`; `#pts-date` selects the newest day. On 2026-10-19 (copy filled at 10:01 JST): session 2026-10-16, `#pts-at` `10-19 10:01（06:02現在）`, row 1 `オンデック 7360` 711.0 → 861.0 `+21.1%`, info `2026-10-16 終値比 / 翌営業日 未取込`. Golden `pts_snapshots` is empty: pane copy 「PTS のデータがまだありません」.
- **Ranking.** Click `.ftab[data-ranking="night_down"]` → `GET /api/pts?ranking=night_down&date=2026-10-16`, 値下がり is `.on`, row 1 has a negative 終値比.
- **To 株価.** Click a name → `#tab-stock` shown (no nav 銘柄 `.active`), 株価 `.on`, hash `#stock/{code}/price?from=ranking-pts`, `GET /api/stock/{code}/overview` then `GET /api/chart/{code}` + `/api/earnings-performance/{code}` (filled DB 2026-10-27: row 1 サークレイス → `#stock/50290/price?from=ranking-pts`). Golden `pts_snapshots` is empty, so this path is `verified-unreachable`.
- **Route.** On `#pts` or `#ranking/pts`, reload → ランキング active, PTS sub `.on`, hash `#ranking/pts` (no query), and `#pts-tbl` filled again. After the inner-tab click (`#ranking/pts?from=ranking`), clicking the ランキング header again keeps `#ranking/pts?from=ranking`.
- **Proof.** Keep `artifacts/pts/body.json`. If CDP: `ui.png` of `#pts-tbl` with the header, and a shot of the header nav with no PTS tab.

## Gotchas

- There is no `nav button[data-tab="pts"]`. Open ランキング first, then `#rk-subs [data-rksub="pts"]`. `#rk-subs` and `#pts-ranking` and `#rk-kind` are all `.ftab`; select by `[data-rksub]`, `[data-ranking]`, or `[data-dir]`, not bare `.ftab`.
- Switching away and back does not reload when `#pts-tbl` already has HTML (`showRankingSub`). Reload or change a control to refetch.
- `session_date` is the day of the 通常取引 close the ranking compares against, not the fetch day. Before the evening session starts, 株探 still shows the previous session, so a Monday-morning fetch lands on Friday's session.
- `fetched_at` is JST without a zone (`app/market/pts.py`). The `at` query must match a `snapshots[].fetched_at` exactly; an unknown `at` falls back to the latest fetch.
- `%` cells use `pct()` (positive `.pos` red, negative `.neg` green).
- Empty `#pts-info` is not only the no-snapshots copy. `dates: []` → 「PTS のデータがまだありません」. Dates exist but this ranking has no snapshot → 「この日のランキングはありません」. A thrown fetch → 「読み込みに失敗しました」 and `#pts-tbl` is cleared. `#pts-info` uses the first row with `next_date` (not `next_open`); a later bar with a null open can show a date and still leave `#pts-summary` blank (`summary.n` is non-null `open_vs_pts_pct` count).
