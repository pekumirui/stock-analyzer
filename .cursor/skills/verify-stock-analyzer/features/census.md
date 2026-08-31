# 型の調査

The census tab lists monthly issuers and how their KPI columns look, for mapping work.

## Sub-features

- `census-open` shows `#tab-census` and calls `loadCensus`.
- `census-rows` fills `#cen-list` from `/api/monthly/census` `rows`.
- `census-detail` fills `#cen-kpi` for one issuer.

## How to get to it (user POV)

- Click `nav button[data-tab="census"]`.
- Click a row in `#cen-list`.

## Driving it with verify-stock-analyzer

Preconditions:

- Doctor reports `ok`.
- `helpers/capture.py census` exits 0. If census `rows` is empty, skip and say monthly ingest never ran.

- **Tab.** Click `nav button[data-tab="census"]`. `#tab-census` is displayed.
- **List.** `artifacts/census/body.json` has `summary` and non-empty `rows`. `#cen-count` is non-empty.
- **Detail.** Choosing a row calls the monthly issuer payload into `#cen-kpi`.
- **Proof.** Keep `body.json` and (if CDP) `ui.png` of `#cen-list` with `株式分析ツール`.

## Gotchas

- Census is a mapping/debug surface, not a trading screen. Empty `rows` means no monthly issuers, not a render bug.
- Opening the tab always refetches (`loadCensus` on every tab click).
