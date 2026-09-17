---
name: monthly-html-ops-ingest
description: >
  Operational IR HTML monthly ingest for stock-analyzer: fill calendar-year gaps via
  archive/year URLs, merge multi-table pages, override parser as_of when live HTML
  proves the fiscal year, clean label ghosts with issuer_aliases + ops merge.
  Use when backfilling HTML 月次, issue #180-style ops, monthly_ingest MCP posts,
  missing 2024/1–2 or H1/H2 halves, or after adding aliases that do not rewrite DB rows.
---

# IR HTML monthly ops ingest

Production loopback via stock-analyzer MCP (`monthly_*`). Design stays in `DESIGN.md` / `docs/DESIGN_monthly.md` / `docs/DESIGN_monthly_labels.md`. This skill is **ops only**.

## When to use

- Fill missing calendar months for HTML issuers in `config/ir_monthly.json`
- Track a wave on a GitHub issue (#180 pattern)
- Clean old row labels after alias/canonicalize ships

Not for: parser code changes (separate PR), TDnet PDF, XBRL, SPA verify (use `verify-stock-analyzer`).

## Pipeline (truth)

1. Live HTML (year/archive URL) → parse locally with `scripts.ir_monthly_html_r2.html_to_ingest`
2. Optional: hand-edit payload (`as_of`, months, element names, merge tables)
3. `plan_column_years(months, fy_end_month, as_of)` — prove calendar years before POST
4. Conflict-check vs `monthly_get` on overlapping cells
5. `monthly_ingest` (MCP) — **no `year`**. UPSERT ok
6. `monthly_get` verify

Cron path is different: `ir_monthly_html_r2` writes `.ingest.json` only; `ir_monthly_html_ingest` POSTs **latest-per-code**. Past years need ops + archive URLs.

## Hard rules

- Agent-authored ingest JSON is allowed. Do **not** treat parser `as_of` as sacred. Override `as_of` / labels when live HTML proves the fiscal year (example: Kappa nav max `YYYY年度` poisoning as_of). Parser bug ≠ cannot ingest.
- HTML parser does **not** apply `issuer_aliases`. Aliases run only in `POST …/ingest` (`app/api.py` `_canonical_element`: canonicalize → `apply_issuer_aliases` → canonicalize). Adding aliases does **not** rewrite existing DB rows — ops merge after.
- Do **not** year-PUT for new data (`monthly_put_values` is correction only).
- Do **not** merge brand renames (例: マックスバリュ関東 ↔ イオンフードスタイル).
- Do **not** POST Watami-class bad units (実数 tagged `yoy_pct`).
- Ask when unsure (as_of/year mapping, dual-scope Alpen, unit ambiguity).

## Tools

| Tool | Use |
|---|---|
| MCP `monthly_get` / `monthly_ingest` / `monthly_put_values` / `monthly_add_element` / `monthly_delete_element` | Prod loopback |
| `config/ir_monthly.json` | `index_url`, `follow_re`, html issuers |
| `.venv/bin/python` + `html_to_ingest` / `plan_column_years` | Local parse + year map |
| `app/issuer_aliases.json` + `docs/DESIGN_monthly_labels.md` | New rename rules (code PR) |
| GitHub issue | Progress before/after |

VPS: `ssh stock-vps` + `curl http://127.0.0.1:8000/api/v1/tables/...` is the same API as MCP.

## Pre-POST checklist

Copy and tick:

```
- [ ] Source URL fetched; parse shows needed months
- [ ] fy_end from prod statements (or known); plan_column_years printed
- [ ] as_of chosen deliberately (parser default or override) — leap days ok (2024-02-29)
- [ ] Element names match DB finals (no ghost prefixes if aliases not on VPS yet: send clean names)
- [ ] Overlap vs monthly_get: no conflicting non-null cells (or force only after proof equal)
- [ ] Not year-PUT for new cells
- [ ] Issue comment: URL, as_of, mapping years, what fills
```

## Fill-gap playbook

1. `monthly_get` for 2024/2025/2026 — list missing months **per code**
2. Find archive/year URL (not only `index_url`). Cron/`follow_re` + `select_latest_per_code` cannot backfill same-URL-only issuers
3. Parse → `plan_column_years`. If the page labeled "2024" maps 1–2 to calendar **2025**, get the **previous FY** page for calendar 2024/1–2
4. Multi-table (上期+下期): merge before POST — parser keeps one table
5. POST `monthly_ingest`; verify `monthly_get`
6. After any "2024" page wave: re-scan **2024 months 1–12** systematically (first wave often leaves 1–2 or H2 empty)

## Label cleanup playbook

1. Prove rule: canonicalize alone vs `issuer_aliases` (grep / local apply)
2. If rule missing → small PR (aliases or canonicalize) + DESIGN_monthly_labels one-liner
3. Ops on prod (aliases do not migrate old rows):
   - conflict-check same month on src vs dst
   - `monthly_add_element` dst if needed (`force` when similar)
   - `monthly_put_values` copy → null-clear src → `monthly_delete_element` empty src
4. Never invent 全店↔全社 folds. **全社 ≠ 全店** globally. Alpen keeps both. USMH ghost `全店` → `全社` only when HTML is 全社-only

## Stop conditions

Stop and ask (do not POST):

- as_of / calendar mapping unclear (Kappa-class)
- unit looks wrong (`yoy_pct` on counts)
- archive 404 and no other URL proves the months
- merge would collide unequal values
- brand/scope rename judgment call

## Related

- Trap table: [gotchas.md](gotchas.md)
- UI proof after data lands: `.cursor/skills/verify-stock-analyzer/SKILL.md`
