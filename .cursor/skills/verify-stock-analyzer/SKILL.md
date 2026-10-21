---
name: verify-stock-analyzer
description: "Drive the stock-analyzer SPA (FastAPI + static/index.html + static/css/ + static/js/) the way a user does: launch uvicorn, open tabs, call the same /api/* the UI uses, capture evidence. Reach for this when proving 決算速報 / チャート / スクリーナー / PTS / 月次 / サプライ after a UI or API change."
---

# verify-stock-analyzer

Personal Japan-equity viewer. The Basic SPA is `/app` (`static/index.html` + `static/css/` + `static/js/`). `GET /` 301s to public `/monthly`. REST under `/api/*` is what the SPA fetches. Batch scripts ingest data. They are not the user UI.

Do not invent selectors or ports.

## Launch

Repo root, `.venv` already created (`.\setup.ps1` on Windows, `./setup.sh` on Linux/mac). Prefer the already-running app on `:8000` when Cloud Agent `start` (or the user's `./run.sh serve`) has it up. Otherwise the helper starts uvicorn on `VERIFY_PORT` (default **8000**).

Prefer a helper that picks the right interpreter (`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` on Linux/mac). Run launch as a **long-lived background command** when it starts a new process (it prints one JSON line when ready, then waits so the child is not reaped). If it **adopts** an existing `:8000`, it prints JSON and exits:

```powershell
# Windows
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\launch.py
```

```bash
# Linux / mac
.venv/bin/python .cursor/skills/verify-stock-analyzer/helpers/launch.py
```

Default URL: `http://127.0.0.1:8000`. Override with env `VERIFY_PORT`. Ready when stdout JSON appears and `GET /app` returns 200 whose HTML contains `<title>株式分析ツール</title>`. Visible header `h1` is `📈 株式分析` (a link `a#home` back to 決算速報). `GET /` は `/monthly` へ 301。 State is `.cursor/skills/verify-stock-analyzer/.run/state.json` (`pid`, `port`, `url`, `adopted`, `db`). Adopted servers have `"adopted": true`, `pid: null`, and `db: null` (their DB is unknown; a leftover server from another worktree can be on a different DB and older code). Do not kill them. Pick another `VERIFY_PORT` when you need your own code or DB. A started server inherits `DATABASE_URL`, and `db` records it (else the repo `stock.db`). Set `VERIFY_WAIT=0` only if you confirmed the child survives after launch.py exits.

Uses the repo's `stock.db` (read-only for UI proofs). Do not `.\run.ps1 reset` / `./run.sh reset`. Do not point `DATABASE_URL` at the user's DB from a second writer. If the port is taken by a non-SPA process, launch exits non-zero. Free the port or pick another `VERIFY_PORT`.

If `stock.db` is missing, launch still starts (empty UI). Doctor then fails until a DB exists: `./run.sh golden` (seconds; golden real data for ~45 codes, all monthly, recent `material_*`) or `./run.sh backfill 2` (all codes, ~40 min). `jobs/golden_db.py` writes to `DATABASE_URL`, so `DATABASE_URL=sqlite:////abs/path/golden.db .venv/bin/python jobs/golden_db.py` builds a side DB (about 6 s) without touching `stock.db`.

If launch prints `uvicorn exited 1`, read `.run/uvicorn.log`. `app.db.SchemaMismatch` (e.g. `xbrl_financials: value_kind, range_side, target_period, source_layer`, or `stocks: edinet_code` / `xbrl_contexts: member_labels_json` from the EDINET ingest #793/#804) means the DB is older than the code. `./run.sh migrate` (`jobs/migrate.py`) adds the columns; run it only on a DB this run owns (a copy), never on a shared or production DB. On start the app also `create_all`s missing tables (e.g. empty `pts_snapshots`).

Teardown: see Cleanup. Keep the server up for the whole drive.

## Doctor

Read-only. Run first whenever anything looks off.

```powershell
# Windows
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\doctor.py
```

```bash
# Linux / mac
.venv/bin/python .cursor/skills/verify-stock-analyzer/helpers/doctor.py
```

Exit 0 only when:

- `.run/state.json` pid is alive and `GET {url}/app` is 200 with `<title>株式分析ツール</title>`
- `GET /api/screener?stage=2&rs_min=70` returns JSON `{date, results}` and `results` is a non-empty list (this DB has been backfilled)
- `GET /api/stocks/search?q=トヨタ` returns at least one hit whose `code` does not start with `0`

Stdout is JSON. If screener is empty, the instance is up but the DB was not backfilled. Say so and stop.

## Drive

HTTP against the same routes the SPA uses, plus the browser when CDP is available.

**Route memory (#130):** `boot.js` calls `restoreRoute()` after priming today's earnings list. Hash / `localStorage` `ui.route` forms are `earnings` | `screener` | `pts` | `chart` | `monthly` | `supply` | `chart/{code}` | `monthly/{code}` | `supply/{id}` (material or company id, `^[a-z][a-z0-9_]*$`). Clear them when proving the cold default tab.

**SPA handles (do not use coordinates):**

| User control | Handle |
|---|---|
| 決算速報 tab | `nav button[data-tab="earnings"]` (fallback default when no `ui.route`/hash; `#tab-earnings` visible). `#home` (header title) also selects it |
| Header ≤720px | Tabs live in drawer `nav#app-nav` (off-screen until `.open`). `#menu-btn` (三本線) toggles it, `#nav-backdrop` / Esc / a tab pick / left swipe close it. `#cur-tab` shows the current tab name. Above 720px `#menu-btn` and `#cur-tab` are `display:none` and the nav is one row |
| 月次 tab | `nav button[data-tab="monthly"]` |
| チャート tab | `nav button[data-tab="chart"]` |
| スクリーナー tab | `nav button[data-tab="screener"]` |
| PTS tab | `nav button[data-tab="pts"]` |
| サプライ tab | `nav button[data-tab="supply"]` |
| Earnings calendar | `#cal-btn` opens `#cal-pop`; `#cal-title` + ◀/▶ (`calMove(±1)`) change month and refetch `/api/earnings/calendar?month=YYYY-MM&today=YYYY-MM-DD` (`today` is `todayISO()`). A month already fetched is drawn from `calCells` first, then refetched; boot prefetches this month after the first list (`calPrefetch`). Day cells are `#cal .d` with `onclick="selectDate('YYYY-MM-DD')"`. A day with filings or a schedule shows `.cnt` (`cell.n` plus `件`). Future schedules add `.cnt.sched`. Today with names still scheduled adds `.cnt.pend` as `+N`. Esc / `#cal-backdrop` / a day click close the popover. |
| Earnings row | `#earn-tbl tr[data-no]` for a filing. `#earn-tbl tr[data-code]` for a scheduled issuer (QoQ/YoY and time are `–`). The first row of the day is auto-selected. Columns 時刻 / 決算期 / 銘柄 / `3か月 / QoQ` ×4 / `累計 / YoY` ×4 (11 cells). Metric cells show the amount with the rate under it in `.g` |
| Earnings search | `#earn-sym` (placeholder `銘柄名 or コード`), suggestions `#earn-suggest` from `/api/stocks/search`. A pick jumps the list to that issuer's latest filing day |
| Earnings sector chips | `#earn-groups .mchip[data-sector]` (すべて = `""`, then `sector33` by issuer count, count in `.n`). Filters `#earn-tbl`; reset on every `selectDate` |
| 累計 / 3か月 / 修正履歴 | `.ftab[data-fin="cum"]` (default on) / `.ftab[data-fin="q"]` / `.ftab[data-fin="rev"]` → `#cum-wrap` / `#q-wrap` / `#rev-wrap`. Use `[data-fin]`: the PTS ranking buttons are `.ftab` too |
| Earnings embedded chart | `#e-chart` / `.a-chart` (same `/api/chart` as chart tab) |
| Event overlays | `.evt-toggles` on earnings + chart (`input[data-evt="earnings"]`, label 決算発表) |
| Chart search | `#sym` (placeholder `銘柄名 or コード`), suggestions `#suggest` |
| Chart 表示 | `#tab-chart button.btn` (label 表示). Required after changing `#tf` / `#mas` / `#rel` |
| Timeframe | `#tf` (`D` / `W` / `M`) |
| MA list | `#mas` (default `50,150,200`) |
| 対TOPIX | `#rel` checkbox |
| Screener stage | `#sc-stage` (default `2`) |
| Screener RS | `#sc-rs` (default `70`) |
| Screener 検索 | `#tab-screener button.btn` (label 検索) |
| PTS controls | `#pts-date` (session day), `#pts-ranking .ftab[data-ranking]` (`night_up` 値上がり / `night_down` 値下がり / `night_volume` 出来高), `#pts-at` (取得時刻), `#pts-info`, `#pts-summary`, `#pts-tbl` (name `.clicky` → `gotoChart`) |
| Monthly list | `#monthly-groups`, `#monthly-list` (host), `#monthly-tbl` |
| Monthly issuer search | `#monthly-sym` (placeholder `銘柄名 or コード`), suggestions `#monthly-suggest` from loaded monthly issuers (not `/api/stocks/search`). Enter or suggestion pick filters `#monthly-tbl`; company-name click opens Detail |
| Monthly detail | `#monthly-detail`, `#monthly-series`, `#monthly-chart`, `#monthly-table-wrap`, `#monthly-back` |
| Monthly timeline | `#monthly-table-wrap table.monthly-tl` (the only Detail table on `/app` and public `/monthly`). Tree toggles are `td.tl-name.tl-tog`. Month cells are `td.num[data-m]` and open `#monthly-pop`. Paired amount/yoy rows show both in the cell (`.tl-sub`) |
| Supply map | `#supply-map` (Cytoscape; nodes are material ids, `.sel` / `.dim` / `.ctry`). Tap a box to pick; empty map or `#supply-reset` (全体表示) clears. `#supply-merge` (線をまとめる) re-lays out |
| Supply search | `#supply-q` + `datalist#supply-names`; commits on `change` (exact label → 4-digit code → material name part → company name part) |
| Supply panel | `#supply-panel`: material chips `.supply-chip[data-id]`, company chips `.supply-chip.co[data-co]`, `.supply-share` donut + rows, `.supply-group`, `#supply-countries`, `#supply-monthly` (`tr.supply-row[data-i]`, `#supply-chart`), company `#supply-chart-link` |

**SPA → API (prove the same payloads the UI renders):**

| UI action | Request |
|---|---|
| Open 決算速報 calendar | `GET /api/earnings/calendar?month=YYYY-MM&today=YYYY-MM-DD` |
| Click a day | `GET /api/earnings?date=YYYY-MM-DD&today=YYYY-MM-DD` |
| Select a filing row | `GET /api/earnings/detail/{code}?disclosure_no=...` (also carries `revisions` for 修正履歴) and `GET /api/chart/{code}?tf=D&ma=50,150,200` into `#e-chart` |
| Select a scheduled row | `GET /api/chart/{code}?tf=D&ma=50,150,200` only. `#cum-tbl`, `#q-tbl`, and `#rev-tbl` read `まだ開示されていません` |
| Chart search typeahead | `GET /api/stocks/search?q=...` |
| Chart 表示 (UI defaults) | `GET /api/chart/{code}?tf=D&ma=50,150,200&rel=false` (may include `earnings_dates`) and `GET /api/earnings-performance/{code}` |
| Screener 検索 (UI defaults) | `GET /api/screener?stage=2&rs_min=70` |
| Open PTS (first time, or reload on `#pts`) | `GET /api/pts?ranking=night_up` |
| PTS ranking / session day / 取得時刻 | `GET /api/pts?ranking=...&date=YYYY-MM-DD` (+ `&at=YYYY-MM-DD HH:MM:SS` from `#pts-at`) |
| 月次 TOP list | `GET /api/v1/tables` |
| 月次 Detail years | `GET /api/v1/tables/{code}` |
| 月次 Detail year table | `GET /api/v1/tables/{code}/{year}` (includes `tree` / `tree_metrics`) |
| 月次 month-cell popup | `GET /api/v1/tables/{code}/{year}` for each year ≥ 2024 not loaded yet |
| Open サプライ (first time per page) | `GET /api/supply/graph` + `GET /api/supply/companies` (+ cytoscape / elkjs from unpkg) |
| Pick a material | `GET /api/supply/monthly/{id}` (every pick) and `GET /api/supply/countries/{id}` (once per id per page). Picking a company calls nothing |

HTTP capture:

```powershell
# Windows
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\capture.py screener
```

```bash
# Linux / mac
.venv/bin/python .cursor/skills/verify-stock-analyzer/helpers/capture.py screener
```

Writes under `artifacts/<feature>/`. For a calendar day, pass `capture.py earnings --date YYYY-MM-DD`. Across a month boundary, pass `--month YYYY-MM` (machine local month, same as `renderCal`). For monthly Detail, pass `capture.py monthly --code CODE`. For サプライ, pass `capture.py supply --id MATERIAL_ID` (default `ethylene`). For PTS, pass `capture.py pts [--ranking night_up|night_down|night_volume] [--date YYYY-MM-DD]`; it exits 0 on an empty DB and prints `dates=` / `rows=`.

**Browser (user path).** When `browser-use` / CDP works: open `{url}/app` from state.json (`new_tab(url + "/app")`), `wait_for_load()`, click `nav button[data-tab="..."]`, then the feature file. Bare `{url}/` is public `/monthly` (301), not the Basic SPA. Do not `goto_url` for the first navigation. Screenshots via `capture_screenshot()` saved into `artifacts/<feature>/`. If Chrome remote debugging is denied, record that and finish the proof with the HTTP captures of the same routes.

`GET /app` rewrites `/static/...` URLs to `?v=<content hash>` and sends `ETag`. `/static/*` gets `Cache-Control: no-cache`. `boot.js` reloads on a bfcache restore. After 60 s hidden it also reloads when the page or asset ETags changed. Expect a reload when you edit `static/` while a tab is open.

Read `features/README.md` and the matching feature file before driving. Cover every entry point the file lists, or report the skipped one with the unmet precondition.

## Evidence

Directory: `.cursor/skills/verify-stock-analyzer/artifacts/<feature>/`. Cleanup must not delete this tree.

Proof standards:

- Exercise the real SPA routes (`/app` + `/api/*` the UI calls). Do not hit TestClient, `tests/test_smoke.py` fixtures, or invent test-only endpoints.
- Capture the **action** (request URL + status, or the click) and the **resulting state** (JSON body and/or screenshot), not only a final screen.
- Screener / search / earnings lists must show real names. API `code` is 5 characters and must not start with `0`. Table labels use the first 4 (`code.slice(0,4)`: 7203 / 262A), not the stored 5-digit string.
- Chart proof needs `candles` length > 0 in `/api/chart/{code}`.
- Side effects: this UI is read-only. Do not write `stock.db`. If a run did write, say so.
- No mocks. J-Quants is not called by `serve`; empty tables mean the DB was never backfilled.

Minimum files per feature:

- `request.txt`: method, URL, status
- `body.json`: response (truncate lists to 20 rows if huge, keep `date` / counts)
- `ui.png`: only when the browser path ran. The visible brand is header `h1` `株式分析` (document title is `株式分析ツール`)

## Cleanup

```powershell
# Windows
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\cleanup.py
```

```bash
# Linux / mac
.venv/bin/python .cursor/skills/verify-stock-analyzer/helpers/cleanup.py
```

Kills **only** a pid this skill started (`adopted: false` in `.run/state.json`). Adopted `:8000` (Cloud Agent `start` / user's serve) is left running. Removes `.run/`. Does **not** remove `artifacts/`, `stock.db`, or `.venv`. Never `taskkill /IM python.exe` or blanket-kill all Python processes.

After cleanup, confirm `artifacts/` still has the files from this run.

## Helpers

All run from the repo root with the venv interpreter (`.venv\Scripts\python.exe` or `.venv/bin/python`). `launch.py` resolves that path itself when spawning uvicorn.

| Script | What |
|---|---|
| `helpers/launch.py` | Adopt or start uvicorn on `VERIFY_PORT` (default 8000), wait for `GET /app`, write `.run/state.json` |
| `helpers/doctor.py` | Read-only health JSON. Exit 1 if screener is empty or the SPA is not up |
| `helpers/capture.py <feature> [--date YYYY-MM-DD] [--month YYYY-MM] [--code CODE] [--id MATERIAL_ID] [--ranking RANKING]` | GET the SPA `/api/*` routes into `artifacts/<feature>/` (features: screener / search / earnings / chart / pts / monthly / supply) |
| `helpers/cleanup.py` | Stop a pid we started; leave adopted servers alone; keep artifacts |
