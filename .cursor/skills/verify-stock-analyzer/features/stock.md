# 銘柄

The 銘柄 tab picks one issuer and shows its 決算短信 original text (TDnet XBRL summary + 添付資料, or an IR BANK PDF converted to HTML for older filings) in sandboxed iframes, with a filing select and 幅に合わせる / 中 / 原寸 zoom. Spec: `docs/DESIGN_stock.md`.

## Sub-features

- `stock-tab` shows `#tab-stock` on `nav button[data-tab="stock"]` (between チャート and スクリーナー). Opening it with no code sends no request; `#st-filing` is `hidden` and `#st-title` / `#st-doc` are empty.
- `stock-search` types into `#st-sym` (placeholder `銘柄名 or コード`) and fills `#st-suggest` from `GET /api/stocks/search?q=` (`4桁コード 銘柄名`). Enter takes the first hit, a click takes that row, Esc closes.
- `stock-filings` loads `GET /api/stock/{code}/filings` (`loadStock`). `#st-title` reads `{name} {code4} / {market} / {sector33}`. `#st-filing` lists filings with documents as `YYYY-MM-DD {title}` (PDF ones end with `（PDF から変換）`); default is the newest annual / semiannual / quarterly 短信, else the newest.
- `stock-doc` puts one `iframe` per document into `#st-doc`, each in `.st-page` (`sandbox="allow-same-origin"`, `scrolling="no"`, title サマリー / 添付資料 / PDF から変換). `src` is the document `url` (`/api/stock/filings/{id}/documents/{seq}` or `/api/stock/pdf/{disclosure_no}`). The iframe grows to its content height. A PDF filing shows `#st-src` 「出典: IR BANK（TDnet）。PDF から変換した原文（画像は出ません）」.
- `stock-zoom` switches `#st-zoom .ftab[data-zoom="fit"|"mid"|"actual"]` (幅に合わせる default / 中 / 原寸) and stores it in `localStorage` `stock.zoom`. `fitStockFrame` scales an iframe wider than `#st-doc` with `transform: scale()` (fit = width, mid = max(fit, 0.75), actual = 1) and sizes `.st-page` to the scaled box. When the document fits (PC), all three look the same.
- `stock-empty` shows `表示できる決算短信がありません。` (no filing with documents, title still filled), `銘柄が見つかりません。` (404), or `読み込めませんでした。`.
- `stock-route` writes `#stock/{code}` (`ui.route`); reload restores the issuer and the default filing.

## How to get to it (user POV)

- Click `nav button[data-tab="stock"]` (at ≤720px inside the `#menu-btn` drawer).
- Type a name or code into `#st-sym`, then Enter or click a `#st-suggest` row.
- Change `#st-filing`.
- Click 幅に合わせる / 中 / 原寸 in `#st-zoom`.
- Reload with `#stock/{code}`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- The code needs `xbrl_filings` with `xbrl_documents` rows (or `tdnet_pdf_documents` `ok` rows). A box DB copy may have none (every code then shows `表示できる決算短信がありません。`). On a copy this run owns, `DATABASE_URL=sqlite:////abs/copy.db .venv/bin/python jobs/xbrl/xbrl_parse.py parse --from D --to D --dest <xbrl dir with that day's manifest>` fills one day (2026-10-23 from `/mnt/vps/data/xbrl` gave 34 filings / 48 documents). Never run it on a shared DB. Without data, report the document paths as `verified-unreachable`.

- **Capture.** `helpers/capture.py stock --code 39120` writes `artifacts/stock/filings.json` (`{code, name, market, sector33, filings[]}`, each filing `{id, source, submitted_at, title, doc_type, period_type, is_amendment, documents[{seq, part, file_name, url}]}`) and `document.html` for the first document. It prints `filings=N` and exits 0 when N is 0.
- **Document HTTP.** `GET /api/stock/filings/{id}/documents/{seq}` is 200 `text/html` with `Content-Security-Policy` and `ETag` `"<sha256>.s<SANITIZE_VERSION>"`, no `<script>`, and `-webkit-text-size-adjust`. The same request with `If-None-Match` is 304. Unknown seq → 404 `原文がありません。`; unknown code `00000` → 404 `銘柄がありません。`.
- **Tab.** Click `nav button[data-tab="stock"]`: hash `#stock`, `#cur-tab` 銘柄, `#st-zoom` 幅に合わせる `.on`, no `/api/stock` request.
- **Search.** Type `モバイルファ` into `#st-sym`: `#st-suggest` shows `3912 モバイルファクトリー`. Enter → one `GET /api/stock/39120/filings`, hash `#stock/39120`, `#st-title` `モバイルファクトリー 3912 / スタンダード / 情報･通信業`, `#st-filing` `2026-10-23 2026年12月期　第３四半期決算短信〔日本基準〕(連結)`, two iframes サマリー (about 1454px) and 添付資料 (about 5211px), `scripts` 0 inside.
- **Zoom.** Click 原寸 / 中: `localStorage.stock.zoom` follows. On a 1400px window the document fits, so no `transform`. At 390px mobile, 幅に合わせる gives `scale(0.52…)` and `.st-page` height ≈ content × scale (1454 → 760px).
- **Route.** Reload on `#stock/39120`: same issuer, filing, and zoom.
- **Empty.** `loadStock('72030')` on a DB without 7203 filings: title filled, `#st-filing` hidden, `#st-doc` `表示できる決算短信がありません。`. `loadStock('99990')`: `銘柄が見つかりません。`.

## Gotchas

- `#st-zoom` buttons are `.ftab` like 決算速報 / PTS. Select by `[data-zoom]`.
- The iframes are `sandbox="allow-same-origin"` without `allow-scripts`; links inside do nothing. Measuring height needs same-origin access, so do not fetch documents from another origin.
- A changed sanitizer without a `SANITIZE_VERSION` bump keeps returning 304 with the old HTML.
- `fitStockFrame` re-measures only on load, zoom change, and window width change. On PC the first iframe can be measured before the page scrollbar appears and end up about 15px wider than `#st-doc` (clipped by `.st-page overflow:hidden`) until a zoom click or resize.
- EDINET 有報 are not listed; only TDnet XBRL and converted PDFs.
