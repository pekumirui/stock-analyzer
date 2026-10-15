"""月次の保存名を今の正規化と銘柄エイリアスへ付け替える。起動時には走らない（保存名は凍結。#545）。

  plan   書かずに、付け替える行・消す行・値の衝突を出す（既定）
  apply  付け替える。終わったら残りのずれを数える

正規化の規則や config/monthly/issuer_aliases.json を変えたあとに、plan で中身を見てから apply する。
本番は Actions の Monthly names（workflow_dispatch）から動かす。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import (  # noqa: E402
    apply_issuer_element_aliases, merge_yoy_duplicate_elements, monthly_name_drift,
)


def _print_drift(title: str) -> int:
    drift = monthly_name_drift()
    print(f"{title}: {len(drift)} 行")
    for code, year, element, want in drift:
        print(f"  {code} {year} {element!r} → {want!r}")
    return len(drift)


def run(mode: str) -> int:
    dry_run = mode != "apply"
    if _print_drift("ずれている保存名") == 0:
        return 0
    label = "plan（書かない）" if dry_run else "apply"
    print(f"\n== {label}: 正規化 ==")
    merged = merge_yoy_duplicate_elements(dry_run=dry_run)
    print(f"\n== {label}: 銘柄エイリアス ==")
    aliased = apply_issuer_element_aliases(dry_run=dry_run)
    conflicts = sum(len(r.get("conflicts") or []) for r in merged)
    print(f"\n正規化 {len(merged)} 件 / エイリアス {len(aliased)} 件 / 値の衝突 {conflicts} セル")
    if dry_run:
        # plan は 2 段を別々に見るので、正規化のあとに初めて当たるエイリアスは apply 後の plan で出る
        return 0
    return 1 if _print_drift("\n残りのずれ") else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", nargs="?", choices=("plan", "apply"), default="plan")
    args = parser.parse_args()
    raise SystemExit(run(args.mode))


if __name__ == "__main__":
    main()
