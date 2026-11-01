# 大量保有報告書

The 大量保有報告書 tab lists large-holding filings for a selected `file_date` (default Asia/Tokyo today), searches issuers / holders without that date, and opens one holder (`#holders/{edinet}`) with current issuers, a 5-year ratio chart, and per-issuer history. Spec: `docs/DESIGN_holders.md` (KAB-267 / KAB-272). Code: `static/js/holders.js`, `app/api/holders.py`, `app/holders.py`. 銘柄 › 株主 names land here via `gotoHolder`.

## Sub-features

- `holders-tab` shows `#tab-holders` on `nav button[data-tab="holders"]` (after ランキング). Hash `#holders`, header crumbs `トップ > 大量保有報告書` (`#cur-tab` hidden), `#hd-date` = Asia/Tokyo today, one `GET /api/holders/activity?file_date=YYYY-MM-DD&limit=1000`, `#hd-top` visible, `#hd-detail` `display:none`. No heading 最近の動き. No `#hd-note`.
- `holders-search` types into `#hd-sym` and fills `#hd-suggest` from `GET /api/holders/search?q=` (`名前  EDINET`). The feed refetches `GET /api/holders/activity?q=...&limit=200` (no `file_date`; `#hd-date` disabled). Clearing the box returns to the selected date. Enter takes the first holder hit.
- `holders-feed` fills `#hd-feed` (`table.sheet`: 提出日 / 種類 / 保有者 / 銘柄 / 保有割合). 提出日 is `file_date` (`YY/MM/DD`; `title` 報告義務発生日). Holder names use `shortName` + EDINET under, kinds use `.qb`. Empty date → `#hd-feed-msg` 「この日の大量保有報告書はありません。」. Empty search → 「該当する大量保有報告書はありません。」. Holder name → detail, issuer name → `gotoChart`.
- `holders-detail` `#holders/{edinet}` loads `GET /api/holders/{edinet}` into `#hd-title`, `#hd-holdings`, `#hd-chart`, `#hd-series`. Missing code → 404 「保有者が見つかりません。」. `#hd-back` (`← 大量保有報告書`) returns to the feed. No `#hd-activity`.
- `holders-below` latest ratio &lt; 5% rows sit under a 5%未満 header and use `tr.below`.

## How to get to it (user POV)

- Click `nav button[data-tab="holders"]`.
- Change `#hd-date` to another filing day.
- Type a holder or issuer name into `#hd-sym`, then Enter or a suggestion.
- From 銘柄 › 株主, click a 提出者 name.
- Reload `#holders` or `#holders/E09096`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Production / golden `large_holding_reports` must be `parsed` and `is_latest`. Empty date is 「この日の大量保有報告書はありません。」 / 404 on a missing EDINET code.

- **Tab.** Click `nav button[data-tab="holders"]` → hash `#holders`, header crumbs `トップ > 大量保有報告書`, `#hd-date` today (Asia/Tokyo), one `GET /api/holders/activity?file_date=...`.
- **Date.** Change `#hd-date` → `GET /api/holders/activity?file_date=` that day. Every `items[].file_date` matches.
- **Search.** Type `ブラックロック` into `#hd-sym`: `#hd-suggest` includes `E09096`, feed uses `GET /api/holders/activity?q=ブラックロック`, `#hd-date` disabled. Clear the box → date list again. Enter → `#holders/E09096`, `GET /api/holders/E09096`.
- **Stock holders.** Reload `#stock/18020/holders` (or another code with large holdings) → `GET /api/stock/{code}/holders`, filer `.clicky` opens the holder tab.

## Gotchas

- EDINET codes are `E` + 5 digits. A bad hash `#holders/foo` falls back to the list.
- List sorts by `file_date`, not `obligation_date`. A 2027-10-01 obligation with a 2026-10-08 filing is a source typo; the row is kept on the holder page and the date is muted when it is more than 30 days after the filing.
- Joint holders without `edinet_code` are omitted from search and holder pages.
- PTS is inside ランキング (`#ranking/pts`, KAB-265). Header has no PTS tab.
- One-character `#hd-sym` is not a search; the date list stays.
