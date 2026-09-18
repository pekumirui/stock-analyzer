# HTML monthly ops gotchas

Short traps from #180-style waves. Codes are examples, not exclusive.

| Trap | What happens | What to do |
|---|---|---|
| Parser `as_of` poisoned | Kappa: nav max `YYYY年度` → wrong year map | Override `as_of` from live HTML / FY end; still ingest |
| Aliases in JSON only | `html_to_ingest` never calls aliases; DB keeps old names | POST still goes through `_canonical_element`; **ops merge** existing rows |
| Latest-only cron | `follow_re` + latest sidecar → one year | Use year/archive URLs for past calendars |
| Same-URL overwrite | No archive → cannot backfill | List as blocked; ask for another URL |
| One table of two | Ministop 9946, Konaka 7494: H1 or H2 only | Merge 上期+下期 before POST |
| "2024" page ≠ full 2024 | Mar–Feb FY: page 1–2 → next calendar year | Previous FY page for calendar 1–2 (USMH `y2023`→2024/1–2; Fuji `year2023` 404 = blocked) |
| First wave incomplete | After ingesting "2024" labeled pages, 1–2 or H2 still empty | Scan 2024/1–12 per code every wave |
| No pair review | Agent self-certifies; 2024–2026 holes or wrong values slip through | Second agent/human: `monthly_get` all three years per code **and** spot-check numbers vs source HTML; fail if any year unchecked or a sample mismatches |
| 全社 vs 全店 | Old canonicalize folded 全社→全店 | Keep both when HTML has both (Alpen). USMH: merge ghost 全店→全社 only if HTML is 全社-only |
| `U.S.M.H` prefix | Company total column | Alias `32220` + ops rename to `全社`/`既存` |
| `売上（%）` prefix | Nitori header stays after canonicalize | Alias `98430` + ops merge; 客数/客単価 `（%）` already drops |
| Circled footnotes `①` | #169 drops `(1)` only | Canonicalize `①`–`⑳` (or alias) + ops merge old `① 全店 …` |
| Bad unit | Watami 拠点数 as `yoy_pct` | Parser treats 拠点数 as `number`. Still stop if a count row is yoy |
| Dual brand columns | マックスバリュ関東 vs イオンフードスタイル | Never merge as same metric |

## Verify snippet

```python
from datetime import date
from scripts.ir_monthly_html_r2 import html_to_ingest
from app.monthly_ingest import plan_column_years

p = html_to_ingest(html, "3222")
# optional: p["as_of"] = "2024-02-29"
layout, mapping = plan_column_years(p["months"], fy_end_month, date.fromisoformat(p["as_of"]))
print(layout, {y: [m for m, yy in mapping if yy == y] for y in sorted({yy for _, yy in mapping})})
```
