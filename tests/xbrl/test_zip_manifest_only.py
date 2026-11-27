from __future__ import annotations

import io
import json
import sys
import zipfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import XBRL_ZIP_MANIFEST_ONLY_PATH
from app.db import SessionLocal
from app.edinet.textblocks import honbun_html, ixbrl_html, public_html
from app.models import XbrlElement, XbrlFact
from app.xbrl.documents import read_documents
from app.xbrl.parser import collect_parser_taxonomy_urls, extract_zip
from app.xbrl.presentation_backfill import read_zip
from app.xbrl.store import parse_zip, parse_zip_into_filing
from app.xbrl.zip_scope import foreign_names, kept_names, manifest_only_doc_ids
from tests.xbrl.test_xbrl_parse import _ixbrl_html, seed_stock, write_zip
from tests.xbrl.test_xbrl_presentation import JPPFS_ROLE, _link, _p, _pre

LISTED = "S100W2KO"
OTHER = "S100ZZZ1"
TITLE = "訂正有価証券報告書－第22期(2019/04/01－2020/03/31)"
OWN = "jpcrp030000-asr-001_E01130-000_2020-03-31_02_2025-06-24"
FOREIGN = "jpcrp030000-asr-001_E01130-000_2024-03-31_02_2025-06-24"
NS_2019 = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2019-11-01/jppfs_cor"
NS_2023 = "http://disclosure.edinet-fsa.go.jp/taxonomy/jppfs/2023-12-01/jppfs_cor"
OWN_ROLE = "rol_ConsolidatedBalanceSheet"
FOREIGN_ROLE = "rol_BalanceSheet"


def _manifest(stem: str, ixbrl: list[str]) -> str:
    items = "".join(f"<ixbrl>{name}</ixbrl>" for name in ixbrl)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<manifest xmlns="http://disclosure.edinet-fsa.go.jp/2013/manifest"><list>'
        f'<instance id="jpcrp030000" type="PublicDoc" preferredFilename="{stem}.xbrl">{items}</instance>'
        "</list></manifest>"
    )


def _bs(role: str) -> str:
    return _pre([_link(JPPFS_ROLE + role, [
        (_p("BalanceSheetHeading"), _p("BalanceSheetLineItems"), 1),
        (_p("BalanceSheetLineItems"), _p("AssetsAbstract"), 1),
        (_p("AssetsAbstract"), _p("Assets"), 1, "totalLabel"),
    ])])


def _zip_bytes() -> bytes:
    own = f"0101010_honbun_{OWN}_ixbrl.htm"
    foreign = f"0101010_honbun_{FOREIGN}_ixbrl.htm"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("PublicDoc/0101000_0639500133706.htm", "<html><body>訂正</body></html>")
        zf.writestr(f"XBRL/PublicDoc/{own}", _ixbrl_html(
            [("Assets", "CurrentYearInstant", "JPY", "6", "1032923")],
            ns_uri=NS_2019, current_end="2020-03-31", current_start="2019-04-01",
        ))
        zf.writestr(f"XBRL/PublicDoc/{foreign}", _ixbrl_html(
            [("Assets", "CurrentYearInstant", "JPY", "6", "1338251")],
            ns_uri=NS_2023, current_end="2024-03-31", current_start="2023-04-01",
        ))
        zf.writestr(f"XBRL/PublicDoc/{OWN}_pre.xml", _bs(OWN_ROLE))
        zf.writestr(f"XBRL/PublicDoc/{FOREIGN}_pre.xml", _bs(FOREIGN_ROLE))
        zf.writestr(f"XBRL/PublicDoc/{OWN}.xbrl", "<xbrli:xbrl xmlns:xbrli='http://www.xbrl.org/2003/instance'/>")
        zf.writestr("XBRL/PublicDoc/manifest_PublicDoc.xml", _manifest(OWN, [own]))
        zf.writestr("XBRL/PublicDoc/fuzoku/50_0639500102406.gif", b"GIF89a")
    return buf.getvalue()


def _zip(tmp: Path) -> Path:
    return write_zip(tmp, f"{LISTED}_type1.zip", _zip_bytes())


def _assets(pkg) -> list[tuple[str, Decimal]]:
    return sorted((f.namespace_uri, f.value_num) for f in pkg.facts
                  if f.local_name == "Assets" and f.context_ref == "CurrentYearInstant")


def test_list_is_well_formed() -> None:
    raw = json.loads(XBRL_ZIP_MANIFEST_ONLY_PATH.read_text(encoding="utf-8"))
    seen = set()
    for item in raw["filings"]:
        assert item["source"] in ("edinet", "tdnet") and item["doc_id"] and item["evidence"]
        assert len(item["code"]) == 5
        assert item["doc_id"] not in seen
        seen.add(item["doc_id"])
    assert manifest_only_doc_ids() == frozenset(seen)
    assert LISTED in seen and OTHER not in seen


def test_foreign_names_are_the_other_document_in_the_manifest_folder() -> None:
    with zipfile.ZipFile(io.BytesIO(_zip_bytes())) as zf:
        names = zf.namelist()
        assert foreign_names(names, zf.read) == {
            f"XBRL/PublicDoc/0101010_honbun_{FOREIGN}_ixbrl.htm",
            f"XBRL/PublicDoc/{FOREIGN}_pre.xml",
        }
        assert kept_names(OTHER, names, zf.read) == names
        assert len(kept_names(LISTED, names, zf.read)) == len(names) - 2


def test_listed_filing_reads_only_the_manifest_document(tmp: Path) -> None:
    pkg = parse_zip(_zip(tmp), title=TITLE, submitted_at="2025-06-24 13:18:00",
                    cache_dir=tmp / "cache", doc_id=LISTED)
    assert _assets(pkg) == [(NS_2019, Decimal("1032923000000"))]
    assert all(FOREIGN not in p.name for p in pkg.ixbrl_files)
    assert [r.role for r in pkg.presentation] == [OWN_ROLE]


def test_unlisted_filing_still_reads_the_union(tmp: Path) -> None:
    pkg = parse_zip(_zip(tmp), title=TITLE, submitted_at="2025-06-24 13:18:00",
                    cache_dir=tmp / "cache", doc_id=OTHER)
    assert _assets(pkg) == [(NS_2019, Decimal("1032923000000")), (NS_2023, Decimal("1338251000000"))]
    assert sorted(r.role for r in pkg.presentation) == sorted([OWN_ROLE, FOREIGN_ROLE])
    no_id = parse_zip(_zip(tmp), title=TITLE, submitted_at="2025-06-24 13:18:00", cache_dir=tmp / "cache")
    assert _assets(no_id) == _assets(pkg)


def test_extract_keeps_non_xbrl_files(tmp: Path) -> None:
    root = extract_zip(_zip(tmp), tmp / "unpacked", doc_id=LISTED)
    left = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
    assert "XBRL/PublicDoc/fuzoku/50_0639500102406.gif" in left
    assert "PublicDoc/0101000_0639500133706.htm" in left
    assert f"XBRL/PublicDoc/{OWN}.xbrl" in left
    assert not [n for n in left if FOREIGN in n]
    collect_parser_taxonomy_urls(root, cache_dir=None)


def test_parse_into_filing_passes_the_doc_id(tmp: Path) -> None:
    db = SessionLocal()
    try:
        seed_stock(db, "52330", "太平洋セメント")
        db.commit()
        for doc_id, want in ((LISTED, 1), (OTHER, 2)):
            filing = parse_zip_into_filing(
                db, zip_path=_zip(tmp), code="52330", doc_id=doc_id, title=TITLE,
                raw_path=f"edinet:{doc_id}", raw_sha256="x", submitted_at="2025-06-24 13:18:00",
                cache_dir=tmp / "cache", source="edinet",
            )
            assert filing.parse_status == "parsed"
            got = (db.query(XbrlFact).join(XbrlElement, XbrlElement.id == XbrlFact.element_id)
                   .filter(XbrlFact.filing_id == filing.id, XbrlElement.local_name == "Assets").count())
            assert got == want
    finally:
        db.close()


def test_zip_readers_in_memory_follow_the_list() -> None:
    content = _zip_bytes()
    assert [r.role for r in read_zip(content, LISTED)] == [OWN_ROLE]
    assert sorted(r.role for r in read_zip(content, OTHER)) == sorted([OWN_ROLE, FOREIGN_ROLE])
    assert len(honbun_html(content, LISTED)) == 1 and len(honbun_html(content, OTHER)) == 2
    assert len(ixbrl_html(content, LISTED)) == 1 and len(ixbrl_html(content)) == 2
    assert len(public_html(content, LISTED)) == 2 and len(public_html(content, OTHER)) == 3


def test_documents_follow_the_list(tmp: Path) -> None:
    path = _zip(tmp)
    assert read_documents(path, OTHER) == read_documents(path)
    assert [d["file_name"] for d in read_documents(path, LISTED)] == [
        d["file_name"] for d in read_documents(path) if FOREIGN not in d["file_name"]
    ]
