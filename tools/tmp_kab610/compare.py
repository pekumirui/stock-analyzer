import importlib.util
import json
import sys
import os
import time
from pathlib import Path
from collections import Counter

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

def load(name, path, config=None):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    if config:
        mod.XBRL_ROW_PLACE_FIXES_PATH = Path(config) / "row_place_fixes.json"
        mod.XBRL_ROW_MERGE_EXCLUSIONS_PATH = Path(config) / "row_merge_exclusions.json"
    return mod


def cells(table):
    out = {}
    for t in [table, *table["earlier"]]:
        for c, p in enumerate(t["periods"]):
            for r in t["rows"]:
                cell = r["cells"][c]
                if cell is not None:
                    out[(p["period_end"], p["doc_id"], r["key"])] = (cell["value"], r["label"])
    return out


def main():
    db_path, old_path, new_path, out_path = sys.argv[1:5]
    only = sys.argv[5:] or None
    old = load("three_statements_old", old_path)
    new = load("three_statements_new", new_path, os.environ.get("NEW_CONFIG"))
    new_load, old_load = new.load_both, old.load_both
    engine = create_engine(f"sqlite:///file:{db_path}?mode=ro&uri=true")
    stats = Counter()
    t0 = time.time()
    with Session(engine) as db, open(out_path, "w") as out:
        codes = only or [r[0] for r in db.execute(text(
            "SELECT DISTINCT code FROM xbrl_filings WHERE parse_status='parsed' ORDER BY code"))]
        for n, code in enumerate(codes, 1):
            both_new = new_load(db, code)
            both_old = old_load(db, code)
            new.load_both = lambda _db, _code, b=both_new: b
            old.load_both = lambda _db, _code, b=both_old: b
            for statement in ("BS", "PL", "CF"):
                for consolidated in (True, False):
                    try:
                        a = cells(old.financial_table(db, code, statement=statement, periods=10**6, consolidated=consolidated))
                        b = cells(new.financial_table(db, code, statement=statement, periods=10**6, consolidated=consolidated))
                    except Exception as exc:
                        stats[("error", type(exc).__name__)] += 1
                        continue
                    if a == b:
                        stats[(statement, "same")] += 1
                        continue
                    stats[(statement, "diff")] += 1
                    for k in sorted(set(a) | set(b)):
                        if a.get(k) != b.get(k):
                            out.write(json.dumps([code, consolidated, statement, *k, a.get(k), b.get(k)], ensure_ascii=False) + "\n")
            new.load_both = new_load
            old.load_both = old_load
            if n % 200 == 0:
                print(f"{n}/{len(codes)} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    print(f"codes={len(codes)} elapsed={time.time() - t0:.0f}s")
    for k in sorted(stats):
        print(*k, stats[k], sep="\t")


if __name__ == "__main__":
    main()
