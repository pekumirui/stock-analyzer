# サプライ

The supply tab draws a material graph (原料 → 中間財 → 最終製品, upstream left to downstream right) from `config/supply/supply_chain.json`, and a side panel for the picked material or company: up/downstream chips, makers, capacity share donuts, import countries, and a monthly volume chart. Spec is `docs/DESIGN_supply.md`; code is `static/js/supply.js` + `app/supply/chain.py`.

## Sub-features

- `supply-map` renders `#supply-map` with Cytoscape.js 3.30.2 + elkjs 0.9.3 (both from unpkg, loaded on first open via `loadSupply` / `supplySetup`). Box color is `kind` (`raw` 原料 / `mid` 中間財 / `prod` 最終製品). `#supply-msg` reads 「読み込み中…」 until ready, 「読み込めませんでした (理由)」 on API or library failure.
- `supply-select` taps a material box (`supplySelect`): ancestors, descendants and the path edges stay, everything else gets `.dim`; the box gets `.sel`. Tapping empty map or `#supply-reset` (全体表示) clears (`supplyClear`).
- `supply-panel` fills `#supply-panel`. No pick: 「素材を選ぶと、原料と行き先が出ます。」. Material: `h2` name + kind, `上流 N ・ 下流 M`, note, 直接の原料 / これから作られるもの / 元をたどった原料 (not for `raw`) / 行き着く最終製品 (only with descendants), 作っている企業 (sourced first; unsourced chips are `.unverified` with 「点線は出典未確認（n 社）」; none → 「まだありません」).
- `supply-share` adds one `.supply-share[data-cap]` per capacity table that lists the material: donut `svg.supply-donut` (top 5 colored, rest one gray 「その他 n 社」), table `tr.supply-row[data-co]`, source line, and `input.supply-group` 「親会社でまとめる」 (sums by top parent; column header 会社 → グループ; shared across tables until reload).
- `supply-company` swaps the panel for a company (`supplySelectCompany`): name + 上場/非上場/海外 (+4-digit code), `#supply-chart-link` 「チャートを開く」 (listed only, `gotoChart(code)`), 作っている素材, 生産能力 table, 親会社 or 出資者, 子会社・出資先, 販売先, 仕入先, 出典. Made materials keep `.sel`, every other node and all edges are `.dim`.
- `supply-search` is `#supply-q` + `datalist#supply-names` (material names, then companies as 「社名 4桁」 when listed). On `change`: exact label, then 4-digit code, then first material name containing it, then first company name containing it. No hit does nothing.
- `supply-merge` toggles `#supply-merge` (線をまとめる, default on = ELK `mergeEdges`) and re-runs layout + fit.
- `supply-countries` (materials with country import rows only) puts temporary `node.ctry` boxes left of the material (top 5 + 「その他 n か国」, ≥0.1%) and fills `#supply-countries` (top 10 table + 「ほか n か国」, 12-month sum). Empty `countries` draws nothing.
- `supply-monthly` fills `#supply-monthly`: unit line, `#supply-legend`, `#supply-chart` (lightweight-charts) + `#supply-tip`, and a table `tr.supply-row[data-i]` (指標 / 最新月 / 値 / 単位, 「速報」 badge when revision 1). Row clicks toggle picked series (multi). No series → 「統計の対応がまだありません。」.
- `supply-route` writes `#supply` or `#supply/{id}` (material or company id, `^[a-z][a-z0-9_]*$`, `tabs.js` `SUPPLY_ID_RE`) to `location.hash` + `ui.route`; reload restores the pick.

## How to get to it (user POV)

- Click `nav button[data-tab="supply"]` (label サプライ).
- Tap a material box on `#supply-map`, a material chip `.supply-chip[data-id]` in the panel, or a 生産能力 row `tr.supply-row[data-id]` in a company panel.
- Type a material name, company name, or 4-digit code into `#supply-q` (or pick a `#supply-names` option) and commit (`change`).
- Click a company chip `.supply-chip.co[data-co]`, a share row `tr.supply-row[data-co]`, or a donut slice `path[data-co]`.
- Toggle 「親会社でまとめる」, `#supply-merge`, or click a monthly table row.
- Click `#supply-chart-link` on a listed company to jump to チャート.
- Click `#supply-reset` or empty map to clear.
- Reload with `#supply/{id}` (or the same path in `ui.route`).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `helpers/capture.py supply [--id MATERIAL_ID]` exits 0 (default `ethylene`; a company id is rejected). Graph and companies come from `config/`, so they never need DB data.
- Monthly and countries need `material_monthly` / `material_trade_country` rows (`jobs/supply/material_stats.py`, prod cron). `.cursor/install.sh` copies only `monthly_*`, so a Cloud Agent or box DB usually has none: then `series: []` and `countries: []` and those two sub-features are `verified-unreachable` (record the empty `monthly.json` / `countries.json`). Do not run the ingest job against the shared DB to fill them.
- The map needs network to unpkg. Offline, `#supply-map` shows 「読み込めませんでした (load https://unpkg.com/…)」 — that is not a code bug.

- **Tab.** Click `nav button[data-tab="supply"]` → `#tab-supply` shown, hash `#supply`, `#supply-panel` filled (set after layout), `supply.cy.nodes().length` equals `graph.json` `materials` length (2026-10-15: 59 materials / 79 flows), panel shows the no-pick copy, `#supply-merge` checked.
- **API.** `capture.py supply` writes `artifacts/supply/{graph,companies,monthly,countries}.json` + `request.txt`. Assert `graph.materials[]` `{id, name, kind, note}` and `flows[]` pairs; `companies` has `sources` / `companies` / `makes` / `capacities` / `trade`. Bad id → 404 `unknown material`; `countries?flow=x` → 400.
- **Select material.** Reload `/app#supply/ethylene` → panel `h2` エチレン 中間財, `上流 4 ・ 下流 18`, 直接の原料 エタン / ナフサ, a `.supply-share` for エチレン生産能力 (center total 6,162 千t/年, top row 出光興産 16.2%). Toggle `.supply-group` → header `グループ`, rows merge by parent.
- **Select company.** Search `4042` (or reload `#supply/tosoh`) → hash `#supply/tosoh`, `h2` 東ソー 上場 4042, `.sel` on ethylene / benzene / xylene / pe / pvc. `#supply-chart-link` href is `#chart/40420`; click → チャート tab active, hash `#chart/40420`.
- **Search order.** `トヨタ自動車 7203` → `toyota`. `ポリ` (no exact label) → first material containing it (`pe`). `zzzz` → selection unchanged.
- **Clear.** `#supply-reset` → hash `#supply`, `#supply-q` empty, no `.dim`, no-pick copy.
- **Monthly / countries.** With prod-like data, pick a material with stats (e.g. `crude`; country rows come from `trade.country_items` 2709 in `config/supply/material_stats.json`): `#supply-monthly` has the table and `#supply-chart`; `node.ctry` boxes appear. On an empty DB the panel reads 「統計の対応がまだありません。」 and `#supply-countries` stays empty.
- **Proof.** Keep `artifacts/supply/*.json`. If CDP: `ui.png` of `/app#supply/ethylene` with header `h1` `株式分析`.

## Gotchas

- The tab starts at 0×0 while hidden; `loadSupply` calls `cy.resize()` on each open. `#supply-msg` is removed before ELK runs, so it is not a ready signal. Wait until `#supply-panel` is non-empty (`supplyPanel()` runs after layout + fit).
- Hash with an uppercase or invalid id (`#supply/NOPE`) is rewritten to `#supply` by `routeArg`. A valid-looking unknown id keeps the hash but selects nothing.
- Donut slices and share rows carry `data-co`, so clicking them switches to the company panel (the share table disappears). Do not chain a share-row click after a donut click.
- Edges have `events: "no"`; you cannot tap a line. Country boxes (`.ctry`) ignore taps.
- While the page itself is pinch-zoomed (`visualViewport.scale` > 1) map pan/zoom is off (#636). Map zoom is capped at 3 (1.2 while flying to a pick).
- Layout: ≥900px the panel is a 300px right column; below that it is under the map. ≤640px the map is `60vh` (390×844 → 506px).
- Prod nginx keeps `/api/supply/*` behind Basic (`deploy/nginx.conf`; only `/api/v1/tables*` is public), and public `/monthly` has no supply surface. Local uvicorn has no auth. `GET /api/supply/countries/{id}?flow=export` exists but the UI never requests it.
- `graph` / `companies` are read once per process; editing `config/supply_*.json` needs a server restart to show.
