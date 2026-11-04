# 銘柄

The 銘柄 tab picks one issuer and shows a common header (name, price, 時価総額, 次回決算) over four sub tabs: 概要 (指標 + the 決算速報 決算表), 株価 (candles, see `chart.md`), ニュース (決算短信 list → the original text in sandboxed iframes with 幅に合わせる / 中 / 原寸 zoom), and 株主 (大量保有 + 自己株). Spec: `docs/DESIGN_stock.md` (KAB-178 #996, KAB-181 #1001, KAB-179 #1002, KAB-267). Code: `static/js/stock.js`, `static/js/stock-price.js`, `static/js/holders.js`, `app/api/stock.py`.

## Sub-features

- `stock-tab` shows `#tab-stock` on `nav button[data-tab="stock"]` (second, between 決算速報 and スクリーナー). With no code it sends no request; `#st-head` is `hidden`, every `.st-sub` is hidden, `#st-msg` is hidden, hash `#stock`.
- `stock-search` types into `#st-sym` (placeholder `銘柄名 or コード`) and fills `#st-suggest` from `GET /api/stocks/search?q=` (`4桁コード 銘柄名`). Enter takes the first hit, a click takes that row, Esc closes. Picking keeps the current sub tab (from a news detail it goes to the list).
- `stock-head` loads `GET /api/stock/{code}/overview` once per code (`showStock`). `#st-title` reads `{name} {code4} / {market} / {sector33}`; `#st-quote` reads `{close} {±change} ({±pct}%) MM/DD 終値 時価総額 N億円` plus `次回決算 MM/DD` (or `未定` when `next_earnings.date` is null; nothing when `next_earnings` is null). No quote → 「株価データなし」. `#st-sym` is set to the issuer name. Then `#st-head` is shown and `#st-tabs.nav-tabs .ftab[data-sub]` gets `.on` on the current sub (folder tab: top+side border, not the filled `.fin-tabs` look).
- `stock-summary` (概要, default) fills `#st-metrics`: PER（予想） `x.x倍` / PBR（実績） `x.xx倍` / 配当利回り（予想） `x.xx%` / ROE（実績） `x.x%` / 自己資本比率 `x.x%`, `–` when the metric is null. Below, `#st-fin-tabs .ftab[data-stfin="cum"|"q"|"prog"|"rev"]` (累計 / 3か月 / 進捗率 / 修正履歴) switch `[data-stfin-wrap]` → `#st-cum-tbl` / `#st-q-tbl` / `#st-prog-tbl` / `#st-rev-tbl`, drawn by earnings.js `renderFin` / `renderProg` / `renderRev` from one `GET /api/earnings/detail/{code}` (no `disclosure_no`, latest filing). A 404 is treated as success with empty rows: 累計 / 3か月 / 進捗率 read `データなし`, 修正履歴 reads `修正履歴なし`. Any other error reads 読み込めませんでした。 in each table.
- `stock-price` (株価) is `features/chart.md`.
- `stock-news` (ニュース) loads `GET /api/stock/{code}/filings` once per code into `table#st-news` (日時 `YY/MM/DD HH:MM` / 種類 / 題名). 種類 is `決算` for annual / semiannual / quarterly, else `修正`, plus `訂正` when `is_amendment`. PDF titles end with `（PDF から変換）`. Rows with documents are `tr.go[data-id]` (`x{id}` XBRL, `p{disclosure_no}` PDF). No filings → `#st-msg` 「ニュースがありません。」. Only /filings today; DESIGN_stock.md's `/news` and `#st-news-kinds` are KAB-180 and not in the code.
- `stock-detail` clicks a `tr.go` → `history.pushState` to `#stock/{code}/news/{id}`, `#st-news-list` hidden, `#st-news-detail` shown: `#st-back` 「← ニュース」, `#st-zoom`, `#st-news-head` (`日時 種類 [訂正] 題名`), `#st-src`, `#st-doc`. An id that matches `NEWS_ID_RE` but is missing from the list → 「書類が見つかりません。」. An id that fails the regex (`NOPE`) is stripped and the list is shown.
- `stock-doc` puts one `iframe` per document into `#st-doc`, each in `.st-page` (`sandbox="allow-same-origin"`, `scrolling="no"`, title サマリー / 添付資料 / PDF から変換). `src` is the document `url` (`/api/stock/filings/{id}/documents/{seq}` or `/api/stock/pdf/{disclosure_no}`). The iframe grows to its content height. A PDF filing shows `#st-src` 「出典: {attribution}。PDF から変換した原文（画像は出ません）」.
- `stock-zoom` switches `#st-zoom .ftab[data-zoom="fit"|"mid"|"actual"]` (幅に合わせる default / 中 / 原寸) and stores it in `localStorage` `stock.zoom`. `fitStockFrame` scales an iframe wider than `#st-doc` with `transform: scale()` (fit = width, mid = max(fit, 0.75), actual = 1) and sizes `.st-page` to the scaled box. When the document fits (PC), all three look the same.
- `stock-holders` (株主) loads `GET /api/stock/{code}/holders` into `#st-holders-large` / `#st-holders-chart` / `#st-holders-treasury`. Filer names with `filer_edinet_code` call `gotoHolder` → `#holders/{edinet}`. Empty large holders → 「大量保有報告はありません。」. No buyback → 「自己株券買付状況報告書はありません。」.
- `stock-empty` `#st-msg`: 「銘柄が見つかりません。」 (overview 404; `#st-head` stays hidden), 「読み込めませんでした。」, 「ニュースがありません。」.
- `stock-route` writes `#stock/{code}/{sub}` or `#stock/{code}/news/{id}` (`ui.route`) via `stockRoutePath()`. Reload restores issuer, sub tab, and news detail. `#stock/{code}` opens 概要; an unknown sub falls back to `summary`. `#st-back` replaces the hash; browser back pops the detail (`popstate`, only while 銘柄 is visible).

## How to get to it (user POV)

- Click `nav button[data-tab="stock"]` (at ≤720px open `#menu-btn` first so `#app-nav` has `.open`).
- Type a name or code into `#st-sym`, then Enter or click a `#st-suggest` row.
- Click 概要 / 株価 / ニュース / 株主 in `#st-tabs.nav-tabs`, and 累計 / 3か月 / 進捗率 / 修正履歴 in `#st-fin-tabs` (still `.fin-tabs`).
- On ニュース click a row, then 幅に合わせる / 中 / 原寸 in `#st-zoom`, and 「← ニュース」 or the browser back button.
- From other tabs, an issuer name (`gotoChart`) opens 株価.
- Reload with `#stock/{code}`, `#stock/{code}/{sub}`, or `#stock/{code}/news/{id}`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- ニュース rows need `xbrl_filings` with `xbrl_documents` rows (or `tdnet_pdf_documents` `ok` rows). A box DB copy may have none (every code then reads 「ニュースがありません。」). On a copy this run owns, `DATABASE_URL=sqlite:////abs/copy.db .venv/bin/python jobs/xbrl/xbrl_parse.py parse --from D --to D --dest <xbrl dir with that day's manifest>` fills one day (2026-10-23 from `/mnt/vps/data/xbrl` gave 34 filings / 48 documents). Never run it on a shared DB. Without data, report the document paths as `verified-unreachable`.

- **Capture.** `helpers/capture.py stock --code 72030` writes `artifacts/stock/overview.json` (keys above; golden 72030: close 3094, change +65, `next_earnings` null, per ~11.4, pbr ~1.01; 31160 `quote` null; 39120 has `next_earnings` null), `filings.json` (`{code, name, market, sector33, filings[]}`, each filing `{id, source, submitted_at, title, doc_type, period_type, is_amendment, documents[{seq, part, file_name, url}]}`), and `document.html` for the first document. It prints `filings=N` and exits 0 when N is 0; an unknown code exits 1 with overview 404 `銘柄がありません。`.
- **Document HTTP.** `GET /api/stock/filings/{id}/documents/{seq}` is 200 `text/html` with `Content-Security-Policy` and `ETag` `"<sha256>.s<SANITIZE_VERSION>"`, no `<script>`, and `-webkit-text-size-adjust`. The same request with `If-None-Match` is 304. Unknown filing id or seq → 404 `原文がありません。`. Overview / filings / holders for unknown code `00000` → 404 `銘柄がありません。` (the document URL has no code).
- **Tab.** Click `nav button[data-tab="stock"]`: hash `#stock`, header crumbs `トップ > 銘柄` (`#cur-tab` hidden), `#st-head` hidden, no `/api/stock` request.
- **Search.** Type `トヨタ` into `#st-sym`: `#st-suggest` `3116 トヨタ紡織`, `7203 トヨタ自動車`. Enter → hash `#stock/31160/summary?from=stock`, `ui.route` `stock/31160/summary` (no query), exactly `GET /api/stock/31160/overview` + `GET /api/earnings/detail/31160`, `#st-title` `トヨタ紡織 3116 / プライム / 輸送用機器`, `#st-quote` 「株価データなし」 on golden (no candles either). Reload `#stock/72030/summary`: `#st-quote` `3,094 +65 (+2.15%) 10/16 終値 時価総額 451,569億円` with no 次回決算 (`next_earnings` null). `#st-cum-tbl` / `#st-q-tbl` headers include 粗利益; `#st-prog-tbl` / `#st-rev-tbl` do not. Header crumbs are `トップ > 銘柄 > 概要` (`#cur-tab` hidden). Inner-tab clicks add `?from=stock-summary` (株価) / `stock-price` / `stock-news`.
- **Fin tabs.** Click each `[data-stfin]`: only its wrap is visible. Golden 31160 累計 reads `データなし` (no filings in this copy). On a box copy with statements: `#st-cum-tbl` 7 rows, `#st-q-tbl` 12, `#st-prog-tbl` 5, `#st-rev-tbl` 13 (期初 / 修正 / 実績 badges).
- **News list.** Reload `#stock/39120/news`: `#st-news` header 日時 / 種類 / 題名, one `tr.go[data-id="x1"]` `26/10/23 13:00 | 決算 | 2026年12月期　第３四半期決算短信〔日本基準〕(連結)` (copy with the 10/23 parse).
- **Detail.** Click the row → hash `#stock/39120/news/x1`, `#st-news-head` filled, two iframes サマリー / 添付資料, `scripts` 0 inside. 「← ニュース」 → `#stock/39120/news`, list visible. Click again then `history.back()` → list again. `#stock/72030/news/zz9999` (id matches `NEWS_ID_RE`) → 「書類が見つかりません。」. `#stock/72030/news/NOPE` fails the regex, rewrites to `#stock/72030/news`, and on golden shows 「ニュースがありません。」.
- **Zoom / fit (KAB-175).** At 1400×1000 on a fresh reload of the detail, iframe `style.width` equals `#st-doc.clientWidth` (1337px) and `#st-doc.scrollWidth` equals `clientWidth` (no clip). At 390px mobile, 幅に合わせる gives `scale(0.52…)` (`style.width` 655px, rect 342px = `#st-doc` width). 原寸 / 中 update `localStorage.stock.zoom`.
- **Holders.** `.ftab[data-sub="holders"]` → hash `…/holders?from=` the current sub, one `GET /api/stock/{code}/holders`. `#st-holders-large` has 提出者 / 保有割合 / 差 / 義務発生日 / 保有目的 (`title` on 義務発生日 is 報告義務発生日). A filer name with `filer_edinet_code` is `.clicky` and opens `#holders/{edinet}?from=stock-holders`. No rows → 「大量保有報告はありません。」 (golden 72030 / 18020; the header row is omitted when empty). `#st-holders-treasury` empty copy is 「自己株券買付状況報告書はありません。」.
- **Empty.** `#stock/31160/news` (no filings in the copy) → 「ニュースがありません。」. `#stock/99990` → hash `#stock/99990/summary`, `#st-head` hidden, `#st-msg` 「銘柄が見つかりません。」. `#stock/72030/bogus` → `#stock/72030/summary`.

## Gotchas

- `#st-tabs` is `.nav-tabs` (folder tabs, KAB-315). `#st-fin-tabs` and `#st-zoom` stay `.fin-tabs` like 決算速報 / PTS. Select by `[data-sub]`, `[data-stfin]`, `[data-zoom]`. At 390px the four `#st-tabs` labels stay on one row; `.on` has top+left+right border and a white bottom that meets the content, inactive tabs sit on the bottom line only.
- There is no `#st-filing` select any more; filings are the ニュース rows.
- The overview is fetched once per code. Switching sub tabs does not refetch it; 概要 fetches `/api/earnings/detail` once per code (`stockFinCode`), ニュース fetches `/filings` once per code.
- Routing has no `hashchange` handler. Setting `location.hash` to `#stock/...` from another tab (same document) does nothing until reload; `popstate` only routes while `#tab-stock` is visible.
- The iframes are `sandbox="allow-same-origin"` without `allow-scripts`; links inside do nothing. Measuring height needs same-origin access, so do not fetch documents from another origin.
- A changed sanitizer without a `SANITIZE_VERSION` bump keeps returning 304 with the old HTML.
- `fitStockFrame` re-measures on load (and the next animation frame), zoom change, window width change, and `#st-doc` width change (`ResizeObserver`). The observer covers the PC first paint where the page scrollbar appears after the first measure and used to clip ~15px (KAB-175, fixed in #988). Height-only resize (mobile address bar) does not re-measure.
- EDINET 有報 are not listed; only TDnet XBRL and converted PDFs.
