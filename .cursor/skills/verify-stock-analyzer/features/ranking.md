# ランキング

The ranking tab lists one month of 決算発表後パフォーマンス as 上昇率 or 下落率. Spec is `docs/DESIGN_ranking.md`; code is `static/js/ranking.js` + `app/api/ranking.py`.

## Sub-features

- `rk-open` shows `#tab-ranking` and calls `loadRanking()` (`GET /api/ranking/earnings-perf`) when `#rk-tbl` is empty. `restoreRoute` on `#ranking` always calls it.
- `rk-dir` switches `#rk-kind .ftab[data-dir]` (`up` 上昇率 default / `down` 下落率). No refetch: the client keeps positives (`return_to_next > 0`, high first) or negatives (`return_to_next < 0`, most-negative first) and re-ranks. 0 and null are in neither list.
- `rk-month` is `#rk-month` (`YYYY年M月決算`, newest first). Changing it refetches that month and keeps the current 上昇率 / 下落率.
- `rk-table` fills `#rk-tbl.sheet`: 順位 / 市場 / 業種 / 銘柄 / パフォーマンス / 決算日 / 経過日数. パフォーマンス heading is `パフォーマンス` with `(対TOPIX)` under it. Each row's lower line is `(+x.x%)` only. 銘柄 is 5 characters + 4-digit code. 経過日数 is `N日` or `〜M/D`.
- `rk-info` reads `N 銘柄 / M/D 終値まで`. Empty dir reads `この月の上昇はありません` / `この月の下落はありません`.
- `rk-share` is `#rk-share` (`X に投稿`). Post text is 3 lines (`YYYY年M月決算 上昇率ランキングTOPN` or `下落率ランキングTOPN`, then the two 計測方法 lines). The PNG title is the first line only.
- `rk-to-chart` clicks a name → `gotoChart(code)` → 銘柄 › 株価, hash `#stock/{code}/price`.
- `rk-route` writes `#ranking` to `location.hash` + `ui.route`; reload reopens the tab.

## How to get to it (user POV)

- Click `nav button[data-tab="ranking"]` (label ランキング). At ≤720px open `#menu-btn` first.
- Click 上昇率 / 下落率 in `#rk-kind`.
- Pick a month in `#rk-month`.
- Click a name in `#rk-tbl` to open 銘柄 › 株価.
- Reload with `#ranking` (or `ui.route` `ranking`).

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- Rows need `earnings_events` with `is_first` and prices. Golden DB has them. Empty is `決算のデータがまだありません`.

- **API.** `GET /api/ranking/earnings-perf` (optional `?month=YYYY-MM`) returns `{months, month, as_of, rows}` with mixed signs in one list, nulls last. The UI splits that payload; there is no `dir=` query.
- **Tab.** Click `nav button[data-tab="ranking"]` → `#tab-ranking` displayed, hash `#ranking`, one `GET /api/ranking/earnings-perf`. 上昇率 is `.on`. `#rk-tbl` last row is still `> 0`. Header cell パフォーマンス has `(対TOPIX)` under the name.
- **下落率.** Click `.ftab[data-dir="down"]` → no extra `/api/ranking` request, 下落率 is `.on`, row 1 is the most negative `return_to_next`, last row is still `< 0`.
- **To 株価.** Click a name → 銘柄 active with 株価 `.on`, hash `#stock/{code}/price`.
- **Route.** On `#ranking`, reload → ランキング active and `#rk-tbl` filled again.
- **Proof.** If CDP: screenshots of both tabs at ~390px and desktop, plus the パフォーマンス header.

## Gotchas

- `#rk-kind` buttons are `.ftab` like 決算速報's 累計 / 3か月. Select by `[data-dir]`, not bare `.ftab`.
- Switching away and back does not reload when `#rk-tbl` already has HTML (`selectTab`). Reload or change the month to refetch.
- Hash-only `#ranking` does not call `selectTab`. Reload after setting the hash, or click the tab.
- Narrow viewports keep `#rk-tbl` and scroll horizontally. Do not look for `.card` or a hidden 対TOPIX column.
