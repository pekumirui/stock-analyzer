"""月次の行名スナップショット（tests/monthly/golden/labels.tsv）。正規化を変えて出力が変わった行を全部出す。

意図した変更なら `python tools/monthly/label_snapshot.py update` で書き直し、TSV の差分を PR に載せる。
入力の取り直しは `collect`（tools/monthly/label_snapshot.py の先頭）。
"""
from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.monthly import label_snapshot as snap


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_snapshot_has_no_diff() -> None:
    rows = snap.read_tsv(snap.GOLDEN)
    if len(rows) < 1000:
        _fail(f"labels.tsv looks truncated: {len(rows)} rows")
    changed = snap.check()
    if changed:
        _fail(snap.format_diff(changed)
              + "\n\n意図した変更なら: python tools/monthly/label_snapshot.py update して差分を PR に載せる")


def test_broad_rule_is_caught() -> None:
    """仕組みの確認: 店舗の行だけのつもりの規則を全行にかけると、店舗以外の行が差分に出る（KAB-10）。"""
    rows = snap.read_tsv(snap.GOLDEN)
    keys = {(r["code"], r["year"]) for r in rows if "新規" in r["source_label"]}
    subset = [r for r in rows if (r["code"], r["year"]) in keys]
    path = Path(tempfile.mkdtemp(prefix="label-snapshot-test-")) / "labels.tsv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=snap.COLS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(subset)
    before = snap.check(path)
    if before:
        _fail("subset must start clean:\n" + snap.format_diff(before))
    orig = snap.canonicalize_element_label
    snap.canonicalize_element_label = lambda s, u, code=None: orig(
        s.replace("新規", "出店"), u, code,
    )
    try:
        changed = snap.check(path)
    finally:
        snap.canonicalize_element_label = orig
    hit = {r["source_label"] for r, _cols in changed}
    if not any("受注" in s or "契約" in s for s in hit):
        _fail(f"broad 新規→出店 must show non-store rows: {sorted(hit)[:10]!r}")


def test_sales_kind_has_metric() -> None:
    """売上・客数・客単価の行は label に指標語がある（KAB-126）。

    指標語が辞書に無いと metric が空になり、metric_kind は行名の「売上」で sales になる
    （売上総利益・売上原価を売上と取り違える）。新しい指標語は monthly_vocab.json の metrics と
    metric_kind に足す。
    """
    bad = sorted({
        (r["code"], r["element"], r["metric_kind"])
        for r in snap.read_tsv(snap.GOLDEN)
        if r["metric_kind"] in ("sales", "customers", "spend") and not r["metric"]
    })
    if bad:
        _fail(f"metric が空の sales / customers / spend の行: {bad[:20]!r}")


def test_known_inputs_are_in_snapshot() -> None:
    known = {(r["code"], r["year"], r["source_label"]) for r in snap.read_tsv(snap.KNOWN)}
    got = {(r["code"], r["year"], r["source_label"])
           for r in snap.read_tsv(snap.GOLDEN) if r["via"] == "known"}
    if known != got:
        _fail(f"labels_known.tsv と labels.tsv の known 行が違う。collect か update をやり直す: {known ^ got!r}")


def main() -> None:
    test_snapshot_has_no_diff()
    test_sales_kind_has_metric()
    test_known_inputs_are_in_snapshot()
    test_broad_rule_is_caught()
    print("ok: monthly label snapshot")


if __name__ == "__main__":
    main()
