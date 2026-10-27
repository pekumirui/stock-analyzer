---
name: verify-stock-analyzer
description: "Drive the stock-analyzer SPA (FastAPI + static/index.html + static/css/ + static/js/) the way a user does: launch uvicorn, open tabs, call the same /api/* the UI uses, capture evidence. Reach for this when proving 決算速報 / 銘柄 (概要 / 株価 / ニュース / 株主) / スクリーナー / PTS / 月次 / サプライ / ランキング after a UI or API change."
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

Default URL: `http://127.0.0.1:8000`. Override with env `VERIFY_PORT`. Ready when stdout JSON appears and `GET /app` returns 200 whose HTML contains `<title>株式分析ツール</title>`. The header has no brand title and no `#home`: its children are `#menu-btn`, `#cur-tab`, `nav#app-nav` (7 tabs: 決算速報 / 銘柄 / スクリーナー / PTS / 月次 / サプライ / ランキング; the チャート tab moved into 銘柄 › 株価 in KAB-181). `meta[name=theme-color]` is `#ffffff` and the header has no top blue line (KAB-184); ≤720px `#menu-btn` is first then `#cur-tab`. `GET /` は `/monthly` へ 301。 State is `.cursor/skills/verify-stock-analyzer/.run/state.json` (`pid`, `port`, `url`, `adopted`, `db`). Adopted servers have `"adopted": true`, `pid: null`, and `db: null` (their DB is unknown; a leftover server from another worktree can be on a different DB and older code). Do not kill them. Pick another `VERIFY_PORT` when you need your own code or DB. A started server inherits `DATABASE_URL`, and `db` records it (else the repo `stock.db`). Set `VERIFY_WAIT=0` only if you confirmed the child survives after launch.py exits.

Uses the repo's `stock.db` (read-only for UI proofs). Do not `.\run.ps1 reset` / `./run.sh reset`. Do not point `DATABASE_URL` at the user's DB from a second writer. If the port is taken by a non-SPA process, launch exits non-zero. Free the port or pick another `VERIFY_PORT`.

If `stock.db` is missing, launch still starts (empty UI). Doctor then fails until a DB exists: `./run.sh golden` (seconds; golden real data for ~45 codes, all monthly, recent `material_*`) or `./run.sh backfill 2` (all codes, ~40 min). `jobs/golden_db.py` writes to `DATABASE_URL`, so `DATABASE_URL=sqlite:////abs/path/golden.db .venv/bin/python jobs/golden_db.py` builds a side DB (about 6 s) without touching `stock.db`.

If launch prints `uvicorn exited 1`, read `.run/uvicorn.log`. `app.db.SchemaMismatch` (e.g. `xbrl_financials: value_kind, range_side, target_period, source_layer`, or `stocks: edinet_code` / `xbrl_contexts: member_labels_json` from the EDINET ingest #793/#804, or `xbrl_filings: documents_status` / `xbrl_std_items: unit` / `xbrl_element_mappings: part` from the 銘柄 tab and XBRL work after #881) means the DB is older than the code. `./run.sh migrate` (`jobs/migrate.py`) adds the columns; run it only on a DB this run owns (a copy), never on a shared or production DB. On start the app also `create_all`s missing tables (e.g. empty `pts_snapshots`, `large_holding_reports` / `large_holdings` from #815, `xbrl_documents` / `tdnet_pdf_documents` / `edinet_buybacks` / `edinet_tender_offers` after #881), so a DB missing only tables starts without SchemaMismatch.

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

**Route memory (#130):** `boot.js` calls `restoreRoute()` after priming today's earnings list. Hash / `localStorage` `ui.route` forms are `earnings` | `screener` | `pts` | `stock` | `monthly` | `supply` | `stock/{code}` | `stock/{code}/{summary|price|news|holders}` | `stock/{code}/news/{news_id}` (`^[a-z][0-9A-Za-z]{1,40}$`; `x{xbrl_filings.id}` or `p{disclosure_no}`) | `monthly/{code}` | `supply/{id}` (material or company id, `^[a-z][a-z0-9_]*$`). An unknown sub falls back to `summary`. Old `chart/{code}` (hash or `ui.route`) is rewritten to `stock/{code}/price`, bare `chart` to `stock`. Clear them when proving the cold default tab. Routing runs on boot and on `popstate` while 銘柄 is visible; there is no `hashchange` handler, so a hash-only jump to another tab (same document) does not switch tabs. Reload after setting the hash.

**SPA handles (do not use coordinates):**

| User control | Handle |
|---|---|
| 決算速報 tab | `nav button[data-tab="earnings"]` (fallback default when no `ui.route`/hash; `#tab-earnings` visible) |
| Header ≤720px | Tabs live in drawer `nav#app-nav` (off-screen until `.open`). `#menu-btn` (三本線, left of `#cur-tab`) toggles it, `#nav-backdrop` / Esc / a tab pick / left swipe close it. `#cur-tab` shows the current tab name. While the drawer is `.open`, `#menu-btn` and `#cur-tab` are `visibility:hidden`. Above 720px `#menu-btn` and `#cur-tab` are `display:none` and the nav is one row |
| 月次 tab | `nav button[data-tab="monthly"]` |
| 銘柄 tab | `nav button[data-tab="stock"]` (between 決算速報 and スクリーナー). Search `#st-sym` + `#st-suggest`. `#st-head` (hidden until the overview loads): `#st-title`, `#st-quote`, sub tabs `#st-tabs .ftab[data-sub="summary"|"price"|"news"|"holders"]` (概要 / 株価 / ニュース / 株主) → `#st-sub-*`. `#st-msg` for 銘柄が見つかりません。 / 読み込めませんでした。 / ニュースがありません。 See `features/stock.md` |
| 銘柄 › 概要 | `#st-metrics` (PER（予想） / PBR（実績） / 配当利回り（予想） / ROE（実績） / 自己資本比率), `#st-fin-tabs .ftab[data-stfin="cum"|"q"|"prog"|"rev"]` → `#st-cum-tbl` / `#st-q-tbl` / `#st-prog-tbl` / `#st-rev-tbl` |
| 銘柄 › 株価 | `#tf`, `#mas`, `#rel`, `#chart-msg`, `#chart`, `.evt-toggles`, `#relwrap`, `#perf-tbl`. No 表示 button: a `change` on `#tf` / `#rel` / `#mas` redraws. See `features/chart.md` |
| 銘柄 › ニュース | `table#st-news` `tr.go[data-id]` → `#st-news-detail`: `#st-back` (← ニュース), `#st-zoom .ftab[data-zoom="fit"|"mid"|"actual"]`, `#st-news-head`, `#st-src`, `#st-doc` (one sandboxed iframe per document) |
| スクリーナー tab | `nav button[data-tab="screener"]` |
| PTS tab | `nav button[data-tab="pts"]` |
| サプライ tab | `nav button[data-tab="supply"]` |
| Earnings calendar | `#cal-btn` opens `#cal-pop`; `#cal-title` + ◀/▶ (`calMove(±1)`) change month and refetch `/api/earnings/calendar?month=YYYY-MM&today=YYYY-MM-DD` (`today` is `todayISO()`). A month already fetched is drawn from `calCells` first, then refetched; boot prefetches this month after the first list (`calPrefetch`). Day cells are `#cal .d` with `onclick="selectDate('YYYY-MM-DD')"`. A day with filings or a schedule shows `.cnt` (`cell.n` plus `件`). Future schedules add `.cnt.sched`. Today with names still scheduled adds `.cnt.pend` as `+N`. Esc / `#cal-backdrop` / a day click close the popover. |
| Earnings row | `#earn-tbl tr[data-no]` for a filing. `#earn-tbl tr[data-code]` for a scheduled issuer (time and the four metric cells are `–`). The first row of the day is auto-selected. One header row: 時刻 / 決算期 / 銘柄 / 売上 / 営利 / 経利 / 純利 (7 cells). The four metric cells follow `#earn-view` (amount with the rate under it in `.g`, or 進捗率 with the prior year's in `.prev`) |
| Earnings list view / Q filter | `#earn-view .ftab[data-view="cum"]` 累計 (default) / `[data-view="q"]` 3か月 / `[data-view="prog"]` 進捗率 (no refetch). `#earn-qf input[value="1Q"…"4Q"]` checkboxes (FY / 5Q count as 4Q); kept across `selectDate` |
| Earnings search | `#earn-sym` (placeholder `銘柄名 or コード`), suggestions `#earn-suggest` from `/api/stocks/search`. A pick jumps the list to that issuer's latest filing day |
| Earnings sector chips | `#earn-groups .mchip[data-sector]` (すべて = `""`, then the top 5 `sector33` by issuer count, count in `.n`, then `.mchip.more[data-more="1"]` 「他 N業種」; open shows all plus `[data-more="0"]` 閉じる). Filters `#earn-tbl`; reset on every `selectDate` |
| 累計 / 3か月 / 進捗率 / 修正履歴 | `.ftab[data-fin="cum"]` (default on) / `[data-fin="q"]` / `[data-fin="prog"]` / `[data-fin="rev"]` → `#cum-wrap` / `#q-wrap` / `#prog-wrap` / `#rev-wrap`. Use `[data-fin]`: the PTS ranking, 銘柄 `[data-sub]` / `[data-stfin]` / `[data-zoom]` buttons are `.ftab` too |
| Earnings embedded chart | `#e-chart` / `.a-chart` (same `/api/chart` as 銘柄 › 株価). The issuer name in `#e-chart-title` (`.clicky`) → `gotoChart` |
| Event overlays | `.evt-toggles` on earnings + 銘柄 › 株価 (`input[data-evt="earnings"]`, label 決算発表) |
| Timeframe | `#tf` (`D` / `W` / `M`) |
| MA list | `#mas` (default `50,150,200`; redraws on `change`, i.e. Enter or blur) |
| 対TOPIX | `#rel` checkbox |
| `gotoChart(code)` | 決算速報 title, スクリーナー / PTS / ランキング name `.clicky`, サプライ `#supply-chart-link` 「株価を開く」 → 銘柄 › 株価, hash `#stock/{code}/price` |
| Screener stage | `#sc-stage` (default `2`) |
| Screener RS | `#sc-rs` (default `70`) |
| Screener 検索 | `#tab-screener button.btn` (label 検索) |
| PTS controls | `#pts-date` (session day), `#pts-ranking .ftab[data-ranking]` (`night_up` 値上がり / `night_down` 値下がり / `night_volume` 出来高), `#pts-at` (取得時刻), `#pts-info`, `#pts-summary`, `#pts-tbl` (name `.clicky` → `gotoChart`) |
| Ranking controls | `#rk-kind .ftab[data-dir]` (`up` 上昇率 default / `down` 下落率), `#rk-month`, `#rk-info`, `#rk-share`, `#rk-tbl` (name `.clicky` → `gotoChart`) |
| Monthly list | `#monthly-groups`, `#monthly-list` (host), `#monthly-tbl` |
| Monthly issuer search | `#monthly-sym` (placeholder `銘柄名 or コード`), suggestions `#monthly-suggest` from loaded monthly issuers (not `/api/stocks/search`). Enter or suggestion pick filters `#monthly-tbl`; company-name click opens Detail |
| Monthly detail | `#monthly-detail`, `#monthly-series`, `#monthly-chart` (one SVG line chart, `.monthly-legend`, `rect.monthly-hit[data-m]` → `.monthly-tip`), `#monthly-table-wrap`, `#monthly-back` |
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
| Select a scheduled row | `GET /api/chart/{code}?tf=D&ma=50,150,200` only. `#cum-tbl`, `#q-tbl`, `#prog-tbl`, and `#rev-tbl` read `まだ開示されていません` |
| Open 銘柄 tab | nothing until a code is picked |
| 銘柄 search typeahead | `GET /api/stocks/search?q=...` |
| 銘柄 pick / reload on `#stock/{code}` | `GET /api/stock/{code}/overview` (`{code, name, market, sector33, quote{date, close, change, change_pct, market_cap}, next_earnings{date, fq}|null, metrics{per, pbr, dividend_yield, roe, equity_ratio}}`; 404 `銘柄がありません。`), then the open sub tab's request. 概要: `GET /api/earnings/detail/{code}` (no `disclosure_no`) |
| 銘柄 › 株価 (first open per code, UI defaults) | `GET /api/chart/{code}?tf=D&ma=50,150,200&rel=false` (may include `earnings_dates`) and `GET /api/earnings-performance/{code}` in parallel. Each `#tf` / `#rel` / `#mas` change repeats both. Reopening the tab for the same code sends nothing |
| 銘柄 › ニュース | `GET /api/stock/{code}/filings` once per code; a detail row loads each iframe `GET /api/stock/filings/{id}/documents/{seq}` (HTML, CSP + ETag, 304 on `If-None-Match`) or `/api/stock/pdf/{disclosure_no}` |
| 銘柄 › 株主 | nothing (準備中) |
| Screener 検索 (UI defaults) | `GET /api/screener?stage=2&rs_min=70` |
| Open PTS (first time, or reload on `#pts`) | `GET /api/pts?ranking=night_up` |
| Open ランキング (first time, or reload on `#ranking`) | `GET /api/ranking/earnings-perf` |
| Ranking 上昇率 / 下落率 | no refetch; client filters the same payload |
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

Writes under `artifacts/<feature>/`. For a calendar day, pass `capture.py earnings --date YYYY-MM-DD`. Across a month boundary, pass `--month YYYY-MM` (machine local month, same as `renderCal`). For monthly Detail, pass `capture.py monthly --code CODE`. For サプライ, pass `capture.py supply --id MATERIAL_ID` (default `ethylene`). `capture.py chart --code CODE` captures the 銘柄 › 株価 payloads (`body.json` + `performance.json`). For PTS, pass `capture.py pts [--ranking night_up|night_down|night_volume] [--date YYYY-MM-DD]`; it exits 0 on an empty DB and prints `dates=` / `rows=`. For 銘柄, pass `capture.py stock --code CODE` (default `72030`); it writes `overview.json` (exits 1 on 404), `filings.json` (+ `document.html` of the first document) and exits 0 with `filings=0` when `xbrl_documents` has nothing for that code.

**Browser (user path).** When `browser-use` / CDP works: open `{url}/app` from state.json (`new_tab(url + "/app")`), `wait_for_load()`, click `nav button[data-tab="..."]`, then the feature file. Bare `{url}/` is public `/monthly` (301), not the Basic SPA. Do not `goto_url` for the first navigation. Screenshots via `capture_screenshot()` saved into `artifacts/<feature>/`. If Chrome remote debugging is denied, record that and finish the proof with the HTTP captures of the same routes.

`GET /app` rewrites `/static/...` URLs to `?v=<content hash>` and sends `ETag`. `/static/*` gets `Cache-Control: no-cache`. `boot.js` reloads on a bfcache restore. After 60 s hidden it also reloads when the page or asset ETags changed. Expect a reload when you edit `static/` while a tab is open.

Read `features/README.md` and the matching feature file before driving. Cover every entry point the file lists, or report the skipped one with the unmet precondition.

## Evidence

Directory: `.cursor/skills/verify-stock-analyzer/artifacts/<feature>/`. Cleanup must not delete this tree.

Proof standards:

- Exercise the real SPA routes (`/app` + `/api/*` the UI calls). Do not hit TestClient, `tests/test_smoke.py` fixtures, or invent test-only endpoints.
- Capture the **action** (request URL + status, or the click) and the **resulting state** (JSON body and/or screenshot), not only a final screen.
- Screener / search / earnings lists must show real names. API `code` is 5 characters and must not start with `0`. Table labels use the first 4 (`code.slice(0,4)`: 7203 / 262A), not the stored 5-digit string.
- 株価 proof needs `candles` length > 0 in `/api/chart/{code}`.
- Side effects: this UI is read-only. Do not write `stock.db`. If a run did write, say so.
- No mocks. J-Quants is not called by `serve`; empty tables mean the DB was never backfilled.

Minimum files per feature:

- `request.txt`: method, URL, status
- `body.json`: response (truncate lists to 20 rows if huge, keep `date` / counts)
- `ui.png`: only when the browser path ran. Document title is `株式分析ツール`. Header has no brand `h1` and no `#home`; ≤720px `#menu-btn` is on the left then `#cur-tab`

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
| `helpers/capture.py <feature> [--date YYYY-MM-DD] [--month YYYY-MM] [--code CODE] [--id MATERIAL_ID] [--ranking RANKING]` | GET the SPA `/api/*` routes into `artifacts/<feature>/` (features: screener / search / earnings / chart / stock / pts / monthly / supply) |
| `helpers/cleanup.py` | Stop a pid we started; leave adopted servers alone; keep artifacts |
