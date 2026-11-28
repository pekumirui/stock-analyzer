# 大量保有報告書

The 大量保有報告書 tab lists large-holding filings for a selected `file_date` (default Asia/Tokyo today), searches issuers / holders without that date, and opens one holder (`#holders/{edinet}`) with current issuers, a 5-year ratio chart, and per-issuer history. Spec: `docs/screen/holders.md` (KAB-267 / KAB-272). Code: `static/js/holders.js`, `app/api/holders.py`, `app/holders.py`. 銘柄 › 株主 names land here via `gotoHolder`.

## Sub-features

- `holders-tab` shows `#tab-holders` on `nav button[data-tab="holders"]` (after ランキング). Hash `#holders`, `#hd-date` = Asia/Tokyo today, `#hd-sym` placeholder `銘柄名・保有者名 or コード`, one `GET /api/holders/activity?file_date=YYYY-MM-DD&limit=1000`, `#hd-top` visible, `#hd-detail` `display:none`. No heading 最近の動き. No `#hd-note`.
- `holders-search` types into `#hd-sym` and fills `#hd-suggest` from `GET /api/holders/search?q=` (`名前  EDINET`; holder name / EDINET only). Two or more characters (or `E`+digits / a 4–5 character code) refetch `GET /api/holders/activity?q=...&limit=200` (no `file_date`; `#hd-date` disabled); that feed also matches issuer name / code. One character is not a search: `#hd-date` stays enabled and the date list stays. Clearing the box returns to the selected date. Enter opens `hdLastSuggest[0]` when that array is non-empty; a suggestion click opens that row's EDINET. An issuer-only query leaves suggest empty, so Enter stays on the feed.
- `holders-feed` fills `#hd-feed` (`table.sheet`: 提出日 / 種類 / 保有者 / 銘柄 / 保有割合). 提出日 is `file_date` (`YY/MM/DD`; `title` 報告義務発生日). Holder names use `shortName` + EDINET under, kinds use `.qb`. Empty date → `#hd-feed-msg` 「この日の大量保有報告書はありません。」. Empty search → 「該当する大量保有報告書はありません。」. Holder name → detail, issuer name → `gotoChart`.
- `holders-detail` `#holders/{edinet}` loads `GET /api/holders/{edinet}` into `#hd-title`, `#hd-holdings`, `#hd-chart`, `#hd-series`. Missing code → HTTP 404 JSON `保有者がありません。`; the screen writes 「保有者が見つかりません。」. `#hd-back` (`← 大量保有報告書`) returns to the feed. No `#hd-activity`.
- `holders-below` latest ratio &lt; 5% rows sit under a `tr.below-h` 「5%未満」 header and use `tr.below` on `#hd-holdings` and 銘柄 › 株主, not on `#hd-feed`.

## How to get to it (user POV)

- Click `nav button[data-tab="holders"]`.
- Change `#hd-date` to another filing day.
- Type a holder name into `#hd-sym`, then Enter (first suggest hit) or a suggestion click (that row). Issuer / code only filters the feed.
- From 銘柄 › 株主, click a 提出者 name.
- Reload `#holders` or `#holders/E09096`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Rows need `large_holding_reports` `parsed` and `is_latest`. Golden has none: `helpers/capture.py holders` prints `items=0 search=0`, the date pane reads 「この日の大量保有報告書はありません。」, and `GET /api/holders/E09096` is 404 JSON `保有者がありません。` (the screen then writes 「保有者が見つかりません。」). Report rows / search hits / holder pages as `verified-unreachable`.

- **Capture.** `helpers/capture.py holders` writes `artifacts/holders/body.json` (`GET /api/holders/activity?file_date=` Asia/Tokyo today `&limit=1000`) and `search.json` (`GET /api/holders/search?q=ブラックロック`). Exits 0 when empty.
- **Tab.** Click `nav button[data-tab="holders"]` → hash `#holders`, `#hd-date` today (Asia/Tokyo), one `GET /api/holders/activity?file_date=...`.
- **Date.** Change `#hd-date` → `GET /api/holders/activity?file_date=` that day. Every `items[].file_date` matches.
- **Search.** Type `ブ` (one character): `#hd-date` stays enabled, the feed still uses `file_date`, Enter does not change the hash. Type `ブラックロック`: `#hd-suggest` includes `E09096` when that holder exists, feed uses `GET /api/holders/activity?q=ブラックロック` (no `file_date`), `#hd-date` disabled. Clear the box → date list again. Enter → `#holders/E09096` only when `hdLastSuggest[0]` is set; a suggestion click opens that row (`ui.route` is `holders/{edinet}`). Golden search is empty (`items=0 search=0`); Enter then stays on `#holders` and the pane reads 「該当する大量保有報告書はありません。」
- **Stock holders.** Reload `#stock/18020/holders` (or another code with large holdings) → `GET /api/stock/{code}/holders`, filer `.clicky` opens the holder tab. Golden 18020 is empty (`large_holders` []).

## Gotchas

- EDINET codes are `E` + 5 digits. A bad hash `#holders/foo` falls back to the list. `#holders/e09096` (lowercase) also restores the list; the API uppercases.
- List sorts by `file_date`, not `obligation_date`. Unchanged-ratio filings (`classify_move` returns `None`) are omitted from the date list, so a 2027-10-01 obligation filed 2026-10-08 does not appear on `#hd-feed` for that day (it still appears on the holder page). Dates more than 30 days after the filing, or after Asia/Tokyo today, get class `odd-date` (warn color, not muted `--sub`) on the feed, holder tables, and 銘柄 › 株主.
- Joint holders without `edinet_code` are omitted from search and holder pages.
- PTS is inside ランキング (`#ranking/pts`, KAB-265). Header has no PTS tab.
- One-character `#hd-sym` is not a search; the date list stays.
