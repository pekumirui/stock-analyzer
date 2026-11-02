# stock-analyzer verification map

Read this index, then the matching feature file, before driving the app.

## Baseline preconditions

- Launch with `helpers/launch.py` at `http://127.0.0.1:8000` (or `VERIFY_PORT`). SPA entry is `GET /app` (`GET /` → public `/monthly`). Reuses an already-running SPA on that port (`adopted: true`).
- `stock.db` is the backfilled SQLite next to the repo (not a CI temp DB, not demo `0`-prefix codes).
- Run `helpers/doctor.py` and require `ok: true`, non-empty screener, and a トヨタ search hit.
- Drive the instance recorded in `.run/state.json` (adopted or launched).
- 月次 Detail needs a non-empty `GET /api/v1/tables`. Cloud Agent builds copy production `monthly_*` when `VPS_SSH_KEY` is set; empty monthly is `verified-unreachable` (see `features/monthly.md`).
- サプライ monthly / countries need `material_monthly` / `material_trade_country`. The golden DB (`./run.sh golden`, the `.cursor/install.sh` default) has them; a backfilled or box DB copy usually does not. Empty is `verified-unreachable` only after trying a golden side DB (see `features/supply.md`).
- PTS rows need `pts_snapshots` (prod cron `jobs/market/pts_snapshot.py`). Neither the golden DB nor a DB copy has them; empty is 「PTS のデータがまだありません」 (see `features/pts.md`).
- 大量保有報告書 rows need `large_holding_reports` (`parsed` and `is_latest`). Golden has none; empty is 「この日の大量保有報告書はありません。」 and `capture.py holders` prints `items=0 search=0` (see `features/holders.md`).
- 銘柄 needs `xbrl_filings` + `xbrl_documents` (TDnet XBRL from `jobs/xbrl/xbrl_parse.py`) or `tdnet_pdf_documents`. A plain DB copy may have none; on a copy you own, `jobs/xbrl/xbrl_parse.py parse --from D --to D --dest <xbrl dir>` fills one day (see `features/stock.md`).
- 修正履歴 with `?rev_source=xbrl` needs `xbrl_financials` rows from TDnet filings (`source='tdnet'`; EDINET rows are not read); the default `jquants` source reads `statements` only.

## Driving conventions

- Boot restores the last tab (and the 銘柄 code + sub tab / news id, monthly code, supply id) from `location.hash` or `localStorage` key `ui.route` via `restoreRoute()` (`static/js/tabs.js`, called from `boot.js`). For a cold 決算速報 start, clear hash + `ui.route` then reload, or open a fresh profile.
- Prefer `nav button[data-tab]`, `#id`, and `data-fin` / `data-no` over pixel clicks.
- Treat every command as literal.
- HTTP proofs use `helpers/capture.py <feature>` against the same `/api/*` the SPA calls (including `capture.py ranking` and `capture.py holders`).
- Browser proofs use `browser-use`: open `{url}/app` then click the tab handle. At ≤720px the tabs are in the `#menu-btn` drawer; open it first (or call `selectTab`).
- Cleanup removes `.run/` only. Leave `artifacts/`.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final screen.
- UI proof includes a screenshot of the header (document title is `株式分析ツール`; no brand `h1`, no `#home`; ≤720px `#menu-btn` then `#cur-tab`).
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

- [決算速報](./earnings.md) calendar day list (累計 / 3か月 / 進捗率 view, 1Q–4Q filter; list metrics 売上 / 粗利 / 営利 / 経利 / 純利), issuer search + sector chips (top 5 + 他N業種), row detail, 累計 / 3か月 / 進捗率 / 修正履歴 tabs.
- [銘柄](./stock.md) issuer search, common header (price, 時価総額, 次回決算), sub tabs 概要 (指標 + 累計 / 3か月 / 進捗率 / 修正履歴) / 株価 / ニュース (決算短信の原文 in sandboxed iframes, 幅に合わせる / 中 / 原寸 zoom) / 株主 (大量保有 + 自己株), `#stock/{code}/{sub}` route.
- [大量保有報告書](./holders.md) date list (`file_date`, default Asia/Tokyo today), search by issuer / holder without the date, holder page with holdings / history / estimates, `#holders/{edinet}`.
- [銘柄 › 株価](./chart.md) daily/weekly/monthly/yearly candles, 対TOPIX, 決算発表後リターン (was the チャート tab until KAB-181; `gotoChart` lands here).
- [スクリーナー](./screener.md) Stage + RS filter table (UI defaults Stage 2, RS≥70).
- [PTS](./pts.md) night PTS ranking inside ランキング (値上がり / 値下がり / 出来高) per session and fetch time, with the next-day open gap and 追随率. Header has no PTS tab; old `#pts` opens ランキング › PTS.
- [ランキング](./ranking.md) inner tabs 決算後パフォーマンス / PTS. 決算後パフォーマンス is 上昇率 / 下落率 per 決算月, with `(対TOPIX)` in the 騰落率 column name.
- [月次](./monthly.md) issuer list + industry chips + TOP search via `/api/v1/tables`, Detail with series select, one multi-year line chart (legend + month tooltip), one 横持ち `table.monthly-tl` (metric ▸ section tree, paired cells show 金額 with (前年比) under it, month-cell popup).
- [サプライ](./supply.md) material graph (原料 → 中間財 → 最終製品, plus リサイクル資源 and 回収 marks), material panel tabs (概要 / 企業 / 輸入元 / 推移) or company panel, capacity share donuts, import-country donut, monthly volume chart, phone bottom sheet, `#supply/{id}` route.
