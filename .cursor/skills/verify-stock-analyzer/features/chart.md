# チャート

The chart tab searches an issuer and draws adjusted daily, weekly, or monthly candles with optional MAs, optional TOPIX relative, and post-earnings α. There is no monthly-KPI panel on this tab.

## Sub-features

- `chart-search` typeahead from `#sym` via `/api/stocks/search`.
- `chart-candles` fills `#chart` from `/api/chart/{code}` `candles`.
- `chart-rel` toggles `#rel` and shows `#relwrap` when `/api/chart/{code}` includes TOPIX.
- `chart-perf` fills `#perf-tbl` from `/api/earnings-performance/{code}`.

## How to get to it (user POV)

- Click `nav button[data-tab="chart"]`.
- Type a name or 5-digit code into `#sym` and pick `#suggest`.
- From 決算速報 (issuer title) or スクリーナー (name cell), use `gotoChart(code)`. There is no chart button on 月次; 型の調査 / census is not in the SPA.
- Change `#tf` (`D` / `W` / `M` — 日足/週足/月足), `#mas`, or `#rel`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- A known code exists; doctor search for トヨタ should yield `72030` or similar.

- **Tab.** Click `nav button[data-tab="chart"]`. `#tab-chart` is displayed.
- **Search.** `GET /api/stocks/search?q=トヨタ` returns `{code,name,market}` with no `0`-prefix `code`. Typing the same into `#sym` opens `#suggest`.
- **Candles.** Run `helpers/capture.py chart --code 72030`. `artifacts/chart/body.json` has `candles` length > 0. Candle fields are `open`/`high`/`low`/`close` already filled from `adj_*`. `#chart-title` is non-empty.
- **Performance.** `artifacts/chart/performance.json` is a list (may be empty for a name with no events; still 200).
- **対TOPIX.** Check `#rel`. `#relwrap` display is not `none` when the chart payload can compute relative series.
- **Proof.** Keep `body.json` and (if CDP) `ui.png` with `#chart` tall enough to show candles and header `h1` `株式分析`.

## Gotchas

- Prices in the chart are **adjusted** (`adj_*`). Do not assert raw `close` against the canvas.
- Codes are 5 characters (`72030`). The table UI shows the first 4 as a label; the API still uses 5.
- Empty `candles` is 404 or missing history, not a drawing bug — fail the precondition.
- `#suggest` is `display:none` until there are hits; do not click it while hidden.
- `#tf` defaults to `D`; `W` and `M` resample on the server (MA windows scale down for week/month).
