# 保有者

The 保有者 tab searches large-holding filers by name or EDINET code, shows a recent-move feed, and opens one holder (`#holders/{edinet}`) with current issuers, a 5-year ratio chart, per-issuer history, and that holder's activity. Spec: `docs/DESIGN_holders.md` (KAB-267). Code: `static/js/holders.js`, `app/api/holders.py`, `app/holders.py`. 銘柄 › 株主 names land here via `gotoHolder`.

## Sub-features

- `holders-tab` shows `#tab-holders` on `nav button[data-tab="holders"]` (after ランキング). Hash `#holders`, one `GET /api/holders/activity`, `#hd-top` visible, `#hd-detail` `display:none`.
- `holders-search` types into `#hd-sym` and fills `#hd-suggest` from `GET /api/holders/search?q=` (`名前  EDINET`). Enter takes the first hit.
- `holders-feed` fills `#hd-feed` (`table.sheet`: 日付 / 種類 / 保有者 / 銘柄 / 保有割合). Holder names use `shortName` + EDINET under, kinds use `.qb`. Empty → `#hd-feed-msg` 「最近の大量保有の動きはありません。」. Holder name → detail, issuer name → `gotoChart`.
- `holders-detail` `#holders/{edinet}` loads `GET /api/holders/{edinet}` into `#hd-title`, `#hd-holdings`, `#hd-chart`, `#hd-series`, `#hd-activity`. Missing code → 404 「保有者が見つかりません。」. `#hd-back` returns to the feed.
- `holders-below` latest ratio &lt; 5% rows sit under a 5%未満 header and use `tr.below`.
- `holders-note` `#hd-note` always reads that 時価 / 平均取得単価 / 評価損益 are 目安.

## How to get to it (user POV)

- Click `nav button[data-tab="holders"]`.
- Type a holder name into `#hd-sym`, then Enter or a suggestion.
- From 銘柄 › 株主, click a 提出者 name.
- Reload `#holders` or `#holders/E09096`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Production / golden `large_holding_reports` must be `parsed` and `is_latest`. Empty is 「最近の大量保有の動きはありません。」 / 404 on a missing EDINET code.

- **Tab.** Click `nav button[data-tab="holders"]` → hash `#holders`, `#cur-tab` 保有者, one `GET /api/holders/activity`.
- **Search.** Type `ブラックロック` into `#hd-sym`: `#hd-suggest` includes `E09096`. Enter → `#holders/E09096`, `GET /api/holders/E09096`.
- **Stock holders.** Reload `#stock/18020/holders` (or another code with large holdings) → `GET /api/stock/{code}/holders`, filer `.clicky` opens the holder tab.

## Gotchas

- EDINET codes are `E` + 5 digits. A bad hash `#holders/foo` falls back to the list.
- Recent feed sorts by `file_date`, not `obligation_date`. A 2027-10-01 obligation with a 2026-10-08 filing is a source typo; the row is kept on the holder page and the date is muted when it is more than 30 days after the filing.
- Joint holders without `edinet_code` are omitted from search and holder pages.
- PTS is inside ランキング (`#ranking/pts`, KAB-265). Header has no PTS tab.
