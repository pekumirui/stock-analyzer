from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.orm import Session

from app.xbrl import three_statements as ts

MIN_CODES = 5
MIN_SHARE = 0.95
STANDARDS = ("IFRS", "JPGAAP")
OUTSIDE = "-"
VERSION = 2
OTHER = "その他"
ABOVE_SUBTOTAL = ":小計まで"
SUBTOTAL_PREFIX = "Subtotal"
BEFORE_ACCUMULATED = ":累計額の前"
NET_TAIL = "(純額)"
ACCUMULATED = frozenset({"減価償却累計額", "減損損失累計額", "減価償却累計額及び減損損失累計額"})


@dataclass
class Seen:
    uses: dict[tuple[str, str, str], dict[tuple[str, str], set[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set)),
    )
    units: dict[str, set[bool]] = field(default_factory=lambda: defaultdict(set))
    codes: set[str] = field(default_factory=set)
    major: dict[str, str] = field(default_factory=dict)
    owners: dict[str, set[str]] = field(default_factory=dict)
    others: dict[tuple[str, str, str], dict[str, set[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set)),
    )
    accumulated: dict[tuple[str, str, str], dict[str, set[str]]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(set)),
    )
    money: dict[str, set[bool]] = field(default_factory=lambda: defaultdict(set))

    def borrowed(self, key: str, label: str) -> bool:
        name = plain(label)
        return name != self.major.get(key, name) and bool(self.owners.get(name, set()) - {key})


def plain(label: str) -> str:
    return ts._canon_name(ts._clean_label(label or ""))


def label_key(raw: str, statement: str) -> str:
    return ts._canon_name(ts._clean_label(ts.strip_heads(ts.SUFFIX_RE.sub("", raw), statement)))


def listed(name: str) -> bool:
    return bool(name) and name not in ts.GENERIC_LABELS and not ts._never_merged(name) and not ts.OPENING_CASH_RE.match(name)


def taxonomy(facts: Iterable[ts.Fact]) -> str | None:
    kinds = Counter(f.key.endswith("IFRS") for f in facts if not f.key.startswith(("ext:", "pdf:")))
    if not kinds or kinds[True] == kinds[False]:
        return None
    return STANDARDS[0] if kinds[True] > kinds[False] else STANDARDS[1]


def other_homes(keys: list[str], names: list[str], statement: str) -> list[str | None]:
    section: str | None = None
    homes: list[str | None] = [None] * len(keys)
    for n in range(len(keys) - 1, -1, -1):
        if ts._is_marker(keys[n], statement):
            section = keys[n]
        elif (
            statement == "CF" and keys[n].removeprefix("ext:").startswith(SUBTOTAL_PREFIX) and section is not None
            and not section.endswith(ABOVE_SUBTOTAL)
        ):
            section += ABOVE_SUBTOTAL
        gross = section is not None and n + 1 < len(names) and names[n + 1] in ACCUMULATED
        homes[n] = section + BEFORE_ACCUMULATED if gross else section
    return homes


def own_element(fact: ts.Fact) -> bool:
    return not fact.key.startswith(("ext:", "pdf:"))


def observe_others(seen: Seen, code: str, standard: str, statement: str, facts: list[ts.Fact]) -> None:
    names = [label_key(f.label, statement) for f in facts]
    homes = other_homes([f.key for f in facts], names, statement)
    count = Counter(h for h, name in zip(homes, names) if name == OTHER)
    for f, home, name in zip(facts, homes, names):
        if name == OTHER and home is not None and count[home] == 1 and own_element(f):
            seen.others[(standard, statement, home)][f.key].add(code)
            seen.money[f.key].add(f.unit in ts.PER_SHARE_UNITS)


def observe_accumulated(seen: Seen, code: str, standard: str, facts: list[ts.Fact]) -> None:
    names = [label_key(f.label, "BS") for f in facts]
    for n, name in enumerate(names):
        if name not in ACCUMULATED:
            continue
        at = n - 1
        while at >= 0 and names[at] in ACCUMULATED:
            at -= 1
        after = n + 1
        while after < len(names) and names[after] in ACCUMULATED:
            after += 1
        if after >= len(names) or not names[after].endswith(NET_TAIL):
            continue
        if at >= 0 and own_element(facts[n]) and own_element(facts[at]):
            seen.accumulated[(standard, name, facts[at].key)][facts[n].key].add(code)
            seen.money[facts[n].key].add(facts[n].unit in ts.PER_SHARE_UNITS)


def observe(seen: Seen, code: str, filings: Iterable[ts.Filing]) -> None:
    for filing in filings:
        if filing.is_pdf:
            continue
        for statement, facts in filing.by_statement.items():
            standard = taxonomy(facts)
            if standard is None:
                continue
            observe_others(seen, code, standard, statement, facts)
            if statement == "BS":
                observe_accumulated(seen, code, standard, facts)
            homes = ts._homes([f.key for f in facts], statement)
            for f in facts:
                if f.key.startswith(("ext:", "pdf:")) or seen.borrowed(f.key, f.label):
                    continue
                name = label_key(f.label, statement)
                if not listed(name):
                    continue
                home = homes.get(f.key) or OUTSIDE
                seen.uses[(standard, statement, name)][(home, f.key)].add(code)
                seen.units[f.key].add(f.unit in ts.PER_SHARE_UNITS)
                seen.codes.add(code)


def _winner(counts: Mapping[str, int], min_codes: int, min_share: float) -> str | None:
    if not counts:
        return None
    best = max(sorted(counts), key=counts.__getitem__)
    if counts[best] < min_codes or counts[best] < sum(counts.values()) * min_share:
        return None
    return best


def entry(
    uses: Mapping[tuple[str, str], set[str]], min_codes: int, min_share: float, drop: str | None = None,
) -> dict[str, Any] | None:
    by_home: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    anywhere: dict[str, set[str]] = defaultdict(set)
    for (home, key), codes in uses.items():
        kept = codes - {drop} if drop is not None else codes
        if kept:
            by_home[home][key] |= kept
            anywhere[key] |= kept
    within = {
        home: key for home, keys in sorted(by_home.items())
        if (key := _winner({k: len(c) for k, c in keys.items()}, min_codes, min_share)) is not None
    }
    best = _winner({k: len(c) for k, c in anywhere.items()}, min_codes, min_share)
    if best is None and not within:
        return None
    out: dict[str, Any] = {"within": within}
    if best is not None:
        homes = {home: len(keys.get(best, ())) for home, keys in by_home.items()}
        out["any"] = best
        out["home"] = _winner({h: n for h, n in homes.items() if n}, 1, min_share)
    return out


def by_place(
    found: Mapping[tuple[str, str, str], Mapping[str, set[str]]], seen: Seen, min_codes: int, min_share: float,
    drop: str | None,
) -> dict[str, dict[str, dict[str, str]]]:
    out: dict[str, dict[str, dict[str, str]]] = defaultdict(lambda: defaultdict(dict))
    for (standard, group, place), uses in found.items():
        counts = {k: len(cs - {drop}) for k, cs in uses.items() if cs - {drop}}
        key = _winner(counts, min_codes, min_share)
        if key is not None and seen.money[key] == {False}:
            out[standard][group][place] = key
    return {s: {g: dict(sorted(v.items())) for g, v in sorted(by.items())} for s, by in sorted(out.items())}


def build(
    seen: Seen, min_codes: int = MIN_CODES, min_share: float = MIN_SHARE, drop: str | None = None,
) -> dict[str, Any]:
    labels: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(dict))
    used: set[str] = set()
    for (standard, statement, name), uses in seen.uses.items():
        found = entry(uses, min_codes, min_share, drop)
        if found is None:
            continue
        keys = set(found["within"].values()) | ({found["any"]} if "any" in found else set())
        if any(len(seen.units[k]) != 1 for k in keys):
            continue
        labels[standard][statement][name] = found
        used |= keys
    return {
        "version": VERSION,
        "min_codes": min_codes,
        "min_share": min_share,
        "codes": len(seen.codes - {drop}),
        "labels": {s: {st: dict(sorted(names.items())) for st, names in sorted(by.items())} for s, by in sorted(labels.items())},
        "per_share": sorted(k for k in used if True in seen.units[k]),
        "others": by_place(seen.others, seen, min_codes, min_share, drop),
        "accumulated": by_place(seen.accumulated, seen, min_codes, min_share, drop),
    }


def target_codes(db: Session) -> list[str]:
    rows = db.execute(text(
        "SELECT DISTINCT code FROM xbrl_filings WHERE source IN ('edinet', 'tdnet') AND parse_status = 'parsed' "
        "ORDER BY code"
    ))
    return [str(r[0]) for r in rows]


def element_labels(db: Session, seen: Seen) -> None:
    sql = text(
        "SELECT local_name, label_ja FROM xbrl_elements WHERE namespace_key IN :ns"
    ).bindparams(bindparam("ns", expanding=True))
    counts: dict[str, Counter] = defaultdict(Counter)
    for local, label in db.execute(sql, {"ns": sorted(ts.STANDARD_NS)}):
        counts[str(local)][plain(str(label or ""))] += 1
    seen.major = {local: min(c, key=lambda n: (-c[n], n)) for local, c in counts.items()}
    owners: dict[str, set[str]] = defaultdict(set)
    for local, name in seen.major.items():
        owners[name].add(local)
    seen.owners = dict(owners)


def collect(db: Session, codes: list[str], batch: int) -> tuple[Seen, Counter]:
    seen = Seen()
    element_labels(db, seen)
    counts: Counter = Counter()
    started = time.monotonic()
    for n, code in enumerate(codes, 1):
        try:
            for filings in ts.load_both(db, code).values():
                observe(seen, code, filings)
            counts["done"] += 1
        except Exception as exc:
            counts["failed"] += 1
            print(f"label_dictionary failed code={code} err={type(exc).__name__}: {exc}", flush=True)
        if n % batch == 0 or n == len(codes):
            print(
                f"label_dictionary batch done={n}/{len(codes)} failed={counts['failed']} "
                f"names={len(seen.uses)} sec={time.monotonic() - started:.0f}",
                flush=True,
            )
    return seen, counts


def write(book: Mapping[str, Any], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(book, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-codes", type=int, default=MIN_CODES)
    parser.add_argument("--min-share", type=float, default=MIN_SHARE)
    parser.add_argument("--code", action="append", default=[])
    parser.add_argument("--batch", type=int, default=100)
    args = parser.parse_args(argv)
    engine = create_engine(f"sqlite:///file:{args.db}?mode=ro&uri=true")
    with Session(engine) as db:
        codes = args.code or target_codes(db)
        seen, counts = collect(db, codes, max(args.batch, 1))
    book = build(seen, args.min_codes, args.min_share)
    write(book, Path(args.out))
    sizes = {s: sum(len(v) for v in by.values()) for s, by in book["labels"].items()}
    others = {s: sum(len(v) for v in by.values()) for s, by in book["others"].items()}
    accumulated = {s: sum(len(v) for v in by.values()) for s, by in book["accumulated"].items()}
    print(
        f"label_dictionary done codes={counts['done']} failed={counts['failed']} labels={sizes} others={others} "
        f"accumulated={accumulated} out={args.out}",
        flush=True,
    )
    return 0 if not counts["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
