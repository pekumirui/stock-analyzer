---
name: verify-stock-analyzer
description: "Drive the stock-analyzer SPA (FastAPI + static/index.html) the way a user does: launch uvicorn, open tabs, call the same /api/* the UI uses, capture evidence. Reach for this when proving 決算速報 / チャート / スクリーナー / 月次 / 型の調査 after a UI or API change."
---

# verify-stock-analyzer

Personal Japan-equity viewer. Primary surface is the single-page UI at `/` (`static/index.html`). REST under `/api/*` is what the SPA fetches; batch scripts are ingest, not the user surface.

This skill is for the **next agent**. Do not invent selectors or ports.

## Launch

Windows, repo root, `.venv` already created (`.\setup.ps1`). Do **not** run `.\run.ps1 serve` for verification: that binds 8000 with `--reload` and fights the user's session.

Run this as a **long-lived background command** (it prints one JSON line when ready, then waits so the child is not reaped):

```powershell
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\launch.py
```

Default URL: `http://127.0.0.1:8011`. Override with env `VERIFY_PORT`. Ready when stdout JSON appears and `GET /` returns 200 whose HTML contains `<title>株式分析ツール</title>`. Visible header `h1` is `📈 株式分析`. State is `.cursor/skills/verify-stock-analyzer/.run/state.json` (`pid`, `port`, `url`). Set `VERIFY_WAIT=0` only if you confirmed the child survives after launch.py exits.

Uses the repo's `stock.db` (read-only for UI proofs). Do not `.\run.ps1 reset`. Do not point `DATABASE_URL` at the user's DB from a second writer. If port 8011 is taken, launch exits non-zero — pick another `VERIFY_PORT` or cleanup the previous verify run. Never steal 8000.

If `stock.db` is missing, launch still starts (empty UI). Doctor then fails until backfill exists.

Teardown: see Cleanup. Keep the server up for the whole drive.

## Doctor

Read-only. Run first whenever anything looks off.

```powershell
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\doctor.py
```

Exit 0 only when:

- `.run/state.json` pid is alive and `GET {url}/` is 200 with `<title>株式分析ツール</title>`
- `GET /api/screener?stage=2&rs_min=70` returns JSON `{date, results}` and `results` is a non-empty list (this DB has been backfilled)
- `GET /api/stocks/search?q=トヨタ` returns at least one hit whose `code` does not start with `0`

Stdout is JSON. If screener is empty, the instance is up but not worth driving as a populated app — say so and stop.

## Drive

Harness: HTTP against the same routes the SPA uses, plus the browser when CDP is available.

**SPA handles (do not use coordinates):**

| User control | Handle |
|---|---|
| 決算速報 tab | `nav button[data-tab="earnings"]` (default, `#tab-earnings` visible) |
| 月次 tab | `nav button[data-tab="monthly"]` |
| 型の調査 tab | `nav button[data-tab="census"]` |
| チャート tab | `nav button[data-tab="chart"]` |
| スクリーナー tab | `nav button[data-tab="screener"]` |
| Earnings calendar | `#cal-btn` opens `#cal-pop`; day cells are `#cal .d` with `onclick="selectDate('YYYY-MM-DD')"`. Days with disclosures show `.cnt`. Esc / `#cal-backdrop` / a day click close the popover. |
| Earnings row | `#earn-tbl tr[data-no]` |
| 累計 / 3か月 | `.ftab[data-fin="cum"]` (default on) / `.ftab[data-fin="q"]` |
| Chart search | `#sym` (placeholder `銘柄名 or コード`), suggestions `#suggest` |
| Timeframe | `#tf` (`D` / `M`) |
| MA list | `#mas` (default `50,150,200`) |
| 対TOPIX | `#rel` checkbox |
| Screener stage | `#sc-stage` (default `2`) |
| Screener RS | `#sc-rs` (default `70`) |
| Screener 検索 | `#tab-screener button.btn` (label 検索) |

**SPA → API (prove the same payloads the UI renders):**

| UI action | Request |
|---|---|
| Open 決算速報 calendar | `GET /api/earnings/calendar?month=YYYY-MM` |
| Click a day | `GET /api/earnings?date=YYYY-MM-DD` |
| Select a row | `GET /api/earnings/detail/{code}` |
| Chart search typeahead | `GET /api/stocks/search?q=...` |
| Chart 表示 (UI defaults) | `GET /api/chart/{code}?tf=D&ma=50,150,200&rel=false` and `GET /api/earnings-performance/{code}` |
| Screener 検索 (UI defaults) | `GET /api/screener?stage=2&rs_min=70` |
| 月次 calendar | `GET /api/monthly/calendar?month=YYYY-MM` |
| 月次 day list | `GET /api/monthly?date=YYYY-MM-DD` |
| 月次 issuer | `GET /api/monthly/{code}` |
| 型の調査 | `GET /api/monthly/census` |

HTTP capture:

```powershell
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\capture.py screener
```

Writes under `artifacts/<feature>/`. For a calendar day, pass `capture.py earnings --date YYYY-MM-DD`.

**Browser (user path).** When `browser-use` / CDP works: `new_tab(url)` from state.json, `wait_for_load()`, click `nav button[data-tab="..."]`, then the feature file. Do not `goto_url` for the first navigation. Screenshots via `capture_screenshot()` saved into `artifacts/<feature>/`. If Chrome remote debugging is denied, record that and finish the proof with the HTTP captures of the same routes — do not click the user's existing tab on :8000.

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
.\.venv\Scripts\python.exe .cursor\skills\verify-stock-analyzer\helpers\cleanup.py
```

Kills **only** the pid in `.run/state.json` (Windows `taskkill /PID /T`). Removes `.run/`. Does **not** remove `artifacts/`, `stock.db`, or `.venv`. Never `taskkill /IM python.exe`.

After cleanup, confirm `artifacts/` still has the files from this run.

## Helpers

All run from the repo root with `.venv\Scripts\python.exe`.

| Script | What |
|---|---|
| `helpers/launch.py` | Start uvicorn on `VERIFY_PORT` (8011), wait for `GET /`, write `.run/state.json` |
| `helpers/doctor.py` | Read-only health JSON; exit 1 if not worth driving |
| `helpers/capture.py <feature> [--date YYYY-MM-DD] [--code CODE]` | Hit the SPA's API and write `artifacts/<feature>/` |
| `helpers/cleanup.py` | Stop the pid we started; keep artifacts |
