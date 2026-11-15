#!/usr/bin/env python3
"""月次の保存名が不動点かを、語彙の語の組み合わせを全部流して確かめる。

保存名を作る道は 2 つ。app/api/monthly/_common.py の関数をそのまま呼ぶ:
  POST    _canonical_element(year=)
  ingest  _canonical_element(as_of=) のあと _year_alias
保存した名前を読み直す道も 2 つ:
  lookup  _lookup_element（PUT / DELETE）。unit は名前の「前年比」から決まる
  drift   monthly_name_drift（app/monthly/maintenance.py）と同じ式。保存した unit で作り直す
どの道で保存した名前も、どちらで読み直しても同じ名前に戻ることを確かめる。戻らないと PUT が別の行を
探し、付け替え（jobs/monthly/monthly_names.py apply）のあとの取込で同じ系列が別の行に割れる。

  python tools/monthly/label_fixed_point.py            # 全数。既知の反例と比べる
  python tools/monthly/label_fixed_point.py --update   # 今の反例を既知として書き直す
  python tools/monthly/label_fixed_point.py --show 国内売上 [--unit number] [--code 81630]

終了コード: 0=既知の反例と同じ / 1=新しい反例がある / 2=既知の反例が消えた（--update で書き直す）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from functools import lru_cache
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.monthly import _common as api  # noqa: E402
from app.monthly import ingest as m  # noqa: E402

KNOWN_PATH = Path(__file__).with_name("label_fixed_point_known.jsonl")
UNITS = ("number", "yoy_index", "yoy_pct")
JOINS = (" ", "")
YOY_WORD_CLASS = "number の行名に前年比の語"
PLAIN_WORDS = ("関東", "A事業", "ブランドX", "店", "数", "高", "率")
NOISE_WORDS = ("(前年比)", "（前年比）", "(1)", "①", "※", "2026年度", "直営・FC", "直営＋FC", "ＦＣ", "うち")
STATUS_AFFIXES = ("", "中", "予定", "準備中")
STORE_COUNTS = ("店舗数", "店数")


def common_words() -> list[str]:
    v = m._VOCAB
    words: set[str] = set(PLAIN_WORDS + NOISE_WORDS)
    words.update(v["yoy_words"], v["time_axis"], v["drop"])
    for src, dst in v["split"]:
        words.add(src)
        words.update(dst.split())
    for key in ("country", "channel", "scope", "store_flow", "breakdown", "metric_kind"):
        for src, dst in dict(v[key]).items():
            words.update((src, dst))
    for item in v["metrics"]:
        words.update((item["word"], item["metric"]))
    words.update(m._FLOW_WORDS, m._UNGLUE_KEEP, m._BREAKDOWN_MARKS, m._ACTIVE_STORE_BREAKDOWN_RANK)
    words.update(m._OPEN_SHARDS, m._CLOSE_SHARDS, m._DUMMY_STORE_TOKS, m._OPEN_QUALIFIERS)
    words.update(m._STORE_ADJ_MARKS, m._GROUP_MODIFIER_TOKS, m._AREA_CHILD_ALIAS)
    for pair in m._METRIC_SHARD_PAIRS:
        words.update(pair)
    for token, _suffix, _digits in m.MONTHLY_DISPLAY_UNITS:
        words.add(token)
    for area, nodes in m._AREA_TREE.items():
        words.add(area)
        for mid, twigs in nodes:
            words.add(mid)
            words.update(twigs)
    for stem, _optional in m._STATUS_STORE_SPACED:
        for affix, count in product(STATUS_AFFIXES, STORE_COUNTS):
            words.add(f"{stem}{affix}{count}")
    return sorted(w for w in words if w.strip())


def issuer_codes() -> list[str]:
    codes = set(m.ISSUER_METRICS) | set(m.ISSUER_ELEMENT_ALIASES) | set(m.MONTHLY_ROW_ORDER)
    codes |= set(m._ISSUER_STATUS_STORE_SEGMENT) | set(m._ISSUER_SEGMENT_AS_BREAKDOWN)
    return sorted(codes)


def issuer_names(code: str) -> tuple[list[str], list[str], list[str]]:
    """(解析を変える語, エイリアスの行名の語, 空白を含む行名そのもの)。"""
    parse: set[str] = set(m.ISSUER_METRICS.get(code, ())) | set(m.MONTHLY_ROW_ORDER.get(code, ()))
    for table in (m._ISSUER_STATUS_STORE_SEGMENT, m._ISSUER_SEGMENT_AS_BREAKDOWN):
        for src, dst in table.get(code, {}).items():
            parse.update((src, dst))
    names: set[str] = set()
    for rule in m.ISSUER_ELEMENT_ALIASES.get(code, ()):
        for src, dst in rule.rename.items():
            names.update((src, dst))
    parse_words = {tok for name in parse for tok in name.split()}
    alias_words = {tok for name in names for tok in name.split()} - parse_words
    whole = (names | parse) - parse_words - alias_words
    return sorted(parse_words), sorted(alias_words), sorted(whole)


def contexts(code: str | None) -> tuple[list[int], list[date]]:
    """エイリアスの効き方が変わる境目の暦年と as_of。エイリアスが無ければ 1 つずつ。"""
    rules = m.ISSUER_ELEMENT_ALIASES.get(code, ()) if code else ()
    if not rules:
        return [2026], [date(2026, 6, 1)]
    years = sorted({y for rule in rules for y in (rule.until.year, rule.until.year + 1)})
    as_ofs = sorted({d for rule in rules for d in (rule.until - timedelta(days=1), rule.until)})
    return years, as_ofs


# canonicalize_element_label は引数だけで決まるので、API 側の参照ごとメモ化して同じ計算を省く
_canon = lru_cache(maxsize=None)(m.canonicalize_element_label)
api.canonicalize_element_label = _canon


def drift_name(name: str, unit: str, code: str | None, year: int) -> str:
    canon = _canon(name, unit, code)
    if code:
        canon = m.apply_issuer_aliases(code, canon, year=year)
    return _canon(canon, unit, code)


def written_names(text: str, unit: str, code: str | None):
    """(道, 暦年, 保存名)。ingest は as_of の年までの列だけ（未来の列は取込が断る）。API が断る入力は出さない。"""
    years, as_ofs = contexts(code)
    try:
        for year in years:
            yield f"POST year={year}", year, api._canonical_element(text, unit, code, year=year)
        for as_of in as_ofs:
            base = api._canonical_element(text, unit, code, as_of=as_of)
            for year in years:
                if year <= as_of.year:
                    yield f"ingest as_of={as_of} year={year}", year, api._year_alias(code, base, unit, year)
    except api.ApiError:
        return


def reread(name: str, stored_unit: str, code: str | None, year: int):
    try:
        yield "lookup", api._lookup_element(name, code, year=year)
    except api.ApiError:
        yield "lookup", "（API が断る）"
    yield "drift", drift_name(name, stored_unit, code, year)


def inputs_common(words: list[str]):
    yield from words
    for a, b in product(words, words):
        for join in JOINS:
            yield f"{a}{join}{b}"


def inputs_issuer(code: str, words: list[str]):
    parse, alias, whole = issuer_names(code)
    own = parse + alias
    yield from own
    yield from whole
    for a, b in product(own, own):
        for join in JOINS:
            yield f"{a}{join}{b}"
    for a, b in product(parse, words):
        for join in JOINS:
            yield f"{a}{join}{b}"
            yield f"{b}{join}{a}"


def _key(hit: dict) -> str:
    return f"{hit['code'] or '-'}|{hit['stored']}|{hit['reread']}"


def run() -> tuple[dict[str, dict], dict | None, int, int]:
    """反例を（保存名, 読み直した名前）ごとに 1 件にまとめる。例はいちばん短い入力。

    number の行名に「前年比」の語が残る反例は原因が 1 つなので、件数と例 1 件にまとめる。
    """
    words = common_words()
    found: dict[str, dict] = {}
    yoy_word: dict | None = None
    yoy_word_count = 0
    checked = 0
    yoy_seen: set[tuple] = set()

    def check(text: str, unit: str, code: str | None) -> None:
        nonlocal checked, yoy_word, yoy_word_count
        checked += 1
        stored_unit = m.coerce_monthly_storage(unit, None)[0]
        for write, year, name in written_names(text, unit, code):
            for read, again in reread(name, stored_unit, code, year):
                if again == name:
                    continue
                hit = {"code": code, "stored": name, "reread": again, "read": read,
                       "input": text, "unit": unit, "write": write}
                if read == "lookup" and stored_unit == "number" and "前年比" in name:
                    if (code, name) not in yoy_seen:
                        yoy_seen.add((code, name))
                        yoy_word_count += 1
                    if yoy_word is None or len(text) < len(yoy_word["input"]):
                        yoy_word = hit
                    continue
                old = found.setdefault(_key(hit), hit)
                if len(text) < len(old["input"]):
                    old.update(input=text, unit=unit, write=write, read=read)

    for text in inputs_common(words):
        for unit in UNITS:
            check(text, unit, None)
    for code in issuer_codes():
        _canon.cache_clear()
        for text in inputs_issuer(code, words):
            for unit in UNITS:
                check(text, unit, code)
    return found, yoy_word, yoy_word_count, checked


def _line(hit: dict) -> str:
    where = f"code={hit['code']} " if hit["code"] else ""
    return (f"  {where}unit={hit['unit']} 入力 {hit['input']!r} → {hit['write']} で保存 {hit['stored']!r}"
            f" → {hit['read']} で読み直すと {hit['reread']!r}")


def show(text: str, unit: str, code: str | None) -> int:
    stored_unit = m.coerce_monthly_storage(unit, None)[0]
    rows = list(written_names(text, unit, code))
    if not rows:
        print("API が断る入力")
    for write, year, name in rows:
        parts = [f"{read} {again!r}" + ("" if again == name else " ✗")
                 for read, again in reread(name, stored_unit, code, year)]
        print(f"{write}: 保存 {name!r} → " + " / ".join(parts))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--show")
    ap.add_argument("--unit", default="number", choices=UNITS)
    ap.add_argument("--code")
    args = ap.parse_args()
    if args.show:
        return show(args.show, args.unit, args.code)
    start = time.time()
    found, yoy_word, yoy_word_count, checked = run()
    print(f"{checked} 入力を確かめた（{time.time() - start:.0f} 秒）")
    print(f"読み直すと変わる保存名: {len(found)} 件 + 「{YOY_WORD_CLASS}」 {yoy_word_count} 件")
    if yoy_word:
        print(f"「{YOY_WORD_CLASS}」の例:")
        print(_line(yoy_word))
    if args.update:
        lines = [json.dumps({YOY_WORD_CLASS: yoy_word is not None}, ensure_ascii=False)]
        lines += [json.dumps(found[k], ensure_ascii=False) for k in sorted(found)]
        KNOWN_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"{KNOWN_PATH.relative_to(ROOT)} を書き直した")
        return 0
    rows = [json.loads(line) for line in KNOWN_PATH.read_text(encoding="utf-8").splitlines() if line]
    known_yoy_word = rows[0][YOY_WORD_CLASS]
    known = {_key(h) for h in rows[1:]}
    new = sorted(set(found) - known)
    gone = sorted(known - set(found))
    print(f"既知 {len(known)} 件 / 新しい反例 {len(new)} 件 / 消えた既知 {len(gone)} 件")
    for key in new[:50]:
        print(_line(found[key]))
    if len(new) > 50:
        print(f"  … ほか {len(new) - 50} 件")
    for key in gone[:50]:
        print(f"  消えた: {key}")
    if yoy_word is not None and not known_yoy_word:
        print(f"新しい反例: 「{YOY_WORD_CLASS}」")
        return 1
    if new:
        return 1
    if yoy_word is None and known_yoy_word:
        print(f"消えた: 「{YOY_WORD_CLASS}」")
        return 2
    return 2 if gone else 0


if __name__ == "__main__":
    sys.exit(main())
