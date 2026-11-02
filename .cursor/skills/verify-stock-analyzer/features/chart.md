# 銘柄 › 株価

The 株価 sub tab of 銘柄 draws adjusted daily, weekly, monthly, or yearly candles with MAs, a volume / turnover pane, an optional TOPIX-relative pane, and post-earnings α for the issuer picked in 銘柄. It was the チャート tab until KAB-181 (#1001); there is no `#tab-chart`, `#sym`, `#suggest`, `#chart-title`, or 表示 button any more. Since KAB-300 the chart is the shared module `static/js/chart-core.js` (also used by 決算速報 `#e-chart`); `static/js/stock-price.js` wires the controls. The library is served from `static/vendor/lightweight-charts-5.2.1/` (no CDN).

## Sub-features

- `price-open` shows `#st-sub-price` on `#st-tabs .ftab[data-sub="price"]` (or route `#stock/{code}/price`). `showStockPrice` runs `loadChart()` only when the drawn code (`stockPriceCode`) differs, so reopening the tab for the same issuer sends nothing.
- `price-candles` fills `#chart` (3 panes: candles + MAs, volume or turnover histogram, TOPIX relative when `#rel`) from `/api/chart/{code}`, which returns daily columns `{t,o,h,l,c,v,tv,earnings_dates}` once per code. Weekly / monthly bars, MAs and the relative line are built in the browser. `.chart-legend` (top-left overlay) shows the crosshair bar (or the last bar). A failed request (this route 404s `no data` when there are no rows; `api()` throws) → `#chart-msg` 「読み込めませんでした。」. Empty `t` on HTTP 200 → 「株価データなし」; `/api/chart` does not emit that 200 today. `#st-quote` 「株価データなし」 is the overview path (`quote` null), not the chart.
- `price-controls` `#tf` (`.fin-tabs` buttons `[data-tf]` `D` 日足 / `W` 週足 / `M` 月足 / `Y` 年足, `.on` on the chosen one; default `D`. There are no period buttons and no 自動 since KAB-306), `#mas` (`change`), `#chart-sub` (`v` 出来高 / `tv` 売買代金), `#chart-hl` 高安値 (default on), `#chart-log`. None of them sends a request. `#tf` / `#chart-sub` / `#chart-hl` / `#chart-log` persist in `localStorage['chart.view']`.
- `price-fund` `#chart-fund` (blank option label `業績なし` / `op` 営業利益 / `per` 予想PER / `mcop` 時価総額÷営業利益) adds a pane under the volume pane and grows `#chart` by 120px (`style.height` 520px → 640px; with `#rel` too, 760px). `#chart-band` draws three dashed `PER n倍` step lines on the candle pane (n = 10/50/90% points of the issuer's 予想PER). Both come from `fund` / `mc` in the same `/api/chart/{code}` body (no extra request) and persist in `chart.view`. The legend gets a third line (`営業利益 四半期 … 12か月 …` / `予想PER 12.1倍` / `時価総額÷営業利益 12.2倍`). On the golden DB, 72030 has no 12か月 value from 2025-08 to 2025-11 (the 2024 1Q filing is outside the golden data); 80010 (1→5 split on 2025-12-29) shows a PER line without a jump at the split.
- `price-nav` (KAB-306) the chart itself does not move: wheel, drag and pinch on `#chart` do nothing (on a phone a vertical swipe scrolls the page). The period is set by `.chart-nav` (canvas, 44px, the sibling right after `#chart`): drag inside the window to move it, drag either edge to resize (min 10 trading days), press outside to jump the window there. The chart follows while dragging and always fits the period to the width (also after a resize). Opening an issuer or switching `#tf` shows the latest bars of that kind, as many as fit at 6px each (min 20). Widening the window does not change the bar kind. `#chart-hl` puts `M/D value` labels (`YY/M/D` when the period is over a year) on swing highs (above) and lows (below); labels that would stick out of the chart edges drop the date or are skipped.
- `price-rel` `#rel` adds the 対TOPIX pane (first common day = 100) from `GET /api/topix` (fetched once per page).
- `price-perf` fills `#perf-tbl` (発表日 / +1d / α / +5d / α / +20d / α / +60d / α / 現在まで / α) from `/api/earnings-performance/{code}`, at most 12 rows.
- `price-events` `.evt-toggles` (決算発表) inside `#st-sub-price` overlays markers from chart JSON `earnings_dates`; on/off is kept in `localStorage.chartEventOverlays`.
- `price-goto` `gotoChart(code)` from 決算速報 (`#e-chart-title .clicky`), スクリーナー / PTS / ランキング / 大量保有報告書 (name `.clicky`), サプライ (`#supply-chart-link` 「株価を開く」) opens 銘柄 with 株価 `.on`, hash `#stock/{code}/price?from=` when the caller screen is allowed (`earnings`, `screener`, `ranking`, `ranking-pts`, `supply`, `holders`, `stock-*`). A reload of `#stock/{code}/price` has no query. `ui.route` stores the path only.
- `price-route` `#stock/{code}/price` restores it on reload. Old `#chart/{code}` or `ui.route` `chart/{code}` is read as `stock/{code}/price`; bare `#chart` → `#stock`.

## How to get to it (user POV)

- In 銘柄, pick an issuer (`#st-sym`) and click 株価 in `#st-tabs`.
- Click an issuer name on 決算速報 (chart title), スクリーナー, PTS, ランキング, or 「株価を開く」 on a listed サプライ company.
- Click a `#tf` button, drag the window in `.chart-nav`, change `#tf` / `#chart-sub`, tick `#chart-log` / `#rel`, or edit `#mas` and dispatch `change` (Enter/blur).
- Tick `.evt-toggles` 決算発表.
- Reload with `#stock/{code}/price` (or an old `#chart/{code}` bookmark).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- A known code exists; doctor search for トヨタ should yield `72030` or similar.

- **Capture.** `helpers/capture.py chart --code 72030` writes `artifacts/chart/body.json` (`t` length > 0, `o`/`h`/`l`/`c` already from `adj_*`, `v`, `tv`, `mc`, `earnings_dates`, `fund` = `{t, eps, op_q, op_ttm}`) and `performance.json` (a list, may be empty; still 200).
- **Open.** Reload `#stock/72030/price` (golden DB has candles; 31160 does not — see Gotchas). Hash `#stock/72030/price`, `#st-title` `トヨタ自動車 7203 / プライム / 輸送用機器`, `#chart canvas` present, `#chart-msg` hidden/empty, requests `GET /api/chart/72030` + `GET /api/earnings-performance/72030`, `#tf .on` is `日足`, `#chart-fund` shows `業績なし`, and the visible range covers the latest ≈ width/6 trading days. No button whose label contains 表示.
- **404.** Cold reload `#stock/31160/price`: overview 200, `#st-quote` 「株価データなし」, chart HTTP 404 `no data`, `#chart-msg` 「読み込めませんでした。」, no canvas from that response. A same-document jump from a drawn 72030 (hash or inner tab) leaves the previous canvas and keeps `#tf` / `#rel` / `#chart-fund`; do not treat leftover 72030 candles as 31160 data. Click 概要 then 株価: both price requests fire again (`stockPriceCode` was cleared).
- **Controls.** On the 72030 draw, click `#tf [data-tf="W"]` (range jumps to the latest ≈ width/6 weeks), set `#mas` to `25` + `change`, `#chart-sub` to `tv` + `change`: no new `/api/` request. Tick `#rel` → one `GET /api/topix` (only the first time) and a third pane. Click `[data-tf="Y"]` → yearly bars (3 on the golden DB) and no MA lines (50/150/200 days are under 2 years).
- **Period bar.** Wheel over `#chart`: `stockChart.chart.timeScale().getVisibleRange()` does not change. Drag the window in `.chart-nav` left: the range moves by the same share of the history. Drag the left edge left to the start → still 日足, just thinner.
- **Reopen.** On that successful 72030 draw, click 概要 then 株価 again: no `/api/chart` or `/api/earnings-performance`. Opening another code and coming back within 10 minutes reuses the in-memory daily data (no `/api/chart`); after that the browser revalidates with `If-None-Match` (304).
- **Old route.** Reload on `#chart/72030` → hash `#stock/72030/price`, `#st-title` `トヨタ自動車 7203 / プライム / 輸送用機器`, only `#st-sub-price` visible. `localStorage` `ui.route=chart/39120` with no hash → `#stock/39120/price`.
- **gotoChart.** From each caller above: 銘柄 active, 株価 `.on`, hash `#stock/{code}/price?from=…`, `GET /api/stock/{code}/overview` then the two price requests.
- **Proof.** Keep `body.json` and (if CDP) a screenshot with `#chart` tall enough to show candles and the 銘柄 header.

## Gotchas

- Prices in the chart are **adjusted** (`adj_*`). Do not assert raw `close` against the canvas; the header `#st-quote` uses the overview `quote.close`.
- Codes are 5 characters (`72030`). Labels show the first 4; the API still uses 5.
- `/api/chart/{code}` with no rows is HTTP 404 `no data`, so `#chart-msg` is 「読み込めませんでした。」 (31160 on the golden DB). Do not expect 「株価データなし」 on the chart for that 404; that copy is the empty-candles 200 branch, which this API does not emit. Overview `#st-quote` 「株価データなし」 is a different field (`quote` null).
- `#tf`, `#mas`, `#chart-sub`, `#chart-log`, `#rel` keep their values across issuers; a new issuer is drawn with the current controls and the latest bars.
- Setting `.value` from a script does not redraw; dispatch `change` like the browser does.
- `#tf` defaults to `D`. `W`, `M`, `Y` are resampled in the browser (`chartBars`); MA windows scale down (÷5 / ÷20 / ÷250) and are dropped under 2 bars.
- `#perf-tbl` shows at most 12 rows even if the API returns more.
