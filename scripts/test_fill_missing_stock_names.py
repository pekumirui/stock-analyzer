"""fill_missing_stock_names と ingest_master の空 CoName 保護。ネットワークには出ない。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-fill-names-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from app.db import SessionLocal, init_db  # noqa: E402
from app.ingest import fill_missing_stock_names, ingest_master  # noqa: E402
from app.models import MonthlyTable, Stock  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


class _FakeJQ:
    def __init__(self, rows: list[dict]):
        self._rows = rows

    def equities_master(self, date=None):
        return self._rows


def _touch_pdf(dest: Path, display: str, ymd: str, name: str, title: str = "月次") -> None:
    folder = dest / display
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{ymd}_{display}_{name}_{title}.pdf").write_bytes(b"%PDF-1.4")


def _clear(db, dest: Path | None = None) -> None:
    db.query(MonthlyTable).delete()
    db.query(Stock).delete()
    db.commit()
    if dest is not None and dest.exists():
        shutil.rmtree(dest)
        dest.mkdir(parents=True)


def _name(db, code: str) -> str | None:
    row = db.get(Stock, code)
    return None if row is None else row.name


def test_nameless_filled_from_pdf(db, dest: Path) -> None:
    _clear(db, dest)
    db.add(MonthlyTable(code="30320", year=2025))
    db.add(Stock(code="30320", name=""))
    db.add(MonthlyTable(code="30660", year=2025))
    db.commit()
    _touch_pdf(dest, "3032", "20240101", "古い名前")
    _touch_pdf(dest, "3032", "20260315", "ゴルフ・ドゥ")
    _touch_pdf(dest, "3066", "20260201", "JBイレブン")
    n = fill_missing_stock_names(db, dest=dest)
    if n != 2:
        _fail(f"expected 2 fills, got {n}")
    if _name(db, "30320") != "ゴルフ・ドゥ":
        _fail(f"nameless 30320: {_name(db, '30320')!r}")
    if _name(db, "30660") != "JBイレブン":
        _fail(f"missing-row 30660: {_name(db, '30660')!r}")


def test_existing_name_kept(db, dest: Path) -> None:
    _clear(db, dest)
    db.add(Stock(code="72030", name="トヨタ自動車"))
    db.add(MonthlyTable(code="72030", year=2025))
    db.commit()
    _touch_pdf(dest, "7203", "20260315", "別名")
    fill_missing_stock_names(db, dest=dest)
    if _name(db, "72030") != "トヨタ自動車":
        _fail(f"existing name overwritten: {_name(db, '72030')!r}")


def test_dest_folder_inserts_alnum(db, dest: Path) -> None:
    _clear(db, dest)
    _touch_pdf(dest, "262a", "20251101", "インターメスティック")
    n = fill_missing_stock_names(db, dest=dest)
    if n != 1:
        _fail(f"262A insert count {n}")
    if _name(db, "262A0") != "インターメスティック":
        _fail(f"262A0: {_name(db, '262A0')!r}")


def test_unparsable_skipped(db, dest: Path) -> None:
    _clear(db, dest)
    folder = dest / "3999"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "readme.pdf").write_bytes(b"%PDF")
    (folder / "notadate_3999_名前_月次.pdf").write_bytes(b"%PDF")
    n = fill_missing_stock_names(db, dest=dest)
    if n != 0:
        _fail(f"unparsable should skip, got {n}")
    if db.get(Stock, "39990") is not None:
        _fail("unparsable folder must not insert")


def test_missing_dest_does_not_raise(db) -> None:
    _clear(db)
    db.add(MonthlyTable(code="30320", year=2025))
    db.add(Stock(code="30320", name=None))
    db.commit()
    missing = Path(_tmp) / "no-such-monthly-dest"
    try:
        n = fill_missing_stock_names(db, dest=missing)
    except Exception as exc:
        _fail(f"missing dest raised: {exc!r}")
    if n != 0:
        _fail(f"missing dest should no-op, got {n}")
    prev = os.environ.get("TDNET_MONTHLY_DEST")
    os.environ["TDNET_MONTHLY_DEST"] = str(missing)
    try:
        n = fill_missing_stock_names(db)
    except Exception as exc:
        _fail(f"missing dest env raised: {exc!r}")
    finally:
        if prev is None:
            os.environ.pop("TDNET_MONTHLY_DEST", None)
        else:
            os.environ["TDNET_MONTHLY_DEST"] = prev
    if n != 0:
        _fail(f"missing dest env should no-op, got {n}")
    if _name(db, "30320") not in (None, ""):
        _fail(f"missing dest must not fill: {_name(db, '30320')!r}")


def test_ingest_master_empty_coname_keeps_tdnet(db) -> None:
    _clear(db)
    db.add(Stock(code="30320", name="ゴルフ・ドゥ"))
    db.add(Stock(code="72030", name="旧トヨタ"))
    db.commit()
    jq = _FakeJQ([
        {"Code": "30320", "CoName": None},
        {"Code": "72030", "CoName": "トヨタ自動車", "S17Nm": "輸送用機器",
         "S33Nm": "輸送用機器", "MktNm": "プライム", "ScaleCat": "TOPIX Large70",
         "MrgnNm": "貸借", "ProdCat": "011"},
        {"Code": "30660", "CoName": ""},
    ])
    ingest_master(db, jq)
    if _name(db, "30320") != "ゴルフ・ドゥ":
        _fail(f"empty CoName wiped TDnet name: {_name(db, '30320')!r}")
    if _name(db, "72030") != "トヨタ自動車":
        _fail(f"non-empty CoName should update: {_name(db, '72030')!r}")
    row = db.get(Stock, "30660")
    if row is None:
        _fail("empty CoName still inserts the code")
    if (row.name or "").strip():
        _fail(f"new empty CoName should stay empty: {row.name!r}")


def main() -> None:
    init_db()
    db = SessionLocal()
    try:
        with tempfile.TemporaryDirectory(prefix="tdnet-monthly-") as d:
            dest = Path(d) / "monthly"
            dest.mkdir()
            test_nameless_filled_from_pdf(db, dest)
            test_existing_name_kept(db, dest)
            test_dest_folder_inserts_alnum(db, dest)
            test_unparsable_skipped(db, dest)
            test_missing_dest_does_not_raise(db)
            test_ingest_master_empty_coname_keeps_tdnet(db)
    finally:
        db.close()
    print("ok: fill missing stock names")


if __name__ == "__main__":
    main()
