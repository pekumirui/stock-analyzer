import csv
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.xbrl.three_statements as ts

SECTIONS = ("op", "inv", "fin")
SECTION_KEY_RE = {
    "op": re.compile(r"^NetCashProvidedByUsedInOperatingActivities"),
    "inv": re.compile(r"^NetCashProvidedByUsedInInvest(ment|ing)Activities"),
    "fin": re.compile(r"^NetCashProvidedByUsedInFinancingActivities"),
}
SECTION_LABEL_RE = {
    "op": re.compile(r"^[IVXⅠ-Ⅻ]*営業活動による(?:正味)?キャッシュ・?フロー(?:合計)?$"),
    "inv": re.compile(r"^[IVXⅠ-Ⅻ]*投資活動による(?:正味)?キャッシュ・?フロー(?:合計)?$"),
    "fin": re.compile(r"^[IVXⅠ-Ⅻ]*財務活動による(?:正味)?キャッシュ・?フロー(?:合計)?$"),
}
CHANGE_KEY_RE = re.compile(r"^NetIncreaseDecreaseInCashAndCashEquivalents(IFRS)?$")
BEFORE_FX_KEY_RE = re.compile(r"BeforeEffectOfExchangeRateChange")
CHANGE_LABEL_RE = re.compile(r"^[IVXⅠ-Ⅻ]*現金(?:及び|および)現金同等物(?:の|に係る)?(?:純)?増(?:加|減)(?:額|\(△は減少\)|又は減少)")
BEFORE_FX_LABEL_RE = re.compile(r"換算差額(?:等)?(?:の影響)?(?:考慮|調整)?前|為替変動の影響(?:考慮)?前")
FX_KEY_RE = re.compile(r"^EffectOfExchangeRateChange")
FX_LABEL_RE = re.compile(r"換算差額|為替変動の影響|為替相場の変動")
CLOSING_KEY_RE = re.compile(r"^CashAndCashEquivalents(IFRS)?$")
SUBTOTAL_KEY_RE = re.compile(r"^Subtotal|^CashGeneratedFromOperations|CashGeneratedFromUsedInOperations")
SUBTOTAL_LABEL_RE = re.compile(r"^小計$|^営業活動(?:による|から生じた|から得た)現金(?:生成額)?$|^営業から(?:得た|生じた)現金$")
PRETAX_KEYS = ("IncomeBeforeIncomeTaxes", "ProfitLossBeforeTaxIFRS", "IncomeLossBeforeIncomeTaxes",
               "ProfitLossBeforeTaxFromContinuingOperationsIFRS")
NET_KEYS = ("ProfitLoss", "ProfitLossIFRS", "NetIncomeLoss")
PRETAX_LABEL_RE = re.compile(r"税引前|税金等調整前")
NET_LABEL_RE = re.compile(r"^(?:四半期|中間)?当?(?:期)?(?:純)?(?:利益|損失)|^(?:四半期|中間)?(?:純)?(?:利益|損失)")
NON_CF_KEY_RE = re.compile(r"(NOI|NOE|EI|EL|SGA|OCI|SS|COS|CGS)(IFRS)?$")
NON_CF_KEYS = frozenset({"NonOperatingIncome", "NonOperatingExpenses", "ExtraordinaryIncome", "ExtraordinaryLoss",
                         "OrdinaryIncome", "OperatingIncome", "GrossProfit", "NetSales", "CostOfSales",
                         "OperatingProfitLossIFRS", "ProfitLossBeforeTaxIFRS", "RevenueIFRS", "GrossProfitIFRS",
                         "PurchaseOfTreasuryStock", "DisposalOfTreasuryStock", "DividendsFromSurplus"})
IFRS_SUBTOTAL_KEY_RE = re.compile(r"^(MovementsInWorkingCapital|AdjustmentsToReconcileProfit|TotalAdjustments)")
MEMO_KEY_RE = re.compile(r"FreeCashFlow|OperatingActivitiesAnd")
MEMO_LABEL_RE = re.compile(r"フリー・?キャッシュ・?フロー|^営業活動及び")
CHECKS = ("op_sub", "op_after_sub", "op", "inv", "fin", "change", "closing", "start_pl", "core")


def norm(label):
    return (label or "").replace("　", "").replace(" ", "").replace("（", "(").replace("）", ")").strip()


def key_of(r):
    k = r["key"].removeprefix("ext:")
    return re.sub(r"#\d+$", "", k)


def unit_of(vals):
    unit = 10**6
    while unit > 1 and any(round(v) % unit for v in vals if v):
        unit //= 10
    return unit


def close(a, b, unit, n):
    return abs(a - b) <= unit * max(n, 1)


def section_total(rows, sec):
    for i, r in enumerate(rows):
        if SECTION_KEY_RE[sec].match(key_of(r)):
            return i
    for i, r in enumerate(rows):
        if SECTION_LABEL_RE[sec].match(norm(r["label"])):
            return i
    return None


def kind_after(r):
    k, lab = key_of(r), norm(r["label"])
    if CLOSING_KEY_RE.match(k) or ts.CLOSING_CASH_RE.match(lab) or lab in ts.CLOSING_CASH_NAMES:
        return "closing"
    if BEFORE_FX_KEY_RE.search(k) or (CHANGE_LABEL_RE.match(lab) and BEFORE_FX_LABEL_RE.search(lab)):
        return "before_fx"
    if FX_KEY_RE.match(k) or (FX_LABEL_RE.search(lab) and not CHANGE_LABEL_RE.match(lab)):
        return "fx"
    if CHANGE_KEY_RE.match(k) or CHANGE_LABEL_RE.match(lab):
        return "change"
    return "other"


def value(rows, i, c):
    if i is None:
        return None
    cell = rows[i]["cells"][c]
    return None if cell is None else cell["value"]


def counted(rows, idxs, c):
    out = []
    stack = []
    for i in idxs:
        d = rows[i]["indent"] or 0
        while stack and stack[-1][0] >= d:
            stack.pop()
        has = value(rows, i, c) is not None
        if has and not any(s[1] for s in stack):
            out.append(i)
        stack.append((d, has))
    return out


def sum_of(rows, idxs, c):
    use = counted(rows, idxs, c)
    return sum(value(rows, i, c) for i in use), len(use)


def start_kind(r):
    k, lab = key_of(r), norm(r["label"])
    if k in PRETAX_KEYS or "BeforeIncomeTax" in k or "BeforeTax" in k or PRETAX_LABEL_RE.search(lab):
        return "pretax"
    if k in NET_KEYS or k.startswith("ProfitLoss") or NET_LABEL_RE.match(lab):
        return "net"
    return None


def pl_value(pl_rows, pc, kind, cf_row):
    ck = key_of(cf_row)
    for r in pl_rows:
        if key_of(r) == ck and r["cells"][pc] is not None:
            return r["cells"][pc]["value"], key_of(r)
    keys = PRETAX_KEYS if kind == "pretax" else NET_KEYS
    for k in keys:
        for r in pl_rows:
            if key_of(r) == k and r["cells"][pc] is not None:
                return r["cells"][pc]["value"], k
    label_re = PRETAX_LABEL_RE if kind == "pretax" else NET_LABEL_RE
    for r in pl_rows:
        lab = norm(r["label"])
        if r["cells"][pc] is not None and (label_re.search(lab) if kind == "pretax" else label_re.match(lab)) \
                and "帰属" not in lab and "包括" not in lab and "1株" not in lab and "継続" not in lab:
            return r["cells"][pc]["value"], key_of(r)
    return None, None


def fiscal_start(period_end, fy_ends):
    candidates = [e for e in fy_ends if e < period_end and (period_end - e).days <= 380]
    return max(candidates) if candidates else None


def layout(rows):
    tot = {s: section_total(rows, s) for s in SECTIONS}
    order = [tot[s] for s in SECTIONS if tot[s] is not None]
    if order != sorted(order):
        return tot, None
    bounds = {}
    prev = -1
    for s in SECTIONS:
        if tot[s] is None:
            continue
        bounds[s] = list(range(prev + 1, tot[s]))
        prev = tot[s]
    after = list(range(prev + 1, len(rows)))
    return tot, (bounds, after)


def is_subtotal(r):
    k, lab = key_of(r), norm(r["label"])
    if is_non_cf(r):
        return False
    return bool("考慮前" in lab or k.startswith("CashFlowsFromOperatingActivitiesBefore") or SUBTOTAL_KEY_RE.search(k) or k.startswith("TotalOf") or IFRS_SUBTOTAL_KEY_RE.match(k)
                or SUBTOTAL_LABEL_RE.match(lab)
                or (lab.endswith("計") and not lab.endswith("合計") and "キャッシュ" not in lab))


def is_non_cf(r):
    k = key_of(r)
    return k in NON_CF_KEYS or bool(NON_CF_KEY_RE.search(k))


def why(rows, use, diff, tol, c, source, mix):
    non_cf = [i for i in use[1:] if is_non_cf(rows[i])]
    if non_cf and abs(sum(value(rows, i, c) for i in non_cf) - diff) <= tol:
        return "non_cf_element:" + "+".join(key_of(rows[i]) for i in non_cf)[:120]
    filled = [i for i in use if (rows[i]["cells"][c].get("source") or source) != source]
    if filled and abs(sum(value(rows, i, c) for i in filled) - diff) <= tol:
        return "filled_from_other_source:" + "+".join(key_of(rows[i]) for i in filled)[:120]
    one = next((i for i in use if abs(value(rows, i, c) - diff) <= tol), None)
    if one is not None:
        tag = "non_cf_element" if is_non_cf(rows[one]) else "one_extra_row"
        return f"{tag}:{key_of(rows[one])}"[:140]
    kids = [i for i in range(len(rows)) if (rows[i]["indent"] or 0) > 0 and value(rows, i, c) is not None
            and i not in use and min(use, default=-1) < i < max(use, default=-1)]
    if kids and (abs(sum(value(rows, i, c) for i in kids) + diff) <= tol
                 or any(abs(value(rows, i, c) + diff) <= tol for i in kids)):
        return "indent_not_child:" + "+".join(key_of(rows[i]) for i in kids)[:120]
    labels = Counter(norm(rows[i]["label"]) for i in use)
    dup = [i for i in use if labels[norm(rows[i]["label"])] > 1]
    if dup and abs(sum(value(rows, i, c) for i in dup) / 2 - diff) <= tol:
        return "dup_label:" + "+".join(key_of(rows[i]) for i in dup)[:120]
    if non_cf:
        return "non_cf_partial:" + "+".join(key_of(rows[i]) for i in non_cf)[:120]
    if source == "tdnet_pdf":
        return "pdf_missing_or_extra"
    if mix:
        return "mixed_unexplained"
    return "unexplained"


def is_memo(r):
    k, lab = key_of(r), norm(r["label"])
    return bool(MEMO_KEY_RE.search(k) or MEMO_LABEL_RE.search(lab))


def continuing_parts(rows, idxs, c):
    present = [i for i in idxs if value(rows, i, c) is not None]
    parts = []
    for i in present:
        lab = norm(rows[i]["label"])
        if "継続事業" in lab or "ContinuingOperations" in key_of(rows[i]) or "DiscontinuedOperations" in key_of(rows[i]):
            parts.append(i)
            continue
        if parts and start_kind(rows[i]) in ("net", "pretax"):
            total = sum(value(rows, j, c) for j in parts)
            return (parts, i) if abs(total - value(rows, i, c)) < 1 else ([], None)
        break
    return [], None


def mixed(rows, c, source):
    got = Counter(rows[i]["cells"][c].get("source") for i in range(len(rows)) if rows[i]["cells"][c] is not None)
    other = {k: v for k, v in got.items() if k != source}
    return ",".join(f"{k}:{v}" for k, v in sorted(other.items(), key=lambda kv: str(kv[0])))


def check_table(cf, pl_by, code, basis, out, stats, detail, shown, closings):
    rows = cf["rows"]
    tot, lay = layout(rows)
    fy_ends = sorted({e for b, e in closings if b == basis})
    results = []
    gaps = defaultdict(list)
    for c, p in enumerate(cf["periods"]):
        vals = [r["cells"][c]["value"] for r in rows if r["cells"][c] is not None and r["unit"] == "money"]
        if not vals:
            continue
        unit = unit_of(vals)
        res = {}
        pe = date.fromisoformat(p["period_end"])
        mix = mixed(rows, c, p["source"])
        base = [code, basis, p["period_end"], p["period_type"], p["source"], p["accounting_standard"], p["doc_id"], unit, mix]
        results.append((c, p, base, res))
        if lay is None:
            res["core"] = ("NG", "section_order", "", "")
            continue
        bounds, after = lay
        absent = [s for s in SECTIONS if value(rows, tot[s], c) is None
                  and not any(value(rows, i, c) is not None for i in bounds.get(s, []))]
        missing = [s for s in SECTIONS if value(rows, tot[s], c) is None and s not in absent]
        if absent:
            res["absent"] = ("absent_section", ",".join(absent), "", "")
        kinds = {i: kind_after(rows[i]) for i in after}
        change_i = next((i for i in after if kinds[i] == "change" and value(rows, i, c) is not None), None)
        if change_i is None:
            change_i = next((i for i in after if kinds[i] == "before_fx" and value(rows, i, c) is not None), None)
        closing_is = [i for i in after if kinds[i] == "closing" and value(rows, i, c) is not None]
        if missing or change_i is None or not closing_is:
            res["core"] = ("missing", ",".join(missing + (["change"] if change_i is None else [])
                                               + (["closing"] if not closing_is else [])), "", "")
        parts, start_i = continuing_parts(rows, bounds.get("op", []), c)
        for s in SECTIONS:
            total = value(rows, tot[s], c)
            if total is None:
                continue
            idxs = [i for i in bounds[s] if i not in parts and not is_memo(rows[i])]
            subs = [i for i in idxs if is_subtotal(rows[i]) and value(rows, i, c) is not None]
            items = [i for i in idxs if i not in subs]
            lead = [si for si in subs if not any(i < si for i in items)]
            subs = [si for si in subs if si not in lead]
            items = sorted(items + lead)
            for si in subs:
                pre = counted(rows, [i for i in items if i < si], c)
                a = sum(value(rows, i, c) for i in pre)
                sv = value(rows, si, c)
                if close(a, sv, unit, len(pre)):
                    res[f"{s}_sub"] = ("OK", a, sv, len(pre))
                else:
                    res[f"{s}_sub"] = ("NG", a, sv, why(rows, pre, a - sv, unit * max(len(pre), 1), c, p["source"], mix))
                    break
            use = counted(rows, items, c)
            a = sum(value(rows, i, c) for i in use)
            if not use:
                res[s] = ("no_items", a, total, 0)
            elif close(a, total, unit, len(use)):
                res[s] = ("OK", a, total, len(use))
            else:
                res[s] = ("NG", a, total, why(rows, use, a - total, unit * len(use), c, p["source"], mix))
        if change_i is not None and not missing:
            change = value(rows, change_i, c)
            three = sum(value(rows, tot[s], c) or 0.0 for s in SECTIONS)
            between = [i for i in after if i < change_i and value(rows, i, c) is not None and not is_memo(rows[i])]
            bf = [i for i in between if kinds[i] == "before_fx"]
            extra = [i for i in between if kinds[i] != "before_fx"]
            a = three + sum(value(rows, i, c) for i in extra)
            ok = close(a, change, unit, 3 + len(extra))
            note = "+".join(kinds[i] for i in extra)
            if bf:
                b0 = value(rows, bf[0], c)
                ok = ok and close(three + sum(value(rows, i, c) for i in extra if i < bf[0]), b0, unit, 3 + len(extra))
                note += "|before_fx"
            res["change"] = ("OK" if ok else "NG", a, change, note)
        if change_i is not None and closing_is:
            start = fiscal_start(pe, fy_ends)
            openings = closings.get((basis, start), []) if start else []
            if not openings:
                res["closing"] = ("no_prior_fy", "", value(rows, closing_is[0], c), str(start or ""))
            else:
                best = None
                for ci in closing_is:
                    adj = [i for i in after if change_i < i < ci and value(rows, i, c) is not None
                           and kinds[i] != "closing" and not is_memo(rows[i])]
                    implied = value(rows, ci, c) - value(rows, change_i, c) - sum(value(rows, i, c) for i in adj)
                    for o in openings:
                        if close(implied, o, unit, 2 + len(adj)):
                            best = ("OK", o, implied, "")
                            break
                        if best is None:
                            best = ("NG", o, implied, "+".join(norm(rows[i]["label"])[:20] for i in adj))
                    if best[0] == "OK":
                        break
                res["closing"] = best
                gaps[start].append((len(results) - 1, best[2] - best[1] if best[0] == "NG" else 0.0, unit))
        if bounds.get("op"):
            first = start_i if start_i is not None else next(
                (i for i in bounds["op"] if value(rows, i, c) is not None), None)
            kind = start_kind(rows[first]) if first is not None else None
            if kind is None:
                res["start_pl"] = ("direct_or_unknown", "", "", norm(rows[first]["label"])[:30] if first is not None else "")
            else:
                pl = pl_by.get((p["period_end"], p["period_type"]))
                if pl is None:
                    res["start_pl"] = ("no_pl", value(rows, first, c), "", kind)
                else:
                    pl_rows, pc, pl_period = pl
                    pv, pk = pl_value(pl_rows, pc, kind, rows[first])
                    cv = value(rows, first, c)
                    if pv is None:
                        res["start_pl"] = ("no_pl_row", cv, "", kind)
                    else:
                        tag = f"{kind}:{key_of(rows[first])}={pk}"
                        if pl_period["doc_id"] != p["doc_id"]:
                            tag += f"|pl_doc={pl_period['doc_id']}"
                        res["start_pl"] = ("OK" if close(cv, pv, unit, 1) else "NG", cv, pv, tag)
    for start, items in gaps.items():
        ng = [g for g in items if g[1] != 0.0]
        if ng and len(ng) == len(items) and all(abs(g[1] - ng[0][1]) <= g[2] * 3 for g in ng):
            for g in ng:
                r = results[g[0]][3]
                r["closing"] = ("opening_gap_same", r["closing"][1], r["closing"][2], f"gap={ng[0][1]:.0f} n={len(ng)}")
    for c, p, base, res in results:
        emit(out, stats, base, res)
        if any(v[0] == "NG" for v in res.values()) and shown[code] < 2:
            shown[code] += 1
            detail.append(f"## {code} {basis} {p['period_end']} {p['period_type']} {p['source']} {p['doc_id']} mix={base[-1]} "
                          + " ".join(f"{k}={v[0]}:{v[1]}/{v[2]}" for k, v in res.items() if v[0] != "OK"))
            for i, r in enumerate(rows):
                v = value(rows, i, c)
                if v is not None:
                    detail.append(f"  {i:3} {r['indent']} {v:>20,.0f} {r['label']} [{r['key']}] {r['cells'][c].get('source')}")


def emit(out, stats, base, res):
    for k, v in res.items():
        stats[(base[1], k, v[0])] += 1
        if v[0] not in ("OK",):
            out.writerow([*base, k, *v])


def closings_of(cf, basis):
    rows = cf["rows"]
    _, lay = layout(rows)
    got = defaultdict(list)
    if lay is None:
        return got
    _, after = lay
    for c, p in enumerate(cf["periods"]):
        if p["period_type"] != "FY":
            continue
        for i in after:
            if kind_after(rows[i]) == "closing" and value(rows, i, c) is not None:
                got[(basis, date.fromisoformat(p["period_end"]))].append(value(rows, i, c))
    return got


def main():
    argv = sys.argv[1:]
    db_path, out_path = argv[0], argv[1]
    only = argv[2:] or None
    if only and only[0].startswith("@"):
        with open(only[0][1:]) as f:
            only = f.read().split()
    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    cache = {}
    orig = ts.load_both

    def cached(db, code):
        if code not in cache:
            cache.clear()
            cache[code] = orig(db, code)
        return cache[code]

    ts.load_both = cached
    stats = Counter()
    ng_codes = defaultdict(set)
    detail = []
    shown = Counter()
    t0 = time.time()
    with Session(engine) as db, open(out_path, "w", newline="") as f:
        out = csv.writer(f)
        out.writerow(["code", "basis", "period_end", "period_type", "source", "standard", "doc_id", "unit", "mixed",
                      "check", "result", "computed", "reported", "note"])
        codes = only or [r[0] for r in db.execute(text(
            "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status='parsed' ORDER BY code"))]
        for n, code in enumerate(codes, 1):
            for consolidated in (True, False):
                basis = "連結" if consolidated else "個別"
                try:
                    cf = ts.financial_table(db, code, statement="CF", periods=10**6, consolidated=consolidated)
                    pl = ts.financial_table(db, code, statement="PL", periods=10**6, consolidated=consolidated)
                except Exception as exc:
                    stats[(basis, "error", type(exc).__name__)] += 1
                    continue
                if cf["empty"]:
                    continue
                pl_by = {}
                for t in [pl, *pl["earlier"]]:
                    for pc, pp in enumerate(t["periods"]):
                        pl_by[(pp["period_end"], pp["period_type"])] = (t["rows"], pc, pp)
                tables = [cf, *cf["earlier"]]
                closings = {}
                for t in tables:
                    for k, v in closings_of(t, basis).items():
                        closings.setdefault(k, []).extend(v)
                before = sum(v for k, v in stats.items() if k[0] == basis and k[2] == "NG")
                for t in tables:
                    check_table(t, pl_by, code, basis, out, stats, detail, shown, closings)
                if sum(v for k, v in stats.items() if k[0] == basis and k[2] == "NG") > before:
                    ng_codes[basis].add(code)
            if n % 200 == 0:
                print(f"{n}/{len(codes)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    print(f"codes={len(codes)} elapsed={time.time() - t0:.0f}s")
    for k in sorted(stats, key=lambda k: tuple(map(str, k))):
        print(*k, stats[k], sep="\t")
    print("codes_with_NG", {k: len(v) for k, v in ng_codes.items()})
    print("\n".join(detail))


if __name__ == "__main__":
    main()
