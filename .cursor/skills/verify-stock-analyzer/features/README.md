# stock-analyzer verification map

This directory is the maintained source for verifying the user-facing behavior of stock-analyzer. Read the index before driving the app, then use the matching feature file as the recipe.

## Baseline preconditions

- Launch with `helpers/launch.py` at `http://127.0.0.1:8011` (or `VERIFY_PORT`). Never drive `:8000` unless that pid is in `.run/state.json`.
- `stock.db` is the backfilled SQLite next to the repo (not a CI temp DB, not demo `0`-prefix codes).
- Run `helpers/doctor.py` and require `ok: true`, non-empty screener, and a トヨタ search hit.
- Never drive an instance that was not started by this verification run.

## Driving conventions

- Start every recipe from the SPA at `{url}/` with the 決算速報 tab showing unless the file says otherwise.
- Prefer `nav button[data-tab]`, `#id`, and `data-fin` / `data-no` over pixel clicks.
- Treat every command as literal.
- HTTP proofs use `helpers/capture.py <feature>` against the same `/api/*` the SPA calls.
- Browser proofs use `browser-use`: `new_tab(url)` then click the tab handle.
- Cleanup removes `.run/` only. Leave `artifacts/`.

## Proof and skip reporting

- Capture the user action and the resulting state, not only the final screen.
- UI proof includes a screenshot with header `h1` `株式分析` visible (document title is `株式分析ツール`).
- HTTP proof includes `request.txt` and `body.json`.
- Record the feature ID and entry point with every artifact.
- Report an unreachable path with the attempted command and the unmet precondition.
- Do not report a skipped entry point as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 title and one paragraph describing the user-visible behavior. It then uses exactly four H2 sections in this order.

1. `Sub-features` lists short IDs with one line for each behavior.
2. `How to get to it (user POV)` lists every user entry point.
3. `Driving it with verify-stock-analyzer` starts with `Preconditions:` and uses labeled bullets that pair each user action with an exact command and observable result.
4. `Gotchas` lists traps that can waste or invalidate a verification run.

## Features

- [決算速報](./earnings.md) calendar day list, row detail, 累計 / 3か月 tabs.
- [チャート](./chart.md) symbol search, daily candles, 対TOPIX, 決算発表後リターン.
- [スクリーナー](./screener.md) Stage + RS filter table (UI defaults Stage 2, RS≥70).
- [月次](./monthly.md) TDnet monthly disclosures by day and issuer.
- [型の調査](./census.md) monthly KPI census across issuers.
