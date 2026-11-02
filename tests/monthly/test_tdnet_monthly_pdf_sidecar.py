"""tdnet_monthly_pdf_sidecar / tdnet_monthly_pdf_ingest。ネットワークにも pdftotext にも出ない。

PDF の代わりに `pdftotext -layout` 相当の等幅テキストを置き、lines_from_layout で読む。
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import sqlite_readonly_engine  # noqa: E402
import jobs.monthly.tdnet_monthly_pdf_ingest as pdf_ingest  # noqa: E402
import jobs.monthly.tdnet_monthly_pdf_sidecar as sc  # noqa: E402

FY_MONTHS = ["9月", "10月", "11月", "12月", "1月", "2月", "上期計", "3月", "4月", "5月", "6月", "7月", "8月", "通期"]


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _row(label: str, cells: list[str], width: int = 8) -> str:
    return f"{label:<12}" + "".join(f"{c:>{width}}" for c in cells)


def _pdf_text(
    sales: list[str],
    stores: list[str],
    existing: list[str],
    *,
    extra_rows: list[str] | None = None,
    header: list[str] | None = None,
) -> str:
    """428A 型: 会計年度 9〜8月、上期計と通期の列、未来の月は空。既存店は ▲ 付きの増減率。"""
    cols = header or FY_MONTHS
    lines = [
        "2026年8月期 月次売上速報",
        "",
        "1. 全店                                        (前年同月比、%)",
        _row("", cols),
        _row("売上高", sales),
        _row("店舗数(店)", stores),
        *(extra_rows or []),
        "",
        "",
        "",
        "2. 既存店                                      (増減率、%)",
        _row("", cols),
        _row("売上高", existing),
        "",
        "(注) 既存店は開店から18ヶ月以上の店舗",
    ]
    return "\n".join(lines) + "\n"


def _cells(values: list[str], total: str = "") -> list[str]:
    """9〜8月の値（足りない月は空）を上期計・通期の列込みの14列にする。"""
    months = values + [""] * (12 - len(values))
    return months[:6] + ["-" if total else ""] + months[6:] + [total]


PREV_SALES = ["105.5", "113.6", "117.8", "115.3", "119.8", "115.5", "112.3", "113.0", "120.0", "109.8"]
PREV_STORES = ["124", "127", "127", "129", "128", "128", "130", "133", "135", "135"]
PREV_EXIST = ["1.3", "3.8", "4.9", "2.1", "3.2", "2.1", "▲0.4", "2.7", "7.4", "▲3.3"]

PREV = _pdf_text(_cells(PREV_SALES, "114.4"), _cells(PREV_STORES, "-"), _cells(PREV_EXIST, "2.4"))
NEW = _pdf_text(
    _cells(PREV_SALES + ["115.1"], "114.4"),
    _cells(PREV_STORES + ["136"], "-"),
    _cells(PREV_EXIST + ["2.5"], "2.4"),
)
PREV_NAME = "20260706_428A_テスト_2026年8月期6月度売上速報.pdf"
NEW_NAME = "20260804_428A_テスト_2026年8月期7月度売上速報.pdf"
FY_YM = [(2025, 9), (2025, 10), (2025, 11), (2025, 12), (2026, 1), (2026, 2),
         (2026, 3), (2026, 4), (2026, 5), (2026, 6), (2026, 7)]


def _num(s: str) -> float:
    return -float(s[1:]) if s.startswith("▲") else float(s)


def _db_rows(n: int = 10) -> list[tuple[str, int, str, int, float]]:
    rows = []
    for (y, m), sales, stores, ex in zip(FY_YM[:n], PREV_SALES, PREV_STORES, PREV_EXIST):
        rows.append(("428A0", y, "全店 売上高(前年比)", m, float(sales)))
        rows.append(("428A0", y, "全店 店舗数", m, float(stores)))
        rows.append(("428A0", y, "既存 売上高(前年比)", m, round(_num(ex) + 100, 1)))
    return rows


def _seed_db(path: Path, rows: list[tuple[str, int, str, int, float]]) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE monthly_values (code TEXT, year INTEGER, element TEXT, month INTEGER, value REAL)")
        conn.execute(
            "CREATE TABLE monthly_elements (code TEXT, year INTEGER, element TEXT, sort_order INTEGER, unit TEXT)"
        )
        conn.executemany("INSERT INTO monthly_values VALUES (?, ?, ?, ?, ?)", rows)
        units = {"全店 売上高(前年比)": "yoy_index", "全店 店舗数": "number", "既存 売上高(前年比)": "yoy_index"}
        seen = sorted({(code, year, element) for code, year, element, _m, _v in rows})
        conn.executemany(
            "INSERT INTO monthly_elements VALUES (?, ?, ?, ?, ?)",
            [(code, year, element, i, units.get(element, "number")) for i, (code, year, element) in enumerate(seen)],
        )
        conn.commit()
    finally:
        conn.close()
    return path


def _db(rows: list[tuple[str, int, str, int, float]]) -> sc.DbIssuer:
    with tempfile.TemporaryDirectory() as tmp:
        path = _seed_db(Path(tmp) / "m.db", rows)
        engine = sqlite_readonly_engine(path)
        try:
            with engine.connect() as conn:
                return sc.load_db_issuer(conn, "428A0")
        finally:
            engine.dispose()


def _grids(text: str) -> list[sc.Grid]:
    return sc.parse_grids(sc.lines_from_layout(text))


def _capture(fn: Any) -> tuple[Any, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = fn()
    return rc, buf.getvalue()


def test_shared_mask_calendar_identity() -> None:
    from app.monthly.calendar import anchor_year, column_years
    from app.monthly.ingest import _anchor_year
    from tools.monthly.text_mask import mask as shared_mask

    if sc.mask is not shared_mask:
        _fail("sidecar.mask must be tools.monthly.text_mask.mask")
    if sc.column_years is not column_years:
        _fail("sidecar.column_years must be app.monthly.calendar.column_years")
    if _anchor_year is not anchor_year:
        _fail("ingest._anchor_year must be calendar.anchor_year")
    if shared_mask("2026年7月度 売上高 1,234") != "<D>度売上高<N>":
        _fail(f"shared mask {shared_mask('2026年7月度 売上高 1,234')!r}")
    if column_years([9, 10, 11, 12, 1], date(2026, 2, 5)) != [
        (2025, 9), (2025, 10), (2025, 11), (2025, 12), (2026, 1),
    ]:
        _fail(f"column_years {column_years([9, 10, 11, 12, 1], date(2026, 2, 5))}")
    labeled = (
        "            2025年9月  2025年10月  2025年11月\n"
        "売上高           101.0       102.0       103.0\n"
    )
    g = _grids(labeled)[0]
    cells = sc._series_cells(g, g.series[0], date(2026, 1, 5))
    if cells != {(2025, 9): 101.0, (2025, 10): 102.0, (2025, 11): 103.0}:
        _fail(f"header years {cells}")
    db = _db(_brand_db_rows())
    template = sc.learn(_grids(_brand_pdf(filled=6, extra=False)), date(2026, 9, 1), db)
    if not template.known or not all(isinstance(k, sc.RowIdentity) for k in template.known):
        _fail("known must be RowIdentity")
    if any(k.ordinal != 0 for k in template.known):
        _fail("single-block fixture ordinal")
    extra = {
        sc.row_identity(g, s)
        for g in _grids(_brand_pdf(filled=7, extra=True))
        for s in g.series
        if g.ordinal == 1
    }
    if not extra:
        _fail("second block must have ordinal 1")
    if extra & template.known:
        _fail("new block identity must not be in known")


def test_workaround_comments_removed() -> None:
    src = (ROOT / "jobs" / "monthly" / "tdnet_monthly_pdf_sidecar.py").read_text(encoding="utf-8")
    gone = (
        "pdfplumber は VPS に無いので import しない",
        "jobs は app を import しない",
        "type: ignore",
        "序数を含めないと",
        "8 (0) の (0) は FC 店舗数",
        "数値の直前の - は空セル",
        "前回の PDF にあった (表の序数",
    )
    for fragment in gone:
        if fragment in src:
            _fail(f"workaround comment remains: {fragment}")


def test_tokens() -> None:
    cases = {"105.5": 105.5, "1,234": 1234.0, "▲3.3": -3.3, "△0.4": -0.4, "-": None, "275店": 275.0, "(6)": 6.0}
    for token, want in cases.items():
        ok, got = sc.parse_number(token)
        if not ok or got != want:
            _fail(f"parse_number {token!r} -> {ok} {got}")
    if sc.parse_number("売上高")[0]:
        _fail("label is not a number")
    months = {"9月": 9, "10月度": 10, "2025年7月": 7, "25年4月": 4, "2025/7": 7, "Sep.": 9, "June": 6, "上期計": None}
    for token, want in months.items():
        if sc._month_of(token) != want:
            _fail(f"_month_of {token!r} -> {sc._month_of(token)}")
    if sc._year_of("25年4月") != 2025 or sc._year_of("2026/7") != 2026 or sc._year_of("7月") is not None:
        _fail("_year_of")


def test_row_grid_columns() -> None:
    grids = _grids(PREV)
    if len(grids) != 2:
        _fail(f"expected 2 grids, got {len(grids)}")
    g = grids[0]
    sales = next(s for s in g.series if s.key == ("売上高", 0))
    cells = sc._series_cells(g, sales, date(2026, 7, 6))
    if cells.get((2026, 6)) != 109.8 or cells.get((2025, 9)) != 105.5 or len(cells) != 10:
        _fail(f"sales cells {cells}")
    stores = next(s for s in g.series if s.key == ("店舗数(店)", 0))
    if sc._series_cells(g, stores, date(2026, 7, 6)).get((2026, 3)) != 130:
        _fail("store count after the blank 上期計 column must land on 3月")


def test_lines_from_tsv_merges_split_month() -> None:
    head = "level\tpage_num\tpar_num\tblock_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext"
    words = [
        (10, 100, "9月"), (60, 100, "10"), (72, 100, "月"), (110, 100, "11月"),
        (10, 115, "売上高"), (40, 115.5, "101.0"), (80, 115.2, "99.5"), (115, 114.8, "▲1.0"),
    ]
    tsv = "\n".join(
        [head] + [f"5\t1\t0\t0\t0\t0\t{x}\t{y}\t{len(t) * 6}\t10\t100\t{t}" for x, y, t in words]
    )
    lines = sc.lines_from_tsv(tsv)
    if [t.text for t in lines[0].toks] != ["9月", "10月", "11月"]:
        _fail(f"header tokens {[t.text for t in lines[0].toks]}")
    if [t.text for t in lines[1].toks] != ["売上高", "101.0", "99.5", "▲1.0"]:
        _fail(f"row tokens {[t.text for t in lines[1].toks]}")


def test_transposed_grid() -> None:
    text = (
        "              月次売上高   前年同月比\n"
        "   3月度         2,559        95.9%\n"
        "   4月度         2,170        98.3%\n"
        "   5月度         2,498       103.1%\n"
        "   6月度         2,415       119.6%\n"
        "   7月度\n"
    )
    grids = _grids(text)
    if len(grids) != 1 or len(grids[0].series) != 2:
        _fail(f"transposed grids {[(g.skeleton, len(g.series)) for g in grids]}")
    g = grids[0]
    cells = sc._series_cells(g, g.series[1], date(2026, 7, 13))
    if cells != {(2026, 3): 95.9, (2026, 4): 98.3, (2026, 5): 103.1, (2026, 6): 119.6}:
        _fail(f"transposed cells {cells}")


def test_learn_apply_payload() -> None:
    db = _db(_db_rows())
    template = sc.learn(_grids(PREV), date(2026, 7, 6), db, source=PREV_NAME)
    units = {r.element: (r.unit, r.shift) for r in template.rules}
    want = {
        "全店 売上高(前年比)": ("yoy_index", 0.0),
        "全店 店舗数": ("number", 0.0),
        "既存 売上高(前年比)": ("yoy_pct", 100.0),
    }
    if units != want:
        _fail(f"learned units {units}")
    new_as_of = date(2026, 8, 4)
    extracted = sc.apply(template, _grids(NEW), new_as_of)
    sc.verify(extracted, db, (2026, 7))
    payload = sc.build_payload("428A", new_as_of, extracted, source=NEW_NAME, learned_from=PREV_NAME)
    if payload["months"] != [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]:
        _fail(f"months {payload['months']}")
    if payload["columns"][0] != "2025-09" or payload["columns"][-1] != "2026-07":
        _fail(f"columns {payload['columns']}")
    rows = {r["element"]: r for r in payload["rows"]}
    if rows["全店 売上高(前年比)"]["values"]["7"] != 115.1:
        _fail(f"sales 7 {rows['全店 売上高(前年比)']}")
    if rows["全店 店舗数"]["values"]["7"] != 136 or not isinstance(rows["全店 店舗数"]["values"]["7"], int):
        _fail("store count stays an int")
    ex = rows["既存 売上高(前年比)"]
    if ex["unit"] != "yoy_pct" or ex["values"]["3"] != -0.4 or ex["values"]["7"] != 2.5:
        _fail(f"existing yoy_pct {ex}")
    if "year" in json.dumps(payload):
        _fail("payload must not send year")


def test_format_change_is_manual() -> None:
    db = _db(_db_rows())
    template = sc.learn(_grids(PREV), date(2026, 7, 6), db)
    header = FY_MONTHS[:-1] + ["累計"]
    changed = _pdf_text(
        _cells(PREV_SALES + ["115.1"], "114.4"),
        _cells(PREV_STORES + ["136"], "-"),
        _cells(PREV_EXIST + ["2.5"], "2.4"),
        header=header,
    )
    try:
        sc.apply(template, _grids(changed), date(2026, 8, 4))
    except sc.FormatChanged as e:
        if "見出し" not in str(e):
            _fail(f"format change reason {e}")
    else:
        _fail("changed header must raise FormatChanged")


def test_new_row_is_manual() -> None:
    db = _db(_db_rows())
    template = sc.learn(_grids(PREV), date(2026, 7, 6), db)
    with_new = _pdf_text(
        _cells(PREV_SALES + ["115.1"], "114.4"),
        _cells(PREV_STORES + ["136"], "-"),
        _cells(PREV_EXIST + ["2.5"], "2.4"),
        extra_rows=[_row("新業態", _cells([""] * 10 + ["25"]))],
    )
    try:
        sc.apply(template, _grids(with_new), date(2026, 8, 4))
    except sc.FormatChanged as e:
        if "新業態" not in str(e):
            _fail(f"new row reason {e}")
    else:
        _fail("a new row with this month's value must raise FormatChanged")


UC_MONTHS = ["3月", "4月", "5月", "6月", "7月", "8月", "9月"]


def _brand_block(title: str, sales: list[str], guests: list[str], prices: list[str], stores: list[str]) -> str:
    """3557 型: 業態見出し + 同じ月見出し + 売上高/客数/客単価/対象店舗数。"""
    return "\n".join([
        title,
        _row("", UC_MONTHS),
        _row("売上高", sales),
        _row("客数", guests),
        _row("客単価", prices),
        _row("対象店舗数", stores),
        "",
    ])


def _brand_filled(n_filled: int, make) -> list[str]:
    return [make(i) if i < n_filled else "-" for i in range(len(UC_MONTHS))]


def _brand_pdf(*, filled: int, extra: bool) -> str:
    first = _brand_block(
        "（3）the 3rd Burger業態",
        _brand_filled(filled, lambda i: f"{100.0 + i:.1f}"),
        _brand_filled(filled, lambda i: "100.0"),
        _brand_filled(filled, lambda i: f"{100.0 + i * 0.1:.1f}"),
        _brand_filled(filled, lambda i: "10"),
    )
    if not extra:
        return first
    blank = ["-"] * (len(UC_MONTHS) - 1)
    return first + _brand_block(
        "（5）もつ焼てけ八業態",
        blank + ["104.9"],
        blank + ["104.6"],
        blank + ["100.3"],
        blank + ["1"],
    )


def _brand_db_rows(last_month: int = 8) -> list[tuple[str, int, str, int, float]]:
    rows = []
    for i, m in enumerate(range(3, last_month + 1)):
        rows.append(("428A0", 2026, "既存 売上高(前年比)", m, 100.0 + i))
        rows.append(("428A0", 2026, "既存 客数(前年比)", m, 100.0))
        rows.append(("428A0", 2026, "既存 客単価(前年比)", m, round(100.0 + i * 0.1, 1)))
        rows.append(("428A0", 2026, "既存 店舗数", m, 10.0))
    return rows


def test_same_skeleton_new_block_is_manual() -> None:
    """3557: 前月と同じ見出し・行名の業態ブロックに今月の値があっても、黙って落とさず FormatChanged。"""
    prev = _brand_pdf(filled=6, extra=False)
    new = _brand_pdf(filled=7, extra=True)
    db = _db(_brand_db_rows())
    template = sc.learn(_grids(prev), date(2026, 9, 1), db)
    prev_grids, new_grids = _grids(prev), _grids(new)
    if len(prev_grids) != 1 or len(new_grids) != 2:
        _fail(f"fixture grids prev={len(prev_grids)} new={len(new_grids)}")
    if prev_grids[0].skeleton != new_grids[1].skeleton:
        _fail("fixture blocks must share a skeleton")
    got_fp = sc.fingerprint(new_grids, sc._used(template.rules))
    if got_fp != template.fingerprint:
        _fail(f"fingerprint must stay the same so only known-keys catch the block: {got_fp}")
    extracted = sc.apply(template, _grids(_brand_pdf(filled=7, extra=False)), date(2026, 10, 1))
    sc.verify(extracted, db, (2026, 9))
    try:
        sc.apply(template, new_grids, date(2026, 10, 1))
    except sc.FormatChanged as e:
        msg = str(e)
        if "新しい行" not in msg or "表2" not in msg or "売上高" not in msg:
            _fail(f"same-skeleton new block reason {e}")
    else:
        _fail("a new block with the same headings must raise FormatChanged")


def test_blank_same_skeleton_block_stays_ready() -> None:
    """新ブロックが全部 ー ならグリッドにならず、既存ブロックだけ投入する。"""
    n = len(UC_MONTHS)
    blank = _brand_pdf(filled=7, extra=False) + _brand_block(
        "（5）もつ焼てけ八業態",
        ["-"] * n,
        ["-"] * n,
        ["-"] * n,
        ["-"] * n,
    )
    db = _db(_brand_db_rows())
    template = sc.learn(_grids(_brand_pdf(filled=6, extra=False)), date(2026, 9, 1), db)
    if len(_grids(blank)) != 1:
        _fail(f"all-blank extra block must not parse as a grid: {len(_grids(blank))}")
    extracted = sc.apply(template, _grids(blank), date(2026, 10, 1))
    sc.verify(extracted, db, (2026, 9))


def test_verify_rejects_mismatch_and_jump() -> None:
    db = _db(_db_rows())
    template = sc.learn(_grids(PREV), date(2026, 7, 6), db)
    shifted = [str(float(v) + 5) for v in PREV_SALES] + ["115.1"]
    bad = _pdf_text(_cells(shifted, "114.4"), _cells(PREV_STORES + ["136"], "-"), _cells(PREV_EXIST + ["2.5"], "2.4"))
    extracted = sc.apply(template, _grids(bad), date(2026, 8, 4))
    try:
        sc.verify(extracted, db, (2026, 7))
    except sc.FormatChanged as e:
        if "食い違う" not in str(e):
            _fail(f"mismatch reason {e}")
    else:
        _fail("rows that disagree with DB must not be written")
    jump = _pdf_text(
        _cells(PREV_SALES + ["115.1"], "114.4"),
        _cells(PREV_STORES + ["1,360"], "-"),
        _cells(PREV_EXIST + ["2.5"], "2.4"),
    )
    extracted = sc.apply(template, _grids(jump), date(2026, 8, 4))
    try:
        sc.verify(extracted, db, (2026, 7))
    except sc.FormatChanged as e:
        if "桁違い" not in str(e):
            _fail(f"jump reason {e}")
    else:
        _fail("a 10x store count must not be written")


def test_learn_needs_every_element() -> None:
    rows = _db_rows() + [("428A0", 2026, "客数(前年比)", 6, 97.0)]
    try:
        sc.learn(_grids(PREV), date(2026, 7, 6), _db(rows))
    except sc.NoTemplate as e:
        if "客数(前年比)" not in str(e):
            _fail(f"missing element reason {e}")
    else:
        _fail("an element the PDF cannot reproduce must stop the template")


def test_duplicate_months_use_column_keys() -> None:
    rule = sc.RowRule("売上高", "number", 0, ("売上高", 0), 0.0)
    cells = {(2025, 7): 10.0, (2026, 6): 11.0, (2026, 7): 12.0}
    payload = sc.build_payload("9267", date(2026, 8, 5), [sc.Extracted(rule, cells)], source="a", learned_from="b")
    if payload["months"] != [7, 6, 7] or payload["columns"] != ["2025-07", "2026-06", "2026-07"]:
        _fail(f"duplicate month columns {payload['months']} {payload['columns']}")
    if payload["rows"][0]["values"] != {"0": 10, "1": 11, "2": 12}:
        _fail(f"column keys {payload['rows'][0]['values']}")


def test_ambiguous_rows_must_agree() -> None:
    text = "\n".join([_row("", FY_MONTHS), _row("全店", _cells(["101.0", "102.0"])), _row("既存店", _cells(["101.0", "102.0"]))])
    rows = [("428A0", 2025, "全店 売上高(前年比)", 9, 101.0), ("428A0", 2025, "全店 売上高(前年比)", 10, 102.0)]
    template = sc.learn(sc.parse_grids(sc.lines_from_layout(text)), date(2025, 11, 5), _db(rows))
    if not template.rules[0].alts:
        _fail("identical rows must be kept as alternatives")
    diverged = "\n".join([
        _row("", FY_MONTHS),
        _row("全店", _cells(["101.0", "102.0", "103.0"])),
        _row("既存店", _cells(["101.0", "102.0", "99.0"])),
    ])
    try:
        sc.apply(template, sc.parse_grids(sc.lines_from_layout(diverged)), date(2025, 12, 5))
    except sc.FormatChanged:
        pass
    else:
        _fail("alternatives that diverge must not be written")


def _dest(tmp: Path, files: dict[str, str]) -> Path:
    dest = tmp / "monthly"
    (dest / "428A").mkdir(parents=True)
    for name, text in files.items():
        (dest / "428A" / name).write_text(text, encoding="utf-8")
    return dest


def _read_layout(path: Path) -> list[sc.Line]:
    return sc.lines_from_layout(path.read_text(encoding="utf-8"))


def test_run_writes_once() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dest = _dest(tmp, {PREV_NAME: PREV, NEW_NAME: NEW})
        db_path = _seed_db(tmp / "m.db", _db_rows())
        rc, out = _capture(lambda: sc.run(dest, db_path, dry_run=True, read_lines=_read_layout))
        if rc != 0 or "plan 428A" not in out or list(dest.glob(f"**/*{sc.SIDECAR_SUFFIX}")):
            _fail(f"dry-run must not write: {out}")
        rc, out = _capture(lambda: sc.run(dest, db_path, read_lines=_read_layout))
        written = list(dest.glob(f"**/*{sc.SIDECAR_SUFFIX}"))
        if rc != 0 or len(written) != 1 or "ok 428A" not in out:
            _fail(f"expected one sidecar: {out}")
        if written[0].name != NEW_NAME[:-4] + sc.SIDECAR_SUFFIX:
            _fail(f"sidecar name {written[0].name}")
        payload = json.loads(written[0].read_text(encoding="utf-8"))
        if payload["code"] != "428A" or payload["as_of"] != "2026-08-04" or payload["learned_from"] != PREV_NAME:
            _fail(f"payload head {payload}")
        _rc, out = _capture(lambda: sc.run(dest, db_path, read_lines=_read_layout))
        if "same=1" not in out:
            _fail(f"second run must be unchanged: {out}")
        current = _seed_db(tmp / "current.db", _db_rows(11) + [("428A0", 2026, "全店 売上高(前年比)", 7, 115.1)])
        _rc, out = _capture(lambda: sc.run(dest, current, only=["428A"], read_lines=_read_layout))
        if "current 428A" not in out:
            _fail(f"ingested month must be current: {out}")


def test_run_reports_manual() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        broken = NEW.replace("売上高", "売上")
        dest = _dest(tmp, {PREV_NAME: PREV, NEW_NAME: broken})
        db_path = _seed_db(tmp / "m.db", _db_rows())
        rc, out = _capture(lambda: sc.run(dest, db_path, read_lines=_read_layout))
        if rc != 0 or "manual 428A" not in out or "manual=1" not in out:
            _fail(f"format change must be reported as manual: {out}")
        if list(dest.glob(f"**/*{sc.SIDECAR_SUFFIX}")):
            _fail("manual must not write a sidecar")


def test_run_reports_same_skeleton_new_block() -> None:
    """3557 型: 同じ見出しの新ブロックは sidecar を書かず manual に理由を残す。"""
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        prev_name = "20260901_428A_テスト_8月度.pdf"
        new_name = "20261001_428A_テスト_9月度.pdf"
        dest = _dest(
            tmp,
            {
                prev_name: _brand_pdf(filled=6, extra=False),
                new_name: _brand_pdf(filled=7, extra=True),
            },
        )
        db_path = _seed_db(tmp / "m.db", _brand_db_rows())
        rc, out = _capture(lambda: sc.run(dest, db_path, read_lines=_read_layout))
        if rc != 0 or "manual 428A" not in out or "新しい行" not in out or "表2" not in out:
            _fail(f"same-skeleton new block must be manual: {out}")
        if list(dest.glob(f"**/*{sc.SIDECAR_SUFFIX}")):
            _fail("unlearned block must not write a sidecar")


@dataclass
class FakeClient:
    mapping: dict[str, int]
    posts: list[dict[str, Any]] = field(default_factory=list)

    def post_json(self, url: str, body: dict[str, Any]) -> tuple[int, Any]:
        self.posts.append(body)
        updated = [] if body.get("dry_run") else [{"year": 2026, "element": "x", "months": [7]}]
        return 200, {
            "code": "428A0", "fy_end_month": 8, "layout": "fiscal", "as_of": body["as_of"],
            "mapping": self.mapping, "updated": updated,
        }

    def get_json(self, url: str) -> tuple[int, Any]:
        return 200, {"years": [2025, 2026]}


def test_ingest_checks_mapping_before_post() -> None:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        dest = _dest(tmp, {PREV_NAME: PREV, NEW_NAME: NEW})
        db_path = _seed_db(tmp / "m.db", _db_rows())
        _capture(lambda: sc.run(dest, db_path, read_lines=_read_layout))
        good = {str(m): (2025 if m >= 9 else 2026) for m in (9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7)}
        client = FakeClient(good)
        kwargs = {
            "ledger_path": tmp / "ledger.json", "client": client, "today": date(2026, 8, 4),
            "pattern": f"*{sc.SIDECAR_SUFFIX}", "label": pdf_ingest.LABEL, "registry": False,
            "empty_ok": True, "precheck": pdf_ingest.check_columns,
        }
        rc, out = _capture(lambda: pdf_ingest.run(dest, **kwargs))
        if rc != 0 or [bool(p.get("dry_run")) for p in client.posts] != [True, False]:
            _fail(f"dry_run then POST expected: {out} {client.posts}")
        if any("columns" in p or "source" in p for p in client.posts):
            _fail("sidecar bookkeeping keys must not be posted")
        _rc, out = _capture(lambda: pdf_ingest.run(dest, **kwargs))
        if "skipped=1" not in out or len(client.posts) != 2:
            _fail(f"ledger must skip the same sidecar: {out}")
        wrong = FakeClient({**good, "9": 2026})
        kwargs.update(client=wrong, ledger_path=tmp / "ledger2.json")
        rc, out = _capture(lambda: pdf_ingest.run(dest, **kwargs))
        if rc != 1 or "fail precheck" not in out or len(wrong.posts) != 1:
            _fail(f"mapping mismatch must stop before POST: {out}")
        empty = tmp / "empty" / "monthly"
        empty.mkdir(parents=True)
        rc, out = _capture(lambda: pdf_ingest.run(empty, **kwargs))
        if rc != 0:
            _fail(f"no sidecar is a normal day: {out}")


def main_tests() -> None:
    test_shared_mask_calendar_identity()
    test_workaround_comments_removed()
    test_tokens()
    test_row_grid_columns()
    test_lines_from_tsv_merges_split_month()
    test_transposed_grid()
    test_learn_apply_payload()
    test_format_change_is_manual()
    test_new_row_is_manual()
    test_same_skeleton_new_block_is_manual()
    test_blank_same_skeleton_block_stays_ready()
    test_verify_rejects_mismatch_and_jump()
    test_learn_needs_every_element()
    test_duplicate_months_use_column_keys()
    test_ambiguous_rows_must_agree()
    test_run_writes_once()
    test_run_reports_manual()
    test_run_reports_same_skeleton_new_block()
    test_ingest_checks_mapping_before_post()
    print("ok: tdnet monthly pdf sidecar")


if __name__ == "__main__":
    main_tests()
