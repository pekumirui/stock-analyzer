# 銘柄 › 株価

The 株価 sub tab of 銘柄 draws adjusted daily, weekly, or monthly candles with optional MAs, optional TOPIX relative, and post-earnings α for the issuer picked in 銘柄. It was the チャート tab until KAB-181 (#1001); there is no `#tab-chart`, `#sym`, `#suggest`, `#chart-title`, or 表示 button any more. Code is `static/js/stock-price.js` (+ `chart-shared.js`).

## Sub-features

- `price-open` shows `#st-sub-price` on `#st-tabs .ftab[data-sub="price"]` (or route `#stock/{code}/price`). `showStockPrice` runs `loadChart()` only when the drawn code (`stockPriceCode`) differs, so reopening the tab for the same issuer sends nothing.
- `price-candles` fills `#chart` (Lightweight Charts 5.2.1 from unpkg; v5 API `addSeries(LightweightCharts.CandlestickSeries)` / `createSeriesMarkers`) from `/api/chart/{code}` `candles`. No candles → `#chart-msg` 「株価データなし」; a failed request → 「読み込めませんでした。」.
- `price-controls` `#tf` (`D` / `W` / `M` 日足/週足/月足) and `#rel` redraw on `change`; `#mas` (default `50,150,200`) redraws on `change` (Enter or blur). Each redraw refetches `/api/chart` and `/api/earnings-performance` together.
- `price-rel` shows `#relwrap` / `#relchart` (対TOPIX相対, 期首=100) when `#rel` is on and the payload has `relative`; otherwise `#relwrap` is `display:none`.
- `price-perf` fills `#perf-tbl` (発表日 / +1d / α / +5d / α / +20d / α / +60d / α / 現在まで / α) from `/api/earnings-performance/{code}`, at most 12 rows.
- `price-events` `.evt-toggles` (決算発表) inside `#st-sub-price` overlays markers from chart JSON `earnings_dates`; on/off is kept in `localStorage.chartEventOverlays`.
- `price-goto` `gotoChart(code)` from 決算速報 (`#e-chart-title .clicky`), スクリーナー / PTS (name `.clicky`), サプライ (`#supply-chart-link` 「株価を開く」) opens 銘柄 with 株価 `.on`, hash `#stock/{code}/price`.
- `price-route` `#stock/{code}/price` restores it on reload. Old `#chart/{code}` or `ui.route` `chart/{code}` is read as `stock/{code}/price`; bare `#chart` → `#stock`.

## How to get to it (user POV)

- In 銘柄, pick an issuer (`#st-sym`) and click 株価 in `#st-tabs`.
- Click an issuer name on 決算速報 (chart title), スクリーナー, PTS, or 「株価を開く」 on a listed サプライ company.
- Change `#tf`, tick `#rel`, or edit `#mas` and press Enter.
- Tick `.evt-toggles` 決算発表.
- Reload with `#stock/{code}/price` (or an old `#chart/{code}` bookmark).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- A known code exists; doctor search for トヨタ should yield `72030` or similar.

- **Capture.** `helpers/capture.py chart --code 72030` writes `artifacts/chart/body.json` (`candles` length > 0, `open`/`high`/`low`/`close` already from `adj_*`, may include `earnings_dates`) and `performance.json` (a list, may be empty; still 200).
- **Open.** On `#stock/31160` click `.ftab[data-sub="price"]`: hash `#stock/31160/price`, `#chart canvas` present, `#chart-msg` empty, requests exactly `GET /api/chart/31160?tf=D&ma=50%2C150%2C200&rel=false` + `GET /api/earnings-performance/31160`. No button whose label contains 表示.
- **Controls.** Set `#tf` to `W` and dispatch `change` → `…?tf=W&ma=50%2C150%2C200&rel=false` + perf. Tick `#rel` → `…&rel=true`, `#relwrap` display `block`. Set `#mas` to `25` + `change` → `…&ma=25&rel=true`.
- **Reopen.** Click 概要 then 株価 again: no `/api/*` request.
- **Old route.** Reload on `#chart/72030` → hash `#stock/72030/price`, `#st-title` `トヨタ自動車 7203 / プライム / 輸送用機器`, only `#st-sub-price` visible. `localStorage` `ui.route=chart/39120` with no hash → `#stock/39120/price`.
- **gotoChart.** From each caller above: 銘柄 active, 株価 `.on`, hash `#stock/{code}/price`, `GET /api/stock/{code}/overview` then the two price requests.
- **Proof.** Keep `body.json` and (if CDP) a screenshot with `#chart` tall enough to show candles and the 銘柄 header.

## Gotchas

- Prices in the chart are **adjusted** (`adj_*`). Do not assert raw `close` against the canvas; the header `#st-quote` uses the overview `quote.close`.
- Codes are 5 characters (`72030`). Labels show the first 4; the API still uses 5.
- Empty `candles` is missing history, not a drawing bug — fail the precondition.
- `#tf`, `#mas`, `#rel` keep their values across issuers; a new issuer is drawn with the current controls.
- Setting `.value` from a script does not redraw; dispatch `change` like the browser does.
- `#tf` defaults to `D`; `W` and `M` resample on the server (MA windows scale down for week/month).
- `#perf-tbl` shows at most 12 rows even if the API returns more.
