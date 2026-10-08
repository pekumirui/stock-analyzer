# stock-analyzer verification map

Read this index, then the matching feature file, before driving the app.

## Baseline preconditions

- Launch with `helpers/launch.py` at `http://127.0.0.1:8000` (or `VERIFY_PORT`). SPA entry is `GET /app` (`GET /` → public `/monthly`). Reuses an already-running SPA on that port (`adopted: true`).
- `stock.db` is the backfilled SQLite next to the repo (not a CI temp DB, not demo `0`-prefix codes).
- Run `helpers/doctor.py` and require `ok: true`, non-empty screener, and a トヨタ search hit.
- Drive the instance recorded in `.run/state.json` (adopted or launched).
- 月次 Detail needs a non-empty `GET /api/v1/tables`. Cloud Agent builds copy production `monthly_*` when `VPS_SSH_KEY` is set; empty monthly is `verified-unreachable` (see `features/monthly.md`).

## Driving conventions

- Boot restores the last tab (and chart/monthly code) from `location.hash` or `localStorage` key `ui.route` via `restoreRoute()` (`static/js/tabs.js`, called from `boot.js`). For a cold 決算速報 start, clear hash + `ui.route` then reload, or open a fresh profile.
- Prefer `nav button[data-tab]`, `#id`, and `data-fin` / `data-no` over pixel clicks.
- Treat every command as literal.
- HTTP proofs use `helpers/capture.py <feature>` against the same `/api/*` the SPA calls.
- Browser proofs use `browser-use`: open `{url}/app` then click the tab handle.
- Cleanup removes `.run/` only. Leave `artifacts/`.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final screen.
- UI proof includes a screenshot with header `h1` `株式分析` visible (document title is `株式分析ツール`).
- HTTP proof includes `request.txt` and `body.json`.
- Record the feature ID and entry point with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with verify-stock-analyzer` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [決算速報](./earnings.md) calendar day list, row detail, 累計 / 3か月 tabs.
- [チャート](./chart.md) symbol search, daily/weekly/monthly candles, 対TOPIX, 決算発表後リターン.
- [スクリーナー](./screener.md) Stage + RS filter table (UI defaults Stage 2, RS≥70).
- [月次](./monthly.md) issuer list + industry chips + TOP search via `/api/v1/tables`, Detail with series select, year-panel bar charts, one 横持ち `table.monthly-tl` (metric ▸ section tree, paired cells show 金額 with (前年比) under it, month-cell popup).
