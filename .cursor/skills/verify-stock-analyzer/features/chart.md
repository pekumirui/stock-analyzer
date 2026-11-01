# 銘柄 › 株価

The 株価 sub tab of 銘柄 draws adjusted daily, weekly, or monthly candles with MAs, a volume / turnover pane, an optional TOPIX-relative pane, and post-earnings α for the issuer picked in 銘柄. It was the チャート tab until KAB-181 (#1001); there is no `#tab-chart`, `#sym`, `#suggest`, `#chart-title`, or 表示 button any more. Since KAB-300 the chart is the shared module `static/js/chart-core.js` (also used by 決算速報 `#e-chart`); `static/js/stock-price.js` wires the controls. The library is served from `static/vendor/lightweight-charts-5.2.1/` (no CDN).

## Sub-features

- `price-open` shows `#st-sub-price` on `#st-tabs .ftab[data-sub="price"]` (or route `#stock/{code}/price`). `showStockPrice` runs `loadChart()` only when the drawn code (`stockPriceCode`) differs, so reopening the tab for the same issuer sends nothing.
- `price-candles` fills `#chart` (3 panes: candles + MAs, volume or turnover histogram, TOPIX relative when `#rel`) from `/api/chart/{code}`, which returns daily columns `{t,o,h,l,c,v,tv,earnings_dates}` once per code. Weekly / monthly bars, MAs and the relative line are built in the browser. `.chart-legend` (top-left overlay) shows the crosshair bar (or the last bar). A failed request (this route 404s `no data` when there are no rows; `api()` throws) → `#chart-msg` 「読み込めませんでした。」. Empty `t` on HTTP 200 → 「株価データなし」; `/api/chart` does not emit that 200 today. `#st-quote` 「株価データなし」 is the overview path (`quote` null), not the chart.
- `price-controls` `#chart-range` (`1M`…`5Y`, `ALL`=全; default `6M`, `.on` cleared after a manual zoom/scroll), `#tf` (`A` 自動 / `D` / `W` / `M`; the 自動 option text shows the bar in use, e.g. `自動（日足）`), `#mas` (`change`), `#chart-sub` (`v` 出来高 / `tv` 売買代金), `#chart-log`. None of them sends a request. `#tf` / `#chart-sub` / `#chart-log` persist in `localStorage['chart.view']`.
- `price-fund` `#chart-fund` (`` なし / `op` 営業利益 / `per` 予想PER / `mcop` 時価総額÷営業利益) adds a pane under the volume pane and grows `#chart` by 120px (`style.height` 520px → 640px; with `#rel` too, 760px). `#chart-band` draws three dashed `PER n倍` step lines on the candle pane (n = 10/50/90% points of the issuer's 予想PER). Both come from `fund` / `mc` in the same `/api/chart/{code}` body (no extra request) and persist in `chart.view`. The legend gets a third line (`営業利益 四半期 … 12か月 …` / `予想PER 12.1倍` / `時価総額÷営業利益 12.2倍`). On the golden DB, 72030 has no 12か月 value from 2025-08 to 2025-11 (the 2024 1Q filing is outside the golden data); 80010 (1→5 split on 2025-12-29) shows a PER line without a jump at the split.
- `price-zoom` with `#tf` = 自動, wheel / pinch out until a bar is < 2px → 日足→週足→月足; zoom in until the finer bar would be ≥ 4px → back. The visible dates are kept.
- `price-rel` `#rel` adds the 対TOPIX pane (first common day = 100) from `GET /api/topix` (fetched once per page).
- `price-perf` fills `#perf-tbl` (発表日 / +1d / α / +5d / α / +20d / α / +60d / α / 現在まで / α) from `/api/earnings-performance/{code}`, at most 12 rows.
- `price-events` `.evt-toggles` (決算発表) inside `#st-sub-price` overlays markers from chart JSON `earnings_dates`; on/off is kept in `localStorage.chartEventOverlays`.
- `price-goto` `gotoChart(code)` from 決算速報 (`#e-chart-title .clicky`), スクリーナー / PTS / ランキング (name `.clicky`), サプライ (`#supply-chart-link` 「株価を開く」) opens 銘柄 with 株価 `.on`, hash `#stock/{code}/price`.
- `price-route` `#stock/{code}/price` restores it on reload. Old `#chart/{code}` or `ui.route` `chart/{code}` is read as `stock/{code}/price`; bare `#chart` → `#stock`.

## How to get to it (user POV)

- In 銘柄, pick an issuer (`#st-sym`) and click 株価 in `#st-tabs`.
- Click an issuer name on 決算速報 (chart title), スクリーナー, PTS, ランキング, or 「株価を開く」 on a listed サプライ company.
- Click a `#chart-range` button, zoom with the wheel, change `#tf` / `#chart-sub`, tick `#chart-log` / `#rel`, or edit `#mas` and press Enter.
- Tick `.evt-toggles` 決算発表.
- Reload with `#stock/{code}/price` (or an old `#chart/{code}` bookmark).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- A known code exists; doctor search for トヨタ should yield `72030` or similar.

- **Capture.** `helpers/capture.py chart --code 72030` writes `artifacts/chart/body.json` (`t` length > 0, `o`/`h`/`l`/`c` already from `adj_*`, `v`, `tv`, `mc`, `earnings_dates`, `fund` = `{t, eps, op_q, op_ttm}`) and `performance.json` (a list, may be empty; still 200).
- **Open.** Reload `#stock/72030/price` (golden DB has candles; 31160 does not — see Gotchas). Hash `#stock/72030/price`, `#st-title` `トヨタ自動車 7203 / プライム / 輸送用機器`, `#chart canvas` present, `#chart-msg` hidden/empty, requests `GET /api/chart/72030` + `GET /api/earnings-performance/72030`, `#chart-range .on` is `6M`. No button whose label contains 表示.
- **404.** Reload `#stock/31160/price`: overview 200, `#st-quote` 「株価データなし」, chart HTTP 404 `no data`, `#chart-msg` 「読み込めませんでした。」, no canvas from that response. Click 概要 then 株価: both price requests fire again (`stockPriceCode` was cleared).
- **Controls.** On the 72030 draw, set `#tf` to `W` + `change`, `#mas` to `25` + `change`, `#chart-sub` to `tv` + `change`: no new `/api/` request. Tick `#rel` → one `GET /api/topix` (only the first time) and a third pane. Click `#chart-range [data-range="ALL"]` → `.on` moves, 自動 picks 週足 or 月足 on the golden DB.
- **Zoom.** Wheel out over `#chart` with 自動: the 自動 option text changes to `自動（週足）`; wheel back in → `自動（日足）`.
- **Reopen.** On that successful 72030 draw, click 概要 then 株価 again: no `/api/chart` or `/api/earnings-performance`. Opening another code and coming back within 10 minutes reuses the in-memory daily data (no `/api/chart`); after that the browser revalidates with `If-None-Match` (304).
- **Old route.** Reload on `#chart/72030` → hash `#stock/72030/price`, `#st-title` `トヨタ自動車 7203 / プライム / 輸送用機器`, only `#st-sub-price` visible. `localStorage` `ui.route=chart/39120` with no hash → `#stock/39120/price`.
- **gotoChart.** From each caller above: 銘柄 active, 株価 `.on`, hash `#stock/{code}/price`, `GET /api/stock/{code}/overview` then the two price requests.
- **Proof.** Keep `body.json` and (if CDP) a screenshot with `#chart` tall enough to show candles and the 銘柄 header.

## Gotchas

- Prices in the chart are **adjusted** (`adj_*`). Do not assert raw `close` against the canvas; the header `#st-quote` uses the overview `quote.close`.
- Codes are 5 characters (`72030`). Labels show the first 4; the API still uses 5.
- `/api/chart/{code}` with no rows is HTTP 404 `no data`, so `#chart-msg` is 「読み込めませんでした。」 (31160 on the golden DB). Do not expect 「株価データなし」 on the chart for that 404; that copy is the empty-candles 200 branch, which this API does not emit. Overview `#st-quote` 「株価データなし」 is a different field (`quote` null).
- `#tf`, `#mas`, `#chart-sub`, `#chart-log`, `#rel` keep their values across issuers; a new issuer is drawn with the current controls and the range resets to `6M`.
- Setting `.value` from a script does not redraw; dispatch `change` like the browser does.
- `#tf` defaults to `A` (自動). `W` and `M` are resampled in the browser (`chartBars`); MA windows scale down for week/month (÷5 / ÷20).
- `#perf-tbl` shows at most 12 rows even if the API returns more.
