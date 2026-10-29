# ランキング

The ranking tab has two inner tabs: 決算後パフォーマンス (default) and PTS. 決算後パフォーマンス lists one month as 上昇率 or 下落率. Spec is `docs/DESIGN_ranking.md`; code is `static/js/ranking.js` + `app/api/ranking.py`. PTS is `features/pts.md`.

## Sub-features

- `rk-subs` switches `#rk-subs .ftab[data-rksub]` (`perf` 決算後パフォーマンス default / `pts` PTS). Hash is `#ranking` or `#ranking/pts`. PTS details are in `features/pts.md`.
- `rk-open` shows `#tab-ranking` with 決算後パフォーマンス `.on` and calls `loadRanking()` (`GET /api/ranking/earnings-perf`) when `#rk-tbl` is empty. `restoreRoute` on `#ranking` always calls it.
- `rk-dir` switches `#rk-kind .ftab[data-dir]` (`up` 上昇率 default / `down` 下落率). No refetch: the client keeps positives (`return_to_next > 0`, high first) or negatives (`return_to_next < 0`, most-negative first) and re-ranks. 0 and null are in neither list.
- `rk-month` is `#rk-month` (`YYYY年M月決算`, newest first). Changing it refetches that month and keeps the current 上昇率 / 下落率.
- `rk-table` fills `#rk-tbl.sheet`: 順位 / 銘柄 / 市場 / 業種 / 騰落率 / 決算日 / 経過日数. 騰落率 heading is `騰落率` with `(対TOPIX)` under it. Each 騰落率 lower line is `(+x.x%)` / `(-x.x%)` / `–` (null α), with no `対TOPIX` word on the row. 銘柄 is `shortName` (5 chars) + `code.slice(0,4)` (7203 / 262A). 市場 is `fmtMarket` (`東証P` / `東証S` / `東証G`). 業種 is `fmtSector`. 決算日 has 場中 / 引け後 under it. 経過日数 is `N日`, `〜M/D`, or `–`. 順位 and 銘柄 pin; there is no pin hairline between 銘柄 and 市場 (`.grp` left border is on 騰落率).
- `rk-info` reads `N 銘柄 / M/D 終値まで` (filtered view count). Empty dir reads `この月の上昇率はありません` / `この月の下落率はありません`.
- `rk-share` is `#rk-share` (`X に投稿`). Post text is 3 lines (`YYYY年M月決算 上昇率ランキングTOPN` or `下落率ランキングTOPN`, then the two 計測方法 lines). The PNG title is the first line only.
- `rk-to-chart` clicks a name → `gotoChart(code)` → 銘柄 › 株価, hash `#stock/{code}/price`.
- `rk-route` writes `#ranking` (決算後パフォーマンス) or `#ranking/pts` to `location.hash` + `ui.route`; reload reopens that inner tab. Old `#pts` is rewritten to `#ranking/pts`.

## How to get to it (user POV)

- Click `nav button[data-tab="ranking"]` (label ランキング). At ≤720px open `#menu-btn` first.
- Click 決算後パフォーマンス / PTS in `#rk-subs`.
- Click 上昇率 / 下落率 in `#rk-kind`.
- Pick a month in `#rk-month`.
- Click a name in `#rk-tbl` to open 銘柄 › 株価.
- Reload with `#ranking` (or `ui.route` `ranking`).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Rows need `earnings_events` with `is_first` and prices. Golden DB has them. Empty is `決算のデータがまだありません`.

- **API.** `helpers/capture.py ranking` (optional `--month YYYY-MM`) writes `artifacts/ranking/body.json` from `GET /api/ranking/earnings-perf`. Body is `{months, month, as_of, rows}` with mixed signs in one list, nulls last. The UI splits that payload; there is no `dir=` query. Empty `months` is `決算のデータがまだありません`.
- **Tab.** Click `nav button[data-tab="ranking"]` → `#tab-ranking` displayed, hash `#ranking`, `#rk-subs [data-rksub="perf"]` `.on`, one `GET /api/ranking/earnings-perf`. 上昇率 is `.on`. `#rk-tbl` last row is still `> 0`. Header cell 騰落率 has `(対TOPIX)` under the name. 市場 cells read `東証P` / `東証S` / `東証G`. 決算日 has 場中 or 引け後 under the date. Header has no PTS tab.
- **下落率.** Click `.ftab[data-dir="down"]` → no extra `/api/ranking` request, 下落率 is `.on`, row 1 is the most negative `return_to_next`, last row is still `< 0`. A month with no negatives (2026-03 on the golden DB) reads `この月の下落率はありません` with an empty table; a month with no positives (2026-09) reads `この月の上昇率はありません`. Do not accept `この月の上昇はありません` / `この月の下落はありません`.
- **To 株価.** Click a name → 銘柄 active with 株価 `.on`, hash `#stock/{code}/price`.
- **Route.** On `#ranking`, reload → ランキング active and `#rk-tbl` filled again.
- **Proof.** If CDP: screenshots of both tabs at ~390px and desktop, plus the 騰落率 header.

## Gotchas

- `#rk-subs` and `#rk-kind` buttons are `.ftab` like 決算速報's 累計 / 3か月. Select by `[data-rksub]` or `[data-dir]`, not bare `.ftab`.
- Switching away and back does not reload when `#rk-tbl` already has HTML (`showRankingSub`). Reload or change the month to refetch. Leaving PTS and returning to 決算後パフォーマンス also skips the fetch when `#rk-tbl` has HTML.
- Hash-only `#ranking` does not call `selectTab`. Reload after setting the hash, or click the tab.
- Narrow viewports keep `#rk-tbl` and scroll horizontally. Do not look for `.card` or a hidden 対TOPIX column. 順位 / 銘柄 pin; there is no `#rk-tbl .pin1` hairline between 銘柄 and 市場. 騰落率 still has `.grp`’s left border.
- An empty direction, empty month, or failed load sets `#rk-tbl` `innerHTML` to `""`, so the next `selectTab("ranking")` refetches.
