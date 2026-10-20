"""標準タクソノミか会社独自かの判定（KAB-81）。キーと URI は実データ（EDINET 有報・TDnet 短信）から。"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-xbrl-custom-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from app.db import SessionLocal, init_db, migrate_xbrl_elements_is_custom  # noqa: E402
from app.models import XbrlElement  # noqa: E402
from app.xbrl.parser import namespace_is_standard  # noqa: E402

STANDARD = [
    ("jppfs_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2025-11-01/jppfs_cor"),
    ("jpcrp_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jpcrp/2021-11-01/jpcrp_cor"),
    ("jpigp_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jpigp/2024-11-01/jpigp_cor"),
    ("jpdei_cor", "http://disclosure.edinet-fsa.go.jp/taxonomy/jpdei/2013-08-31/jpdei_cor"),
    ("tse-ed-t", "http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12"),
]
CUSTOM = [
    # EDINET 有報・半期報・四半期報の会社独自（キーにも URI にも jpcrp が入る）
    ("jpcrp030000-asr_E02144-000",
     "http://disclosure.edinet-fsa.go.jp/jpcrp030000/asr/001/E02144-000/2025-03-31/01/2025-06-18"),
    ("jpcrp040300-ssr_E01737-000",
     "http://disclosure.edinet-fsa.go.jp/jpcrp040300/ssr/001/E01737-000/2025-09-30/01/2025-11-11"),
    ("jpcrp040300-q3r_E04707-000",
     "http://disclosure.edinet-fsa.go.jp/jpcrp040300/q3r/001/E04707-000/2022-12-31/01/2023-02-14"),
    # TDnet 短信の会社独自（今までどおり独自）
    ("tse-scediffr-47550", "http://www.xbrl.tdnet.info/jp/tse/tdnet/sc/edif/fr/47550/2026-06-30/01/2026-08-10"),
    ("tse-qcediffr-87290", "http://www.xbrl.tdnet.info/jp/tse/tdnet/qc/edif/fr/87290/2026-06-30/01/2026-08-10"),
]


def test_namespace_is_standard() -> None:
    for key, uri in STANDARD:
        assert namespace_is_standard(key, uri), key
    for key, uri in CUSTOM:
        assert not namespace_is_standard(key, uri), key
    # URI が無いときはキーの完全一致
    assert namespace_is_standard("jpcrp_cor")
    assert not namespace_is_standard("jpcrp030000-asr_E02144-000")
    assert not namespace_is_standard("custom")
    assert not namespace_is_standard(None)


def test_migrate_relabels_existing_elements() -> None:
    init_db()
    db = SessionLocal()
    try:
        now = datetime(2026, 10, 20)  # noqa: DTZ001
        # KAB-73 以降の誤り（会社独自が is_custom=0）と、逆向きの誤り
        rows = [(k, u, False) for k, u in CUSTOM] + [(STANDARD[0][0], STANDARD[0][1], True)]
        rows += [(STANDARD[1][0], STANDARD[1][1], False)]
        for i, (key, uri, is_custom) in enumerate(rows):
            db.add(XbrlElement(namespace_uri=uri, namespace_key=key, local_name=f"E{i}",
                               is_custom=is_custom, first_seen_at=now))
        db.commit()
    finally:
        db.close()
    assert migrate_xbrl_elements_is_custom() == {"to_custom": len(CUSTOM), "to_standard": 1}
    assert migrate_xbrl_elements_is_custom() == {"to_custom": 0, "to_standard": 0}
    db = SessionLocal()
    try:
        got = {e.namespace_key: e.is_custom for e in db.query(XbrlElement)}
        assert all(got[k] for k, _ in CUSTOM)
        assert not got["jppfs_cor"] and not got["jpcrp_cor"]
    finally:
        db.close()
