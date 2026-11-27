import json
import sys
import time
from collections import defaultdict

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

import app.xbrl.three_statements as ts


def operating(keys):
    end = next((i for i, k in enumerate(keys) if ts.CF_SECTION_RE.match(k.removeprefix("ext:"))), len(keys))
    sub = next((i for i, k in enumerate(keys[:end]) if ts.STANDARD_SUBTOTAL_RE.match(k)), None)
    return sub, end


def unit_of(values):
    unit = 10**6
    while unit > 1 and any(round(v) % unit for v in values if v):
        unit //= 10
    return unit


def main():
    db_path, out_path = sys.argv[1], sys.argv[2]
    only = sys.argv[3:] or None
    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    t0 = time.time()
    counts = defaultdict(int)
    with Session(engine) as db, open(out_path, "w") as out:
        codes = only or [r[0] for r in db.execute(text(
            "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status='parsed' ORDER BY code"))]
        for n, code in enumerate(codes, 1):
            both = ts.load_both(db, code)
            ts_load, ts.load_both = ts.load_both, (lambda _db, _code, b=both: b)
            try:
                indented = {}
                for basis in (True, False):
                    table = ts.financial_table(db, code, statement="CF", periods=10**6, consolidated=basis)
                    indented[basis] = {r["key"] for t in [table, *table["earlier"]] for r in t["rows"] if r["indent"]}
            except Exception:
                indented = {True: set(), False: set()}
            finally:
                ts.load_both = ts_load
            for consolidated, filings in both.items():
                sides = defaultdict(lambda: {"pre": [], "post": []})
                for f in filings:
                    if f.is_pdf or "CF" not in f.by_statement:
                        continue
                    facts = f.by_statement["CF"]
                    values = {x.key: x.value for x in facts}
                    keys = [x.key for x in facts]
                    sub, end = operating(keys)
                    if sub is not None:
                        for i, k in enumerate(keys[:end]):
                            if i != sub:
                                sides[k]["post" if i > sub else "pre"].append(
                                    [f.doc_id, f.source, str(f.period_end), values[k]])
                    kids = defaultdict(list)
                    for k, part in f.parts.get("CF", {}).items():
                        if part.under and k in values and k in indented[consolidated]:
                            kids[part.under].append(k)
                    unit = unit_of(list(values.values()))
                    for parent, children in kids.items():
                        if parent not in values:
                            continue
                        total = sum(values[k] for k in children)
                        if abs(total - values[parent]) > unit * len(children):
                            counts["bad_children"] += 1
                            out.write(json.dumps({"kind": "child", "code": code, "consolidated": consolidated,
                                                  "doc_id": f.doc_id, "source": f.source, "period_end": str(f.period_end),
                                                  "parent": parent, "parent_value": values[parent], "keys": children,
                                                  "sum": total}, ensure_ascii=False) + "\n")
                for key, s in sides.items():
                    if s["pre"] and s["post"]:
                        counts["both_sides"] += 1
                        out.write(json.dumps({"kind": "sides", "code": code, "consolidated": consolidated, "key": key, **s},
                                             ensure_ascii=False) + "\n")
            if n % 200 == 0:
                print(f"{n}/{len(codes)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    print(f"codes={len(codes)} {dict(counts)} elapsed={time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
