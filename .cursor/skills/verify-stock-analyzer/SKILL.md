---
name: verify-stock-analyzer
description: "Drive the stock-analyzer SPA (FastAPI + static/index.html + static/css/ + static/js/) the way a user does: launch uvicorn, open tabs, call the same /api/* the UI uses, capture evidence. Reach for this when proving 決算速報 / チャート / スクリーナー / 月次 after a UI or API change."
---

# verify-stock-analyzer

Personal Japan-equity viewer. Primary surface is the single-page UI at `/` (`static/index.html` + `static/css/` + `static/js/`). REST under `/api/*` is what the SPA fetches; batch scripts are ingest, not the user surface.

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

Default URL: `http://127.0.0.1:8000`. Override with env `VERIFY_PORT`. Ready when stdout JSON appears and `GET /` returns 200 whose HTML contains `<title>株式分析ツール</title>`. Visible header `h1` is `📈 株式分析`. State is `.cursor/skills/verify-stock-analyzer/.run/state.json` (`pid`, `port`, `url`, `adopted`). Adopted servers have `"adopted": true` and `pid: null` — do not kill them. Set `VERIFY_WAIT=0` only if you confirmed the child survives after launch.py exits.

Uses the repo's `stock.db` (read-only for UI proofs). Do not `.\run.ps1 reset` / `./run.sh reset`. Do not point `DATABASE_URL` at the user's DB from a second writer. If the port is taken by a non-SPA process, launch exits non-zero — free the port or pick another `VERIFY_PORT`.

If `stock.db` is missing, launch still starts (empty UI). Doctor then fails until backfill exists.

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

- `.run/state.json` pid is alive and `GET {url}/` is 200 with `<title>株式分析ツール</title>`
- `GET /api/screener?stage=2&rs_min=70` returns JSON `{date, results}` and `results` is a non-empty list (this DB has been backfilled)
- `GET /api/stocks/search?q=トヨタ` returns at least one hit whose `code` does not start with `0`

Stdout is JSON. If screener is empty, the instance is up but not worth driving as a populated app — say so and stop.

## Drive

Harness: HTTP against the same routes the SPA uses, plus the browser when CDP is available.

**Route memory (#130):** `boot.js` calls `restoreRoute()` after priming today's earnings list. Hash / `localStorage` `ui.route` forms are `earnings` | `screener` | `chart` | `monthly` | `chart/{code}` | `monthly/{code}`. Clear them when proving the cold default tab.

**SPA handles (do not use coordinates):**

| User control | Handle |
|---|---|
| 決算速報 tab | `nav button[data-tab="earnings"]` (fallback default when no `ui.route`/hash; `#tab-earnings` visible) |
| 月次 tab | `nav button[data-tab="monthly"]` |
| チャート tab | `nav button[data-tab="chart"]` |
| スクリーナー tab | `nav button[data-tab="screener"]` |
| Earnings calendar | `#cal-btn` opens `#cal-pop`; `#cal-title` + ◀/▶ (`calMove(±1)`) change month and refetch `/api/earnings/calendar?month=YYYY-MM`; day cells are `#cal .d` with `onclick="selectDate('YYYY-MM-DD')"`. Days with disclosures show `.cnt`. Esc / `#cal-backdrop` / a day click close the popover. |
| Earnings row | `#earn-tbl tr[data-no]` (first row auto-selected on day load) |
| 累計 / 3か月 | `.ftab[data-fin="cum"]` (default on) / `.ftab[data-fin="q"]` |
| Earnings period chips | `#q-chips` (display only) |
| Earnings embedded chart | `#e-chart` / `.a-chart` (same `/api/chart` as chart tab) |
| Event overlays | `.evt-toggles` on earnings + chart (`input[data-evt="earnings"]`, label 決算発表) |
| Chart search | `#sym` (placeholder `銘柄名 or コード`), suggestions `#suggest` |
| Chart 表示 | `#tab-chart button.btn` (label 表示) — required after changing `#tf` / `#mas` / `#rel` |
| Timeframe | `#tf` (`D` / `W` / `M`) |
| MA list | `#mas` (default `50,150,200`) |
| 対TOPIX | `#rel` checkbox |
| Screener stage | `#sc-stage` (default `2`) |
| Screener RS | `#sc-rs` (default `70`) |
| Screener 検索 | `#tab-screener button.btn` (label 検索) |
| Monthly list | `#monthly-groups`, `#monthly-list` (host), `#monthly-tbl` |
| Monthly issuer search | `#monthly-sym` (placeholder `銘柄名 or コード`), suggestions `#monthly-suggest` from loaded monthly issuers (not `/api/stocks/search`). Enter or suggestion pick filters `#monthly-tbl`; company-name click opens Detail |
| Monthly detail | `#monthly-detail`, `#monthly-series`, `#monthly-chart`, `#monthly-table-wrap`, `#monthly-back` |

**SPA → API (prove the same payloads the UI renders):**

| UI action | Request |
|---|---|
| Open 決算速報 calendar | `GET /api/earnings/calendar?month=YYYY-MM` |
| Click a day | `GET /api/earnings?date=YYYY-MM-DD` |
| Select a row | `GET /api/earnings/detail/{code}?disclosure_no=...` and `GET /api/chart/{code}?tf=D&ma=50,150,200` into `#e-chart` |
| Chart search typeahead | `GET /api/stocks/search?q=...` |
| Chart 表示 (UI defaults) | `GET /api/chart/{code}?tf=D&ma=50,150,200&rel=false` (may include `earnings_dates`) and `GET /api/earnings-performance/{code}` |
| Screener 検索 (UI defaults) | `GET /api/screener?stage=2&rs_min=70` |
| 月次 TOP list | `GET /api/v1/tables` |
| 月次 Detail years | `GET /api/v1/tables/{code}` |
| 月次 Detail year table | `GET /api/v1/tables/{code}/{year}` |

HTTP capture:

```powershell
# Windows
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\capture.py screener
```

```bash
# Linux / mac
.venv/bin/python .cursor/skills/verify-stock-analyzer/helpers/capture.py screener
```

Writes under `artifacts/<feature>/`. For a calendar day, pass `capture.py earnings --date YYYY-MM-DD`. For monthly Detail, pass `capture.py monthly --code CODE`.

**Browser (user path).** When `browser-use` / CDP works: `new_tab(url)` from state.json, `wait_for_load()`, click `nav button[data-tab="..."]`, then the feature file. Do not `goto_url` for the first navigation. Screenshots via `capture_screenshot()` saved into `artifacts/<feature>/`. If Chrome remote debugging is denied, record that and finish the proof with the HTTP captures of the same routes.

Read `features/README.md` and the matching feature file before driving. Cover every entry point the file lists, or report the skipped one with the unmet precondition.

## Evidence

Directory: `.cursor/skills/verify-stock-analyzer/artifacts/<feature>/`. Cleanup must not delete this tree.

Proof standards:

- Exercise the real SPA routes (`/` + `/api/*` the UI calls). Do not hit TestClient, `ci_smoke.py` fixtures, or invent test-only endpoints.
- Capture the **action** (request URL + status, or the click) and the **resulting state** (JSON body and/or screenshot), not only a final screen.
- Screener / search / earnings lists must show real names and 5-digit codes that do not start with `0`.
- Chart proof needs `candles` length > 0 in `/api/chart/{code}`.
- Side effects: this UI is read-only. Do not write `stock.db`. If a run did write, say so.
- No mocks. J-Quants is not called by `serve`; empty tables mean the DB was never backfilled.

Minimum files per feature:

- `request.txt` — method, URL, status
- `body.json` — response (truncate lists to 20 rows if huge, keep `date` / counts)
- `ui.png` — only when the browser path ran. The visible brand is header `h1` `株式分析` (document title is `株式分析ツール`)

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
| `helpers/launch.py` | Adopt or start uvicorn on `VERIFY_PORT` (default 8000), wait for `GET /`, write `.run/state.json` |
| `helpers/doctor.py` | Read-only health JSON; exit 1 if not worth driving |
| `helpers/capture.py <feature> [--date YYYY-MM-DD] [--code CODE]` | GET the SPA `/api/*` routes into `artifacts/<feature>/` |
| `helpers/cleanup.py` | Stop a pid we started; leave adopted servers alone; keep artifacts |
