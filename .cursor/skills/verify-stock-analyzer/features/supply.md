# サプライ

The supply tab draws a material graph (原料 → 中間財 → 最終製品, plus リサイクル資源, upstream left to downstream right) from `config/supply/supply_chain.json`, and a panel for the picked material (tabs 概要 / 企業 / 輸入元 / 推移: up/downstream chips, makers and capacity share donuts, an import-country donut, a monthly volume chart) or company. Below 900px the panel is a bottom sheet. Spec is `docs/DESIGN_supply.md`; code is `static/js/supply.js` + `app/supply/chain.py`.

## Sub-features

- `supply-map` renders `#supply-map` with Cytoscape.js 3.30.2 + elkjs 0.9.3 (both from unpkg, loaded on first open via `loadSupply` / `supplySetup`). Box color is `kind` (`raw` 原料 / `mid` 中間財 / `prod` 最終製品 / `recycled` リサイクル資源, purple). `graph.returns` (`[product, recycled]`, 回収) draws no line: the product box gets a purple recycle mark on its right edge (node `data.rec`, box 14px wider). The legend `.supply-legend` lists the four kinds and 使い終わると回収. `#supply-msg` reads 「読み込み中…」 until ready, 「読み込めませんでした (理由)」 on API or library failure.
- `supply-select` taps a material box (`supplySelect`): ancestors, descendants and the path edges stay, everything else gets `.dim`; the box gets `.sel`. The pick's 回収 partners (`retFrom` / `retTo`) stay undimmed with `.ret` (purple frame), without walking further. Tapping empty map or `#supply-reset` (全体表示) clears (`supplyClear`).
- `supply-panel` fills `#supply-body` inside `#supply-panel` (which also holds the sheet grip `#supply-grip`). No pick: 「素材を選ぶと、原料と行き先が出ます。」. Material: `h2` name + kind, `.supply-meta` `上流 N ・ 下流 M`, then `.supply-tabs` buttons `[data-tab]` `overview` 概要 / `co` 企業 n (n = makers, omitted at 0) / `ctry` 輸入元 (`hidden` until country rows arrive) / `monthly` 推移, each over a `.supply-pane[data-pane]`. The picked tab (`supplyTab`) survives re-picks; a hidden one falls back to 概要. 概要: note, 直接の原料 / これから作られるもの / 元をたどった原料 (`raw` + `recycled` ancestors; not for `raw` / `recycled`) / 行き着く最終製品 (only with descendants) / 回収元 (`recycled` only) / 使い終わると (only with returns). 企業: 作っている企業 (sourced first; unsourced chips are `.unverified` with 「点線は出典未確認（n 社）」; none → 「まだありません」) and the share blocks.
- `supply-share` adds (in the 企業 tab) one `.supply-share[data-cap]` per capacity table that lists the material: donut `svg.supply-donut` (top 5 colored, rest one gray 「その他 n 社」), table `tr.supply-row[data-co]`, source line, and `input.supply-group` 「親会社でまとめる」 (sums by top parent; column header 会社 → グループ; shared across tables until reload).
- `supply-company` swaps the panel for a company (`supplySelectCompany`): name + 上場/非上場/海外 (+4-digit code), `#supply-chart-link` 「チャートを開く」 (listed only, `gotoChart(code)`), 作っている素材, 生産能力 table, 親会社 or 出資者, 子会社・出資先, 販売先, 仕入先, 出典. Made materials keep `.sel`, every other node and all edges are `.dim`.
- `supply-search` is `#supply-q` + `datalist#supply-names` (material names, then companies as 「社名 4桁」 when listed). On `change`: exact label, then 4-digit code, then first material name containing it, then first company name containing it. No hit does nothing.
- `supply-merge` toggles `#supply-merge` (線をまとめる, default on = ELK `mergeEdges`) and re-runs layout + fit.
- `supply-countries` (materials with country import rows only) fills `#supply-countries` (the 輸入元 pane) with `h3` 輸入元の国, a donut (top 5 ≥0.1% + gray 「その他 n か国」; center is the 12-month total rounded to 万 / 億 over `{unit}・12か月計`), a color / 国 / share table, and a source line `YYYY-MM〜YYYY-MM（最新月は速報）の輸入量 …`, then unhides the 輸入元 tab. Nothing goes on the map (no `node.ctry`). Empty `countries` leaves the tab hidden.
- `supply-monthly` fills `#supply-monthly` (推移 pane, `h3` 月次の生産・輸出入): unit line, `#supply-legend`, `#supply-chart` (lightweight-charts) + `#supply-tip`, and a table `tr.supply-row[data-i]` (指標 / 最新月 / 値 / 単位, 「速報」 badge when revision 1). Row clicks toggle picked series (multi). No series → 「統計の対応がまだありません。」.
- `supply-sheet` (below 900px, `matchMedia("(max-width:899px)")`): `#supply-panel` is a fixed bottom sheet 45dvh high, closed (translated off-screen) until a material or company pick adds `.open`; `supplyClear` closes it. Tapping `#supply-grip`, or a 40px+ downward swipe (from the grip any time, from the body only when scrolled to top), closes it and keeps the pick. Flying to a pick fits the part of the map above the sheet (zoom floor 0.6); a tapped box hidden under the sheet pans up.
- `supply-route` writes `#supply` or `#supply/{id}` (material or company id, `^[a-z][a-z0-9_]*$`, `tabs.js` `SUPPLY_ID_RE`) to `location.hash` + `ui.route`; reload restores the pick.

## How to get to it (user POV)

- Click `nav button[data-tab="supply"]` (label サプライ).
- Tap a material box on `#supply-map`, a panel tab `.supply-tabs [data-tab]`, a material chip `.supply-chip[data-id]` in the panel, or a 生産能力 row `tr.supply-row[data-id]` in a company panel.
- Type a material name, company name, or 4-digit code into `#supply-q` (or pick a `#supply-names` option) and commit (`change`).
- Click a company chip `.supply-chip.co[data-co]`, a share row `tr.supply-row[data-co]`, or a donut slice `path[data-co]`.
- Toggle 「親会社でまとめる」, `#supply-merge`, or click a monthly table row.
- Click `#supply-chart-link` on a listed company to jump to チャート.
- Click `#supply-reset` or empty map to clear. On a phone, tap `#supply-grip` or swipe the sheet down to close it.
- Reload with `#supply/{id}` (or the same path in `ui.route`).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `helpers/capture.py supply [--id MATERIAL_ID]` exits 0 (default `ethylene`; a company id is rejected). Graph and companies come from `config/`, so they never need DB data.
- Monthly and countries need `material_monthly` / `material_trade_country` rows (`jobs/supply/material_stats.py`, prod cron). `.cursor/install.sh` copies only `monthly_*`, so a Cloud Agent or box DB usually has none: then `series: []` and `countries: []` and those two sub-features are `verified-unreachable` (record the empty `monthly.json` / `countries.json`). Do not run the ingest job against the shared DB to fill them.
- The map needs network to unpkg. Offline, `#supply-map` shows 「読み込めませんでした (load https://unpkg.com/…)」 — that is not a code bug.

- **Tab.** Click `nav button[data-tab="supply"]` → `#tab-supply` shown, hash `#supply`, `#supply-body` filled (set after layout), `supply.cy.nodes().length` equals `graph.json` `materials` length and `edges()` equals `flows` (2026-10-19: 78 materials / 118 flows / 15 returns; kinds raw 8 / mid 46 / prod 21 / recycled 3; 9 boxes carry `rec`), panel shows the no-pick copy, `#supply-merge` checked.
- **API.** `capture.py supply` writes `artifacts/supply/{graph,companies,monthly,countries}.json` + `request.txt`. Assert `graph.materials[]` `{id, name, kind, note}`, `flows[]` and `returns[]` pairs; `companies` has `sources` / `companies` / `makes` / `capacities` / `trade`. Bad id → 404 `unknown material`; `countries?flow=x` → 400.
- **Select material.** Reload `/app#supply/ethylene` → panel `h2` エチレン 中間財, `上流 4 ・ 下流 18`, tabs 概要 (`.on`) / `企業 11` / 推移 with 輸入元 hidden on an empty DB, 概要 lists 直接の原料 エタン / ナフサ. Click `[data-tab="co"]` → `.supply-share` for エチレン生産能力 (center total 6,162 千t/年, top row 出光興産 16.2%). Toggle `.supply-group` → header `グループ`, rows merge by parent. Pick `pe` next: the 企業 (or 推移) tab stays on.
- **Recycled.** Pick `steel_scrap` → `h2` 鉄スクラップ リサイクル資源, 概要 has これから作られるもの 粗鋼 and 回収元 自動車 / 家電 / 建設（建物・橋） / 普通鋼鋼材 (no 元をたどった原料), and those four boxes get `.ret`. Pick `car` → 使い終わると 鉄スクラップ / 銅スクラップ / アルミスクラップ, all three `.ret`.
- **Select company.** Search `4042` (or reload `#supply/tosoh`) → hash `#supply/tosoh`, `h2` 東ソー 上場 4042, `.sel` on ethylene / benzene / xylene / pe / pvc. `#supply-chart-link` href is `#chart/40420`; click → チャート tab active, hash `#chart/40420`.
- **Search order.** `トヨタ自動車 7203` → `toyota`. `ポリ` (no exact label) → first material containing it (`pe`). `zzzz` → selection unchanged.
- **Clear.** `#supply-reset` → hash `#supply`, `#supply-q` empty, no `.dim`, no-pick copy.
- **Monthly / countries.** With prod-like data, pick a material with stats (e.g. `crude`; country rows come from `trade.country_items` 2709 in `config/supply/material_stats.json`): the 推移 tab's `#supply-monthly` has the table and `#supply-chart`; the 輸入元 tab appears with the donut. On an empty DB 推移 reads 「統計の対応がまだありません。」, `#supply-countries` stays empty, and 輸入元 stays hidden.
- **Phone sheet.** At 390×844: `#supply-map` is `100dvh - 186px` (658px), `#supply-panel` is `position: fixed`, 380px high, off-screen without a pick. Pick `ethylene` → `.open`, top at 464px. Tap `#supply-grip` → closed, `supply.selected` and hash `#supply/ethylene` stay.
- **Proof.** Keep `artifacts/supply/*.json`. If CDP: `ui.png` of `/app#supply/ethylene` with header `h1` `株式分析`.

## Gotchas

- The tab starts at 0×0 while hidden; `loadSupply` calls `cy.resize()` on each open. `#supply-msg` is removed before ELK runs, so it is not a ready signal. Wait until `#supply-body` is non-empty (`supplyPanel()` runs after layout + fit). `#supply-panel` always holds `#supply-grip` + `#supply-body`, so its own emptiness says nothing. The unpkg libraries can take well over 20 s on the box.
- Hash with an uppercase or invalid id (`#supply/NOPE`) is rewritten to `#supply` by `routeArg`. A valid-looking unknown id keeps the hash but selects nothing.
- Donut slices and share rows carry `data-co`, so clicking them switches to the company panel (the share table disappears). Do not chain a share-row click after a donut click.
- Edges have `events: "no"`; you cannot tap a line. Returns have no edge at all. There are no country boxes on the map any more.
- While the page itself is pinch-zoomed (`visualViewport.scale` > 1) map pan/zoom is off (#636). Map zoom is capped at 3 (1.2 while flying to a pick).
- Layout: ≥900px the panel is a 300px right column and the map fills the rest of the screen. Below 900px the panel is the bottom sheet and the map is `calc(100dvh - 186px)` (min 300px).
- Prod nginx keeps `/api/supply/*` behind Basic (`deploy/nginx-site.sh`; only `/api/v1/tables*` is public), and public `/monthly` has no supply surface. Local uvicorn has no auth. `GET /api/supply/countries/{id}?flow=export` exists but the UI never requests it.
- `graph` / `companies` are read once per process; editing `config/supply/supply_*.json` needs a server restart to show.
