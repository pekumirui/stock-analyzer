# スクリーナー

The screener tab lists issuers matching Minervini stage and RS rank, using the latest `indicators` date.

## Sub-features

- `screener-open` shows `#tab-screener` and auto-runs `loadScreener` when `#sc-tbl` is empty.
- `screener-default` uses Stage `2` and RS `70` (the select/input defaults).
- `screener-filter` changes `#sc-stage` / `#sc-rs` and 検索.
- `screener-to-chart` clicks a name (`.clicky` → `gotoChart`).

## How to get to it (user POV)

- Click `nav button[data-tab="screener"]`.
- Change ステージ / RS≧ and press 検索.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok` (this already called `GET /api/screener?stage=2&rs_min=70`).

- **Tab.** Click `nav button[data-tab="screener"]`. The button is `.active`. `#tab-screener` is displayed. `#sc-stage` value is `2`, `#sc-rs` value is `70` unless you changed them.
- **Auto load.** Opening the tab with an empty `#sc-tbl` calls `loadScreener` without an extra click.
- **Default payload.** Run `helpers/capture.py screener`. `artifacts/screener/body.json` has `date` (ISO) and `results` with `code`, `name`, `rs_rank`, `stage`. Every `stage` is `2` and every `rs_rank` ≥ 70. No `code` starts with `0`.
- **検索.** Click `#tab-screener button.btn` (label 検索) after changing RS to `80`. `GET /api/screener?stage=2&rs_min=80` has no row with `rs_rank` < 80.
- **Proof.** Keep `body.json` (action URL in `request.txt`) and (if CDP) `ui.png` showing `#sc-tbl` rows, `基準日:` in `#sc-date`, and the header.

## Gotchas

- Bare `GET /api/screener` (no query) is **not** the UI default. The UI always sends `stage` when the select is not empty, and `rs_min`. Prove `stage=2&rs_min=70` unless you changed the controls.
- The API caps `limit=100` (default). A full Stage∩RS set can be truncated; `helpers/capture.py` may also trim the saved `results` list.
- Switching away and back does not reload if `#sc-tbl` already has HTML.
- When any `Stock.product_category` in `RS_PRODUCT_CATEGORIES` (`011`) exists, the screener query itself filters to those categories (not ranking-only). A name missing from the table can still exist in search. The "no `0`-prefix code" rule is DB hygiene, not a screener filter.
- `restoreRoute` to screener always calls `loadScreener()` (unlike `selectTab`, which skips reload when `#sc-tbl` already has HTML).
