"""タクソノミ同梱用に、EDINET の XBRL ZIP の見本を手元に落とす（EDINET_API_KEY）。

  # 過去 N 年の決算期ごとの見本（有報・四半期報・半期報。日本基準と IFRS）
  python tools/edinet/sample_zips.py --sample-years 10 --out /tmp/edinet-zips
  # TaxonomyMissingError で failed になった EDINET の filing の ZIP
  python tools/edinet/sample_zips.py --failed --out /tmp/edinet-zips

そのあと `python tools/xbrl/taxonomy_bundle.py fetch --zips /tmp/edinet-zips`。
ZIP は解析しない（落とすだけ）。docs/jobs/edinet.md §4。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal
from app.models import XbrlFiling

# 提出が集中する日（3 月決算の有報 / 1Q / 2Q / 3Q）。土日なら前後にずらして探す
SAMPLE_DAYS = ("06-26", "08-13", "11-13", "02-13")
SAMPLE_TYPES = ("120", "140", "160")
# 長く IFRS の会社（武田、ソフトバンクG、日立、ソニー G、トヨタ）。jpigp の版を拾う
IFRS_CODES = {"45020", "99840", "65010", "67580", "72030"}
DAY_SHIFTS = (0, 1, -1, 2, -2, 3, -3)


def _save(client, doc_id: str, out: Path) -> bool:
    from app.edinet_wrapper import DocumentFileType

    dest = out / f"{doc_id}.zip"
    if dest.is_file():
        return False
    got = client.download(doc_id, DocumentFileType.MAIN)
    dest.write_bytes(got.content)
    print(f"saved {dest.name}", flush=True)
    return True


def sample(client, years: int, out: Path) -> int:
    from datetime import timedelta

    today = date.today()
    n = 0
    for year in range(today.year - years, today.year + 1):
        for md in SAMPLE_DAYS:
            base = date.fromisoformat(f"{year}-{md}")
            if base > today or (today - base).days > 10 * 365:
                continue
            for shift in DAY_SHIFTS:
                day = base + timedelta(days=shift)
                if day > today:
                    continue
                rows = [e for e in client.list_documents(day).results
                        if e.has_xbrl and e.sec_code and e.doc_type_code in SAMPLE_TYPES]
                if not rows:
                    continue
                picks = {}
                for e in rows:
                    picks.setdefault(e.doc_type_code, e)
                for e in rows:
                    if e.sec_code in IFRS_CODES:
                        picks.setdefault(f"ifrs-{e.doc_type_code}", e)
                for e in picks.values():
                    n += _save(client, e.doc_id, out)
                break
    return n


def failed(client, out: Path) -> int:
    db = SessionLocal()
    try:
        rows = db.query(XbrlFiling).filter(
            XbrlFiling.source == "edinet", XbrlFiling.parse_status == "failed",
            XbrlFiling.parse_error.like("%TaxonomyMissingError%"),
        ).all()
        return sum(_save(client, f.doc_id, out) for f in rows)
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sample-years", type=int)
    g.add_argument("--failed", action="store_true")
    args = ap.parse_args()

    from app.edinet_wrapper import EdinetClient

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with EdinetClient() as client:
        n = failed(client, out) if args.failed else sample(client, args.sample_years, out)
    print(f"sample zips saved={n} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
