"""月次データ登録 API (/api/v1/tables) の仕様テスト。手元の stock.db は触らない。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/stock.db"

from fastapi.testclient import TestClient  # noqa: E402

from app.api.monthly._common import ApiError, _apply_incoming_unit, _parse_code  # noqa: E402
from app.main import app  # noqa: E402
import app.monthly.ingest as monthly_ingest_mod  # noqa: E402
from app.monthly.ingest import (  # noqa: E402
    MONTHLY_DISPLAY_UNITS,
    MonthlyIngestError,
    apply_issuer_aliases,
    canonicalize_element_label,
    format_monthly_cell,
    monthly_display_label,
    monthly_element_label,
    monthly_row_scope_rank,
    monthly_row_segment,
    monthly_row_slot,
    parse_monthly_unit,
    union_monthly_elements,
)

INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"


def _spa_source() -> str:
    """Markup + CSS + monthly JS. Guards used to read static/index.html as one file."""
    files = [
        ROOT / "static" / "index.html",
        ROOT / "static" / "css" / "app.css",
        ROOT / "static" / "js" / "monthly.js",
    ]
    missing = [str(p.relative_to(ROOT)) for p in files if not p.is_file()]
    if missing:
        _fail("SPA source missing: " + ", ".join(missing))
    return "\n".join(p.read_text(encoding="utf-8") for p in files)


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_apply_incoming_unit_conflict_and_force() -> None:
    from app.models import MonthlyElement

    row = MonthlyElement(code="72030", year=2026, element="売上高", sort_order=1, unit="number")
    _apply_incoming_unit(row, "number", False)
    if row.unit != "number":
        _fail(f"same unit must no-op: {row.unit!r}")
    try:
        _apply_incoming_unit(row, "yoy_index", False)
    except ApiError as exc:
        if exc.status != 409 or exc.payload["error"]["code"] != "UNIT_CONFLICT":
            _fail(f"conflict: {exc.status} {exc.payload!r}")
        extra = exc.payload["error"]
        if extra.get("existing") != "number" or extra.get("incoming") != "yoy_index":
            _fail(f"conflict extra: {extra!r}")
    else:
        _fail("unit mismatch without force must 409")
    _apply_incoming_unit(row, "yoy_index", True)
    if row.unit != "yoy_index":
        _fail(f"force must overwrite unit: {row.unit!r}")


def test_startup_keeps_names_and_job_renames() -> None:
    """保存名は凍結（#545）。起動時は書き換えずドリフトを数えるだけ。付け替えは jobs/monthly_names.py。"""
    from app.db import SessionLocal, init_db
    from app.monthly.maintenance import merge_yoy_duplicate_elements, monthly_name_drift, report_monthly_name_drift
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    # Isolated year so later 72030/2026 API cases keep their own rows.
    code, year = "69810", 1991
    init_db()
    _ensure_stock(code, "村田製作所")
    db = SessionLocal()
    try:
        if db.get(MonthlyTable, (code, year)) is None:
            db.add(MonthlyTable(code=code, year=year))
        db.add(MonthlyElement(
            code=code, year=year, element="売上高前年比",
            sort_order=1, unit="yoy_index",
        ))
        db.add(MonthlyValue(
            code=code, year=year, element="売上高前年比", month=4, value=105.0,
        ))
        db.commit()
    finally:
        db.close()
    try:
        init_db()
        if (code, year, "売上高前年比", "売上高(前年比)") not in monthly_name_drift():
            _fail(f"drift must list the short name: {monthly_name_drift()!r}")
        if report_monthly_name_drift(verbose=False) < 1:
            _fail("drift report must count the short name")
        merge_yoy_duplicate_elements(verbose=False, dry_run=True)
        db = SessionLocal()
        try:
            if db.get(MonthlyElement, (code, year, "売上高前年比")) is None:
                _fail("startup / dry_run must not rename stored names")
        finally:
            db.close()
        merge_yoy_duplicate_elements(verbose=False)
        db = SessionLocal()
        try:
            if db.get(MonthlyElement, (code, year, "売上高前年比")) is not None:
                _fail("job must rename the short yoy name")
            val = db.get(MonthlyValue, (code, year, "売上高(前年比)", 4))
            if val is None or float(val.value) != 105.0:
                _fail(f"renamed value: {val!r}")
        finally:
            db.close()
    finally:
        db = SessionLocal()
        try:
            db.query(MonthlyValue).filter_by(code=code, year=year).delete()
            db.query(MonthlyElement).filter_by(code=code, year=year).delete()
            tbl = db.get(MonthlyTable, (code, year))
            if tbl is not None:
                db.delete(tbl)
            db.commit()
        finally:
            db.close()

def test_migrate_restores_dropped_columns() -> None:
    from sqlalchemy import text

    from app.db import engine, init_db, migrate_add_columns, missing_columns

    init_db()
    if missing_columns():
        _fail(f"fresh db must have no missing columns: {missing_columns()}")
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE stocks DROP COLUMN product_category"))
        conn.execute(text("ALTER TABLE monthly_elements DROP COLUMN unit"))
    got = missing_columns()
    if got != {"stocks": ["product_category"], "monthly_elements": ["unit"]}:
        _fail(f"missing_columns after drop: {got}")
    if migrate_add_columns(verbose=False) != 2 or missing_columns():
        _fail(f"migrate must add both columns back: {missing_columns()}")
    # NOT NULL 列は server_default ごと戻らないと INSERT できない
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO monthly_elements (code, year, element, sort_order) "
            "VALUES ('69810', 1990, 'migrate-probe', 1)"
        ))
        unit = conn.execute(text(
            "SELECT unit FROM monthly_elements WHERE element = 'migrate-probe'"
        )).scalar()
        conn.execute(text("DELETE FROM monthly_elements WHERE element = 'migrate-probe'"))
    if unit != "number":
        _fail(f"restored unit column default: {unit!r}")


def _assert_calendar_values(vals: dict, present: dict[str, float], label: str) -> None:
    """GET values は 1〜12。present 以外は null。"""
    keys = [str(m) for m in range(1, 13)]
    if list(vals.keys()) != keys:
        _fail(f"{label} months: {list(vals.keys())!r}")
    for k in keys:
        got = vals[k]
        if k in present:
            if got is None or float(got) != float(present[k]):
                _fail(f"{label} {k}: want {present[k]!r} got {got!r}")
        elif got is not None:
            _fail(f"{label} {k}: want null got {got!r}")


def _select_tags(html: str) -> list[str]:
    out: list[str] = []
    low = html.lower()
    start = 0
    while True:
        i = low.find("<select", start)
        if i < 0:
            break
        j = html.find(">", i)
        if j < 0:
            break
        out.append(html[i : j + 1])
        start = j + 1
    return out


def _err(resp) -> dict:
    body = resp.json()
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict) or "code" not in err or "message" not in err:
        _fail(f"error shape: {resp.status_code} {body!r}")
    return err


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        _fail(f"{label}: {resp.status_code} {resp.text[:400]}")


def _ensure_stock(code: str, name: str | None = None) -> None:
    from app.db import SessionLocal
    from app.models import Stock

    db = SessionLocal()
    try:
        if db.get(Stock, code) is None:
            db.add(Stock(code=code, name=name))
            db.commit()
    finally:
        db.close()


def _add_fy_end(code: str, disclosed: date, fy_end: date, disclosure_no: str) -> None:
    from app.db import SessionLocal
    from app.models import Statement

    _ensure_stock(code)
    db = SessionLocal()
    try:
        db.add(Statement(
            disclosure_no=disclosure_no,
            code=code,
            disclosed_date=disclosed,
            fiscal_year_end=fy_end,
        ))
        db.commit()
    finally:
        db.close()


def test_monthly_tree_assign() -> None:
    """rows の完全一致 → 最長 prefix。見出しは行を取らない。文字列の子は親の prefix を前置する。指標は葉で振り分ける。"""
    from app.monthly.tree import assign_tree_rows, parse_issuer_tree, tree_payload

    tree = parse_issuer_tree({
        "tree": [
            {"name": "連結", "rows": ["総額 連結売上高(前年比)"], "children": [
                {"name": "百貨店", "prefix": "百貨店事業", "children": [
                    {"name": "店計", "prefix": "大丸松坂屋 店計", "children": [
                        {"name": "大丸", "heading": True, "prefix": "大丸", "children": ["心斎橋店"]},
                    ]},
                    {"name": "うち商品", "prefix": "大丸松坂屋百貨店 商品", "part": True},
                ]},
            ]},
        ],
        "metrics": [
            {"name": "総額", "leaves": ["総額 連結売上高(前年比)", "総額 売上高(前年比)", "売上高(前年比)"]},
            {"name": "IFRS", "leaves": ["売上収益(IFRS)(前年比)"]},
        ],
    })
    if tree is None:
        _fail("tree must parse")
    trees = {"99990": tree}
    els = [
        "百貨店事業 売上収益(IFRS)(前年比)",
        "百貨店事業 総額 売上高(前年比)",
        "大丸 心斎橋店 売上高(前年比)",
        "大丸 心斎橋店 入店 客数(前年比)",
        "大丸松坂屋 店計 売上高(前年比)",
        "大丸松坂屋百貨店 売上高(前年比)",
        "大丸松坂屋百貨店 商品 売上高(前年比)",
        "総額 連結売上高(前年比)",
    ]
    rows = assign_tree_rows("99990", els, trees)
    want = {
        "百貨店事業 売上収益(IFRS)(前年比)": (["連結", "百貨店"], "売上収益(IFRS)(前年比)", "IFRS"),
        "百貨店事業 総額 売上高(前年比)": (["連結", "百貨店"], "総額 売上高(前年比)", "総額"),
        "大丸 心斎橋店 売上高(前年比)": (["連結", "百貨店", "店計", "大丸", "心斎橋店"], "売上高(前年比)", "総額"),
        "大丸 心斎橋店 入店 客数(前年比)": (["連結", "百貨店", "店計", "大丸", "心斎橋店"], "入店 客数(前年比)", None),
        "大丸松坂屋 店計 売上高(前年比)": (["連結", "百貨店", "店計"], "売上高(前年比)", "総額"),
        "大丸松坂屋百貨店 商品 売上高(前年比)": (["連結", "百貨店", "うち商品"], "売上高(前年比)", "総額"),
        "総額 連結売上高(前年比)": (["連結"], "総額 連結売上高(前年比)", "総額"),
    }
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got != want:
        _fail(f"tree rows: {got!r}")
    t = tree_payload("99990", els, trees)
    if t["tree_metrics"] != ["総額", "IFRS"]:
        _fail(f"tree_metrics: {t['tree_metrics']!r}")
    dept = t["tree"][0]["children"][0]
    daimaru = dept["children"][0]["children"][0]
    if not daimaru["heading"] or daimaru["elements"] or daimaru["children"][0]["name"] != "心斎橋店":
        _fail(f"heading node: {daimaru!r}")
    if not dept["children"][1]["part"] or dept["children"][0]["part"]:
        _fail("part flag")
    if tree_payload("72030", els, trees) is not None or assign_tree_rows("72030", els, trees):
        _fail("issuer without tree must be untouched")
    # PPIH: 国内リテール ▸ ディスカウント / UNY ▸ 既存店売上高の商品別（PDF の表）。ロビン・フッドは UNY の内数、Olympic は木の外
    from app.monthly.tree import load_monthly_trees

    ppih = assign_tree_rows("75320", [
        "国内リテール 既存 売上高(前年比)",
        "ディスカウント 既存 売上高(前年比)",
        "ディスカウント 家電製品 既存 売上高(前年比)",
        "UNY 衣料品 既存 売上高(前年比)",
        "ロビンフッド 店舗数",
        "Olympic 店舗数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["metric"]) for k, v in ppih.items()}
    want = {
        "国内リテール 既存 売上高(前年比)": (["国内リテール"], "既存 売上高"),
        "ディスカウント 既存 売上高(前年比)": (["国内リテール", "ディスカウント"], "既存 売上高"),
        "ディスカウント 家電製品 既存 売上高(前年比)": (["国内リテール", "ディスカウント", "家電製品"], "既存 売上高"),
        "UNY 衣料品 既存 売上高(前年比)": (["国内リテール", "UNY", "衣料品"], "既存 売上高"),
        "ロビンフッド 店舗数": (["国内リテール", "UNY", "ロビン・フッド"], "全店 店舗数"),
    }
    if got != want:
        _fail(f"PPIH tree rows: {got!r}")
    # エー・ピーHD: 店舗数 合計 ▸ 国内（全店 店舗数）▸ 事業 / 海外 ▸ 直営 / FC / 中食。ライセンスは合計の外の別の指標
    ap = assign_tree_rows("31750", [
        "店舗数",
        "全店 店舗数",
        "日本 居酒屋事業 店舗数",
        "海外 FC 店舗数",
        "ライセンス 店舗数",
        "ライセンス じとっこ 店舗数",
        "直営",
    ], load_monthly_trees())
    got = {k: (v["path"], v["metric"]) for k, v in ap.items()}
    want = {
        "店舗数": (["全体"], "店舗数"),
        "全店 店舗数": (["全体", "国内"], "店舗数"),
        "日本 居酒屋事業 店舗数": (["全体", "国内", "居酒屋事業"], "店舗数"),
        "海外 FC 店舗数": (["全体", "海外", "FC"], "店舗数"),
        "ライセンス 店舗数": (["全体"], "ライセンス 店舗数"),
        "ライセンス じとっこ 店舗数": (["全体", "じとっこ"], "ライセンス 店舗数"),
        "直営": (["全体"], "直営店合計"),
    }
    if got != want:
        _fail(f"31750 tree rows: {got!r}")
    # マツキヨココカラ: グループ調剤は グループ 店舗数 の内数。マツモトキヨシ 既存はグループの兄弟のまま
    mk = assign_tree_rows("30880", [
        "グループ 店舗数",
        "マツモトキヨシグループ 店舗数",
        "ココカラファイングループ 店舗数",
        "アンドカンパニー 店舗数",
        "グループ調剤 店舗数",
        "マツモトキヨシ 既存 売上高(前年比)",
        "マツモトキヨシグループ 既存 売上高(前年比)",
        "全店 売上高 ドラッグ(前年比)",
        "マツモトキヨシグループ 全店 売上高 ドラッグ(前年比)",
        "グループ 出店",
        "グループ 子会社化等",
        "マツモトキヨシグループ FC 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in mk.items()}
    want = {
        "グループ 店舗数": (["全体"], "グループ 店舗数", "店舗数"),
        "マツモトキヨシグループ 店舗数": (["全体", "マツモトキヨシグループ"], "店舗数", "店舗数"),
        "ココカラファイングループ 店舗数": (["全体", "ココカラファイングループ"], "店舗数", "店舗数"),
        "アンドカンパニー 店舗数": (["全体", "アンドカンパニー"], "店舗数", "店舗数"),
        "グループ調剤 店舗数": (["全体", "グループ調剤"], "店舗数", "店舗数"),
        "マツモトキヨシ 既存 売上高(前年比)": (["全体", "マツモトキヨシ"], "既存 売上高(前年比)", "既存 売上高"),
        "マツモトキヨシグループ 既存 売上高(前年比)": (["全体", "マツモトキヨシグループ"], "既存 売上高(前年比)", "既存 売上高"),
        "全店 売上高 ドラッグ(前年比)": (["全体", "ドラッグ"], "全店 売上高 ドラッグ(前年比)", "全店 売上高"),
        "マツモトキヨシグループ 全店 売上高 ドラッグ(前年比)": (
            ["全体", "マツモトキヨシグループ", "ドラッグ"], "全店 売上高 ドラッグ(前年比)", "全店 売上高",
        ),
        "グループ 出店": (["全体"], "グループ 出店", "出店"),
        "グループ 子会社化等": (["全体"], "グループ 子会社化等", "子会社化等"),
        # FC は全店の内訳ではない別表（FC 単月売上高）。全店 を付けない
        "マツモトキヨシグループ FC 売上高(前年比)": (
            ["全体", "マツモトキヨシグループ"], "FC 売上高(前年比)", "FC 売上高",
        ),
    }
    if got != want:
        _fail(f"30880 tree rows: {got!r}")
    mk_tree = tree_payload("30880", list(want), load_monthly_trees())
    # 資料の並び: 既存 → FC → 全店、出店 → 退店 → 子会社化等 → 店舗数
    mk_metrics = mk_tree["tree_metrics"]
    if mk_metrics[mk_metrics.index("既存 客単価") + 1] != "FC 売上高" or mk_metrics[-4:] != [
        "出店", "退店", "子会社化等", "店舗数",
    ]:
        _fail(f"30880 tree_metrics: {mk_metrics!r}")
    mk_kids = {c["name"]: c for c in mk_tree["tree"][0]["children"]}
    if not mk_kids["グループ調剤"]["part"] or mk_kids["マツモトキヨシグループ"]["part"] or mk_kids["アンドカンパニー"]["part"]:
        _fail(f"30880 part: {mk_kids!r}")
    # すかいらーく: 転換準備は計に含まない参考値（ライセンスと同じ別指標）。海外は計の子
    sk = assign_tree_rows("31970", [
        "店舗数",
        "ガスト 店舗数",
        "海外 店舗数",
        "その他 店舗数",
        "転換準備による一時閉店中 店舗数",
        "ステーキガスト 店舗数",
        "リモデル",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in sk.items()}
    want = {
        "店舗数": (["全体"], "店舗数", "店舗数"),
        "ガスト 店舗数": (["全体", "ガスト"], "店舗数", "店舗数"),
        "海外 店舗数": (["全体", "海外"], "店舗数", "店舗数"),
        "その他 店舗数": (["全体", "その他"], "店舗数", "店舗数"),
        "転換準備による一時閉店中 店舗数": (["全体"], "転換準備による一時閉店中 店舗数", "転換準備による一時閉店中 店舗数"),
        "ステーキガスト 店舗数": (["全体", "ステーキガスト"], "店舗数", "店舗数"),
        "リモデル": (["全体"], "リモデル", "リモデル"),
    }
    if got != want:
        _fail(f"31970 tree rows: {got!r}")
    sk_tree = tree_payload("31970", list(want), load_monthly_trees())
    if any(c["name"] == "転換準備による一時閉店中" for c in sk_tree["tree"][0]["children"]):
        _fail("31970 転換準備 must not be a summing child")
    if "転換準備による一時閉店中 店舗数" not in sk_tree["tree"][0]["elements"]:
        _fail(f"31970 root elements: {sk_tree['tree'][0]['elements']!r}")
    # ユニシア: 店舗数は串カツ田中G / ピソラG。ブランド別は串カツ田中G 直営の内訳。当年の残り 全店 店舗数 も直営
    un = assign_tree_rows("35470", [
        "串カツ田中G 店舗数",
        "串カツ田中G 直営 店舗数",
        "串カツ田中G FC 店舗数",
        "串カツ田中 店舗数",
        "鳥玉 店舗数",
        "全店 店舗数",
        "ピソラG 店舗数",
        "ピソラG 直営 店舗数",
        "串カツ田中G 既存 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in un.items()}
    want = {
        "串カツ田中G 店舗数": (["全体", "串カツ田中G"], "店舗数", "店舗数"),
        "串カツ田中G 直営 店舗数": (["全体", "串カツ田中G", "直営"], "店舗数", "店舗数"),
        "串カツ田中G FC 店舗数": (["全体", "串カツ田中G", "FC"], "店舗数", "店舗数"),
        "串カツ田中 店舗数": (["全体", "串カツ田中G", "直営", "串カツ田中"], "店舗数", "店舗数"),
        "鳥玉 店舗数": (["全体", "串カツ田中G", "直営", "鳥玉"], "店舗数", "店舗数"),
        "全店 店舗数": (["全体", "串カツ田中G", "直営"], "全店 店舗数", "店舗数"),
        "ピソラG 店舗数": (["全体", "ピソラG"], "店舗数", "店舗数"),
        "ピソラG 直営 店舗数": (["全体", "ピソラG", "直営"], "店舗数", "店舗数"),
        "串カツ田中G 既存 売上高(前年比)": (["全体", "串カツ田中G"], "既存 売上高(前年比)", "既存 売上高"),
    }
    if got != want:
        _fail(f"35470 tree rows: {got!r}")
    # 当年店舗数 → 全店 店舗数 → 串カツ田中G 直営（#447）。既存 DB の 全店 店舗数 も同じ行へ
    got_alias = apply_issuer_aliases(
        "35470", canonicalize_element_label("当年 全店 店舗数", "number"), year=2025,
    )
    if got_alias != "串カツ田中G 直営 店舗数":
        _fail(f"35470 当年 全店 店舗数 alias: {got_alias!r}")
    # パーク24: 総駐車場数 ▸ 直営 / 管理受託。内併設とタイムズカー専用車両は part。開発は件数と別指標
    pk = assign_tree_rows("46660", [
        "駐車場 件数",
        "駐車場 件数 直営",
        "タイムズパーキング 件数 直営",
        "タイムズパーキング 件数",
        "タイムズパーキング スタンダード 件数",
        "タイムズパーキング 売上高",
        "タイムズパーキング スタンダード 売上高",
        "月極駐車場 内併設 件数 直営",
        "管理受託 駐車場 件数",
        "管理受託 時間貸駐車場 件数",
        "管理受託 月極駐車場 内併設 件数",
        "タイムズパーキング 開発 件数",
        "タイムズパーキング 開発 スタンダード 件数",
        "タイムズカー 車両",
        "タイムズカー 専用車両",
        "タイムズパーキング 調整額 売上高",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in pk.items()}
    want = {
        "駐車場 件数": (["全体", "駐車場"], "件数", "件数"),
        "駐車場 件数 直営": (["全体", "駐車場", "直営"], "駐車場 件数 直営", "件数"),
        "タイムズパーキング 件数 直営": (["全体", "駐車場", "直営", "タイムズパーキング"], "件数 直営", "件数"),
        "タイムズパーキング 件数": (["全体", "駐車場", "直営", "タイムズパーキング"], "件数", "件数"),
        "タイムズパーキング スタンダード 件数": (
            ["全体", "駐車場", "直営", "タイムズパーキング", "スタンダード"], "件数", "件数",
        ),
        "タイムズパーキング 売上高": (["全体", "駐車場", "直営", "タイムズパーキング"], "売上高", "売上高"),
        "タイムズパーキング スタンダード 売上高": (
            ["全体", "駐車場", "直営", "タイムズパーキング", "スタンダード"], "売上高", "売上高",
        ),
        "月極駐車場 内併設 件数 直営": (
            ["全体", "駐車場", "直営", "月極", "内併設"], "件数 直営", "件数",
        ),
        "管理受託 駐車場 件数": (["全体", "駐車場", "管理受託"], "件数", "件数"),
        "管理受託 時間貸駐車場 件数": (["全体", "駐車場", "管理受託", "時間貸"], "件数", "件数"),
        "管理受託 月極駐車場 内併設 件数": (
            ["全体", "駐車場", "管理受託", "月極", "内併設"], "件数", "件数",
        ),
        "タイムズパーキング 開発 件数": (
            ["全体", "駐車場", "直営", "タイムズパーキング", "開発"],
            "タイムズパーキング 開発 件数", "タイムズパーキング 開発 件数",
        ),
        "タイムズパーキング 開発 スタンダード 件数": (
            ["全体", "駐車場", "直営", "タイムズパーキング", "開発", "スタンダード"],
            "タイムズパーキング 開発 スタンダード 件数", "タイムズパーキング 開発 件数",
        ),
        "タイムズカー 車両": (["全体", "タイムズカー"], "車両", "車両"),
        "タイムズカー 専用車両": (["全体", "タイムズカー", "専用車両"], "タイムズカー 専用車両", "車両"),
        "タイムズパーキング 調整額 売上高": (
            ["全体", "駐車場", "直営", "タイムズパーキング", "調整額"], "売上高", "売上高",
        ),
    }
    if got != want:
        _fail(f"46660 tree rows: {got!r}")
    pk_tree = tree_payload("46660", list(want), load_monthly_trees())
    pk_park = {c["name"]: c for c in pk_tree["tree"][0]["children"]}["駐車場"]
    pk_chiku = {c["name"]: c for c in pk_park["children"]}
    pk_direct = {c["name"]: c for c in pk_chiku["直営"]["children"]}
    pk_getsuji = {c["name"]: c for c in pk_direct["月極"]["children"]}
    pk_kanri = {c["name"]: c for c in pk_chiku["管理受託"]["children"]}
    pk_kanri_getsuji = {c["name"]: c for c in pk_kanri["月極"]["children"]}
    pk_car = {c["name"]: c for c in pk_tree["tree"][0]["children"]}["タイムズカー"]
    if (
        not pk_getsuji["内併設"]["part"]
        or not pk_kanri_getsuji["内併設"]["part"]
        or not {c["name"]: c for c in pk_car["children"]}["専用車両"]["part"]
        or pk_chiku["直営"]["part"]
        or pk_direct["タイムズパーキング"]["part"]
    ):
        _fail(f"46660 part: {pk_tree!r}")
    if any(c["name"] == "開発" for c in pk_tree["tree"][0]["children"]):
        _fail("46660 開発 must not be a root sibling")
    # 識学: プラットフォーム売上は会社の行。契約社数の子。alias は置かない
    sg = assign_tree_rows("70490", [
        "プラットフォーム 売上高",
        "プラットフォーム 売上高(前年比)",
        "契約社数",
        "識学基本サービス 契約社数",
        "識学基本サービスライト 契約社数",
        "識学クラウド 契約社数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in sg.items()}
    want = {
        "プラットフォーム 売上高": (["全体"], "プラットフォーム 売上高", "売上高"),
        "プラットフォーム 売上高(前年比)": (["全体"], "プラットフォーム 売上高(前年比)", "売上高"),
        "契約社数": (["全体"], "契約社数", "契約社数"),
        "識学基本サービス 契約社数": (["全体", "識学基本サービス"], "契約社数", "契約社数"),
        "識学基本サービスライト 契約社数": (["全体", "識学基本サービスライト"], "契約社数", "契約社数"),
        "識学クラウド 契約社数": (["全体", "識学クラウド"], "契約社数", "契約社数"),
    }
    if got != want:
        _fail(f"70490 tree rows: {got!r}")
    sg_tree = tree_payload("70490", list(want), load_monthly_trees())
    if any(c["name"] == "プラットフォーム" for c in sg_tree["tree"][0]["children"]):
        _fail("70490 プラットフォーム must be a company row")
    if apply_issuer_aliases("70490", "全店 売上高(前年比)", year=2026) != "全店 売上高(前年比)":
        _fail("70490 must not alias 全店 売上高(前年比)")
    # アズワン: 単体が会社の行。1日当たりは節にしない。主要3分野は part。空白なし前年比は分野の節
    ao = assign_tree_rows("74760", [
        "単体 全店 売上高",
        "単体(前年比)",
        "営業日数",
        "1日当たり 全店 売上高(前年比)",
        "主要3分野 全店 売上高",
        "主要3分野(前年比)",
        "ラボラトリー 全店 売上高",
        "ラボラトリー(前年比)",
        "ラボラトリー 1日当たり(前年比)",
        "インダストリー 全店 売上高",
        "メディカル(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in ao.items()}
    want = {
        "単体 全店 売上高": (["全体"], "単体 全店 売上高", "全店 売上高"),
        "単体(前年比)": (["全体"], "単体(前年比)", "全店 売上高"),
        "営業日数": (["全体"], "営業日数", "営業日数"),
        "1日当たり 全店 売上高(前年比)": (["全体"], "1日当たり 全店 売上高(前年比)", "1日当たり 売上高"),
        "主要3分野 全店 売上高": (["全体", "主要3分野"], "全店 売上高", "全店 売上高"),
        "主要3分野(前年比)": (["全体", "主要3分野"], "主要3分野(前年比)", "全店 売上高"),
        "ラボラトリー 全店 売上高": (["全体", "主要3分野", "ラボラトリー"], "全店 売上高", "全店 売上高"),
        "ラボラトリー(前年比)": (["全体", "主要3分野", "ラボラトリー"], "ラボラトリー(前年比)", "全店 売上高"),
        "ラボラトリー 1日当たり(前年比)": (
            ["全体", "主要3分野", "ラボラトリー"], "1日当たり(前年比)", "1日当たり 売上高",
        ),
        "インダストリー 全店 売上高": (["全体", "主要3分野", "インダストリー"], "全店 売上高", "全店 売上高"),
        "メディカル(前年比)": (["全体", "主要3分野", "メディカル"], "メディカル(前年比)", "全店 売上高"),
    }
    if got != want:
        _fail(f"74760 tree rows: {got!r}")
    ao_tree = tree_payload("74760", list(want), load_monthly_trees())
    ao_kids = {c["name"]: c for c in ao_tree["tree"][0]["children"]}
    if not ao_kids["主要3分野"]["part"]:
        _fail(f"74760 主要3分野 must be part: {ao_kids!r}")
    if any(c["name"] == "1日当たり" for c in ao_tree["tree"][0]["children"]):
        _fail("74760 1日当たり must not be a section")
    if any(c["name"] == "単体" for c in ao_tree["tree"][0]["children"]):
        _fail("74760 単体 must be a company row")
    if any(c["part"] for c in ao_kids["主要3分野"]["children"]):
        _fail("74760 3分野 must sum to 主要3分野")
    # ピープル: 全体 ▸ 国内販売 ▸ 旧カテゴリ、海外販売。裸の前年比は各カテゴリの節（#480）
    people_rows = [
        "全店 売上高",
        "全店 売上高(前年比)",
        "国内販売 全店 売上高",
        "国内販売 全店 売上高(前年比)",
        "海外販売 全店 売上高",
        "海外販売 全店 売上高(前年比)",
        "乳児・知育玩具 全店 売上高",
        "乳児・知育玩具(前年比)",
        "乳児・知育・構成玩具 全店 売上高",
        "乳児・知育・構成玩具 全店 売上高(前年比)",
        "メイキングトイ 全店 売上高",
        "メイキングトイ 全店 売上高(前年比)",
        "メイキングトイ(前年比)",
        "屋内遊具・乗り物 全店 売上高",
        "屋内遊具・乗り物(前年比)",
        "その他 全店 売上高",
        "その他 全店 売上高(前年比)",
        "その他(前年比)",
        "海外販売・ロイヤリティ 全店 売上高",
        "海外販売・ロイヤリティ 全店 売上高(前年比)",
    ]
    people = assign_tree_rows("78650", people_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in people.items()}
    want = {
        "全店 売上高": (["全体"], "全店 売上高", "全店 売上高"),
        "全店 売上高(前年比)": (["全体"], "全店 売上高(前年比)", "全店 売上高"),
        "国内販売 全店 売上高": (["全体", "国内販売"], "全店 売上高", "全店 売上高"),
        "国内販売 全店 売上高(前年比)": (["全体", "国内販売"], "全店 売上高(前年比)", "全店 売上高"),
        "海外販売 全店 売上高": (["全体", "海外販売"], "全店 売上高", "全店 売上高"),
        "海外販売 全店 売上高(前年比)": (["全体", "海外販売"], "全店 売上高(前年比)", "全店 売上高"),
        "乳児・知育玩具 全店 売上高": (["全体", "国内販売", "乳児・知育玩具"], "全店 売上高", "全店 売上高"),
        "乳児・知育玩具(前年比)": (
            ["全体", "国内販売", "乳児・知育玩具"], "乳児・知育玩具(前年比)", "全店 売上高"),
        "乳児・知育・構成玩具 全店 売上高": (
            ["全体", "国内販売", "乳児・知育玩具"], "乳児・知育・構成玩具 全店 売上高", "全店 売上高"),
        "乳児・知育・構成玩具 全店 売上高(前年比)": (
            ["全体", "国内販売", "乳児・知育玩具"], "乳児・知育・構成玩具 全店 売上高(前年比)", "全店 売上高"),
        "メイキングトイ 全店 売上高": (["全体", "国内販売", "メイキングトイ"], "全店 売上高", "全店 売上高"),
        "メイキングトイ 全店 売上高(前年比)": (
            ["全体", "国内販売", "メイキングトイ"], "全店 売上高(前年比)", "全店 売上高"),
        "メイキングトイ(前年比)": (
            ["全体", "国内販売", "メイキングトイ"], "メイキングトイ(前年比)", "全店 売上高"),
        "屋内遊具・乗り物 全店 売上高": (
            ["全体", "国内販売", "屋内遊具・乗り物"], "全店 売上高", "全店 売上高"),
        "屋内遊具・乗り物(前年比)": (
            ["全体", "国内販売", "屋内遊具・乗り物"], "屋内遊具・乗り物(前年比)", "全店 売上高"),
        "その他 全店 売上高": (["全体", "国内販売", "その他"], "全店 売上高", "全店 売上高"),
        "その他 全店 売上高(前年比)": (["全体", "国内販売", "その他"], "全店 売上高(前年比)", "全店 売上高"),
        "その他(前年比)": (["全体", "国内販売", "その他"], "その他(前年比)", "全店 売上高"),
        "海外販売・ロイヤリティ 全店 売上高": (
            ["全体", "海外販売"], "海外販売・ロイヤリティ 全店 売上高", "全店 売上高"),
        "海外販売・ロイヤリティ 全店 売上高(前年比)": (
            ["全体", "海外販売"], "海外販売・ロイヤリティ 全店 売上高(前年比)", "全店 売上高"),
    }
    if got != want:
        _fail(f"78650 tree rows: {got!r}")
    people_tree = tree_payload("78650", people_rows, load_monthly_trees())
    if people_tree is None or people_tree.get("tree_auto"):
        _fail("78650 must use the config tree")
    if people_tree["tree_metrics"] != ["全店 売上高"]:
        _fail(f"78650 metrics: {people_tree!r}")
    people_root = people_tree["tree"][0]
    if people_root["elements"] != ["全店 売上高", "全店 売上高(前年比)"]:
        _fail(f"78650 company rows: {people_root!r}")
    if [n["name"] for n in people_root["children"]] != ["国内販売", "海外販売"]:
        _fail(f"78650 section order: {people_root['children']!r}")
    people_dom = people_root["children"][0]
    if [n["name"] for n in people_dom["children"]] != [
        "乳児・知育玩具", "メイキングトイ", "屋内遊具・乗り物", "その他",
    ]:
        _fail(f"78650 国内販売 children: {people_dom!r}")
    if people_dom["part"] or any(c["part"] for c in people_dom["children"]):
        _fail("78650 old categories sum to 国内販売")
    # 共立メンテナンス: 寮事業 ▸ 学生寮 / 社員寮 / ドミール / 受託寮、ホテル事業 ▸ ドーミーイン / リゾート
    km = assign_tree_rows("96160", [
        "売上高",
        "寮事業 売上高",
        "学生寮 売上高(前年比)",
        "ホテル事業 売上高",
        "リゾート事業 売上高",
        "フーズ事業 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in km.items()}
    want = {
        "売上高": (["全体"], "売上高", "売上高"),
        "寮事業 売上高": (["全体", "寮事業"], "売上高", "売上高"),
        "学生寮 売上高(前年比)": (["全体", "寮事業", "学生寮"], "売上高(前年比)", "売上高"),
        "ホテル事業 売上高": (["全体", "ホテル事業"], "売上高", "売上高"),
        "リゾート事業 売上高": (["全体", "ホテル事業", "リゾート事業"], "売上高", "売上高"),
        "フーズ事業 売上高(前年比)": (["全体", "フーズ事業"], "売上高(前年比)", "売上高"),
    }
    if got != want:
        _fail(f"96160 tree rows: {got!r}")
    # アスモ: 会社別の前年比と拠点数は各社の節。資料に範囲の語が無いので 全店 は付けない
    am = assign_tree_rows("26540", [
        "売上高",
        "売上高(前年比)",
        "アスモフードサービス 売上高",
        "アスモフードサービス 売上高(前年比)",
        "アスモフードサービス 受託施設数",
        "アスモ介護サービス 訪問介護事業所数",
        "アスモ介護サービス 有料老人ホーム数",
        "TrustGrowth 売上高",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in am.items()}
    want = {
        "売上高": (["全体"], "売上高", "売上高"),
        "売上高(前年比)": (["全体"], "売上高(前年比)", "売上高"),
        "アスモフードサービス 売上高": (["全体", "アスモフードサービス"], "売上高", "売上高"),
        "アスモフードサービス 売上高(前年比)": (["全体", "アスモフードサービス"], "売上高(前年比)", "売上高"),
        "アスモフードサービス 受託施設数": (["全体", "アスモフードサービス"], "受託施設数", "受託施設数"),
        "アスモ介護サービス 訪問介護事業所数": (["全体", "アスモ介護サービス"], "訪問介護事業所数", "訪問介護事業所数"),
        "アスモ介護サービス 有料老人ホーム数": (["全体", "アスモ介護サービス"], "有料老人ホーム数", "有料老人ホーム数"),
        "TrustGrowth 売上高": (["全体", "TrustGrowth"], "売上高", "売上高"),
    }
    if got != want:
        _fail(f"26540 tree rows: {got!r}")
    # ＨＡＮＡＴＯＵＲ ＪＡＰＡＮ: ホテル ▸ 札幌 / その他3棟（表の字下げ）。バス事業の指標は 運行 売上高
    ht = assign_tree_rows("65610", [
        "旅行事業 取扱高",
        "ホテル 客室収入",
        "ホテル 札幌 客室収入",
        "ホテル その他3棟 客室収入",
        "ホテル 客室収入(前年比)",
        "バス事業 運行 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in ht.items()}
    want = {
        "旅行事業 取扱高": (["全体", "旅行事業"], "取扱高", "取扱高"),
        "ホテル 客室収入": (["全体", "ホテル"], "客室収入", "客室収入"),
        "ホテル 札幌 客室収入": (["全体", "ホテル", "札幌"], "客室収入", "客室収入"),
        "ホテル その他3棟 客室収入": (["全体", "ホテル", "その他3棟"], "客室収入", "客室収入"),
        "ホテル 客室収入(前年比)": (["全体", "ホテル"], "客室収入(前年比)", "客室収入"),
        "バス事業 運行 売上高(前年比)": (["全体", "バス事業"], "運行 売上高(前年比)", "運行 売上高"),
    }
    if got != want:
        _fail(f"65610 tree rows: {got!r}")
    # Ｅｎｊｉｎ: 成約高は PR事業 の指標（見出し「PR 事業成約高」）。2026年5月までの旧セグメントも根の子
    ej = assign_tree_rows("73700", [
        "グループ 売上高",
        "PR事業 売上高",
        "PR事業 成約高",
        "PR事業 成約高(前年比)",
        "PRコンサルティングサービス 売上高",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in ej.items()}
    want = {
        "グループ 売上高": (["全体"], "売上高", "売上高"),
        "PR事業 売上高": (["全体", "PR事業"], "売上高", "売上高"),
        "PR事業 成約高": (["全体", "PR事業"], "成約高", "成約高"),
        "PR事業 成約高(前年比)": (["全体", "PR事業"], "成約高(前年比)", "成約高"),
        "PRコンサルティングサービス 売上高": (["全体", "PRコンサルティングサービス"], "売上高", "売上高"),
    }
    if got != want:
        _fail(f"73700 tree rows: {got!r}")
    # 高島屋: 全体 ▸ 髙島屋（計）▸ 髙島屋各店 ▸ 各店。法人・クロスメディアは 髙島屋 の下、子会社は全体の下。商品別は内数。会社全体の客数は根
    tk = assign_tree_rows("82330", [
        "髙島屋および国内百貨店子会社計 売上高(前年比)",
        "髙島屋計 売上高(前年比)",
        "髙島屋各店計 入店 客数(前年比)",
        "大阪店 売上高(前年比)",
        "法人事業 売上高(前年比)",
        "岡山髙島屋 入店 客数(前年比)",
        "入店 客数(前年比)",
        "入店 既存 客数(前年比)",
        "髙島屋各店および国内百貨店子会社 既存 売上高(前年比)",
        "商品別 髙島屋 紳士服・洋品 売上高(前年比)",
        "商品別 髙島屋および国内百貨店子会社 その他(雑貨) 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["metric"]) for k, v in tk.items()}
    want = {
        "髙島屋および国内百貨店子会社計 売上高(前年比)": (["全体"], "売上高"),
        "髙島屋計 売上高(前年比)": (["全体", "髙島屋"], "売上高"),
        "髙島屋各店計 入店 客数(前年比)": (["全体", "髙島屋", "髙島屋各店"], "入店客数"),
        "大阪店 売上高(前年比)": (["全体", "髙島屋", "髙島屋各店", "大阪店"], "売上高"),
        "法人事業 売上高(前年比)": (["全体", "髙島屋", "法人事業"], "売上高"),
        "岡山髙島屋 入店 客数(前年比)": (["全体", "岡山髙島屋"], "入店客数"),
        "入店 客数(前年比)": (["全体"], "入店客数"),
        "入店 既存 客数(前年比)": (["全体"], "既存 入店客数"),
        "髙島屋各店および国内百貨店子会社 既存 売上高(前年比)": (["全体", "うち各店および国内百貨店子会社"], "既存 売上高"),
        "商品別 髙島屋 紳士服・洋品 売上高(前年比)": (["全体", "髙島屋", "商品別", "衣料品", "紳士服・洋品"], "売上高"),
        "商品別 髙島屋および国内百貨店子会社 その他(雑貨) 売上高(前年比)": (["全体", "商品別", "雑貨", "その他(雑貨)"], "売上高"),
    }
    if got != want:
        _fail(f"82330 tree rows: {got!r}")
    # 光フード: 店舗数は 大黒・魚椿 ▸ 直営 / 業務委託 / FC とその他。売上高・客数・客単価は 大黒・魚椿（直営店）
    hikari = assign_tree_rows("138A0", [
        "店舗数",
        "大黒・魚椿 店舗数",
        "大黒・魚椿 直営 店舗数",
        "大黒・魚椿 業務委託店 店舗数",
        "大黒・魚椿 FC 店舗数",
        "その他 店舗数",
        "全店 売上高(前年比)",
        "既存 売上高(前年比)",
        "全店 客数(前年比)",
        "既存 客単価(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in hikari.items()}
    want = {
        "店舗数": (["全体"], "店舗数", "店舗数"),
        "大黒・魚椿 店舗数": (["全体", "大黒・魚椿"], "店舗数", "店舗数"),
        "大黒・魚椿 直営 店舗数": (["全体", "大黒・魚椿", "直営"], "店舗数", "店舗数"),
        "大黒・魚椿 業務委託店 店舗数": (["全体", "大黒・魚椿", "業務委託"], "店舗数", "店舗数"),
        "大黒・魚椿 FC 店舗数": (["全体", "大黒・魚椿", "FC"], "店舗数", "店舗数"),
        "その他 店舗数": (["全体", "その他"], "店舗数", "店舗数"),
        "全店 売上高(前年比)": (["全体", "大黒・魚椿"], "全店 売上高(前年比)", "全店 売上高"),
        "既存 売上高(前年比)": (["全体", "大黒・魚椿"], "既存 売上高(前年比)", "既存 売上高"),
        "全店 客数(前年比)": (["全体", "大黒・魚椿"], "全店 客数(前年比)", "全店 客数"),
        "既存 客単価(前年比)": (["全体", "大黒・魚椿"], "既存 客単価(前年比)", "既存 客単価"),
    }
    if got != want:
        _fail(f"138A0 tree rows: {got!r}")
    # フィットイージー: 地域別は全店 店舗数の内訳。会員数 既存は会員数の内数。増減は別指標
    fit_rows = [
        "会員数",
        "会員数(前年比)",
        "会員数 既存",
        "会員数 既存(前年比)",
        "全店 店舗数",
        "増減 全店 店舗数",
        "北海道 店舗数",
        "東北 店舗数",
        "関東 店舗数",
        "甲信越 店舗数",
        "北陸 店舗数",
        "東海 店舗数",
        "関西 店舗数",
        "中国 店舗数",
        "四国 店舗数",
        "九州沖縄 店舗数",
    ]
    fit = assign_tree_rows("212A0", fit_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in fit.items()}
    want = {
        "会員数": (["全体"], "会員数", "会員数"),
        "会員数(前年比)": (["全体"], "会員数(前年比)", "会員数"),
        "会員数 既存": (["全体", "既存"], "既存", "会員数"),
        "会員数 既存(前年比)": (["全体", "既存"], "既存(前年比)", "会員数"),
        "全店 店舗数": (["全体"], "全店 店舗数", "全店 店舗数"),
        "増減 全店 店舗数": (["全体"], "増減 全店 店舗数", "増減 全店 店舗数"),
        "北海道 店舗数": (["全体", "北海道"], "店舗数", "全店 店舗数"),
        "東北 店舗数": (["全体", "東北"], "店舗数", "全店 店舗数"),
        "関東 店舗数": (["全体", "関東"], "店舗数", "全店 店舗数"),
        "甲信越 店舗数": (["全体", "甲信越"], "店舗数", "全店 店舗数"),
        "北陸 店舗数": (["全体", "北陸"], "店舗数", "全店 店舗数"),
        "東海 店舗数": (["全体", "東海"], "店舗数", "全店 店舗数"),
        "関西 店舗数": (["全体", "関西"], "店舗数", "全店 店舗数"),
        "中国 店舗数": (["全体", "中国"], "店舗数", "全店 店舗数"),
        "四国 店舗数": (["全体", "四国"], "店舗数", "全店 店舗数"),
        "九州沖縄 店舗数": (["全体", "九州沖縄"], "店舗数", "全店 店舗数"),
    }
    if got != want:
        _fail(f"212A0 tree rows: {got!r}")
    fit_tree = tree_payload("212A0", fit_rows, load_monthly_trees())
    if fit_tree is None or fit_tree.get("tree_auto") or fit_tree["tree_metrics"] != [
        "会員数", "全店 店舗数", "増減 全店 店舗数",
    ]:
        _fail(f"212A0 metrics: {fit_tree!r}")
    fit_kids = {c["name"]: c for c in fit_tree["tree"][0]["children"]}
    if [c["name"] for c in fit_tree["tree"][0]["children"]] != [
        "北海道", "東北", "関東", "甲信越", "北陸", "東海", "関西", "中国", "四国", "九州沖縄", "既存",
    ]:
        _fail(f"212A0 section order: {fit_tree['tree'][0]['children']!r}")
    if not fit_kids["既存"]["part"] or any(fit_kids[n]["part"] for n in ("北海道", "東海", "九州沖縄")):
        _fail(f"212A0 part: {fit_kids!r}")
    if "増減" in fit_kids:
        _fail("212A0 増減 must stay a company metric, not a group")
    # 太洋物産: 食料部 ▸ 品目、農産部 ▸ 農産品、中国開拓部 ▸ 中国関連 / 化学品、生活産業部 ▸ 豚肉
    taiyo = assign_tree_rows("99410", [
        "全店 売上高",
        "売上高(前年比)",
        "食料部計 全店 売上高",
        "食料部 全店 売上高",
        "牛肉 全店 売上高",
        "牛肉 売上高(前年比)",
        "加工食品",
        "農産品 全店 売上高",
        "中国開拓部計 全店 売上高",
        "中国関連 全店 売上高",
        "化学品 全店 売上高",
        "生活産業部 全店 売上高",
        "豚肉 全店 売上高",
        "その他畜産",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in taiyo.items()}
    want = {
        "全店 売上高": (["全体"], "全店 売上高", "売上高"),
        "売上高(前年比)": (["全体"], "売上高(前年比)", "売上高"),
        "食料部計 全店 売上高": (["全体", "食料部"], "全店 売上高", "売上高"),
        "食料部 全店 売上高": (["全体", "食料部"], "食料部 全店 売上高", "売上高"),
        "牛肉 全店 売上高": (["全体", "食料部", "牛肉"], "全店 売上高", "売上高"),
        "牛肉 売上高(前年比)": (["全体", "食料部", "牛肉"], "売上高(前年比)", "売上高"),
        "加工食品": (["全体", "食料部", "加工食品"], "加工食品", "売上高"),
        "農産品 全店 売上高": (["全体", "農産部", "農産品"], "全店 売上高", "売上高"),
        "中国開拓部計 全店 売上高": (["全体", "中国開拓部"], "全店 売上高", "売上高"),
        "中国関連 全店 売上高": (["全体", "中国開拓部", "中国関連"], "全店 売上高", "売上高"),
        "化学品 全店 売上高": (["全体", "中国開拓部", "化学品"], "全店 売上高", "売上高"),
        "生活産業部 全店 売上高": (["全体", "生活産業部"], "全店 売上高", "売上高"),
        "豚肉 全店 売上高": (["全体", "生活産業部", "豚肉"], "全店 売上高", "売上高"),
        "その他畜産": (["全体", "食料部", "その他畜産"], "その他畜産", "売上高"),
    }
    if got != want:
        _fail(f"99410 tree rows: {got!r}")
    taiyo_tree = tree_payload("99410", [
        "全店 売上高", "農産品 全店 売上高", "食料部計 全店 売上高",
    ], load_monthly_trees())
    if taiyo_tree is None:
        _fail("99410 tree payload")
    if [n["name"] for n in taiyo_tree["tree"][0]["children"]] != [
        "食料部", "農産部", "中国開拓部", "生活産業部",
    ]:
        _fail(f"99410 dept order: {taiyo_tree['tree'][0]['children']!r}")
    if not taiyo_tree["tree"][0]["children"][1]["heading"]:
        _fail("99410 農産部 must be a heading")
    # 神戸物産: 総店舗数 ▸ 直営 / FC ▸ 直轄 / 地方エリア。出荷実績は直轄エリアの節、全国は根
    kb = assign_tree_rows("30380", [
        "店舗数",
        "FC 店舗数",
        "直営 店舗数",
        "直轄エリア 店舗数",
        "地方エリア 出店",
        "直轄エリア 仕入れ 既存 売上高(前年比)",
        "全国 仕入れ 全店 売上高(前年比)",
        "経常利益(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in kb.items()}
    want = {
        "店舗数": (["全体"], "店舗数", "店舗数"),
        "FC 店舗数": (["全体", "FC"], "店舗数", "店舗数"),
        "直営 店舗数": (["全体", "直営"], "店舗数", "店舗数"),
        "直轄エリア 店舗数": (["全体", "FC", "直轄"], "店舗数", "店舗数"),
        "地方エリア 出店": (["全体", "FC", "地方エリア"], "出店", "出店"),
        "直轄エリア 仕入れ 既存 売上高(前年比)": (
            ["全体", "FC", "直轄"], "仕入れ 既存 売上高(前年比)", "仕入れ 既存 売上高"),
        "全国 仕入れ 全店 売上高(前年比)": (["全体"], "全国 仕入れ 全店 売上高(前年比)", "仕入れ 全店 売上高"),
        "経常利益(前年比)": (["全体"], "経常利益(前年比)", "経常利益"),
    }
    if got != want:
        _fail(f"30380 tree rows: {got!r}")
    # ＫｅｅＰｅｒ技研: 決算のセグメント キーパーLABO運営事業（直営+FC の店舗数）▸ 直営 / FC、キーパー製品等関連事業 ▸ マーケット・海外
    kp = assign_tree_rows("60360", [
        "全社 売上高",
        "キーパーLABO運営事業 売上高",
        "キーパーLABO運営事業 直営+FC 店舗数",
        "キーパーLABO運営事業 直営 全店 店舗数",
        "キーパーLABO運営事業 直営 既存 店舗数",
        "キーパーLABO運営事業 直営 既存 売上高(前年比)",
        "キーパーLABO運営事業 直営 既存 客数(前年比)",
        "キーパーLABO運営事業 FC 売上高",
        "キーパーLABO運営事業 FC 店舗数",
        "海外 売上高",
    ], load_monthly_trees())
    got = {k: (v["path"], v["metric"]) for k, v in kp.items()}
    want = {
        "全社 売上高": (["全体"], "売上高"),
        "キーパーLABO運営事業 売上高": (["全体", "キーパーLABO運営事業"], "売上高"),
        "キーパーLABO運営事業 直営+FC 店舗数": (["全体", "キーパーLABO運営事業"], "店舗数"),
        "キーパーLABO運営事業 直営 全店 店舗数": (["全体", "キーパーLABO運営事業", "直営"], "店舗数"),
        "キーパーLABO運営事業 直営 既存 店舗数": (["全体", "キーパーLABO運営事業", "直営"], "店舗数"),
        "キーパーLABO運営事業 直営 既存 売上高(前年比)": (["全体", "キーパーLABO運営事業", "直営"], "既存 売上高"),
        "キーパーLABO運営事業 直営 既存 客数(前年比)": (["全体", "キーパーLABO運営事業", "直営"], "既存 客数"),
        "キーパーLABO運営事業 FC 売上高": (["全体", "キーパーLABO運営事業", "FC"], "売上高"),
        "キーパーLABO運営事業 FC 店舗数": (["全体", "キーパーLABO運営事業", "FC"], "店舗数"),
        "海外 売上高": (["全体", "キーパー製品等関連事業", "海外"], "売上高"),
    }
    if got != want:
        _fail(f"60360 tree rows: {got!r}")
    # 三陽商会: 全社を根の売上高。ブランド別・販路別は見出し。直営店はチャネルにしない（#470）
    sanyo_rows = [
        "全社 売上高(前年比)",
        "ブランド別 MACKINTOSH LONDON 売上高(前年比)",
        "ブランド別 MACKINTOSH PHILOSOPHY 売上高(前年比)",
        "ブランド別 Paul Stuart 売上高(前年比)",
        "ブランド別 EPOCA 売上高(前年比)",
        "ブランド別 婦人服*1 売上高(前年比)",
        "ブランド別 コーポレート*2 売上高(前年比)",
        "ブランド別 LOVELESS 売上高(前年比)",
        "販路別 百貨店 売上高(前年比)",
        "販路別 直営 売上高(前年比)",
        "販路別 EC・通販 売上高(前年比)",
        "販路別 アウトレット 売上高(前年比)",
    ]
    sy = assign_tree_rows("80110", sanyo_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in sy.items()}
    want = {
        "全社 売上高(前年比)": (["全体"], "全社 売上高(前年比)", "売上高"),
        "ブランド別 MACKINTOSH LONDON 売上高(前年比)": (
            ["全体", "ブランド別", "MACKINTOSH LONDON"], "売上高(前年比)", "売上高"),
        "ブランド別 MACKINTOSH PHILOSOPHY 売上高(前年比)": (
            ["全体", "ブランド別", "MACKINTOSH PHILOSOPHY"], "売上高(前年比)", "売上高"),
        "ブランド別 Paul Stuart 売上高(前年比)": (
            ["全体", "ブランド別", "Paul Stuart"], "売上高(前年比)", "売上高"),
        "ブランド別 EPOCA 売上高(前年比)": (
            ["全体", "ブランド別", "EPOCA"], "売上高(前年比)", "売上高"),
        "ブランド別 婦人服*1 売上高(前年比)": (
            ["全体", "ブランド別", "婦人服*1"], "売上高(前年比)", "売上高"),
        "ブランド別 コーポレート*2 売上高(前年比)": (
            ["全体", "ブランド別", "コーポレート*2"], "売上高(前年比)", "売上高"),
        "ブランド別 LOVELESS 売上高(前年比)": (
            ["全体", "ブランド別", "LOVELESS"], "売上高(前年比)", "売上高"),
        "販路別 百貨店 売上高(前年比)": (["全体", "販路別", "百貨店"], "売上高(前年比)", "売上高"),
        "販路別 直営 売上高(前年比)": (["全体", "販路別", "直営店"], "売上高(前年比)", "売上高"),
        "販路別 EC・通販 売上高(前年比)": (["全体", "販路別", "EC・通販"], "売上高(前年比)", "売上高"),
        "販路別 アウトレット 売上高(前年比)": (["全体", "販路別", "アウトレット"], "売上高(前年比)", "売上高"),
    }
    if got != want:
        _fail(f"80110 tree rows: {got!r}")
    sy_tree = tree_payload("80110", sanyo_rows, load_monthly_trees())
    if sy_tree is None or sy_tree.get("tree_metrics") != ["売上高"] or "tree_auto" in sy_tree:
        _fail(f"80110 tree payload: {sy_tree!r}")
    root = sy_tree["tree"][0]
    brands = root["children"][0]
    channels = root["children"][1]
    if (root["elements"] != ["全社 売上高(前年比)"]
            or [c["name"] for c in root["children"]] != ["ブランド別", "販路別"]
            or not brands["heading"] or not channels["heading"]
            or [c["name"] for c in brands["children"]] != [
                "MACKINTOSH LONDON", "MACKINTOSH PHILOSOPHY", "Paul Stuart", "EPOCA",
                "婦人服*1", "コーポレート*2", "LOVELESS"]
            or [c["name"] for c in channels["children"]] != ["百貨店", "直営店", "EC・通販", "アウトレット"]):
        _fail(f"80110 tree shape: {sy_tree['tree']!r}")
    # アートネイチャー: 総売上高は根。主要商品 ▸ 男性 / 女性 ▸ 新規 / リピート。
    # 資料の合計の新規は保存名が 出店 だが、リピートと並ぶ売上高の節。延べ来店は合計 ▸ 男女。
    an = assign_tree_rows("78230", [
        "総 全店 売上高(前年比)",
        "オーダーメイドウィッグ 男性(前年比)",
        "主要商品(前年比)",
        "主要商品 男性(前年比)",
        "主要商品 男性新規(前年比)",
        "主要商品 男性リピート(前年比)",
        "主要商品 女性新規(前年比)",
        "主要商品 出店(前年比)",
        "主要商品 リピート(前年比)",
        "ヘアケア商品 女性(前年比)",
        "ジュリア・オージェ(前年比)",
        "その他商品(前年比)",
        "延べ来店顧 全店 客数(前年比)",
        "延べ来店顧 男性 全店 客数(前年比)",
        "延べ来店顧 女性 全店 客数(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in an.items()}
    want = {
        "総 全店 売上高(前年比)": (["全体"], "総 全店 売上高(前年比)", "売上高(前年比)"),
        "オーダーメイドウィッグ 男性(前年比)": (
            ["全体", "オーダーメイドウィッグ", "男性"], "オーダーメイドウィッグ 男性(前年比)", "売上高(前年比)"),
        "主要商品(前年比)": (["全体", "主要商品"], "主要商品(前年比)", "売上高(前年比)"),
        "主要商品 男性(前年比)": (["全体", "主要商品", "男性"], "主要商品 男性(前年比)", "売上高(前年比)"),
        "主要商品 男性新規(前年比)": (
            ["全体", "主要商品", "男性", "新規"], "主要商品 男性新規(前年比)", "売上高(前年比)"),
        "主要商品 男性リピート(前年比)": (
            ["全体", "主要商品", "男性", "リピート"], "主要商品 男性リピート(前年比)", "売上高(前年比)"),
        "主要商品 女性新規(前年比)": (
            ["全体", "主要商品", "女性", "新規"], "主要商品 女性新規(前年比)", "売上高(前年比)"),
        "主要商品 出店(前年比)": (["全体", "主要商品", "新規"], "主要商品 出店(前年比)", "売上高(前年比)"),
        "主要商品 リピート(前年比)": (["全体", "主要商品", "リピート"], "主要商品 リピート(前年比)", "売上高(前年比)"),
        "ヘアケア商品 女性(前年比)": (
            ["全体", "ヘアケア商品", "女性"], "ヘアケア商品 女性(前年比)", "売上高(前年比)"),
        "ジュリア・オージェ(前年比)": (["全体", "ジュリア・オージェ"], "ジュリア・オージェ(前年比)", "売上高(前年比)"),
        "その他商品(前年比)": (["全体", "その他商品"], "その他商品(前年比)", "売上高(前年比)"),
        "延べ来店顧 全店 客数(前年比)": (["全体", "延べ来店顧"], "全店 客数(前年比)", "全店 客数"),
        "延べ来店顧 男性 全店 客数(前年比)": (["全体", "延べ来店顧", "男性"], "全店 客数(前年比)", "全店 客数"),
        "延べ来店顧 女性 全店 客数(前年比)": (["全体", "延べ来店顧", "女性"], "全店 客数(前年比)", "全店 客数"),
    }
    if got != want:
        _fail(f"78230 tree rows: {got!r}")
    an_tree = tree_payload("78230", list(want), load_monthly_trees())
    if an_tree is None or an_tree["tree_metrics"] != ["売上高(前年比)", "全店 客数"]:
        _fail(f"78230 metrics: {an_tree!r}")
    if "出店" in an_tree["tree_metrics"]:
        _fail("78230 主要商品の新規 must not be metric 出店")
    kids = [n["name"] for n in an_tree["tree"][0]["children"]]
    if kids[:4] != ["オーダーメイドウィッグ", "増毛", "育毛ケア・サービス", "主要商品"] or kids[-1] != "延べ来店顧":
        _fail(f"78230 child order: {kids!r}")
    shuyo = next(n for n in an_tree["tree"][0]["children"] if n["name"] == "主要商品")
    shuyo_kids = [c["name"] for c in shuyo["children"]]
    if shuyo["heading"] or shuyo_kids != ["男性", "女性", "新規", "リピート"]:
        _fail(f"78230 主要商品: {shuyo!r}")
    if not an_tree["tree"][0]["children"][0]["heading"]:
        _fail("78230 オーダーメイドウィッグ is a heading (no total row)")
    # ワタミ: 国内外食 ▸ サブウェイ除く / サブウェイ。海外は別表なので宅食の出店の子にしない
    watami = assign_tree_rows("75220", [
        "国内外食 全店 店舗数",
        "国内外食 全店 売上高(前年比)",
        "国内外食 サブウェイ除く 全店 店舗数",
        "国内外食 サブウェイ除く 既存 売上高(前年比)",
        "国内外食 サブウェイ除く 全店 客単価(前年比)",
        "国内外食 サブウェイ 全店 店舗数",
        "国内外食 サブウェイ 既存 店舗数",
        "海外 出店",
        "海外 転換出店",
        "海外 退店",
        "海外 店舗数",
        "宅食 出店",
        "宅食 退店",
        "宅食 営業所数",
        "宅食 調理済み商品お届け数（千食）",
        "直営 全店 店舗数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in watami.items()}
    want = {
        "国内外食 全店 店舗数": (["全体", "国内外食"], "全店 店舗数", "全店 店舗数"),
        "国内外食 全店 売上高(前年比)": (["全体", "国内外食"], "全店 売上高(前年比)", "全店 売上高"),
        "国内外食 サブウェイ除く 全店 店舗数": (
            ["全体", "国内外食", "サブウェイ除く"], "全店 店舗数", "全店 店舗数"),
        "国内外食 サブウェイ除く 既存 売上高(前年比)": (
            ["全体", "国内外食", "サブウェイ除く"], "既存 売上高(前年比)", "既存 売上高"),
        "国内外食 サブウェイ除く 全店 客単価(前年比)": (
            ["全体", "国内外食", "サブウェイ除く"], "全店 客単価(前年比)", "全店 客単価"),
        "国内外食 サブウェイ 全店 店舗数": (
            ["全体", "国内外食", "サブウェイ"], "全店 店舗数", "全店 店舗数"),
        "国内外食 サブウェイ 既存 店舗数": (
            ["全体", "国内外食", "サブウェイ"], "既存 店舗数", "既存 店舗数"),
        "海外 出店": (["全体", "海外"], "海外 出店", "海外 出店"),
        "海外 転換出店": (["全体", "海外"], "海外 転換出店", "海外 転換出店"),
        "海外 退店": (["全体", "海外"], "海外 退店", "海外 退店"),
        "海外 店舗数": (["全体", "海外"], "海外 店舗数", "海外 店舗数"),
        "宅食 出店": (["全体", "宅食"], "出店", "出店"),
        "宅食 退店": (["全体", "宅食"], "退店", "退店"),
        "宅食 営業所数": (["全体", "宅食"], "営業所数", "営業所数"),
        "宅食 調理済み商品お届け数（千食）": (
            ["全体", "宅食"], "調理済み商品お届け数（千食）", "調理済み商品お届け数（千食）"),
    }
    if got != want:
        _fail(f"75220 tree rows: {got!r}")
    if "直営 全店 店舗数" in watami:
        _fail("75220 直営 全店 店舗数 is outside the three tables")
    watami_tree = tree_payload("75220", list(want), load_monthly_trees())
    if watami_tree is None or watami_tree.get("tree_auto"):
        _fail("75220 must use the config tree")
    kids = watami_tree["tree"][0]["children"]
    if [n["name"] for n in kids] != ["国内外食", "海外", "宅食"]:
        _fail(f"75220 section order: {kids!r}")
    if [n["name"] for n in kids[0]["children"]] != ["サブウェイ除く", "サブウェイ"]:
        _fail(f"75220 subway must be under 国内外食: {kids[0]['children']!r}")
    if kids[1]["elements"] != ["海外 出店", "海外 転換出店", "海外 退店", "海外 店舗数"]:
        _fail(f"75220 overseas rows: {kids[1]['elements']!r}")
    # TABAYA: 小売 / ネット通販は小売＋ネット通販の子。既存店カウントは根の既存店舗数（#479）
    tabaya_rows = [
        "全社 売上高(前年比)",
        "ビジネスユニット計 売上高(前年比)",
        "小売＋ネット通販 売上高(前年比)",
        "小売 売上高(前年比)",
        "ネット通販 売上高(前年比)",
        "アウトレット その他 売上高(前年比)",
        "小売＋ネット通販 既存 売上高(前年比)",
        "小売 既存 売上高(前年比)",
        "ネット通販 既存 売上高(前年比)",
        "小売＋ネット通販 既存 客数(前年比)",
        "小売 既存 客数(前年比)",
        "ネット通販 既存 客数(前年比)",
        "小売＋ネット通販 既存 客単価(前年比)",
        "小売 既存 客単価(前年比)",
        "ネット通販 既存 客単価(前年比)",
        "小売＋ネット通販 客数(前年比)",
        "小売 客数(前年比)",
        "ネット通販 客数(前年比)",
        "小売＋ネット通販 客単価(前年比)",
        "小売 客単価(前年比)",
        "ネット通販 客単価(前年比)",
        "カウント 既存 店舗数",
        "小売 既存 店舗数",
        "ネット通販 既存 店舗数",
        "店舗数",
        "小売 店舗数",
        "ネット通販 店舗数",
        "アウトレット 店舗数",
    ]
    tabaya = assign_tree_rows("76060", tabaya_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in tabaya.items()}
    retail = ["全体", "小売＋ネット通販", "小売"]
    web = ["全体", "小売＋ネット通販", "ネット通販"]
    both = ["全体", "小売＋ネット通販"]
    want = {
        "全社 売上高(前年比)": (["全体"], "全社 売上高(前年比)", "売上高"),
        "ビジネスユニット計 売上高(前年比)": (["全体", "ビジネスユニット計"], "売上高(前年比)", "売上高"),
        "小売＋ネット通販 売上高(前年比)": (both, "売上高(前年比)", "売上高"),
        "小売 売上高(前年比)": (retail, "売上高(前年比)", "売上高"),
        "ネット通販 売上高(前年比)": (web, "売上高(前年比)", "売上高"),
        "アウトレット その他 売上高(前年比)": (["全体", "アウトレット その他"], "売上高(前年比)", "売上高"),
        "小売＋ネット通販 既存 売上高(前年比)": (both, "既存 売上高(前年比)", "既存 売上高"),
        "小売 既存 売上高(前年比)": (retail, "既存 売上高(前年比)", "既存 売上高"),
        "ネット通販 既存 売上高(前年比)": (web, "既存 売上高(前年比)", "既存 売上高"),
        "小売＋ネット通販 既存 客数(前年比)": (both, "既存 客数(前年比)", "既存 客数"),
        "小売 既存 客数(前年比)": (retail, "既存 客数(前年比)", "既存 客数"),
        "ネット通販 既存 客数(前年比)": (web, "既存 客数(前年比)", "既存 客数"),
        "小売＋ネット通販 既存 客単価(前年比)": (both, "既存 客単価(前年比)", "既存 客単価"),
        "小売 既存 客単価(前年比)": (retail, "既存 客単価(前年比)", "既存 客単価"),
        "ネット通販 既存 客単価(前年比)": (web, "既存 客単価(前年比)", "既存 客単価"),
        "小売＋ネット通販 客数(前年比)": (both, "客数(前年比)", "客数"),
        "小売 客数(前年比)": (retail, "客数(前年比)", "客数"),
        "ネット通販 客数(前年比)": (web, "客数(前年比)", "客数"),
        "小売＋ネット通販 客単価(前年比)": (both, "客単価(前年比)", "客単価"),
        "小売 客単価(前年比)": (retail, "客単価(前年比)", "客単価"),
        "ネット通販 客単価(前年比)": (web, "客単価(前年比)", "客単価"),
        "カウント 既存 店舗数": (["全体"], "既存 店舗数", "既存 店舗数"),
        "小売 既存 店舗数": (retail, "既存 店舗数", "既存 店舗数"),
        "ネット通販 既存 店舗数": (web, "既存 店舗数", "既存 店舗数"),
        "店舗数": (["全体"], "店舗数", "店舗数"),
        "小売 店舗数": (retail, "店舗数", "店舗数"),
        "ネット通販 店舗数": (web, "店舗数", "店舗数"),
        "アウトレット 店舗数": (["全体", "アウトレット"], "店舗数", "店舗数"),
    }
    if got != want:
        _fail(f"76060 tree rows: {got!r}")
    tabaya_tree = tree_payload("76060", tabaya_rows, load_monthly_trees())
    if tabaya_tree is None or tabaya_tree.get("tree_auto"):
        _fail("76060 must use the config tree")
    if tabaya_tree["tree_metrics"] != [
        "既存 売上高", "既存 客数", "既存 客単価", "既存 店舗数",
        "売上高", "客数", "客単価", "店舗数",
    ]:
        _fail(f"76060 metrics: {tabaya_tree!r}")
    tabaya_kids = tabaya_tree["tree"][0]["children"]
    if [n["name"] for n in tabaya_kids] != [
        "ビジネスユニット計", "小売＋ネット通販", "アウトレット その他", "アウトレット",
    ]:
        _fail(f"76060 section order: {tabaya_kids!r}")
    tabaya_both = tabaya_kids[1]
    if [n["name"] for n in tabaya_both["children"]] != ["小売", "ネット通販"]:
        _fail(f"76060 小売 / ネット通販 must be under 小売＋ネット通販: {tabaya_both!r}")
    if "カウント 既存 店舗数" not in tabaya_tree["tree"][0]["elements"]:
        _fail(f"76060 既存店カウント must sit on the root: {tabaya_tree['tree'][0]!r}")
    # バローHD: グループ総店舗数は根。事業は資料の a→c（SM / ドラッグストア / HC）
    valor_rows = [
        "グループ総 店舗数",
        "スーパーマーケット 全店 売上高(前年比)",
        "スーパーマーケット 既存 売上高(前年比)",
        "スーパーマーケット 既存 客数(前年比)",
        "スーパーマーケット 既存 客単価(前年比)",
        "ドラッグストア 全店 売上高(前年比)",
        "ドラッグストア 既存 売上高(前年比)",
        "ドラッグストア 既存 客数(前年比)",
        "ドラッグストア 既存 客単価(前年比)",
        "ホームセンター 全店 売上高(前年比)",
        "ホームセンター 既存 売上高(前年比)",
        "ホームセンター 既存 客数(前年比)",
        "ホームセンター 既存 客単価(前年比)",
    ]
    valor = assign_tree_rows("99560", valor_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in valor.items()}
    want = {
        "グループ総 店舗数": (["全体"], "グループ総 店舗数", "店舗数"),
        "スーパーマーケット 全店 売上高(前年比)": (
            ["全体", "スーパーマーケット"], "全店 売上高(前年比)", "全店 売上高"),
        "スーパーマーケット 既存 売上高(前年比)": (
            ["全体", "スーパーマーケット"], "既存 売上高(前年比)", "既存 売上高"),
        "スーパーマーケット 既存 客数(前年比)": (
            ["全体", "スーパーマーケット"], "既存 客数(前年比)", "既存 客数"),
        "スーパーマーケット 既存 客単価(前年比)": (
            ["全体", "スーパーマーケット"], "既存 客単価(前年比)", "既存 客単価"),
        "ドラッグストア 全店 売上高(前年比)": (
            ["全体", "ドラッグストア"], "全店 売上高(前年比)", "全店 売上高"),
        "ドラッグストア 既存 売上高(前年比)": (
            ["全体", "ドラッグストア"], "既存 売上高(前年比)", "既存 売上高"),
        "ドラッグストア 既存 客数(前年比)": (
            ["全体", "ドラッグストア"], "既存 客数(前年比)", "既存 客数"),
        "ドラッグストア 既存 客単価(前年比)": (
            ["全体", "ドラッグストア"], "既存 客単価(前年比)", "既存 客単価"),
        "ホームセンター 全店 売上高(前年比)": (
            ["全体", "ホームセンター"], "全店 売上高(前年比)", "全店 売上高"),
        "ホームセンター 既存 売上高(前年比)": (
            ["全体", "ホームセンター"], "既存 売上高(前年比)", "既存 売上高"),
        "ホームセンター 既存 客数(前年比)": (
            ["全体", "ホームセンター"], "既存 客数(前年比)", "既存 客数"),
        "ホームセンター 既存 客単価(前年比)": (
            ["全体", "ホームセンター"], "既存 客単価(前年比)", "既存 客単価"),
    }
    if got != want:
        _fail(f"99560 tree rows: {got!r}")
    valor_tree = tree_payload("99560", valor_rows, load_monthly_trees())
    if valor_tree is None or valor_tree.get("tree_auto"):
        _fail("99560 must use the config tree")
    if valor_tree["tree_metrics"] != [
        "既存 売上高", "既存 客数", "既存 客単価", "全店 売上高", "店舗数",
    ]:
        _fail(f"99560 metrics: {valor_tree!r}")
    root = valor_tree["tree"][0]
    if root["elements"] != ["グループ総 店舗数"]:
        _fail(f"99560 グループ総 must be the root: {root!r}")
    if [n["name"] for n in root["children"]] != ["スーパーマーケット", "ドラッグストア", "ホームセンター"]:
        _fail(f"99560 section order: {root['children']!r}")
    # イオン: 注記の会社を事業の下へ。キャンドゥ・ミニストップは所属未確認で木の外（#471）
    aeon_rows = [
        "イオンリテール 全店 売上高(前年比)",
        "イオンリテール 既存 売上高(前年比)",
        "イオン北海道 全店 売上高(前年比)",
        "イオン北海道 既存 売上高(前年比)",
        "イオン九州 全店 売上高(前年比)",
        "イオン九州 既存 売上高(前年比)",
        "マックスバリュ東海 全店 売上高(前年比)",
        "マックスバリュ東海 既存 売上高(前年比)",
        "フジ 全店 売上高(前年比)",
        "フジ 既存 売上高(前年比)",
        "いなげや 全店 売上高(前年比)",
        "いなげや 既存 売上高(前年比)",
        "イオンモール 全店 売上高(前年比)",
        "イオンモール 既存 売上高(前年比)",
        "コックス 全店 売上高(前年比)",
        "コックス 既存 売上高(前年比)",
        "ジーフット 全店 売上高(前年比)",
        "ジーフット 既存 売上高(前年比)",
        "GMS事業 既存 売上高(前年比)",
        "GMS事業 既存 客数(前年比)",
        "スーパーマーケット事業 既存 売上高(前年比)",
        "スーパーマーケット事業 既存 客数(前年比)",
        "ディスカウントストア事業 既存 売上高(前年比)",
        "ディスカウントストア事業 既存 客数(前年比)",
        "ヘルス＆ウエルネス事業 既存 売上高(前年比)",
        "ヘルス＆ウエルネス事業 既存 客数(前年比)",
        "ディベロッパー事業 既存 売上高(前年比)",
        "ディベロッパー事業 既存 客数(前年比)",
        "エンターテイメント事業 既存 売上高(前年比)",
        "エンターテイメント事業 既存 客数(前年比)",
        "サービス・専門店事業 既存 売上高(前年比)",
        "サービス・専門店事業 既存 客数(前年比)",
        "サービス事業 既存 売上高(前年比)",
        "サービス事業 既存 客数(前年比)",
        "専門店事業 既存 売上高(前年比)",
        "専門店事業 既存 客数(前年比)",
        "キャンドゥ 全店 売上高(前年比)",
        "キャンドゥ 既存 売上高(前年比)",
        "ミニストップ 全店 売上高(前年比)",
        "ミニストップ 既存 売上高(前年比)",
    ]
    aeon = assign_tree_rows("82670", aeon_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in aeon.items()}
    want = {
        "イオンリテール 全店 売上高(前年比)": (
            ["全体", "GMS事業", "イオンリテール"], "全店 売上高(前年比)", "全店 売上高"),
        "イオンリテール 既存 売上高(前年比)": (
            ["全体", "GMS事業", "イオンリテール"], "既存 売上高(前年比)", "既存 売上高"),
        "イオン北海道 全店 売上高(前年比)": (
            ["全体", "GMS事業", "イオン北海道"], "全店 売上高(前年比)", "全店 売上高"),
        "イオン北海道 既存 売上高(前年比)": (
            ["全体", "GMS事業", "イオン北海道"], "既存 売上高(前年比)", "既存 売上高"),
        "イオン九州 全店 売上高(前年比)": (
            ["全体", "GMS事業", "イオン九州"], "全店 売上高(前年比)", "全店 売上高"),
        "イオン九州 既存 売上高(前年比)": (
            ["全体", "GMS事業", "イオン九州"], "既存 売上高(前年比)", "既存 売上高"),
        "マックスバリュ東海 全店 売上高(前年比)": (
            ["全体", "スーパーマーケット事業", "マックスバリュ東海"], "全店 売上高(前年比)", "全店 売上高"),
        "マックスバリュ東海 既存 売上高(前年比)": (
            ["全体", "スーパーマーケット事業", "マックスバリュ東海"], "既存 売上高(前年比)", "既存 売上高"),
        "フジ 全店 売上高(前年比)": (
            ["全体", "スーパーマーケット事業", "フジ"], "全店 売上高(前年比)", "全店 売上高"),
        "フジ 既存 売上高(前年比)": (
            ["全体", "スーパーマーケット事業", "フジ"], "既存 売上高(前年比)", "既存 売上高"),
        "いなげや 全店 売上高(前年比)": (
            ["全体", "スーパーマーケット事業", "いなげや"], "全店 売上高(前年比)", "全店 売上高"),
        "いなげや 既存 売上高(前年比)": (
            ["全体", "スーパーマーケット事業", "いなげや"], "既存 売上高(前年比)", "既存 売上高"),
        "イオンモール 全店 売上高(前年比)": (
            ["全体", "ディベロッパー事業", "イオンモール"], "全店 売上高(前年比)", "全店 売上高"),
        "イオンモール 既存 売上高(前年比)": (
            ["全体", "ディベロッパー事業", "イオンモール"], "既存 売上高(前年比)", "既存 売上高"),
        "コックス 全店 売上高(前年比)": (
            ["全体", "サービス・専門店事業", "コックス"], "全店 売上高(前年比)", "全店 売上高"),
        "コックス 既存 売上高(前年比)": (
            ["全体", "サービス・専門店事業", "コックス"], "既存 売上高(前年比)", "既存 売上高"),
        "ジーフット 全店 売上高(前年比)": (
            ["全体", "サービス・専門店事業", "ジーフット"], "全店 売上高(前年比)", "全店 売上高"),
        "ジーフット 既存 売上高(前年比)": (
            ["全体", "サービス・専門店事業", "ジーフット"], "既存 売上高(前年比)", "既存 売上高"),
        "GMS事業 既存 売上高(前年比)": (["全体", "GMS事業"], "既存 売上高(前年比)", "既存 売上高"),
        "GMS事業 既存 客数(前年比)": (["全体", "GMS事業"], "既存 客数(前年比)", "既存 客数"),
        "スーパーマーケット事業 既存 売上高(前年比)": (
            ["全体", "スーパーマーケット事業"], "既存 売上高(前年比)", "既存 売上高"),
        "スーパーマーケット事業 既存 客数(前年比)": (
            ["全体", "スーパーマーケット事業"], "既存 客数(前年比)", "既存 客数"),
        "ディスカウントストア事業 既存 売上高(前年比)": (
            ["全体", "ディスカウントストア事業"], "既存 売上高(前年比)", "既存 売上高"),
        "ディスカウントストア事業 既存 客数(前年比)": (
            ["全体", "ディスカウントストア事業"], "既存 客数(前年比)", "既存 客数"),
        "ヘルス＆ウエルネス事業 既存 売上高(前年比)": (
            ["全体", "ヘルス＆ウエルネス事業"], "既存 売上高(前年比)", "既存 売上高"),
        "ヘルス＆ウエルネス事業 既存 客数(前年比)": (
            ["全体", "ヘルス＆ウエルネス事業"], "既存 客数(前年比)", "既存 客数"),
        "ディベロッパー事業 既存 売上高(前年比)": (
            ["全体", "ディベロッパー事業"], "既存 売上高(前年比)", "既存 売上高"),
        "ディベロッパー事業 既存 客数(前年比)": (
            ["全体", "ディベロッパー事業"], "既存 客数(前年比)", "既存 客数"),
        "エンターテイメント事業 既存 売上高(前年比)": (
            ["全体", "エンターテイメント事業"], "既存 売上高(前年比)", "既存 売上高"),
        "エンターテイメント事業 既存 客数(前年比)": (
            ["全体", "エンターテイメント事業"], "既存 客数(前年比)", "既存 客数"),
        "サービス・専門店事業 既存 売上高(前年比)": (
            ["全体", "サービス・専門店事業"], "既存 売上高(前年比)", "既存 売上高"),
        "サービス・専門店事業 既存 客数(前年比)": (
            ["全体", "サービス・専門店事業"], "既存 客数(前年比)", "既存 客数"),
        "サービス事業 既存 売上高(前年比)": (["全体", "サービス事業"], "既存 売上高(前年比)", "既存 売上高"),
        "サービス事業 既存 客数(前年比)": (["全体", "サービス事業"], "既存 客数(前年比)", "既存 客数"),
        "専門店事業 既存 売上高(前年比)": (["全体", "専門店事業"], "既存 売上高(前年比)", "既存 売上高"),
        "専門店事業 既存 客数(前年比)": (["全体", "専門店事業"], "既存 客数(前年比)", "既存 客数"),
    }
    if got != want:
        _fail(f"82670 tree rows: {got!r}")
    for outside in (
        "キャンドゥ 全店 売上高(前年比)", "キャンドゥ 既存 売上高(前年比)",
        "ミニストップ 全店 売上高(前年比)", "ミニストップ 既存 売上高(前年比)",
    ):
        if outside in aeon:
            _fail(f"82670 {outside} must stay outside the tree")
    aeon_tree = tree_payload("82670", aeon_rows, load_monthly_trees())
    if aeon_tree is None or aeon_tree.get("tree_auto"):
        _fail("82670 must use the config tree")
    if aeon_tree["tree_metrics"] != ["既存 売上高", "既存 客数", "全店 売上高"]:
        _fail(f"82670 metrics: {aeon_tree!r}")
    aeon_kids = aeon_tree["tree"][0]["children"]
    if [n["name"] for n in aeon_kids] != [
        "GMS事業", "スーパーマーケット事業", "ディスカウントストア事業", "ヘルス＆ウエルネス事業",
        "ディベロッパー事業", "エンターテイメント事業", "サービス・専門店事業", "サービス事業", "専門店事業",
    ]:
        _fail(f"82670 section order: {aeon_kids!r}")
    if [n["name"] for n in aeon_kids[0]["children"]] != ["イオンリテール", "イオン北海道", "イオン九州"]:
        _fail(f"82670 GMS children: {aeon_kids[0]['children']!r}")
    if [n["name"] for n in aeon_kids[1]["children"]] != ["マックスバリュ東海", "フジ", "いなげや"]:
        _fail(f"82670 supermarket children: {aeon_kids[1]['children']!r}")
    if [n["name"] for n in aeon_kids[4]["children"]] != ["イオンモール"]:
        _fail(f"82670 developer children: {aeon_kids[4]['children']!r}")
    if [n["name"] for n in aeon_kids[6]["children"]] != ["コックス", "ジーフット"]:
        _fail(f"82670 specialty children: {aeon_kids[6]['children']!r}")
    # オートバックスセブン: 商品群は既存売上高の内訳。車検・整備はサービスの子（#473）
    ab_rows = [
        "日本 全店 売上高(前年比)",
        "日本 既存 売上高(前年比)",
        "日本 全店 客数(前年比)",
        "日本 既存 客数(前年比)",
        "既存 客単価(前年比)",
        "土日祝日前年差異",
        "タイヤ 既存 売上高(前年比)",
        "カーエレクトロニクス 既存 売上高(前年比)",
        "オイル 既存 売上高(前年比)",
        "バッテリー 既存 売上高(前年比)",
        "アクセサリー 既存 売上高(前年比)",
        "サービス 既存 売上高(前年比)",
        "車検・整備 既存 売上高(前年比)",
        "車販売 既存 売上高(前年比)",
    ]
    ab = assign_tree_rows("98320", ab_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in ab.items()}
    want = {
        "日本 全店 売上高(前年比)": (["全体"], "日本 全店 売上高(前年比)", "日本 全店 売上高"),
        "日本 既存 売上高(前年比)": (["全体"], "日本 既存 売上高(前年比)", "既存 売上高"),
        "日本 全店 客数(前年比)": (["全体"], "日本 全店 客数(前年比)", "日本 全店 客数"),
        "日本 既存 客数(前年比)": (["全体"], "日本 既存 客数(前年比)", "日本 既存 客数"),
        "既存 客単価(前年比)": (["全体"], "既存 客単価(前年比)", "既存 客単価"),
        "土日祝日前年差異": (["全体"], "土日祝日前年差異", "土日祝日前年差異"),
        "タイヤ 既存 売上高(前年比)": (["全体", "タイヤ"], "既存 売上高(前年比)", "既存 売上高"),
        "カーエレクトロニクス 既存 売上高(前年比)": (
            ["全体", "カーエレクトロニクス"], "既存 売上高(前年比)", "既存 売上高"),
        "オイル 既存 売上高(前年比)": (["全体", "オイル"], "既存 売上高(前年比)", "既存 売上高"),
        "バッテリー 既存 売上高(前年比)": (["全体", "バッテリー"], "既存 売上高(前年比)", "既存 売上高"),
        "アクセサリー 既存 売上高(前年比)": (["全体", "アクセサリー"], "既存 売上高(前年比)", "既存 売上高"),
        "サービス 既存 売上高(前年比)": (["全体", "サービス"], "既存 売上高(前年比)", "既存 売上高"),
        "車検・整備 既存 売上高(前年比)": (
            ["全体", "サービス", "車検・整備"], "既存 売上高(前年比)", "既存 売上高"),
        "車販売 既存 売上高(前年比)": (["全体", "車販売"], "既存 売上高(前年比)", "既存 売上高"),
    }
    if got != want:
        _fail(f"98320 tree rows: {got!r}")
    ab_tree = tree_payload("98320", ab_rows, load_monthly_trees())
    if ab_tree is None or ab_tree.get("tree_auto"):
        _fail("98320 must use the config tree")
    if ab_tree["tree_metrics"] != [
        "既存 売上高", "日本 既存 客数", "既存 客単価",
        "日本 全店 売上高", "日本 全店 客数", "土日祝日前年差異",
    ]:
        _fail(f"98320 metrics: {ab_tree!r}")
    ab_kids = ab_tree["tree"][0]["children"]
    if [n["name"] for n in ab_kids] != [
        "タイヤ", "カーエレクトロニクス", "オイル", "バッテリー", "アクセサリー", "サービス", "車販売",
    ]:
        _fail(f"98320 section order: {ab_kids!r}")
    ab_svc = ab_kids[5]
    if ab_svc["part"] or [n["name"] for n in ab_svc["children"]] != ["車検・整備"] or ab_svc["children"][0]["part"]:
        _fail(f"98320 車検・整備 must be a child of サービス: {ab_svc!r}")
    # アークランズ: かつや / その他は外食事業の子。国内総店舗数は外食の店舗数（#484）
    arc_rows = [
        "小売事業 既存 売上高(前年比)",
        "小売事業 既存 客数(前年比)",
        "小売事業 既存 客単価(前年比)",
        "小売事業 全店 売上高(前年比)",
        "外食事業 かつや 既存 売上高(前年比)",
        "外食事業 かつや 既存 客数(前年比)",
        "外食事業 かつや 既存 客単価(前年比)",
        "外食事業 全店 売上高(前年比)",
        "外食事業 かつや 店舗数",
        "外食事業 その他 店舗数",
        "日本 外食事業 店舗数",
    ]
    arc = assign_tree_rows("98420", arc_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in arc.items()}
    want = {
        "小売事業 既存 売上高(前年比)": (["全体", "小売事業"], "既存 売上高(前年比)", "既存 売上高"),
        "小売事業 既存 客数(前年比)": (["全体", "小売事業"], "既存 客数(前年比)", "既存 客数"),
        "小売事業 既存 客単価(前年比)": (["全体", "小売事業"], "既存 客単価(前年比)", "既存 客単価"),
        "小売事業 全店 売上高(前年比)": (["全体", "小売事業"], "全店 売上高(前年比)", "全店 売上高"),
        "外食事業 かつや 既存 売上高(前年比)": (
            ["全体", "外食事業", "かつや"], "既存 売上高(前年比)", "既存 売上高"),
        "外食事業 かつや 既存 客数(前年比)": (
            ["全体", "外食事業", "かつや"], "既存 客数(前年比)", "既存 客数"),
        "外食事業 かつや 既存 客単価(前年比)": (
            ["全体", "外食事業", "かつや"], "既存 客単価(前年比)", "既存 客単価"),
        "外食事業 全店 売上高(前年比)": (["全体", "外食事業"], "全店 売上高(前年比)", "全店 売上高"),
        "外食事業 かつや 店舗数": (["全体", "外食事業", "かつや"], "店舗数", "店舗数"),
        "外食事業 その他 店舗数": (["全体", "外食事業", "その他"], "店舗数", "店舗数"),
        "日本 外食事業 店舗数": (["全体", "外食事業"], "日本 外食事業 店舗数", "店舗数"),
    }
    if got != want:
        _fail(f"98420 tree rows: {got!r}")
    arc_tree = tree_payload("98420", arc_rows, load_monthly_trees())
    if arc_tree is None or arc_tree.get("tree_auto"):
        _fail("98420 must use the config tree")
    if arc_tree["tree_metrics"] != [
        "既存 売上高", "既存 客数", "既存 客単価", "全店 売上高", "店舗数",
    ]:
        _fail(f"98420 metrics: {arc_tree!r}")
    arc_kids = arc_tree["tree"][0]["children"]
    if [n["name"] for n in arc_kids] != ["小売事業", "外食事業"]:
        _fail(f"98420 section order: {arc_kids!r}")
    arc_food = arc_kids[1]
    if arc_food["elements"] != ["日本 外食事業 店舗数", "外食事業 全店 売上高(前年比)"]:
        _fail(f"98420 国内総店舗数 must sit on 外食事業: {arc_food!r}")
    if [n["name"] for n in arc_food["children"]] != ["かつや", "その他"]:
        _fail(f"98420 かつや / その他 must be children of 外食事業: {arc_food['children']!r}")
    # ラストワンマイル: ストック型合計が根。旧「X ストック型」は同じ節。内燃料調整費は電気の内数（#472）
    lom_rows = [
        "ストック型 売上高",
        "売上高",
        "電気 売上高",
        "宅配水 売上高",
        "インターネット 売上高",
        "その他 売上高",
        "電気 ストック型 売上高",
        "宅配水 ストック型 売上高",
        "インターネット ストック型 売上高",
        "その他 ストック型 売上高",
        "電気 燃料調整費",
    ]
    lom = assign_tree_rows("92520", lom_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in lom.items()}
    want = {
        "ストック型 売上高": (["全体"], "ストック型 売上高", "売上高"),
        "売上高": (["全体"], "売上高", "売上高"),
        "電気 売上高": (["全体", "電気"], "売上高", "売上高"),
        "宅配水 売上高": (["全体", "宅配水"], "売上高", "売上高"),
        "インターネット 売上高": (["全体", "インターネット"], "売上高", "売上高"),
        "その他 売上高": (["全体", "その他"], "売上高", "売上高"),
        "電気 ストック型 売上高": (["全体", "電気"], "ストック型 売上高", "売上高"),
        "宅配水 ストック型 売上高": (["全体", "宅配水"], "ストック型 売上高", "売上高"),
        "インターネット ストック型 売上高": (["全体", "インターネット"], "ストック型 売上高", "売上高"),
        "その他 ストック型 売上高": (["全体", "その他"], "ストック型 売上高", "売上高"),
        "電気 燃料調整費": (["全体", "電気", "内燃料調整費"], "電気 燃料調整費", "売上高"),
    }
    if got != want:
        _fail(f"92520 tree rows: {got!r}")
    if apply_issuer_aliases("92520", "電気 ストック型 売上高", year=2026) != "電気 売上高":
        _fail("92520 電気 ストック型 must alias to 電気 売上高")
    if apply_issuer_aliases("92520", "宅配水 ストック型 売上高", year=2025) != "宅配水 売上高":
        _fail("92520 宅配水 ストック型 must alias to 宅配水 売上高")
    if apply_issuer_aliases("92520", "インターネット ストック型 売上高", year=2024) != "インターネット 売上高":
        _fail("92520 インターネット ストック型 must alias to インターネット 売上高")
    if apply_issuer_aliases("92520", "その他 ストック型 売上高", year=2024) != "その他 売上高":
        _fail("92520 その他 ストック型 must alias to その他 売上高")
    if apply_issuer_aliases("92520", "売上高", year=2026) != "ストック型 売上高":
        _fail("92520 bare 売上高 must alias to ストック型 売上高")
    if apply_issuer_aliases("92520", "ストック型 売上高", year=2026) != "ストック型 売上高":
        _fail("92520 ストック型 売上高 is already the stored total")
    if apply_issuer_aliases("92520", "電気 燃料調整費", year=2026) != "電気 燃料調整費":
        _fail("92520 燃料調整費 must keep its stored name")
    lom_tree = tree_payload("92520", lom_rows, load_monthly_trees())
    if lom_tree is None or lom_tree.get("tree_metrics") != ["売上高"] or lom_tree.get("tree_auto"):
        _fail(f"92520 tree payload: {lom_tree!r}")
    lom_root = lom_tree["tree"][0]
    lom_kids = lom_root["children"]
    if [n["name"] for n in lom_kids] != ["電気", "宅配水", "インターネット", "その他"]:
        _fail(f"92520 section order: {lom_kids!r}")
    if lom_root["elements"] != ["ストック型 売上高", "売上高"]:
        _fail(f"92520 root rows: {lom_root['elements']!r}")
    fuel = lom_kids[0]["children"]
    if len(fuel) != 1 or fuel[0]["name"] != "内燃料調整費" or fuel[0]["part"] is not True:
        _fail(f"92520 fuel must be a part of 電気: {fuel!r}")
    if fuel[0]["elements"] != ["電気 燃料調整費"]:
        _fail(f"92520 fuel row: {fuel[0]['elements']!r}")
    # ブックオフ: チェーン合計は根。国内・海外は事業計を節の店舗数にし、直営 / 加盟店を子にする（#483）
    bookoff_rows = [
        "チェーン 店舗数",
        "国内ブックオフ事業計 店舗数",
        "国内ブックオフ 直営 店舗数",
        "国内ブックオフ 加盟店 店舗数",
        "国内ブックオフ 直営 既存 売上高(前年比)",
        "国内ブックオフ 直営 全店 売上高(前年比)",
        "プレミアムサービス事業 店舗数",
        "海外事業計 店舗数",
        "海外事業 直営 店舗数",
        "海外事業 加盟店 店舗数",
        "その他 店舗数",
    ]
    bookoff = assign_tree_rows("92780", bookoff_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in bookoff.items()}
    want = {
        "チェーン 店舗数": (["全体"], "チェーン 店舗数", "店舗数"),
        "国内ブックオフ事業計 店舗数": (["全体", "国内ブックオフ"], "国内ブックオフ事業計 店舗数", "店舗数"),
        "国内ブックオフ 直営 店舗数": (
            ["全体", "国内ブックオフ", "直営"], "国内ブックオフ 直営 店舗数", "店舗数"),
        "国内ブックオフ 加盟店 店舗数": (
            ["全体", "国内ブックオフ", "加盟店"], "国内ブックオフ 加盟店 店舗数", "店舗数"),
        "国内ブックオフ 直営 既存 売上高(前年比)": (
            ["全体", "国内ブックオフ", "直営"],
            "国内ブックオフ 直営 既存 売上高(前年比)", "既存 売上高"),
        "国内ブックオフ 直営 全店 売上高(前年比)": (
            ["全体", "国内ブックオフ", "直営"],
            "国内ブックオフ 直営 全店 売上高(前年比)", "全店 売上高"),
        "プレミアムサービス事業 店舗数": (
            ["全体", "プレミアムサービス事業"], "プレミアムサービス事業 店舗数", "店舗数"),
        "海外事業計 店舗数": (["全体", "海外事業"], "海外事業計 店舗数", "店舗数"),
        "海外事業 直営 店舗数": (["全体", "海外事業", "直営"], "海外事業 直営 店舗数", "店舗数"),
        "海外事業 加盟店 店舗数": (["全体", "海外事業", "加盟店"], "海外事業 加盟店 店舗数", "店舗数"),
        "その他 店舗数": (["全体", "その他"], "その他 店舗数", "店舗数"),
    }
    if got != want:
        _fail(f"92780 tree rows: {got!r}")
    if apply_issuer_aliases("92780", "国内ブックオフ 加盟 店舗数", year=2024) != "国内ブックオフ 加盟店 店舗数":
        _fail("92780 国内ブックオフ 加盟 must alias to 加盟店")
    if apply_issuer_aliases("92780", "国内ブックオフ事業 店舗数", year=2024) != "国内ブックオフ事業計 店舗数":
        _fail("92780 国内ブックオフ事業 must alias to 事業計")
    if apply_issuer_aliases("92780", "海外事業 加盟 店舗数", year=2024) != "海外事業 加盟店 店舗数":
        _fail("92780 海外事業 加盟 must alias to 加盟店")
    if apply_issuer_aliases("92780", "海外事業 店舗数", year=2024) != "海外事業計 店舗数":
        _fail("92780 海外事業 店舗数 must alias to 海外事業計")
    if apply_issuer_aliases("92780", "国内ブックオフ 加盟店 店舗数", year=2026) != "国内ブックオフ 加盟店 店舗数":
        _fail("92780 stored 加盟店 name must stay")
    bookoff_tree = tree_payload("92780", bookoff_rows, load_monthly_trees())
    if bookoff_tree is None or bookoff_tree.get("tree_auto"):
        _fail("92780 must use the config tree")
    if bookoff_tree["tree_metrics"] != ["既存 売上高", "全店 売上高", "店舗数"]:
        _fail(f"92780 metrics: {bookoff_tree!r}")
    bo_root = bookoff_tree["tree"][0]
    if bo_root["elements"] != ["チェーン 店舗数"]:
        _fail(f"92780 チェーン must be the root: {bo_root!r}")
    bo_kids = bo_root["children"]
    if [n["name"] for n in bo_kids] != ["国内ブックオフ", "プレミアムサービス事業", "海外事業", "その他"]:
        _fail(f"92780 section order: {bo_kids!r}")
    if bo_kids[0]["elements"] != ["国内ブックオフ事業計 店舗数"]:
        _fail(f"92780 国内 must use 事業計 as the section count: {bo_kids[0]!r}")
    if [n["name"] for n in bo_kids[0]["children"]] != ["直営", "加盟店"]:
        _fail(f"92780 国内 children: {bo_kids[0]['children']!r}")
    if bo_kids[2]["elements"] != ["海外事業計 店舗数"]:
        _fail(f"92780 海外 must use 事業計 as the section count: {bo_kids[2]!r}")
    if [n["name"] for n in bo_kids[2]["children"]] != ["直営", "加盟店"]:
        _fail(f"92780 海外 children: {bo_kids[2]['children']!r}")
    # 小田急: 鉄道は 定期+定期外 ▸ 定期計 ▸ 通勤/通学 と 定期外。百貨店の店は子。箱根のホテルは箱根（#482）
    oda_rows = [
        "鉄道 定期+定期外 輸送人員",
        "鉄道 定期+定期外 輸送人員(前年比)",
        "鉄道 定期計 輸送人員",
        "鉄道 通勤定期 輸送人員",
        "鉄道 通学定期 輸送人員",
        "鉄道 定期外 輸送人員",
        "鉄道 定期+定期外 運輸収入",
        "鉄道 定期計 運輸収入",
        "鉄道 通勤定期 運輸収入",
        "鉄道 通学定期 運輸収入",
        "鉄道 定期外 運輸収入",
        "百貨店 取扱高",
        "百貨店 取扱高(前年比)",
        "百貨店 新宿店 取扱高",
        "百貨店 町田店 取扱高",
        "百貨店 ふじさわ 取扱高",
        "ストア 既存 取扱高",
        "ストア 既存 売上高(前年比)",
        "ストア 全店 取扱高",
        "ストア 全店 売上高(前年比)",
        "ホテル センチュリー 客室稼働率",
        "ホテル センチュリー 稼働率前年差",
        "ホテル センチュリー 稼働率前年差(前年比)",
        "ホテル 山のホテル 客室稼働率",
        "ホテル はつはな 客室稼働率",
        "ホテル 箱根ゆとわ 客室稼働率",
        "ホテル RETONA 客室稼働率",
        "ホテル CLAD 客室稼働率",
        "ホテル CLAD 稼働率前年差",
        "ホテル 箱根ハイランド 客室稼働率",
        "ホテル 箱根ハイランド 稼働率前年差",
    ]
    oda = assign_tree_rows("90070", oda_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in oda.items()}
    want = {
        "鉄道 定期+定期外 輸送人員": (["全体", "鉄道", "定期+定期外"], "輸送人員", "輸送人員"),
        "鉄道 定期+定期外 輸送人員(前年比)": (["全体", "鉄道", "定期+定期外"], "輸送人員(前年比)", "輸送人員"),
        "鉄道 定期計 輸送人員": (["全体", "鉄道", "定期+定期外", "定期計"], "輸送人員", "輸送人員"),
        "鉄道 通勤定期 輸送人員": (["全体", "鉄道", "定期+定期外", "定期計", "通勤"], "輸送人員", "輸送人員"),
        "鉄道 通学定期 輸送人員": (["全体", "鉄道", "定期+定期外", "定期計", "通学"], "輸送人員", "輸送人員"),
        "鉄道 定期外 輸送人員": (["全体", "鉄道", "定期+定期外", "定期外"], "輸送人員", "輸送人員"),
        "鉄道 定期+定期外 運輸収入": (["全体", "鉄道", "定期+定期外"], "運輸収入", "運輸収入"),
        "鉄道 定期計 運輸収入": (["全体", "鉄道", "定期+定期外", "定期計"], "運輸収入", "運輸収入"),
        "鉄道 通勤定期 運輸収入": (["全体", "鉄道", "定期+定期外", "定期計", "通勤"], "運輸収入", "運輸収入"),
        "鉄道 通学定期 運輸収入": (["全体", "鉄道", "定期+定期外", "定期計", "通学"], "運輸収入", "運輸収入"),
        "鉄道 定期外 運輸収入": (["全体", "鉄道", "定期+定期外", "定期外"], "運輸収入", "運輸収入"),
        "百貨店 取扱高": (["全体", "百貨店"], "取扱高", "取扱高"),
        "百貨店 取扱高(前年比)": (["全体", "百貨店"], "取扱高(前年比)", "取扱高"),
        "百貨店 新宿店 取扱高": (["全体", "百貨店", "新宿店"], "取扱高", "取扱高"),
        "百貨店 町田店 取扱高": (["全体", "百貨店", "町田店"], "取扱高", "取扱高"),
        "百貨店 ふじさわ 取扱高": (["全体", "百貨店", "ふじさわ"], "取扱高", "取扱高"),
        "ストア 既存 取扱高": (["全体", "ストア"], "既存 取扱高", "既存 取扱高"),
        "ストア 既存 売上高(前年比)": (["全体", "ストア"], "既存 売上高(前年比)", "既存 売上高(前年比)"),
        "ストア 全店 取扱高": (["全体", "ストア"], "全店 取扱高", "全店 取扱高"),
        "ストア 全店 売上高(前年比)": (["全体", "ストア"], "全店 売上高(前年比)", "全店 売上高(前年比)"),
        "ホテル センチュリー 客室稼働率": (["全体", "ホテル", "センチュリー"], "客室稼働率", "客室稼働率"),
        "ホテル センチュリー 稼働率前年差": (["全体", "ホテル", "センチュリー"], "稼働率前年差", "稼働率前年差"),
        "ホテル センチュリー 稼働率前年差(前年比)": (
            ["全体", "ホテル", "センチュリー"], "稼働率前年差(前年比)", "稼働率前年差"),
        "ホテル 山のホテル 客室稼働率": (["全体", "ホテル", "箱根", "山のホテル"], "客室稼働率", "客室稼働率"),
        "ホテル はつはな 客室稼働率": (["全体", "ホテル", "箱根", "はつはな"], "客室稼働率", "客室稼働率"),
        "ホテル 箱根ゆとわ 客室稼働率": (["全体", "ホテル", "箱根", "箱根ゆとわ"], "客室稼働率", "客室稼働率"),
        "ホテル RETONA 客室稼働率": (["全体", "ホテル", "箱根", "RETONA"], "客室稼働率", "客室稼働率"),
        "ホテル CLAD 客室稼働率": (["全体", "ホテル", "箱根", "CLAD"], "客室稼働率", "客室稼働率"),
        "ホテル CLAD 稼働率前年差": (["全体", "ホテル", "箱根", "CLAD"], "稼働率前年差", "稼働率前年差"),
        "ホテル 箱根ハイランド 客室稼働率": (["全体", "ホテル", "箱根", "箱根ハイランド"], "客室稼働率", "客室稼働率"),
        "ホテル 箱根ハイランド 稼働率前年差": (
            ["全体", "ホテル", "箱根", "箱根ハイランド"], "稼働率前年差", "稼働率前年差"),
    }
    if got != want:
        _fail(f"90070 tree rows: {got!r}")
    oda_tree = tree_payload("90070", oda_rows, load_monthly_trees())
    if oda_tree is None or oda_tree.get("tree_auto"):
        _fail("90070 must use the config tree")
    if oda_tree["tree_metrics"] != [
        "輸送人員", "運輸収入", "取扱高", "既存 取扱高", "既存 売上高(前年比)",
        "全店 取扱高", "全店 売上高(前年比)", "客室稼働率", "稼働率前年差",
    ]:
        _fail(f"90070 metrics: {oda_tree['tree_metrics']!r}")
    oda_kids = oda_tree["tree"][0]["children"]
    if [n["name"] for n in oda_kids] != ["鉄道", "百貨店", "ストア", "ホテル"]:
        _fail(f"90070 section order: {oda_kids!r}")
    rail, dept, store, hotel = oda_kids
    if not rail["heading"] or [n["name"] for n in rail["children"]] != ["定期+定期外"]:
        _fail(f"90070 鉄道 must be a heading over 定期+定期外: {rail!r}")
    total = rail["children"][0]
    if [n["name"] for n in total["children"]] != ["定期計", "定期外"]:
        _fail(f"90070 定期外 must sit beside 定期計: {total['children']!r}")
    if [n["name"] for n in total["children"][0]["children"]] != ["通勤", "通学"]:
        _fail(f"90070 通勤/通学 must be under 定期計: {total['children'][0]!r}")
    if [n["name"] for n in dept["children"]] != ["新宿店", "町田店", "ふじさわ"] or dept["heading"]:
        _fail(f"90070 stores must be under 百貨店: {dept!r}")
    if store["children"] or store["heading"]:
        _fail(f"90070 ストア is one section: {store!r}")
    if not hotel["heading"] or [n["name"] for n in hotel["children"]] != ["センチュリー", "箱根"]:
        _fail(f"90070 ホテル sections: {hotel!r}")
    hakone = hotel["children"][1]
    if not hakone["heading"] or [n["name"] for n in hakone["children"]] != [
        "山のホテル", "はつはな", "箱根ゆとわ", "RETONA", "CLAD", "箱根ハイランド",
    ]:
        _fail(f"90070 箱根 hotels: {hakone!r}")
    # マックスバリュ東海: 同規模・一品単価・買上点数は会社全体の根。都市型小型店の新設は新設店舗の内数（#481）
    mv_rows = [
        "全店 売上高(前年比)",
        "同規模 全店 売上高(前年比)",
        "全店 客数(前年比)",
        "同規模 全店 客数(前年比)",
        "全店 客単価(前年比)",
        "同規模 全店 客単価(前年比)",
        "一品 全店 客単価(前年比)",
        "同規模 一品 全店 客単価(前年比)",
        "買上点数 全店 売上高(前年比)",
        "同規模 買上点数(前年比)",
        "新設店舗",
        "都市型小型店 新設",
        "退店",
        "店舗数",
    ]
    mv = assign_tree_rows("81980", mv_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in mv.items()}
    want = {
        "全店 売上高(前年比)": (["全体"], "全店 売上高(前年比)", "全店 売上高"),
        "同規模 全店 売上高(前年比)": (["全体"], "同規模 全店 売上高(前年比)", "同規模 全店 売上高"),
        "全店 客数(前年比)": (["全体"], "全店 客数(前年比)", "全店 客数"),
        "同規模 全店 客数(前年比)": (["全体"], "同規模 全店 客数(前年比)", "同規模 全店 客数"),
        "全店 客単価(前年比)": (["全体"], "全店 客単価(前年比)", "全店 客単価"),
        "同規模 全店 客単価(前年比)": (["全体"], "同規模 全店 客単価(前年比)", "同規模 全店 客単価"),
        "一品 全店 客単価(前年比)": (["全体"], "一品 全店 客単価(前年比)", "一品単価"),
        "同規模 一品 全店 客単価(前年比)": (
            ["全体"], "同規模 一品 全店 客単価(前年比)", "同規模 一品単価"),
        "買上点数 全店 売上高(前年比)": (["全体"], "買上点数 全店 売上高(前年比)", "買上点数"),
        "同規模 買上点数(前年比)": (["全体"], "同規模 買上点数(前年比)", "同規模 買上点数"),
        "新設店舗": (["全体"], "新設店舗", "新設店舗"),
        "都市型小型店 新設": (["全体", "内都市型小型店"], "都市型小型店 新設", "新設店舗"),
        "退店": (["全体"], "退店", "退店"),
        "店舗数": (["全体"], "店舗数", "店舗数"),
    }
    if got != want:
        _fail(f"81980 tree rows: {got!r}")
    mv_tree = tree_payload("81980", mv_rows, load_monthly_trees())
    if mv_tree is None or mv_tree.get("tree_auto"):
        _fail("81980 must use the config tree")
    if mv_tree["tree_metrics"] != [
        "全店 売上高", "同規模 全店 売上高", "全店 客数", "同規模 全店 客数",
        "全店 客単価", "同規模 全店 客単価", "一品単価", "同規模 一品単価",
        "買上点数", "同規模 買上点数", "新設店舗", "退店", "店舗数",
    ]:
        _fail(f"81980 metrics: {mv_tree!r}")
    mv_root = mv_tree["tree"][0]
    if mv_root["elements"] != [r for r in mv_rows if r != "都市型小型店 新設"]:
        _fail(f"81980 root rows: {mv_root['elements']!r}")
    if (len(mv_root["children"]) != 1 or mv_root["children"][0]["name"] != "内都市型小型店"
            or mv_root["children"][0]["part"] is not True
            or mv_root["children"][0]["elements"] != ["都市型小型店 新設"]):
        _fail(f"81980 都市型小型店 must be a part of 新設店舗: {mv_root['children']!r}")
    # ラクス: 全社は根。ストックはクラウドの内数。楽楽精算 / 楽楽明細はクラウドの子（#477）
    rakus_rows = [
        "全社（連結） 売上高",
        "全社（連結） 売上高(前年比)",
        "クラウド事業 売上高",
        "クラウド事業 売上高(前年比)",
        "クラウド事業 ストック 売上高",
        "クラウド事業 ストック 売上高(前年比)",
        "楽楽精算 売上高",
        "楽楽精算 売上高(前年比)",
        "楽楽明細 売上高",
        "楽楽明細 売上高(前年比)",
        "IT人材事業 売上高",
        "IT人材事業 売上高(前年比)",
    ]
    rakus = assign_tree_rows("39230", rakus_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rakus.items()}
    want = {
        "全社（連結） 売上高": (["全体"], "全社（連結） 売上高", "売上高"),
        "全社（連結） 売上高(前年比)": (["全体"], "全社（連結） 売上高(前年比)", "売上高"),
        "クラウド事業 売上高": (["全体", "クラウド事業"], "売上高", "売上高"),
        "クラウド事業 売上高(前年比)": (["全体", "クラウド事業"], "売上高(前年比)", "売上高"),
        "クラウド事業 ストック 売上高": (["全体", "クラウド事業", "ストック"], "売上高", "売上高"),
        "クラウド事業 ストック 売上高(前年比)": (
            ["全体", "クラウド事業", "ストック"], "売上高(前年比)", "売上高"),
        "楽楽精算 売上高": (["全体", "クラウド事業", "楽楽精算"], "売上高", "売上高"),
        "楽楽精算 売上高(前年比)": (["全体", "クラウド事業", "楽楽精算"], "売上高(前年比)", "売上高"),
        "楽楽明細 売上高": (["全体", "クラウド事業", "楽楽明細"], "売上高", "売上高"),
        "楽楽明細 売上高(前年比)": (["全体", "クラウド事業", "楽楽明細"], "売上高(前年比)", "売上高"),
        "IT人材事業 売上高": (["全体", "IT人材事業"], "売上高", "売上高"),
        "IT人材事業 売上高(前年比)": (["全体", "IT人材事業"], "売上高(前年比)", "売上高"),
    }
    if got != want:
        _fail(f"39230 tree rows: {got!r}")
    rakus_tree = tree_payload("39230", rakus_rows, load_monthly_trees())
    if rakus_tree is None or rakus_tree.get("tree_metrics") != ["売上高"] or rakus_tree.get("tree_auto"):
        _fail(f"39230 tree payload: {rakus_tree!r}")
    rakus_root = rakus_tree["tree"][0]
    if rakus_root["elements"] != ["全社（連結） 売上高", "全社（連結） 売上高(前年比)"]:
        _fail(f"39230 root rows: {rakus_root['elements']!r}")
    rakus_kids = rakus_root["children"]
    if [n["name"] for n in rakus_kids] != ["クラウド事業", "IT人材事業"]:
        _fail(f"39230 section order: {rakus_kids!r}")
    rakus_cloud = rakus_kids[0]["children"]
    if [n["name"] for n in rakus_cloud] != ["ストック", "楽楽精算", "楽楽明細"]:
        _fail(f"39230 cloud children: {rakus_cloud!r}")
    if rakus_cloud[0]["part"] is not True or rakus_cloud[1]["part"] or rakus_cloud[2]["part"]:
        _fail(f"39230 stock must be the only part: {rakus_cloud!r}")
    if rakus_cloud[0]["elements"] != ["クラウド事業 ストック 売上高", "クラウド事業 ストック 売上高(前年比)"]:
        _fail(f"39230 stock rows: {rakus_cloud[0]['elements']!r}")
    # Trailhead: 子会社は連結の見出しの下。業態の店舗数は全店店舗数の内訳（#476）
    th_rows = [
        "連結売上高",
        "連結売上高(前年比)",
        "全店 売上高",
        "売上高(前年比)",
        "客数(前年比)",
        "客単価(前年比)",
        "全店 店舗数",
        "店舗数",
        "ワイエスフード 売上高",
        "Yappa 売上高",
        "KINKA FAMILY JAPAN 売上高",
        "バーガーレボリューション 売上高",
        "食べる 売上高",
        "SBWorks 売上高",
        "ラーメン 売上高",
        "ラーメン 売上高(前年比)",
        "ラーメン 客数(前年比)",
        "ラーメン 客単価(前年比)",
        "ラーメン 店舗数",
        "焼肉 売上高",
        "焼肉 店舗数",
        "その他専門店 売上高",
        "その他専門店 店舗数",
    ]
    th = assign_tree_rows("33580", th_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in th.items()}
    want = {
        "連結売上高": (["全体"], "連結売上高", "連結売上高"),
        "連結売上高(前年比)": (["全体"], "連結売上高(前年比)", "連結売上高"),
        "全店 売上高": (["全体"], "全店 売上高", "全店 売上高"),
        "売上高(前年比)": (["全体"], "売上高(前年比)", "売上高"),
        "客数(前年比)": (["全体"], "客数(前年比)", "客数"),
        "客単価(前年比)": (["全体"], "客単価(前年比)", "客単価"),
        "全店 店舗数": (["全体"], "全店 店舗数", "全店 店舗数"),
        "店舗数": (["全体"], "店舗数", "店舗数"),
        "ワイエスフード 売上高": (
            ["全体", "主要子会社別売上高", "ワイエスフード"], "ワイエスフード 売上高", "連結売上高"),
        "Yappa 売上高": (
            ["全体", "主要子会社別売上高", "Yappa"], "Yappa 売上高", "連結売上高"),
        "KINKA FAMILY JAPAN 売上高": (
            ["全体", "主要子会社別売上高", "KINKA FAMILY JAPAN"],
            "KINKA FAMILY JAPAN 売上高", "連結売上高"),
        "バーガーレボリューション 売上高": (
            ["全体", "主要子会社別売上高", "バーガーレボリューション"],
            "バーガーレボリューション 売上高", "連結売上高"),
        "食べる 売上高": (
            ["全体", "主要子会社別売上高", "食べる"], "食べる 売上高", "連結売上高"),
        "SBWorks 売上高": (
            ["全体", "主要子会社別売上高", "SBWorks"], "SBWorks 売上高", "連結売上高"),
        "ラーメン 売上高": (["全体", "ラーメン"], "ラーメン 売上高", "全店 売上高"),
        "ラーメン 売上高(前年比)": (["全体", "ラーメン"], "ラーメン 売上高(前年比)", "全店 売上高"),
        "ラーメン 客数(前年比)": (["全体", "ラーメン"], "ラーメン 客数(前年比)", "客数"),
        "ラーメン 客単価(前年比)": (["全体", "ラーメン"], "ラーメン 客単価(前年比)", "客単価"),
        "ラーメン 店舗数": (["全体", "ラーメン"], "ラーメン 店舗数", "全店 店舗数"),
        "焼肉 売上高": (["全体", "焼肉"], "焼肉 売上高", "全店 売上高"),
        "焼肉 店舗数": (["全体", "焼肉"], "焼肉 店舗数", "全店 店舗数"),
        "その他専門店 売上高": (["全体", "その他専門店"], "その他専門店 売上高", "全店 売上高"),
        "その他専門店 店舗数": (["全体", "その他専門店"], "その他専門店 店舗数", "全店 店舗数"),
    }
    if got != want:
        _fail(f"33580 tree rows: {got!r}")
    th_tree = tree_payload("33580", th_rows, load_monthly_trees())
    if th_tree is None or th_tree.get("tree_auto"):
        _fail("33580 must use the config tree")
    if th_tree["tree_metrics"] != [
        "連結売上高", "全店 売上高", "売上高", "客数", "客単価", "全店 店舗数", "店舗数",
    ]:
        _fail(f"33580 metrics: {th_tree!r}")
    th_root = th_tree["tree"][0]
    if [n["name"] for n in th_root["children"]] != [
        "主要子会社別売上高", "ラーメン", "焼肉", "その他専門店",
    ]:
        _fail(f"33580 section order: {th_root['children']!r}")
    th_sub = th_root["children"][0]
    if not th_sub["heading"] or th_sub["part"] or th_sub["elements"]:
        _fail(f"33580 subsidiaries must be a heading: {th_sub!r}")
    if [n["name"] for n in th_sub["children"]] != [
        "ワイエスフード", "Yappa", "KINKA FAMILY JAPAN", "バーガーレボリューション", "食べる", "SBWorks",
    ]:
        _fail(f"33580 subsidiary order: {th_sub['children']!r}")
    if th_root["children"][1]["elements"] != [
        "ラーメン 売上高", "ラーメン 売上高(前年比)", "ラーメン 客数(前年比)",
        "ラーメン 客単価(前年比)", "ラーメン 店舗数",
    ]:
        _fail(f"33580 ramen rows: {th_root['children'][1]!r}")


def test_monthly_auto_tree() -> None:
    """config に tree の無い発行体。group は根の子の節（ゲートしない）、内訳はその節の子、指標は葉から (前年比) を外したもの。"""
    from app.monthly.tree import auto_tree_payload, build_auto_tree

    y26 = [
        ("直営+FC 全店 売上高(前年比)", "yoy_index"),
        ("直営+FC 既存 売上高(前年比)", "yoy_index"),
        ("全店 売上高 ドラッグ(前年比)", "yoy_index"),
        ("焼肉 直営+FC 既存 売上高(前年比)", "yoy_index"),
        ("焼肉 直営 全店 店舗数", "number"),
        ("日本 直営 全店 店舗数", "number"),
        ("日本+海外 直営+FC 全店 店舗数", "number"),
        ("焼肉 直営+FC 全店 店舗数", "number"),
        ("庄や 店舗数", "number"),
    ]
    # ラーメンは 2025 年だけ。1 行でも節にする（9979 の 庄や 店舗数 と同じ）
    y25 = [("ラーメン 直営+FC 既存 売上高(前年比)", "yoy_index")]
    auto = build_auto_tree("99991", {2026: y26, 2025: y25})
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in auto.rows.items()}
    want = {
        "直営+FC 全店 売上高(前年比)": (["全体"], "直営+FC 全店 売上高(前年比)", "直営+FC 全店 売上高"),
        "直営+FC 既存 売上高(前年比)": (["全体"], "直営+FC 既存 売上高(前年比)", "直営+FC 既存 売上高"),
        "全店 売上高 ドラッグ(前年比)": (["全体", "ドラッグ"], "全店 売上高(前年比)", "全店 売上高"),
        "焼肉 直営+FC 既存 売上高(前年比)": (["全体", "焼肉"], "直営+FC 既存 売上高(前年比)", "直営+FC 既存 売上高"),
        "焼肉 直営 全店 店舗数": (["全体", "焼肉"], "直営 全店 店舗数", "直営 全店 店舗数"),
        # 節にしか無い指標は 日本 付きの会社の行を親にする
        "日本 直営 全店 店舗数": (["全体"], "日本 直営 全店 店舗数", "直営 全店 店舗数"),
        # 日本+海外 は会社全体の合計。根に置いて葉から剥がす
        "日本+海外 直営+FC 全店 店舗数": (["全体"], "直営+FC 全店 店舗数", "直営+FC 全店 店舗数"),
        "焼肉 直営+FC 全店 店舗数": (["全体", "焼肉"], "直営+FC 全店 店舗数", "直営+FC 全店 店舗数"),
        "庄や 店舗数": (["全体", "庄や"], "店舗数", "店舗数"),
        "ラーメン 直営+FC 既存 売上高(前年比)": (["全体", "ラーメン"], "直営+FC 既存 売上高(前年比)", "直営+FC 既存 売上高"),
    }
    if got != want:
        _fail(f"auto tree rows: {got!r}")
    # 見る順: チャネル → 既存 → 全店 → 売上高 → 店舗
    want_metrics = ["直営+FC 既存 売上高", "直営+FC 全店 売上高", "全店 売上高", "直営+FC 全店 店舗数", "店舗数", "直営 全店 店舗数"]
    if auto.metrics != want_metrics:
        _fail(f"auto tree metrics: {auto.metrics!r}")
    # 指標どうしの親子: 直営 は 直営+FC の子。直営 全店 店舗数 は会社の 日本 の行が代表なので 日本 直営+FC を先に探す（ここには無いので合計）
    # group の中の 合計の語（企業計 / 総 …）は節にも葉にも残さない。指標の語（増減 / 同規模 / 一品 …）は葉に残す
    tok = build_auto_tree("99992", {2026: [
        ("企業計 全店 売上高(前年比)", "yoy_index"),
        ("衣料 企業計 全店 売上高(前年比)", "yoy_index"),
        ("総 全店 売上高(前年比)", "yoy_index"),
        ("チェーン 店舗数", "number"),
        ("ピラティスK 増減 店舗数", "number"),
        ("増減 店舗数", "number"),
        ("同規模 一品 全店 客単価(前年比)", "yoy_index"),
        ("グループ 稼働率", "number"),
    ]})
    got = {k: (v["path"], v["metric"]) for k, v in tok.rows.items()}
    want = {
        "企業計 全店 売上高(前年比)": (["全体"], "全店 売上高"),
        "衣料 企業計 全店 売上高(前年比)": (["全体", "衣料"], "全店 売上高"),
        "総 全店 売上高(前年比)": (["全体"], "全店 売上高"),
        "チェーン 店舗数": (["全体"], "店舗数"),
        "ピラティスK 増減 店舗数": (["全体", "ピラティスK"], "増減 店舗数"),
        "増減 店舗数": (["全体"], "増減 店舗数"),
        "同規模 一品 全店 客単価(前年比)": (["全体"], "同規模 一品 全店 客単価"),
        # 指標の無い行も会社全体の語は剥がす
        "グループ 稼働率": (["全体"], "稼働率"),
    }
    if got != want:
        _fail(f"auto tree total/metric tokens: {got!r}")
    # 指標の語の無い前年比は、その節の金額の売上高が 1 つに決まればその前年比（アスモ）。国付きの 直営 / FC は
    # 国の合計が無ければ国なしの同じチャネルの子（ハードオフ）。件数指標は config/monthly/issuer_metrics.json の語だけ
    monthly_ingest_mod.ISSUER_METRICS["99993"] = frozenset({"受託施設数"})
    try:
        bare = build_auto_tree("99993", {2026: [
            ("全店 売上高", "number"),
            ("合計(前年比)", "yoy_index"),
            ("アスモトレーディング 全店 売上高", "number"),
            ("アスモトレーディング(前年比)", "yoy_index"),
            ("アスモフード 受託施設数", "number"),
            ("100円ショップ 直営 既存(前年比)", "yoy_index"),
            ("直営 店舗数", "number"),
            ("海外 直営 店舗数", "number"),
        ]})
    finally:
        monthly_ingest_mod.ISSUER_METRICS.pop("99993", None)
    got = {k: (v["path"], v["metric"]) for k, v in bare.rows.items()}
    want = {
        "全店 売上高": (["全体"], "全店 売上高"),
        "合計(前年比)": (["全体"], "全店 売上高"),
        "アスモトレーディング 全店 売上高": (["全体", "アスモトレーディング"], "全店 売上高"),
        "アスモトレーディング(前年比)": (["全体", "アスモトレーディング"], "全店 売上高"),
        "アスモフード 受託施設数": (["全体", "アスモフード"], "受託施設数"),
        # チャネル・スコープのある行はそのまま
        "100円ショップ 直営 既存(前年比)": (["全体", "100円ショップ"], "直営 既存"),
        "直営 店舗数": (["全体"], "直営 店舗数"),
        "海外 直営 店舗数": (["全体"], "海外 直営 店舗数"),
    }
    if got != want:
        _fail(f"auto tree bare yoy: {got!r}")
    if bare.metric_parent.get("海外 直営 店舗数") != "直営 店舗数":
        _fail(f"auto tree overseas channel parent: {bare.metric_parent!r}")
    monthly_ingest_mod.ISSUER_METRICS["99994"] = frozenset({"受託施設数", "総レセプト数(鍼灸込み)"})
    try:
        counts = build_auto_tree("99994", {2026: [
            ("アスモフードサービス 受託施設数", "number"),
            ("マッサージ事業 総レセプト数(鍼灸込み) 直営", "number"),
            ("数 出店", "number"),
        ]})
    finally:
        monthly_ingest_mod.ISSUER_METRICS.pop("99994", None)
    got_counts = {k: (v["path"], v["leaf"], v["metric"]) for k, v in counts.rows.items()}
    want_counts = {
        "アスモフードサービス 受託施設数": (
            ["全体", "アスモフードサービス"], "受託施設数", "受託施設数",
        ),
        "マッサージ事業 総レセプト数(鍼灸込み) 直営": (
            ["全体", "マッサージ事業"], "総レセプト数(鍼灸込み) 直営",
            "総レセプト数(鍼灸込み) 直営",
        ),
        "数 出店": (["全体", "数"], "出店", "出店"),
    }
    if got_counts != want_counts:
        _fail(f"auto tree count metrics: {got_counts!r}")
    if auto.metric_parent != {"直営 全店 店舗数": "直営+FC 全店 店舗数"}:
        _fail(f"auto metric parents: {auto.metric_parent!r}")
    from app.monthly.tree import auto_metric_parents
    got_parent = auto_metric_parents(
        ["直営+FC 既存 売上高", "直営 既存 売上高", "直営+FC 全店 店舗数", "日本 直営+FC 全店 店舗数",
         "海外 全店 店舗数", "直営 全店 店舗数", "FC 全店 店舗数", "店舗数", "直営 店舗数"],
        ["直営 全店 店舗数", "FC 全店 店舗数"],
    )
    want_parent = {
        "直営 既存 売上高": "直営+FC 既存 売上高",
        "日本 直営+FC 全店 店舗数": "直営+FC 全店 店舗数",
        "海外 全店 店舗数": "直営+FC 全店 店舗数",
        "直営 全店 店舗数": "日本 直営+FC 全店 店舗数",
        "FC 全店 店舗数": "日本 直営+FC 全店 店舗数",
        "直営 店舗数": "店舗数",
    }
    if got_parent != want_parent:
        _fail(f"metric parents (物語 / 9979): {got_parent!r}")
    t25 = auto_tree_payload(auto, [e for e, _ in y25])
    root = t25["tree"][0]
    if [n["name"] for n in root["children"]] != ["ドラッグ", "焼肉", "庄や", "ラーメン"]:
        _fail(f"auto tree shape must not change by year: {root!r}")
    if root["children"][3]["elements"] != ["ラーメン 直営+FC 既存 売上高(前年比)"] or root["elements"]:
        _fail(f"2025 elements: {root!r}")
    if not t25["tree_auto"] or t25["tree_metrics"] != auto.metrics or t25["tree_metric_parent"] != auto.metric_parent:
        _fail(f"auto payload keys: {t25!r}")
    lone = build_auto_tree("99992", {2026: [("全店 売上高(前年比)", "yoy_index"), ("既存 売上高(前年比)", "yoy_index")]})
    if [n for n, _ in lone.nodes] != ["全体"] or lone.nodes[0][1] or lone.metrics != ["既存 売上高", "全店 売上高"]:
        _fail(f"会社だけの発行体は根 1 つ、既存が先: {lone.metrics!r}")
    # 柿安: 会社は `全社 売上高`、事業は `売上高`。1 つの指標 売上高 にまとまり、会社の行が代表
    kakiyasu = build_auto_tree("22940", {2026: [
        ("精肉事業 売上高", "number"), ("精肉事業 既存 売上高", "number"), ("惣菜事業 売上高", "number"),
        ("全社 売上高", "number"), ("全社 売上高(前年比)", "yoy_index"), ("既存 売上高", "number"),
    ]})
    if kakiyasu.metrics != ["既存 売上高", "売上高"]:
        _fail(f"全社 売上高 と 売上高 は 1 つの指標: {kakiyasu.metrics!r}")
    if {e: kakiyasu.rows[e]["metric"] for e in ("全社 売上高", "全社 売上高(前年比)", "精肉事業 売上高")} != {
            "全社 売上高": "売上高", "全社 売上高(前年比)": "売上高", "精肉事業 売上高": "売上高"}:
        _fail(f"全社 の会社の行は 売上高 に寄る: {kakiyasu.rows!r}")


def _assert_jfront_tree_and_merge(c) -> None:
    """J.フロント: 表1と表2の同じ数字は 1 行に寄る。食い違いは 409。GET は木を返す。"""
    code = "30860"
    _add_fy_end(code, date(2026, 4, 10), date(2026, 2, 28), "tree-30860")
    rows = [
        {"element": "百貨店事業 売上収益(IFRS)", "unit": "yoy_pct", "values": [-0.3]},
        {"element": "百貨店事業 総額 売上高", "unit": "yoy_pct", "values": [-0.8]},
        {"element": "大丸 心斎橋店 売上高", "unit": "yoy_pct", "values": [2.1]},
        {"element": "大丸 心斎橋店 入店 客数", "unit": "yoy_pct", "values": [-7.1]},
        {"element": "百貨店事業合計 売上高", "unit": "yoy_pct", "values": [-0.8]},
        {"element": "百貨店事業合計 入店 客数", "unit": "yoy_pct", "values": [-4.4]},
    ]
    body = {"months": [8], "as_of": "2026-09-15", "rows": rows}
    bad = {**body, "rows": [*rows[:-2], {**rows[-2], "values": [-0.9]}]}
    r = c.post(f"/api/v1/tables/{code}/ingest", json=bad)
    _expect(r, 409, "merge conflict")
    if r.json()["error"]["code"] != "ROW_MERGE_CONFLICT":
        _fail(f"merge conflict code: {r.text}")
    r = c.post(f"/api/v1/tables/{code}/ingest", json=body)
    _expect(r, 200, "jfront ingest")
    got = r.json()
    names = [u["element"] for u in got["updated"]]
    if names.count("百貨店事業 総額 売上高(前年比)") != 1 or any("百貨店事業合計" in n for n in names):
        _fail(f"merged rows: {names!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "jfront GET")
    d = r.json()
    by = {e["element"]: e for e in d["elements"]}
    if len(by) != 5 or by["百貨店事業 総額 売上高(前年比)"]["values"]["8"] != 99.2:
        _fail(f"jfront rows: {sorted(by)!r}")
    if by["百貨店事業 入店 客数(前年比)"]["values"]["8"] != 95.6:
        _fail("百貨店事業合計 入店 客数 must land on 百貨店事業 入店 客数")
    shin = by["大丸 心斎橋店 入店 客数(前年比)"]["tree"]
    if shin != {"path": ["連結", "百貨店事業", "大丸松坂屋百貨店", "店計", "大丸", "心斎橋店"],
                "leaf": "入店 客数(前年比)", "metric": "入店客数"}:
        _fail(f"心斎橋店 tree: {shin!r}")
    if by["百貨店事業 売上収益(IFRS)(前年比)"]["tree"]["metric"] != "売上収益(IFRS)":
        _fail("IFRS は百貨店事業の子ではなく同じ節の別の指標")
    if d.get("tree_metrics") != ["総額売上高", "売上収益(IFRS)", "入店客数"]:
        _fail(f"tree_metrics: {d.get('tree_metrics')!r}")
    tree = d.get("tree") or []
    if [n["name"] for n in tree] != ["連結"]:
        _fail(f"tree roots: {tree!r}")
    dept = tree[0]["children"][0]
    if dept["name"] != "百貨店事業" or dept["elements"][0] != "百貨店事業 総額 売上高(前年比)":
        _fail(f"百貨店事業 node: {dept!r}")
    r = c.get("/api/v1/tables/31800/2026")
    d = r.json()
    if not d.get("tree_auto") or [n["name"] for n in d.get("tree") or []] != ["全体"]:
        _fail(f"issuer without config tree must get the auto tree: {d.get('tree')!r}")
    if not all(e.get("tree", {}).get("path", [None])[0] == "全体" for e in d["elements"]):
        _fail("auto tree must place every row")
    if "tree_auto" in by["百貨店事業 総額 売上高(前年比)"] or "tree_auto" in d["elements"][0]:
        _fail("tree_auto is a table key")


def _assert_ingest_dry_run_only_months(c) -> None:
    """dry_run は書かない。only_months は mapping 後の暦年だけ書く。"""
    from app.db import SessionLocal
    from app.models import MonthlyIssuer, MonthlyTable, MonthlyValue

    code = "31800"
    _add_fy_end(code, date(2026, 5, 8), date(2026, 3, 31), "dry-31800")
    months = [4, 5, 6, 7, 8]
    values = {str(m): float(m) for m in months}
    body = {
        "months": months,
        "rows": [{"element": "売上高", "unit": "number", "values": values}],
        "as_of": "2026-08-05",
    }
    r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, "dry_run": True})
    _expect(r, 200, "dry_run")
    got = r.json()
    if got.get("dry_run") is not True or got.get("fy_end_month") != 3 or got.get("layout") != "fiscal":
        _fail(f"dry_run header: {got!r}")
    if got.get("mapping") != {str(m): 2026 for m in months}:
        _fail(f"dry_run mapping: {got.get('mapping')!r}")
    if got.get("updated") != [{"year": 2026, "element": "売上高", "months": months}]:
        _fail(f"dry_run updated: {got.get('updated')!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 404, "dry_run writes nothing")
    db = SessionLocal()
    try:
        if db.get(MonthlyIssuer, code) is not None or db.get(MonthlyTable, (code, 2026)) is not None:
            _fail("dry_run created monthly rows")
    finally:
        db.close()

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        **body, "only_months": ["2026-07", "2026-08"],
    })
    _expect(r, 200, "only_months")
    got = r.json()
    if "dry_run" in got:
        _fail(f"write must not set dry_run: {got!r}")
    if got.get("updated") != [{"year": 2026, "element": "売上高", "months": [7, 8]}]:
        _fail(f"only_months updated: {got.get('updated')!r}")
    if got.get("mapping") != {str(m): 2026 for m in months}:
        _fail("only_months must keep the full mapping")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET after only_months")
    vals = r.json()["elements"][0]["values"]
    _assert_calendar_values(vals, {"7": 7.0, "8": 8.0}, "only_months")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        **body,
        "dry_run": True,
        "only_months": ["2026-04"],
        "rows": [{"element": "売上高", "unit": "number", "values": {"4": 99.0, "7": 70.0}}],
    })
    _expect(r, 200, "dry_run only_months")
    if r.json().get("updated") != [{"year": 2026, "element": "売上高", "months": [4]}]:
        _fail(f"dry_run only_months updated: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    vals = r.json()["elements"][0]["values"]
    if float(vals["7"]) != 7.0 or vals["4"] is not None:
        _fail(f"dry_run changed cells: {vals!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, "only_months": ["2025-07"]})
    _expect(r, 200, "only_months miss")
    if r.json().get("updated") != []:
        _fail(f"miss updated: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 404, "miss year")

    for bad, label in (
        ({"dry_run": "yes"}, "dry_run type"),
        ({"dry_run": 1}, "dry_run int"),
        ({"only_months": []}, "empty only_months"),
        ({"only_months": "2026-07"}, "only_months string"),
        ({"only_months": ["2026-13"]}, "only_months month"),
        ({"only_months": ["2026-7"]}, "only_months pad"),
    ):
        r = c.post(f"/api/v1/tables/{code}/ingest", json={**body, **bad})
        _expect(r, 400, label)
        if _err(r)["code"] != "INVALID_PARAM":
            _fail(f"{label}: {r.json()!r}")

    wrap = "31820"
    _add_fy_end(wrap, date(2026, 5, 1), date(2026, 2, 28), "dry-31820")
    r = c.post(f"/api/v1/tables/{wrap}/ingest", json={
        "months": [11, 12, 1, 2],
        "rows": [{"element": "売上高", "unit": "number", "values": {"11": 11, "12": 12, "1": 1, "2": 2}}],
        "as_of": "2026-08-31",
        "only_months": ["2026-01"],
    })
    _expect(r, 200, "year wrap only_months")
    if r.json().get("layout") != "partial":
        _fail(f"wrap layout: {r.json()!r}")
    if r.json().get("updated") != [{"year": 2026, "element": "売上高", "months": [1]}]:
        _fail(f"wrap updated: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{wrap}/2025")
    _expect(r, 404, "wrap must not write 2025")
    r = c.get(f"/api/v1/tables/{wrap}/2026")
    _expect(r, 200, "wrap 2026")
    _assert_calendar_values(r.json()["elements"][0]["values"], {"1": 1.0}, "wrap january")

    dup = "31830"
    _add_fy_end(dup, date(2026, 5, 1), date(2026, 2, 28), "dry-31830")
    dup_months = [3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    r = c.post(f"/api/v1/tables/{dup}/ingest", json={
        "months": dup_months,
        "rows": [{"element": "売上高", "unit": "number", "values": [float(i) for i in range(13)]}],
        "as_of": "2026-08-31",
        "only_months": ["2026-03", "2026-01"],
    })
    _expect(r, 200, "duplicate months only_months")
    updated = r.json().get("updated")
    if updated != [{"year": 2026, "element": "売上高", "months": [1, 3]}]:
        _fail(f"dup updated: {updated!r}")
    if not isinstance(r.json().get("mapping"), list) or len(r.json()["mapping"]) != 13:
        _fail(f"dup mapping: {r.json().get('mapping')!r}")
    r = c.get(f"/api/v1/tables/{dup}/2026")
    _expect(r, 200, "dup 2026")
    _assert_calendar_values(r.json()["elements"][0]["values"], {"1": 10.0, "3": 12.0}, "dup kept")
    r = c.get(f"/api/v1/tables/{dup}/2025")
    _expect(r, 404, "dup must not write the earlier March")
    db = SessionLocal()
    try:
        early = db.get(MonthlyValue, (dup, 2025, "売上高", 3))
        if early is not None:
            _fail(f"earlier March written: {early.value}")
    finally:
        db.close()


def main() -> None:
    # display is index 100=flat (not +X.X% / ▲). number rows round; unit goes on the label.
    if format_monthly_cell(None, "yoy_index") != "-":
        _fail("empty cell")
    if format_monthly_cell(105.2, "yoy_index") != "105.2":
        _fail(f"yoy_index 105.2: {format_monthly_cell(105.2, 'yoy_index')!r}")
    if format_monthly_cell(125.8, "yoy_index") != "125.8":
        _fail(f"yoy_index 125.8: {format_monthly_cell(125.8, 'yoy_index')!r}")
    if format_monthly_cell(94.8, "yoy_index") != "94.8":
        _fail(f"yoy_index 94.8: {format_monthly_cell(94.8, 'yoy_index')!r}")
    if format_monthly_cell(92.9, "yoy_index") != "92.9":
        _fail(f"yoy_index 92.9: {format_monthly_cell(92.9, 'yoy_index')!r}")
    if format_monthly_cell(303, "number") != "303":
        _fail(f"number 303: {format_monthly_cell(303, 'number')!r}")
    if format_monthly_cell(None, "number") != "-":
        _fail("empty number")
    if format_monthly_cell(0, "number") != "0":
        _fail(f"zero number: {format_monthly_cell(0, 'number')!r}")
    if format_monthly_cell(29296.646549, "number", "受注高") != "29297":
        _fail(f"受注高 display: {format_monthly_cell(29296.646549, 'number', '受注高')!r}")
    if format_monthly_cell(97.48, "number", "家賃ベース入居率") != "97.48":
        _fail(f"入居率 display: {format_monthly_cell(97.48, 'number', '家賃ベース入居率')!r}")
    if format_monthly_cell(97.48, "number", "家賃ベース入居率 事業用") != "97.48":
        _fail("入居率 事業用 must use the same digits")
    if format_monthly_cell(1.2640801936119992, "number", "空室在庫") != "1.26":
        _fail(f"空室在庫 display: {format_monthly_cell(1.2640801936119992, 'number', '空室在庫')!r}")
    if format_monthly_cell(30470.0, "number", "空室数 既存") != "30470":
        _fail(f"空室数 既存 display: {format_monthly_cell(30470.0, 'number', '空室数 既存')!r}")
    if format_monthly_cell(2884.77, "number", "営業人員数") != "2885":
        _fail(f"営業人員数 display: {format_monthly_cell(2884.77, 'number', '営業人員数')!r}")
    if format_monthly_cell(1326.1196840995988, "number", "1人当たり受注高") != "1326":
        _fail(f"1人当たり受注高 display: {format_monthly_cell(1326.1196840995988, 'number', '1人当たり受注高')!r}")
    if format_monthly_cell(125.8, "yoy_index", "受注高") != "125.8":
        _fail("yoy_index must ignore display units")
    if format_monthly_cell(303, "number", "店舗数") != "303":
        _fail("unknown number rows stay stored")
    if monthly_display_label("受注高", "number") != "受注高（百万円）":
        _fail(f"受注高 label: {monthly_display_label('受注高', 'number')!r}")
    if monthly_display_label("受注高（百万円）", "number") != "受注高（百万円）":
        _fail("display label must be idempotent")
    if monthly_display_label("空室数 （戸） 既存（戸）", "number") != "空室数 既存（戸）":
        _fail("display label must strip a duplicated unit before appending")
    if monthly_display_label("家賃ベース入居率 事業用", "number") != "家賃ベース入居率 事業用（%）":
        _fail("入居率 label")
    if monthly_display_label("空室数 既存", "number") != "空室数 既存（戸）":
        _fail("空室数 既存 label")
    if monthly_display_label("1人当たり受注高", "number") != "1人当たり受注高（万円）":
        _fail("1人当たり受注高 must not take 受注高 百万円")
    if monthly_display_label("全店 売上高(前年比)", "yoy_index") != "全店 売上高(前年比)":
        _fail("yoy label must stay stored")
    if monthly_display_label("店舗数", "number") != "店舗数":
        _fail("unknown number labels stay stored")
    try:
        parse_monthly_unit("percent")
    except MonthlyIngestError as exc:
        if exc.status != 400 or exc.code != "INVALID_PARAM":
            _fail(f"invalid unit: {exc.status} {exc.code}")
    else:
        _fail("invalid unit should 400")
    test_apply_incoming_unit_conflict_and_force()
    test_monthly_tree_assign()
    test_monthly_auto_tree()
    test_startup_keeps_names_and_job_renames()
    test_migrate_restores_dropped_columns()
    _assert_monthly_year_window()
    _assert_monthly_year_color_stable()
    _assert_union_collapses_yoy_display_names()
    _assert_segment_tail_row_order()
    html = _spa_source()
    if "fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell")
    if "function fmtMonthlyCell" not in html:
        _fail("index.html missing fmtMonthlyCell function")
    if "function monthlyDisplayUnit" not in html:
        _fail("index.html missing monthlyDisplayUnit")
    if "const MONTHLY_DISPLAY_UNITS" not in html:
        _fail("index.html missing MONTHLY_DISPLAY_UNITS")
    if "fmtMonthlyCell(v, unit, id)" not in html:
        _fail("table cells must pass element into fmtMonthlyCell")
    js_fn = html.find("const MONTHLY_DISPLAY_UNITS")
    js_end = html.find("function monthlyDisplayUnit")
    if js_fn < 0 or js_end <= js_fn:
        _fail("MONTHLY_DISPLAY_UNITS / monthlyDisplayUnit bounds")
    js_rows = re.findall(r'\["([^"]+)", "([^"]+)", (\d+)\]', html[js_fn:js_end])
    py_rows = [(token, suffix, str(digits)) for token, suffix, digits in MONTHLY_DISPLAY_UNITS]
    if js_rows != py_rows:
        _fail(f"JS/Python display units drifted: {js_rows} vs {py_rows}")
    cell_fn = html.find("function fmtMonthlyCell")
    if cell_fn < 0:
        _fail("fmtMonthlyCell bounds")
    cell_end = html.find("window.setMonthlyGroup")
    if cell_end <= cell_fn:
        _fail("fmtMonthlyCell / window.setMonthlyGroup bounds")
    cell_body = html[cell_fn:cell_end]
    if 'return "-"' not in cell_body:
        _fail("fmtMonthlyCell empty must display -")
    if 'return ""' in cell_body:
        _fail("fmtMonthlyCell must not return blank for empty")
    if "+= 100" in cell_body or "n+100" in cell_body.replace(" ", ""):
        _fail("fmtMonthlyCell must not convert leftover yoy_pct")
    if "spec.suffix" in cell_body:
        _fail("fmtMonthlyCell must not append the unit to the number")
    yoy_fn = html.find("function fmtMonthlyYoyList")
    if yoy_fn < 0:
        _fail("fmtMonthlyYoyList missing")
    yoy_body = html[yoy_fn:yoy_fn + 220]
    if 'return "-"' not in yoy_body:
        _fail("fmtMonthlyYoyList empty must display -")
    if "function fmtMonthlyElement" in html:
        _fail("SPA must not re-parse row names with fmtMonthlyElement")
    if "function parseMonthlyLayers" in html:
        _fail("SPA must not keep parseMonthlyLayers")
    if "L.display" not in html:
        _fail("monthlyRowOrder must fold on label.display")
    if "▲" in html or "+X.X%" in html:
        _fail("index.html still has percent/▲ monthly display")
    _assert_monthly_spa(html)
    _assert_monthly_layout_js()
    if canonicalize_element_label("売上高前年同月比", "yoy_index") != "売上高(前年比)":
        _fail("canonicalize strip+suffix")
    if canonicalize_element_label("既存売上高(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("canonicalize no double")
    if canonicalize_element_label("売上高前年同月比", "number") != "売上高":
        _fail("canonicalize number tokens")

    c = TestClient(app)
    code, year = "72030", "2024"
    base = f"/api/v1/tables/{code}/{year}"

    r = c.get(base)
    _expect(r, 404, "GET missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"GET missing table code: {r.json()!r}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables empty")
    if r.json() != []:
        _fail(f"empty tables: {r.json()!r}")

    r = c.get(f"/api/v1/tables/{code}")
    _expect(r, 200, "GET years empty")
    if r.json() != {"code": code, "years": []}:
        _fail(f"empty years: {r.json()!r}")

    r = c.post("/api/v1/tables/72/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 400, "bad code short")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"bad code: {r.json()!r}")

    r = c.post("/api/v1/tables/720300/2024/elements", json={"element": "売上高前年比"})
    _expect(r, 400, "bad code long")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"bad code long: {r.json()!r}")

    r = c.post("/api/v1/tables/7203/24/elements", json={"element": "売上高前年比"})
    _expect(r, 400, "bad year")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"bad year: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "", "unit": "number"})
    _expect(r, 400, "empty element")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"empty element: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "あ" * 51, "unit": "number"})
    _expect(r, 400, "element too long")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"long element: {r.json()!r}")

    r = c.post("/api/v1/tables/30320/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 404, "POST without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"POST without master: {r.json()!r}")

    _ensure_stock(code)

    r = c.post(f"{base}/elements", json={"element": " 売上高 ", "unit": "number"})
    _expect(r, 201, "create element")
    if r.json() != {"code": code, "year": 2024, "element": "売上高",
                    "unit": "number", "created": True}:
        _fail(f"create: {r.json()!r}")

    r = c.get(base)
    _expect(r, 200, "GET after auto-create")
    if r.json()["code"] != code or r.json()["year"] != 2024:
        _fail(f"get table: {r.json()!r}")
    if r.json()["elements"]:
        _fail(f"empty year row must be omitted: {r.json()!r}")

    r = c.get(f"/api/v1/tables/{code}")
    _expect(r, 200, "GET years after create")
    if r.json() != {"code": code, "years": [2024]}:
        _fail(f"years: {r.json()!r}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables one code")
    body = r.json()
    if not isinstance(body, list) or len(body) != 1:
        _fail(f"tables one code: {body!r}")
    one = body[0]
    if one.get("code") != code or one.get("name") is not None or one.get("years") != [2024]:
        _fail(f"tables one code core: {one!r}")
    if one.get("industry") != "その他":
        _fail(f"default industry: {one!r}")
    if one.get("last_ingested_at") is not None:
        _fail(f"no ingest yet: {one!r}")
    if one.get("is_new") is not False or one.get("ingested_today") is not False:
        _fail(f"new flags before ingest: {one!r}")
    if "all_store_yoy" not in one or "existing_store_yoy" not in one:
        _fail(f"missing yoy fields: {one!r}")

    r = c.post(f"{base}/elements", json={"element": "売上高", "unit": "number"})
    _expect(r, 200, "idempotent exact")
    if r.json() != {"code": code, "year": 2024, "element": "売上高",
                    "unit": "number", "created": False}:
        _fail(f"idempotent: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": " 売上高 ", "unit": "number"})
    _expect(r, 200, "idempotent trimmed")
    if r.json()["created"] is not False:
        _fail(f"idempotent trimmed: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "number drops 前年比 token")
    if r.json()["element"] != "売上高" or r.json()["created"] is not False:
        _fail(f"売上高前年比 number folds: {r.json()!r}")

    # similar-element 409 (空白差。トークン正規化後も別名)
    r = c.post(f"{base}/elements", json={"element": "世界観ビジネス", "unit": "number"})
    _expect(r, 201, "segment create")
    r = c.post(f"{base}/elements", json={"element": "世界観 ビジネス", "unit": "number"})
    _expect(r, 409, "similar")
    err = _err(r)
    if err["code"] != "ELEMENT_SIMILAR_EXISTS" or err["message"] != SIMILAR_MSG:
        _fail(f"similar err: {r.json()!r}")
    if err.get("similar") != ["世界観ビジネス"]:
        _fail(f"similar list: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "世界観 ビジネス", "force": True, "unit": "number"})
    _expect(r, 201, "force create")
    if r.json() != {"code": code, "year": 2024, "element": "世界観 ビジネス",
                    "unit": "number", "created": True}:
        _fail(f"force: {r.json()!r}")

    r = c.post(f"{base}/elements", json={"element": "営業利益", "unit": "number"})
    _expect(r, 201, "third element")

    r = c.put("/api/v1/tables/7203/2025/values",
              json={"element": "営業利益", "month": 4, "value": 1})
    _expect(r, 404, "PUT missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"PUT missing table: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "未登録要素", "month": 4, "value": 1.0})
    _expect(r, 404, "unregistered element")
    if _err(r)["code"] != "ELEMENT_NOT_FOUND":
        _fail(f"ELEMENT_NOT_FOUND: {r.json()!r}")
    r = c.get(base)
    names = [e["element"] for e in r.json()["elements"]]
    if "未登録要素" in names:
        _fail("values PUT must not auto-register element")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": "105.2%"})
    _expect(r, 400, "percent string")
    err = _err(r)
    if err["code"] != "INVALID_VALUE" or err["message"] != INVALID_VALUE_MSG:
        _fail(f"105.2%: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": "1,234百万円"})
    _expect(r, 400, "unit string")
    if _err(r)["code"] != "INVALID_VALUE" or _err(r)["message"] != INVALID_VALUE_MSG:
        _fail(f"1,234百万円: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 0, "value": 1})
    _expect(r, 400, "month 0")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month 0: {r.json()!r}")
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 13, "value": 1})
    _expect(r, 400, "month 13")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month 13: {r.json()!r}")
    r = c.put(f"{base}/values", json={"element": "売上高", "month": "4", "value": 1})
    _expect(r, 400, "month string")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"month string: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": 105.2})
    _expect(r, 200, "put value")
    if r.json() != {"code": code, "year": 2024, "element": "売上高", "updated": [4]}:
        _fail(f"put: {r.json()!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 6, "value": 110})
    _expect(r, 200, "put month 6")

    r = c.get(base)
    _expect(r, 200, "GET values")
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(by_name["売上高"], {"4": 105.2, "6": 110}, "omit empty")
    if "世界観ビジネス" in by_name:
        _fail(f"empty element must be omitted: {by_name!r}")
    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": 99.9})
    _expect(r, 200, "overwrite")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if float(by_name["売上高"]["4"]) != 99.9:
        _fail(f"overwrite: {by_name['売上高']!r}")

    r = c.put(f"{base}/values", json={"element": "売上高", "month": 4, "value": None})
    _expect(r, 200, "delete cell")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(by_name["売上高"], {"6": 110}, "null delete")
    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": 10}, {"month": 2, "value": "105.2%"}],
    })
    _expect(r, 400, "batch invalid")
    if _err(r)["code"] != "INVALID_VALUE":
        _fail(f"batch invalid: {r.json()!r}")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    if "営業利益" in by_name:
        _fail(f"batch leaked: {by_name.get('営業利益')!r}")

    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": 105.2}, {"month": 2, "value": 98.7}],
    })
    _expect(r, 200, "batch ok")
    if r.json()["updated"] != [1, 2]:
        _fail(f"batch updated: {r.json()!r}")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    ov = by_name["営業利益"]
    if float(ov["1"]) != 105.2 or float(ov["2"]) != 98.7:
        _fail(f"batch values: {ov!r}")

    r = c.put(f"{base}/values/batch", json={
        "element": "営業利益",
        "values": [{"month": 1, "value": None}, {"month": 3, "value": 1.5}],
    })
    _expect(r, 200, "batch mixed")
    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(
        by_name["営業利益"], {"2": 98.7, "3": 1.5}, "batch null")
    r = c.get(base)
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["売上高", "営業利益"]:
        _fail(f"registration order: {names!r}")

    r = c.post("/api/v1/tables/7203/2025/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 201, "other year")
    r = c.get(f"/api/v1/tables/{code}")
    if r.json()["years"] != [2024, 2025]:
        _fail(f"years multi: {r.json()!r}")

    r = c.post("/api/v1/tables/72030/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "5-digit same issuer")
    r = c.get("/api/v1/tables/7203")
    if r.json() != {"code": "72030", "years": [2024, 2025]}:
        _fail(f"4-digit years: {r.json()!r}")
    r = c.get("/api/v1/tables/72030")
    if r.json() != {"code": "72030", "years": [2024, 2025]}:
        _fail(f"5-digit years: {r.json()!r}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables list")
    body = r.json()
    if not isinstance(body, list):
        _fail(f"tables should be a list: {body!r}")
    if [x.get("code") for x in body] != ["72030"]:
        _fail(f"tables sort by code: {body!r}")
    t72030 = body[0]
    if t72030["years"] != [2024, 2025]:
        _fail(f"years sorted: {t72030!r}")
    if t72030.get("name") not in (None, ""):
        _fail(f"missing stock name should be null/empty: {t72030!r}")

    from app.db import SessionLocal
    from app.models import Stock

    db = SessionLocal()
    try:
        row = db.get(Stock, "72030")
        if row is None:
            db.add(Stock(code="72030", name="トヨタ自動車"))
        else:
            row.name = "トヨタ自動車"
        db.commit()
    finally:
        db.close()

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables with stock name")
    by = {x["code"]: x for x in r.json()}
    if set(by) != {"72030"}:
        _fail(f"unique codes after stock: {list(by)!r}")
    if by["72030"]["name"] != "トヨタ自動車":
        _fail(f"name from stocks: {by['72030']!r}")
    if by["72030"]["years"] != [2024, 2025]:
        _fail(f"years after stock join: {by!r}")

    # JPX alphanumeric (Intermestic 262A / canonical 262A0)
    if _parse_code("262A") != "262A0":
        _fail("parse 262A")
    if _parse_code("262A0") != "262A0":
        _fail("parse 262A0")
    if _parse_code("262a") != "262A0":
        _fail("parse 262a")
    if _parse_code("7203") != "72030":
        _fail("parse 7203")
    if _parse_code("72030") != "72030":
        _fail("72030")
    if _parse_code("9279") != "92790":
        _fail("parse 9279")
    if _parse_code("92790") != "92790":
        _fail("92790")
    if _parse_code("7649") != "76490":
        _fail("parse 7649")
    for bad in ("abc", "12", "262A/../x", "262A.0", "7203/../etc", "A2620"):
        try:
            _parse_code(bad)
        except ApiError as exc:
            if exc.status != 400 or exc.payload["error"]["code"] != "INVALID_PARAM":
                _fail(f"bad code {bad!r}: {exc.payload!r}")
        else:
            _fail(f"should reject {bad!r}")

    _ensure_stock("262A0")
    r = c.post("/api/v1/tables/262A/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 201, "262A create")
    if r.json()["code"] != "262A0":
        _fail(f"262A should canonicalize: {r.json()!r}")
    if r.json()["element"] != "売上高":
        _fail(f"262A token name: {r.json()!r}")
    r = c.get("/api/v1/tables/262A0/2024")
    _expect(r, 200, "GET 262A0 empty")
    if r.json()["code"] != "262A0" or r.json()["elements"]:
        _fail(f"GET 262A0 empty: {r.json()!r}")
    r = c.put("/api/v1/tables/262A0/2024/values",
              json={"element": "売上高", "month": 1, "value": 1.0})
    _expect(r, 200, "262A0 seed value")
    r = c.get("/api/v1/tables/262A0/2024")
    _expect(r, 200, "GET 262A0")
    if r.json()["code"] != "262A0" or [e["element"] for e in r.json()["elements"]] != ["売上高"]:
        _fail(f"GET 262A0: {r.json()!r}")
    r = c.get("/api/v1/tables/262A/2024")
    _expect(r, 200, "GET 262A display")
    if r.json()["code"] != "262A0":
        _fail(f"GET 262A display: {r.json()!r}")
    r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "売上高前年比", "unit": "number"})
    _expect(r, 200, "262A0 idempotent")
    if r.json() != {"code": "262A0", "year": 2024, "element": "売上高",
                    "unit": "number", "created": False}:
        _fail(f"262A0 idempotent: {r.json()!r}")

    r = c.post("/api/v1/tables/abc/2024/elements", json={"element": "x"})
    _expect(r, 400, "abc")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"abc: {r.json()!r}")
    r = c.post("/api/v1/tables/12/2024/elements", json={"element": "x"})
    _expect(r, 400, "12")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"12: {r.json()!r}")
    r = c.post("/api/v1/tables/262A/../x/2024/elements", json={"element": "x"})
    if r.status_code == 201:
        _fail(f"traversal created: {r.json()!r}")

    r = c.get(base)
    _expect(r, 200, "GET units default")
    for el in r.json()["elements"]:
        if el.get("unit") != "number":
            _fail(f"7203 default unit: {el!r}")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "全店売上高", "unit": "yoy_index"})
    _expect(r, 201, "create yoy_index")
    if r.json()["element"] != "全店 売上高(前年比)":
        _fail(f"yoy_index saved name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index" or r.json()["created"] is not True:
        _fail(f"yoy_index create: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "全店売上高(前年比)", "month": 11, "value": 125.8})
    _expect(r, 200, "put yoy_index 125.8")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "売上変化", "unit": "yoy_pct"})
    _expect(r, 201, "create yoy_pct")
    if r.json()["element"] != "売上変化(前年比)":
        _fail(f"yoy_pct saved name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index":
        _fail(f"POST yoy_pct must store yoy_index: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "売上変化(前年比)", "month": 11, "value": 92.9})
    _expect(r, 200, "put stored yoy_index 92.9")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "店舗数", "unit": "number"})
    _expect(r, 201, "create number")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "店舗数", "month": 11, "value": 303})
    _expect(r, 200, "put number 303")

    r = c.get("/api/v1/tables/7203/2026")
    _expect(r, 200, "GET 2026 units")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店 売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"store yoy_index: {by['全店 売上高(前年比)']!r}")
    if format_monthly_cell(float(by["全店 売上高(前年比)"]["values"]["11"]), by["全店 売上高(前年比)"]["unit"]) != "125.8":
        _fail("display yoy_index 125.8")
    if by["売上変化(前年比)"]["unit"] != "yoy_index" or float(by["売上変化(前年比)"]["values"]["11"]) != 92.9:
        _fail(f"PUT stored yoy_index 92.9: {by['売上変化(前年比)']!r}")
    if format_monthly_cell(float(by["売上変化(前年比)"]["values"]["11"]), by["売上変化(前年比)"]["unit"]) != "92.9":
        _fail("display yoy_index 92.9")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"store number: {by['店舗数']!r}")
    if format_monthly_cell(float(by["店舗数"]["values"]["11"]), by["店舗数"]["unit"]) != "303":
        _fail("display number 303")

    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_pct"})
    _expect(r, 200, "PUT unit only")
    if r.json() != {"code": "72030", "year": 2026, "element": "全店 売上高(前年比)", "unit": "yoy_index"}:
        _fail(f"PUT yoy_pct must store yoy_index: {r.json()!r}")
    r = c.get("/api/v1/tables/7203/2026")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店 売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"PUT unit must not convert value: {by['全店 売上高(前年比)']!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "全店売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "PUT unit restore")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "全店売上高(前年比)"})
    _expect(r, 400, "POST omit unit")
    if _err(r)["code"] != "UNIT_REQUIRED":
        _fail(f"omit unit: {r.json()!r}")

    _ensure_stock("60590", "ウチヤマ")
    r = c.post("/api/v1/tables/60590/2026/elements", json={"element": "売上高", "unit": "yoy_index"})
    _expect(r, 201, "60590 yoy 売上高")
    if r.json()["element"] != "売上高(前年比)" or r.json()["created"] is not True:
        _fail(f"60590 yoy short saved: {r.json()!r}")
    r = c.post("/api/v1/tables/60590/2026/elements",
               json={"element": "売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "60590 yoy canonical same")
    if r.json()["created"] is not False or r.json()["element"] != "売上高(前年比)":
        _fail(f"60590 yoy canonical: {r.json()!r}")
    r = c.get("/api/v1/tables/60590/2026")
    if r.json()["elements"]:
        _fail(f"60590 empty rows omitted: {r.json()!r}")

    r = c.post("/api/v1/tables/60590/2026/elements", json={"element": "カラオケ全店 売上高", "unit": "number"})
    _expect(r, 201, "60590 karaoke number")
    r = c.put("/api/v1/tables/60590/2026/elements",
              json={"element": "カラオケ全店 売上高", "unit": "yoy_index"})
    _expect(r, 200, "60590 karaoke PUT unit")
    r = c.post("/api/v1/tables/60590/2026/elements",
               json={"element": "カラオケ全店 売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "60590 karaoke rename")
    if r.json()["created"] is not False or r.json()["element"] != "カラオケ 全店 売上高(前年比)":
        _fail(f"60590 karaoke POST rename: {r.json()!r}")
    r = c.get("/api/v1/tables/60590/2026")
    names = [e["element"] for e in r.json()["elements"]]
    if names:
        _fail(f"60590 after karaoke rename empty: {names!r}")

    r = c.post("/api/v1/tables/7203/2026/elements",
               json={"element": "大きい指数", "unit": "yoy_pct"})
    _expect(r, 201, "yoy_pct with 125.8")
    if r.json()["element"] != "大きい指数(前年比)":
        _fail(f"yoy_pct 大きい指数 name: {r.json()!r}")
    if r.json()["unit"] != "yoy_index":
        _fail(f"POST yoy_pct must store yoy_index: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values",
              json={"element": "大きい指数(前年比)", "month": 1, "value": 125.8})
    _expect(r, 200, "put 125.8 as stored index")
    r = c.get("/api/v1/tables/7203/2026")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["大きい指数(前年比)"]["unit"] != "yoy_index" or float(by["大きい指数(前年比)"]["values"]["1"]) != 125.8:
        _fail(f"PUT must not convert 125.8: {by['大きい指数(前年比)']!r}")

    r = c.post("/api/v1/tables/7203/2026/elements", json={"element": "x", "unit": "percent"})
    _expect(r, 400, "invalid unit POST")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"invalid unit POST: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "店舗数", "unit": "percent"})
    _expect(r, 400, "invalid unit PUT")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"invalid unit PUT: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 400, "PUT missing unit")
    r = c.put("/api/v1/tables/7203/2099/elements",
              json={"element": "店舗数", "unit": "number"})
    _expect(r, 404, "PUT unit missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"PUT missing table: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/elements",
              json={"element": "未登録", "unit": "number"})
    _expect(r, 404, "PUT unit missing element")
    if _err(r)["code"] != "ELEMENT_NOT_FOUND":
        _fail(f"PUT missing element: {r.json()!r}")

    r = c.request("DELETE", "/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 409, "DELETE with values")
    if _err(r)["code"] != "ELEMENT_HAS_VALUES":
        _fail(f"DELETE with values: {r.json()!r}")
    r = c.put("/api/v1/tables/7203/2026/values/batch", json={
        "element": "店舗数",
        "values": [{"month": 11, "value": None}],
    })
    _expect(r, 200, "clear 店舗数 before DELETE")
    r = c.request("DELETE", "/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 200, "DELETE empty element")
    if r.json() != {"code": "72030", "year": 2026, "element": "店舗数", "deleted": True}:
        _fail(f"DELETE body: {r.json()!r}")
    r = c.get("/api/v1/tables/7203/2026")
    names = [e["element"] for e in r.json()["elements"]]
    if "店舗数" in names:
        _fail(f"DELETE leftover: {names!r}")
    r = c.request("DELETE", "/api/v1/tables/7203/2026/elements", json={"element": "店舗数"})
    _expect(r, 404, "DELETE missing element")
    if _err(r)["code"] != "ELEMENT_NOT_FOUND":
        _fail(f"DELETE missing: {r.json()!r}")
    r = c.request("DELETE", "/api/v1/tables/7203/2099/elements", json={"element": "店舗数"})
    _expect(r, 404, "DELETE missing table")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"DELETE missing table: {r.json()!r}")

    r = c.get(base)
    by_name = {e["element"]: e["values"] for e in r.json()["elements"]}
    _assert_calendar_values(
        by_name["営業利益"], {"2": 98.7, "3": 1.5}, "7203 numeric")
    _assert_monthly_row_order(c)
    _assert_known_prod_units(c)
    _assert_ingest_dry_run_only_months(c)
    _assert_jfront_tree_and_merge(c)

    print("ok: monthly tables api")


def _post_monthly_elements(c, code: str, year: int, rows: list[tuple[str, str]]) -> None:
    for name, unit in rows:
        r = c.post(f"/api/v1/tables/{code}/{year}/elements",
                   json={"element": name, "unit": unit})
        if r.status_code not in (200, 201):
            _fail(f"POST {code}/{year} {name!r}: {r.status_code} {r.text[:200]}")
        stored = r.json()["element"]
        r = c.put(f"/api/v1/tables/{code}/{year}/values",
                  json={"element": stored, "month": 1, "value": 1.0})
        if r.status_code != 200:
            _fail(f"PUT {code}/{year} {stored!r}: {r.status_code} {r.text[:200]}")


_MONTHLY_YEAR_COLORS = ("#0077b6", "#2e8b57", "#b8860b", "#8e4ec6")


def _monthly_display_years(years: list[int]) -> list[int]:
    ys = sorted({int(y) for y in years}, reverse=True)
    if not ys:
        return []
    latest = ys[0]
    return [y for y in ys if y >= latest - 2]


def _monthly_year_color(year: int) -> str:
    n = len(_MONTHLY_YEAR_COLORS)
    return _MONTHLY_YEAR_COLORS[int(year) % n]


def _assert_monthly_year_window() -> None:
    """最新暦年とその前2年。2023–2026 なら 2026/2025/2024。新しい年が先。"""
    got = _monthly_display_years([2023, 2024, 2025, 2026])
    if got != [2026, 2025, 2024]:
        _fail(f"last-3-year window 2023-2026: {got!r}")
    if 2023 in got:
        _fail("years older than latest-2 must not render")
    if _monthly_display_years([2026, 2023, 2024, 2025]) != [2026, 2025, 2024]:
        _fail("window must ignore input order")
    if _monthly_display_years([2026, 2023]) != [2026]:
        _fail("gap: only years in [latest, latest-2]")
    if _monthly_display_years([2024]) != [2024]:
        _fail("single year stays")
    if _monthly_display_years([]) != []:
        _fail("empty years")


def _assert_monthly_year_color_stable() -> None:
    """色は暦年。2025 は 2026 の有無で変わらない。"""
    if _monthly_year_color(2025) != _MONTHLY_YEAR_COLORS[2025 % 4]:
        _fail("2025 must use year % 4, not display index")
    colors_with = {y: _monthly_year_color(y) for y in _monthly_display_years([2024, 2025, 2026])}
    colors_without = {y: _monthly_year_color(y) for y in _monthly_display_years([2023, 2024, 2025])}
    if colors_with[2025] != colors_without[2025]:
        _fail(f"2025 color moved when 2026 dropped: {colors_with[2025]!r} vs {colors_without[2025]!r}")
    if colors_with[2024] != colors_without[2024]:
        _fail("2024 color must stay when the window slides")


def _assert_union_collapses_yoy_display_names() -> None:
    """年Aの短い yoy 名と年Bの正規名は表示名で1行。number は分けたまま。"""
    short = "全店 売上高前期比"
    canon = "全店 売上高(前年比)"
    leftover = [
        {"year": 2026, "elements": [
            {"element": short, "unit": "yoy_index", "values": {"1": 110.0}},
            {"element": "店舗数", "unit": "number", "values": {"1": 50.0}},
            {"element": "売上高", "unit": "number", "values": {"1": 1000.0}},
        ]},
        {"year": 2025, "elements": [
            {"element": canon, "unit": "yoy_index", "values": {"1": 105.0}},
            {"element": "店舗数", "unit": "number", "values": {"1": 48.0}},
            {"element": "売上高", "unit": "number", "values": {"1": 900.0}},
        ]},
    ]
    rows = union_monthly_elements(leftover)
    order = [row["element"] for row in rows]
    if order != ["売上高", canon, "店舗数"]:
        _fail(f"cross-year yoy short/canonical must be one row: {order!r}")
    if monthly_row_slot(rows[0]["element"], rows[0]["unit"]) != 1:
        _fail("collapsed yoy 全店売上 stays slot 1")
    if monthly_row_slot("店舗数", "number") != 7:
        _fail("number 店舗数 stays slot 7")
    hit = next(
        e for e in leftover[0]["elements"]
        if canonicalize_element_label(e["element"], e["unit"]) == canon
    )
    if float(hit["values"]["1"]) != 110.0:
        _fail("2026 values come from the short stored row")
    swapped = [row["element"] for row in union_monthly_elements(list(reversed(leftover)))]
    if swapped != order:
        _fail(f"display-name union must ignore input order: {swapped!r}")


def _assert_monthly_row_order(c) -> None:
    """全店→全社→既存店→FC店→セグメント。各塊は 売上高→客数→客単価→出店→退店→店舗数。全社は別スコープ。"""
    if monthly_row_segment("カラオケ全店 売上高(前年比)") != "カラオケ":
        _fail("segment karaoke all-store")
    if monthly_row_segment("飲食既存店 店舗数") != "飲食":
        _fail("segment food existing stores")
    if monthly_row_segment("全店売上高(前年比)") != "":
        _fail("segment empty prefix")
    if monthly_row_segment("全社 売上高(前年比)") != "":
        _fail("全社 is company-wide, not a brand")
    if monthly_row_slot("カラオケ全店 売上高(前年比)", "yoy_index") != 1:
        _fail("slot sales")
    if monthly_row_slot("カラオケ既存店 売上高(前年比)", "yoy_index") != 1:
        _fail("existing sales is still 売上高")
    if monthly_row_slot("カラオケ全店 来客数(前年比)", "yoy_index") != 2:
        _fail("slot customers")
    if monthly_row_slot("カラオケ全店 客単価(前年比)", "yoy_index") != 3:
        _fail("slot unit price")
    if monthly_row_slot("平均単価", "number") != 3:
        _fail("slot 単価")
    if monthly_row_slot("カラオケ全店 店舗数", "number") != 7:
        _fail("slot stores")
    if monthly_row_slot("カラオケ既存店 店舗数", "number") != 7:
        _fail("stores must not be 売上")
    if monthly_row_slot("出店", "number") != 4:
        _fail("slot 出店")
    if monthly_row_slot("退店", "number") != 5:
        _fail("slot 退店")
    if monthly_row_slot("100円ショップ 閉鎖", "number") != 5:
        _fail("閉鎖 folds to 退店 slot")
    if monthly_row_slot("純増 店舗数", "number") != 6:
        _fail("slot 純増 before 店舗数")
    if monthly_row_slot("新設店舗", "number") != 6:
        _fail("slot 新設")
    if monthly_row_slot("改装", "number") != 6:
        _fail("slot 改装")
    if monthly_row_slot("業態転換", "number") != 6:
        _fail("slot 業態転換")
    if monthly_row_slot("全店売上高(前年比)", "yoy_index") != 1:
        _fail("slot MONTHLY_SERIES 全店売上高")
    if monthly_row_slot("全社 売上高(前年比)", "yoy_index") != 1:
        _fail("全社 売上高 is sales")
    if monthly_row_scope_rank("全店 売上高(前年比)") != 0:
        _fail("全店 is scope 0")
    if monthly_row_scope_rank("全社 売上高(前年比)") != 1:
        _fail("全社 is scope 1")
    if monthly_row_scope_rank("既存店 売上高(前年比)") != 2:
        _fail("既存店 is scope 2")
    if monthly_row_scope_rank("売上高") != 0:
        _fail("unlabeled 売上高 stays 全店")
    if monthly_row_scope_rank("全店 店舗数") != 0:
        _fail("全店 店舗数 stays in 全店")
    if monthly_row_scope_rank("既存店 店舗数") != 2:
        _fail("既存店 店舗数 stays in 既存店")
    if monthly_row_scope_rank("合計 店舗数") != 3:
        _fail("合計 店舗数 must not fold into 全店")
    if monthly_row_scope_rank("店舗数") != 3:
        _fail("bare 店舗数 must not fold into 全店")
    if monthly_row_scope_rank("当期計店舗数") != 3:
        _fail("当期計店舗数 must not fold into 全店")

    code = "60590"
    _ensure_stock(code, "ウチヤマ")
    # 新しい年: カラオケ先だが店舗数が売上より前
    _post_monthly_elements(c, code, 2025, [
        ("カラオケ全店 店舗数", "number"),
        ("カラオケ既存店 店舗数", "number"),
        ("カラオケ全店 売上高", "yoy_index"),
        ("カラオケ既存店 売上高", "yoy_index"),
        ("カラオケ全店 客単価", "yoy_index"),
        ("飲食全店 売上高", "yoy_index"),
        ("飲食既存店 売上高", "yoy_index"),
        ("飲食全店 店舗数", "number"),
        ("飲食既存店 店舗数", "number"),
        ("飲食全店 客単価", "yoy_index"),
    ])
    # 古い年: 飲食先・店舗数先。来客数はこの年だけ
    _post_monthly_elements(c, code, 2024, [
        ("飲食全店 店舗数", "number"),
        ("飲食既存店 店舗数", "number"),
        ("飲食全店 売上高", "yoy_index"),
        ("飲食既存店 売上高", "yoy_index"),
        ("飲食全店 客単価", "yoy_index"),
        ("カラオケ全店 店舗数", "number"),
        ("カラオケ既存店 店舗数", "number"),
        ("カラオケ全店 売上高", "yoy_index"),
        ("カラオケ既存店 売上高", "yoy_index"),
        ("カラオケ全店 客単価", "yoy_index"),
        ("カラオケ全店 来客数", "yoy_index"),
    ])
    r = c.put(f"/api/v1/tables/{code}/2024/values",
              json={"element": "カラオケ全店 来客数(前年比)", "month": 1, "value": 104.6})
    _expect(r, 200, "60590 2024 visitors")

    y2025 = c.get(f"/api/v1/tables/{code}/2025")
    y2024 = c.get(f"/api/v1/tables/{code}/2024")
    _expect(y2025, 200, "GET 60590 2025")
    _expect(y2024, 200, "GET 60590 2024")
    ingest_2025 = [e["element"] for e in y2025.json()["elements"]]
    ingest_2024 = [e["element"] for e in y2024.json()["elements"]]
    if ingest_2025[0] != "カラオケ 全店 店舗数":
        _fail(f"API 2025 stays registration order: {ingest_2025!r}")
    if ingest_2024[0] != "飲食 全店 店舗数":
        _fail(f"API 2024 stays registration order: {ingest_2024!r}")
    if ingest_2025 == ingest_2024:
        _fail("fixture years must differ in ingest order")

    order = [row["element"] for row in union_monthly_elements([y2025.json(), y2024.json()])]
    swapped = [row["element"] for row in union_monthly_elements([y2024.json(), y2025.json()])]
    if order != swapped:
        _fail(f"newest-first must ignore input list order: {order!r} vs {swapped!r}")

    want = [
        "カラオケ 全店 売上高(前年比)",
        "カラオケ 全店 客数(前年比)",
        "カラオケ 全店 客単価(前年比)",
        "カラオケ 全店 店舗数",
        "カラオケ 既存 売上高(前年比)",
        "カラオケ 既存 店舗数",
        "飲食 全店 売上高(前年比)",
        "飲食 全店 客単価(前年比)",
        "飲食 全店 店舗数",
        "飲食 既存 売上高(前年比)",
        "飲食 既存 店舗数",
    ]
    if order != want:
        _fail(f"60590 union order: {order!r}")

    k_names = [n for n in order if n.startswith("カラオケ")]
    f_names = [n for n in order if n.startswith("飲食")]
    if not k_names or not f_names:
        _fail(f"missing segment block: {order!r}")
    if order.index(k_names[-1]) >= order.index(f_names[0]):
        _fail(f"karaoke block must precede food: {order!r}")
    if any(n.startswith("飲食") for n in order[:order.index(k_names[-1]) + 1]):
        _fail(f"segments interleaved: {order!r}")
    if (order.index("カラオケ 全店 売上高(前年比)")
            >= order.index("カラオケ 既存 売上高(前年比)")
            or order.index("カラオケ 既存 売上高(前年比)")
            >= order.index("カラオケ 既存 店舗数")):
        _fail(f"karaoke 全店 before 既存, 売上 before 店舗数: {order!r}")
    if (order.index("飲食 全店 売上高(前年比)")
            >= order.index("飲食 既存 売上高(前年比)")
            or order.index("飲食 既存 売上高(前年比)")
            >= order.index("飲食 既存 店舗数")):
        _fail(f"food 全店 before 既存, 売上 before 店舗数: {order!r}")

    present_2025 = {e["element"] for e in y2025.json()["elements"]}
    if "カラオケ 全店 客数(前年比)" in present_2025:
        _fail("2025 must omit 来客数 so the union row is empty")
    if "カラオケ 全店 客数(前年比)" not in order:
        _fail("missing-year row must stay in the union")

    default = next(
        (row["element"] for row in union_monthly_elements([y2025.json(), y2024.json()])
         if monthly_row_slot(row["element"], row["unit"]) == 1
         and "全店" in row["element"] and "既存" not in row["element"]),
        "",
    )
    if default != "カラオケ 全店 売上高(前年比)":
        _fail(f"default 全店売上: {default!r}")

    mixed = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "焼肉 既存店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "FC 月末店舗数", "unit": "number"},
            {"element": "全社 売上高(前年比)", "unit": "yoy_index"},
            {"element": "既存直営店 客単価(前年比)", "unit": "yoy_index"},
            {"element": "焼肉 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "全社 客数(前年比)", "unit": "yoy_index"},
        ],
    }])
    mixed_names = [row["element"] for row in mixed]
    want_mixed = [
        "全社 売上高(前年比)",
        "全社 客数(前年比)",
        "直営 既存 客単価(前年比)",
        "FC 店舗数",
        "焼肉 全店 売上高(前年比)",
        "焼肉 既存 売上高(前年比)",
    ]
    if mixed_names != want_mixed:
        _fail(f"company-wide before segment, 全社 stays 全社: {mixed_names!r}")

    aoki = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "既存店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "全店 客数(前年比)", "unit": "yoy_index"},
            {"element": "既存店 客数(前年比)", "unit": "yoy_index"},
            {"element": "全店 客単価(前年比)", "unit": "yoy_index"},
            {"element": "既存店 客単価(前年比)", "unit": "yoy_index"},
            {"element": "合計 店舗数", "unit": "number"},
        ],
    }])
    aoki_names = [row["element"] for row in aoki]
    want_aoki = [
        "全店 売上高(前年比)",
        "全店 客数(前年比)",
        "全店 客単価(前年比)",
        "既存 売上高(前年比)",
        "既存 客数(前年比)",
        "既存 客単価(前年比)",
        "店舗数",
    ]
    if aoki_names != want_aoki:
        _fail(f"合計店舗数 must follow 既存店, not sit in 全店: {aoki_names!r}")
    if aoki_names.index("店舗数") < aoki_names.index("既存 客単価(前年比)"):
        _fail(f"店舗数 in the middle: {aoki_names!r}")

    monogatari = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "焼肉 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "焼肉 既存店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "焼肉 直営店舗数", "unit": "number"},
            {"element": "焼肉 既存店 店舗数", "unit": "number"},
        ],
    }])
    monogatari_names = [row["element"] for row in monogatari]
    want_monogatari = [
        "焼肉 全店 売上高(前年比)",
        "焼肉 既存 売上高(前年比)",
        "焼肉 既存 店舗数",
        "焼肉 直営 店舗数",
    ]
    if monogatari_names != want_monogatari:
        _fail(f"直営 is channel, not 全店: {monogatari_names!r}")

    watts = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "100円ショップ 出店 店舗数", "unit": "number"},
            {"element": "100円ショップ 閉鎖 店舗数", "unit": "number"},
            {"element": "100円ショップ 店舗数", "unit": "number"},
            {"element": "100円ショップ 直営 既存(前年比)", "unit": "yoy_index"},
            {"element": "100円ショップ 直営 全店(前年比)", "unit": "yoy_index"},
        ],
    }])
    watts_names = [row["element"] for row in watts]
    want_watts = [
        "全店 売上高(前年比)",
        "100円ショップ 直営 全店(前年比)",
        "100円ショップ 直営 既存(前年比)",
        "100円ショップ 出店",
        "100円ショップ 退店",
        "100円ショップ 店舗数",
    ]
    if watts_names != want_watts:
        _fail(f"出店/退店 must precede 店舗数, not split as segments: {watts_names!r}")

    adj = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "店舗数", "unit": "number"},
            {"element": "純増 店舗数", "unit": "number"},
            {"element": "出店", "unit": "number"},
            {"element": "退店", "unit": "number"},
            {"element": "改装", "unit": "number"},
            {"element": "新設店舗", "unit": "number"},
            {"element": "業態転換", "unit": "number"},
            {"element": "M&A", "unit": "number"},
        ],
    }])
    adj_names = [row["element"] for row in adj]
    want_adj = [
        "出店", "退店", "純増 店舗数", "改装", "新設店舗", "業態転換", "店舗数", "M&A",
    ]
    if monthly_row_segment("純増 店舗数") != "":
        _fail("純増 is not a segment")
    if monthly_row_segment("ジンズ 純増 店舗数") != "ジンズ":
        _fail("純増 must not steal the brand")
    if monthly_row_segment("トライアル 新規 全店 出店") != "トライアル":
        _fail("新規 must not steal the brand")
    if monthly_row_slot("トライアル 出店", "number") != 4:
        _fail("出店 slot")
    if monthly_row_scope_rank("トライアル 全店 出店") != 0:
        _fail("全店 出店 stays 全店")
    if monthly_row_scope_rank("トライアル 出店") != 3:
        _fail("bare 出店 stays after 既存")
    if adj_names != want_adj:
        _fail(f"純増/新設/改装/業態転換 before 店舗数, names unchanged: {adj_names!r}")

    trial = union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "トライアル 既存 売上高(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 既存 客数(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 既存 客単価(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "西友 既存 売上高(前年比)", "unit": "yoy_index"},
            {"element": "西友 既存 客数(前年比)", "unit": "yoy_index"},
            {"element": "西友 既存 客単価(前年比)", "unit": "yoy_index"},
            {"element": "西友 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "トライアル 新規 全店 出店", "unit": "number"},
            {"element": "トライアル 全店 退店", "unit": "number"},
            {"element": "トライアル 全店 店舗数", "unit": "number"},
            {"element": "西友 新規 全店 出店", "unit": "number"},
            {"element": "西友 全店 退店", "unit": "number"},
            {"element": "西友 全店 店舗数", "unit": "number"},
            {"element": "西友 業態転換数 全店", "unit": "number"},
            {"element": "西友 業態転換数", "unit": "number"},
        ],
    }])
    trial_names = [row["element"] for row in trial]
    want_trial = [
        "トライアル 全店 売上高(前年比)",
        "トライアル 全店 出店",
        "トライアル 全店 退店",
        "トライアル 全店 店舗数",
        "トライアル 既存 売上高(前年比)",
        "トライアル 既存 客数(前年比)",
        "トライアル 既存 客単価(前年比)",
        "西友 全店 売上高(前年比)",
        "西友 全店 出店",
        "西友 全店 退店",
        "西友 全店 業態転換数",
        "西友 全店 店舗数",
        "西友 既存 売上高(前年比)",
        "西友 既存 客数(前年比)",
        "西友 既存 客単価(前年比)",
        "西友 業態転換数",
    ]
    if trial_names != want_trial:
        _fail(f"出店/退店 keep 全店: {trial_names!r}")



def _assert_segment_tail_row_order() -> None:
    """76830: 内アパレルは segment_tail で非 tail の後。他銘柄は初出順のまま。"""
    cfg = ROOT / "config" / "monthly" / "monthly_row_order.json"
    if not cfg.is_file():
        _fail("monthly_row_order.json missing under config/")

    elements = [
        {"element": "内アパレル 全店 売上高", "unit": "number"},
        {"element": "内アパレル 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "店舗販売 全店 売上高", "unit": "number"},
        {"element": "店舗販売 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "オンライン販売 全店 売上高", "unit": "number"},
        {"element": "オンライン販売 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "その他 全店 売上高", "unit": "number"},
        {"element": "その他 全店 売上高(前年比)", "unit": "yoy_index"},
        {"element": "全店 売上高", "unit": "number"},
        {"element": "全店 売上高(前年比)", "unit": "yoy_index"},
    ]
    table = {"code": "76830", "year": 2026, "elements": elements}
    order = [row["element"] for row in union_monthly_elements([table])]
    want = [
        "全店 売上高",
        "全店 売上高(前年比)",
        "店舗販売 全店 売上高",
        "店舗販売 全店 売上高(前年比)",
        "オンライン販売 全店 売上高",
        "オンライン販売 全店 売上高(前年比)",
        "その他 全店 売上高",
        "その他 全店 売上高(前年比)",
        "内アパレル 全店 売上高",
        "内アパレル 全店 売上高(前年比)",
    ]
    if order != want:
        _fail(f"76830 segment_tail order: {order!r}")
    via_kw = [row["element"] for row in union_monthly_elements(
        [{"year": 2026, "elements": elements}], code="76830",
    )]
    if via_kw != want:
        _fail(f"76830 code= kw order: {via_kw!r}")

    other = [row["element"] for row in union_monthly_elements(
        [{"code": "99999", "year": 2026, "elements": elements}],
    )]
    other_named = [n for n in other if monthly_row_segment(n)]
    if not other_named or not other_named[0].startswith("内アパレル"):
        _fail(f"other issuer must keep first-seen 内アパレル first: {other!r}")

    # Lane B: 内 MM → 内アパレル（canonicalize 後キー）
    for src, unit, want_name in [
        ("内 MM 全店 売上高", "number", "内アパレル 全店 売上高"),
        ("内 MM 売上", "number", "内アパレル 売上高"),
        ("内 MM 全店 売上高(前年比)", "yoy_index", "内アパレル 全店 売上高(前年比)"),
        ("内 MM 売上高(前年比)", "yoy_index", "内アパレル 売上高(前年比)"),
    ]:
        got = apply_issuer_aliases(
            "76830", canonicalize_element_label(src, unit), year=2026,
        )
        got = canonicalize_element_label(got, unit)
        if got != want_name:
            _fail(f"76830 alias {src!r} → {got!r}, want {want_name!r}")


def _assert_monthly_spa(html: str) -> None:
    """月次 SPA: TOP はチップ+6列。Detail はグラフと横持ちの 1 枚表（木）。年ドロップダウン無し。"""
    start = html.find('id="tab-monthly"')
    if start < 0:
        _fail("index.html missing monthly tab")
    end = html.find("</section>", start)
    if end < 0:
        _fail("index.html monthly tab unclosed")
    section = html[start:end]
    if 'id="monthly-year"' in html:
        _fail("SPA still has #monthly-year")
    if "<select" not in section.lower() or 'id="monthly-series"' not in section:
        _fail("Detail series control must be <select id=\"monthly-series\">")
    for chunk in _select_tags(section):
        if "monthly-year" in chunk:
            _fail("monthly tab still has a year select")
        if 'id="monthly-series"' not in chunk:
            _fail("monthly tab select must be #monthly-series only")
    # 年別の表とゲート（節 + stub）はやめた。表は横持ちの 1 枚だけ（#434 の続き）
    for gone in ('id="monthly-layout-debug"', 'id="monthly-view-debug"', "function monthlySegmentGate",
                 "function monthlyTableLayout", "monthly.layoutMode.debug", "monthly.viewMode.proto",
                 "__gate__", "ゲート（一時）", "MONTHLY_GATE_CODES_KEY"):
        if gone in html:
            _fail(f"years view / gate must be gone from the SPA: {gone!r}")
    if ">項目<" in html:
        _fail("monthly thead still uses 項目")
    if 'id="monthly-groups"' not in section:
        _fail("TOP must have #monthly-groups")
    if 'data-group="__ir__"' not in html and "setMonthlyGroup('__ir__')" not in html:
        _fail("TEMP IR list chip __ir__ must exist on TOP")
    if "IR（一時）" not in html:
        _fail("TEMP IR list chip label IR（一時） missing")
    if "/static/ir_monthly_codes.json" not in html:
        _fail("SPA must fetch /static/ir_monthly_codes.json")
    if "IR対象はありません" not in html:
        _fail("TEMP IR list empty copy missing")
    grp_fn = html.find("function renderMonthlyGroups")
    grp_end = html.find("async function setMonthlyGroup")
    if grp_fn < 0 or grp_end <= grp_fn:
        _fail("renderMonthlyGroups / setMonthlyGroup bounds")
    grp_body = html[grp_fn:grp_end]
    priv = grp_body.find("if(!monthlyPublic())")
    if priv < 0 or grp_body.find("IR（一時）") < priv:
        _fail("TEMP IR chip must stay inside the private branch")
    if 'id="monthly-chart"' not in section:
        _fail("Detail must have #monthly-chart")
    if 'id="monthly-back"' not in section:
        _fail("Detail must have #monthly-back")
    if "function uniqueMonthlyIssuers" not in html:
        _fail("index.html missing uniqueMonthlyIssuers")
    if "uniqueMonthlyIssuers(monthlyRows)" not in html:
        _fail("list must uniqueMonthlyIssuers(monthlyRows)")
    if "seen.has(code)" not in html:
        _fail("uniqueMonthlyIssuers must skip duplicate codes")
    if "function drawMonthlyChart" not in html:
        _fail("index.html missing drawMonthlyChart")
    if "monthly-grid" in html.replace(" ", "") and "grid-template-columns:minmax(240px,1fr)" in html.replace(" ", ""):
        _fail("monthly tab must not use 1100px two-column grid")
    list_fn = html.find("function renderMonthlyList")
    show_fn = html.find("function showMonthlyTop")
    if list_fn < 0 or show_fn < 0:
        _fail("renderMonthlyList / showMonthlyTop bounds")
    list_body = html[list_fn:show_fn] if show_fn > list_fn else html[list_fn:list_fn + 1800]
    if "r.year" in list_body or "${r.year}" in list_body:
        _fail("TOP list must not show year")
    if '<th class="l">コード</th><th class="l">銘柄</th>' in list_body:
        _fail("TOP list must not be code + name only")
    if "会社名" not in list_body or "月次業種" not in list_body:
        _fail("TOP headers must include 会社名 and 月次業種")
    if "function fmtMonthlyIssuerLabel" not in html:
        _fail("TOP name must use fmtMonthlyIssuerLabel")
    if "fmtMonthlyIssuerLabel(r.code, r.name)" not in list_body:
        _fail("TOP name cell must be code + truncated company name")
    if "function fmtMonthlyMarket" not in html:
        _fail("TOP market must use fmtMonthlyMarket")
    if "東証P" not in html or "東証S" not in html or "東証G" not in html:
        _fail("TOP market labels must be 東証P / 東証S / 東証G")
    if "mkt-p" not in html or "mkt-s" not in html or "mkt-g" not in html:
        _fail("TOP market cells must have colored mkt classes")
    if "td.mkt::before" not in html:
        _fail("TOP market cells must have a color square")
    if "function fmtMonthlySector" not in html:
        _fail("TOP sector must use fmtMonthlySector")
    if 'replace(/業$/, "")' not in html and "replace(/業$/," not in html:
        _fail("TOP sector must drop trailing 業")
    if "fmtMonthlySector(r.sector33)" not in list_body:
        _fail("TOP sector cell must be truncated sector33")
    if "${r.market||\"\"}" in list_body or "${r.market||''}" in list_body:
        _fail("TOP must not dump raw market names")
    if "${r.sector33||\"\"}" in list_body:
        _fail("TOP must not dump raw sector33")
    if ".slice(0, 5)" not in html and ".slice(0,5)" not in html:
        _fail("TOP company name must be first 5 characters")
    label_fn = html.find("function fmtMonthlyIssuerLabel")
    if label_fn < 0:
        _fail("fmtMonthlyIssuerLabel missing")
    label_body = html[label_fn:label_fn + 280]
    if "「" in label_body or "」" in label_body:
        _fail("TOP name must not wrap the code in 「」")
    if "`${monthlyCode4(code)} ${short}`" not in label_body:
        _fail("TOP name is 4-digit code, space, then 5-char name")
    if "function monthlyCode4" not in html:
        _fail("display code must use monthlyCode4")
    if "全店(前年比)" not in list_body or "既存店(前年比)" not in list_body:
        _fail("TOP headers must include yoy columns")
    top_start = html.find('id="monthly-top"')
    det_start = html.find('id="monthly-detail"')
    if top_start < 0 or det_start < 0 or det_start <= top_start:
        _fail("monthly-top / monthly-detail bounds")
    if 'class="panel"' in html[top_start:det_start]:
        _fail("TOP must not wrap in .panel")
    tbl_css = html.find("#monthly-tbl th")
    if tbl_css < 0:
        tbl_css = html.find("#monthly-tbl td")
    if tbl_css < 0:
        _fail("TOP table cell CSS missing")
    tbl_block = html[tbl_css:tbl_css + 220]
    if "1px solid" not in tbl_block and "1pxsolid" not in tbl_block.replace(" ", ""):
        _fail("TOP table cells must have full grid lines")
    tbl_rule = html.find("#monthly-tbl {")
    if tbl_rule < 0:
        _fail("#monthly-tbl rule missing")
    tbl_rule_block = html[tbl_rule:tbl_rule + 180].replace(" ", "")
    if "margin-inline:auto" not in tbl_rule_block:
        _fail("TOP table must center with margin-inline:auto when narrower than the viewport")
    monthly_rule = html.find("table.monthly {")
    if monthly_rule < 0:
        _fail("table.monthly rule missing")
    monthly_rule_block = html[monthly_rule:monthly_rule + 280].replace(" ", "")
    if "margin-inline:auto" not in monthly_rule_block:
        _fail("year tables must center with margin-inline:auto when narrower than the detail column")
    if "function loadMonthlyTables" not in html:
        _fail("index.html missing loadMonthlyTables")
    if "function monthlyDisplayYears" not in html:
        _fail("index.html missing monthlyDisplayYears")
    disp_fn = html.find("function monthlyDisplayYears")
    disp_end = html.find("function monthlyYearColor")
    if disp_fn < 0 or disp_end <= disp_fn:
        _fail("monthlyDisplayYears / monthlyYearColor bounds")
    disp_body = html[disp_fn:disp_end]
    if "latest - 2" not in disp_body and "latest-2" not in disp_body.replace(" ", ""):
        _fail("monthlyDisplayYears must keep latest and previous 2 calendar years")
    if "function monthlyYearColor" not in html:
        _fail("index.html missing monthlyYearColor")
    if 'const MONTHLY_YEAR_COLORS = ["#0077b6","#2e8b57","#b8860b","#8e4ec6"]' not in html:
        _fail("SPA MONTHLY_YEAR_COLORS palette")
    if "`/api/v1/tables/${selMonthlyCode}`" not in html:
        _fail("SPA must GET /api/v1/tables/{code}")
    if "`/api/v1/tables/${selMonthlyCode}/${y}`" not in html:
        _fail("SPA must GET /api/v1/tables/{code}/{year}")
    if "Promise.all" not in html:
        _fail("year tables must be fetched in parallel")
    if ".sort((a,b)=>b-a)" not in html:
        _fail("calendar-year tables must stack newest first")
    if "monthlyDisplayYears((info && info.years) || [])" not in html:
        _fail("stacked year tables must use the last-3-year window")
    if "table-layout:fixed" not in html.replace(" ", ""):
        _fail("monthly tables must use table-layout:fixed")
    if "--monthly-m-w" not in html:
        _fail("one shared --monthly-m-w for all month cols")
    wrap_rule = html.find("#monthly-table-wrap")
    if wrap_rule < 0:
        _fail("#monthly-table-wrap CSS missing")
    wrap_block = html[wrap_rule:wrap_rule + 180]
    if "overflow-x:auto" not in wrap_block.replace(" ", ""):
        _fail("#monthly-table-wrap must overflow-x:auto")
    name_rule = html.find("table.monthly thead th:first-child")
    if name_rule < 0:
        name_rule = html.find("table.monthly td:first-child")
    if name_rule < 0:
        _fail("monthly first-child column CSS missing")
    name_block = html[name_rule:name_rule + 280]
    if "min-width:16em" not in name_block.replace(" ", "") and "min-width:14em" not in name_block.replace(" ", ""):
        _fail("monthly first column must have a readable min-width")
    if "z-index:2" not in name_block.replace(" ", "") and "z-index:3" not in name_block.replace(" ", ""):
        _fail("sticky first cells need z-index so month numbers stay underneath")
    if "var(--panel)" not in name_block:
        _fail("sticky first cells need opaque panel background")
    if "function monthlyRowOrder" not in html:
        _fail("index.html missing monthlyRowOrder")
    if "function monthlyRowScopeRank" not in html:
        _fail("index.html missing monthlyRowScopeRank")
    scope_fn = html.find("function monthlyRowScopeRank")
    scope_end = html.find("function isAllStoreSalesYoy")
    if scope_fn < 0 or scope_end <= scope_fn:
        _fail("monthlyRowScopeRank / isAllStoreSalesYoy bounds")
    scope_body = html[scope_fn:scope_end]
    compact = scope_body.replace(" ", "")
    if 'label.metric_kind==="stores"' not in compact:
        _fail("SPA must not fold unlabeled 店舗数 into 全店")
    row_fn = html.find("function monthlyRowOrder")
    row_end = html.find("function escHtml")
    if row_fn < 0 or row_end <= row_fn:
        _fail("monthlyRowOrder / escHtml bounds")
    row_body = html[row_fn:row_end]
    if "L.display" not in row_body:
        _fail("monthlyRowOrder must fold on label.display")
    if "label: L" not in row_body and "label:L" not in row_body:
        _fail("monthlyRowOrder must keep label on items")
    if "seen.has(name)" in row_body:
        _fail("monthlyRowOrder must not key by raw stored element name")
    if "MONTHLY_BREAKDOWN_RANK[L.breakdown]" not in row_body:
        _fail("monthlyRowOrder must sort 売上高 内訳 after the parent")
    if "unitRank" not in row_body:
        _fail("monthlyRowOrder must sort number before yoy within a metric")
    if "function monthlySeriesKind" in html:
        _fail("SPA must not keep monthlySeriesKind string parser")
    if "function monthlySeriesChoices" not in html:
        _fail("index.html missing monthlySeriesChoices")
    choices_fn = html.find("function monthlySeriesChoices")
    choices_end = html.find("function pickDefaultMonthlySeries")
    if choices_fn < 0 or choices_end <= choices_fn:
        _fail("monthlySeriesChoices / pickDefaultMonthlySeries bounds")
    choices_body = html[choices_fn:choices_end]
    if "return MONTHLY_SERIES" in choices_body:
        _fail("select options must be the table's element names, not MONTHLY_SERIES")
    if "e.label.display" not in choices_body:
        _fail("#monthly-series ids must use label.display")
    if "monthlyRowOrder(" not in choices_body:
        _fail("#monthly-series options must follow year-table row order")
    load_fn = html.find("function loadMonthlyTables")
    if load_fn < 0:
        _fail("loadMonthlyTables missing")
    load_body = html[load_fn:load_fn + 1200]
    if "await loadMonthlyRowOrderConfig()" not in load_body:
        _fail("loadMonthlyTables must await monthly_row_order.json")
    if "function loadMonthlyRowOrderConfig" not in html:
        _fail("SPA missing loadMonthlyRowOrderConfig")
    if "function issuerSegmentTail" not in html:
        _fail("SPA missing issuerSegmentTail")
    if "/static/monthly_row_order.json" not in html:
        _fail("SPA must fetch /static/monthly_row_order.json")
    if "MONTHLY_SEGMENT_TAIL_BASE" not in html:
        _fail("SPA missing MONTHLY_SEGMENT_TAIL_BASE for segment_tail")
    if "monthlyDisplayYears" not in load_body:
        _fail("loadMonthlyTables must window to the last 3 calendar years")
    if "renderMonthlyYearTables(monthlyYearTables)" not in load_body:
        _fail("loadMonthlyTables must render via renderMonthlyYearTables")
    render_fn = html.find("function renderMonthlyYearTables")
    if render_fn < 0:
        _fail("renderMonthlyYearTables missing")
    render_body = html[render_fn:render_fn + 900]
    if "renderMonthlyTimeline(" not in render_body:
        _fail("renderMonthlyYearTables must draw the timeline")
    tl_fn = html.find("function renderMonthlyTimeline")
    if tl_fn < 0:
        _fail("renderMonthlyTimeline missing")
    tl_body = html[tl_fn:tl_fn + 900]
    if "monthlyTreeModel(" not in tl_body:
        _fail("timeline must draw from the API tree")
    if "byName[src.element]" in html:
        _fail("table cells must map stored names through label.display")
    if "byName[src.label.display]" not in html:
        _fail("table cells must look up rows by label.display")
    pick_fn = html.find("function pickDefaultMonthlySeries")
    pick_end = html.find("function monthlySeriesElement")
    if pick_fn < 0 or pick_end <= pick_fn:
        _fail("pickDefaultMonthlySeries / monthlySeriesElement bounds")
    pick_body = html[pick_fn:pick_end]
    if "isAllStoreSalesYoy" not in pick_body:
        _fail("default series must prefer 全店/全社 売上 yoy")
    yoy_pick = html.find("function isAllStoreSalesYoy")
    yoy_pick_end = html.find("function monthlyRowOrder")
    if yoy_pick < 0 or yoy_pick_end <= yoy_pick:
        _fail("isAllStoreSalesYoy / monthlyRowOrder bounds")
    yoy_pick_body = html[yoy_pick:yoy_pick_end]
    yoy_pick_compact = yoy_pick_body.replace(" ", "")
    if 'label.scope==="全社"' not in yoy_pick_compact:
        _fail("isAllStoreSalesYoy must treat scope 全社 as company-wide")
    if "!label.country" not in yoy_pick_compact:
        _fail("isAllStoreSalesYoy must skip country-only 売上高 yoy")
    if "series_kind" not in pick_body:
        _fail("default series must still fall back to all_store")
    if "function monthlySeriesElement" not in html:
        _fail("chart must resolve the selected row by element name")
    series_el_fn = html.find("function monthlySeriesElement")
    series_el_end = html.find("function fmtMonthlyYoyList")
    if series_el_fn < 0 or series_el_end <= series_el_fn:
        _fail("monthlySeriesElement / fmtMonthlyYoyList bounds")
    series_el_body = html[series_el_fn:series_el_end]
    if "e.label.display" not in series_el_body:
        _fail("chart row lookup must match leftover short names via label.display")
    if "e.element===seriesId" in series_el_body.replace(" ", ""):
        _fail("monthlySeriesElement must not require stored name == series id")
    if "renderMonthlySeriesSelect" not in html:
        _fail("series <select> must be filled from actual table elements")
    if 'data-series="${escHtml(monthlyTlShown(rec))}"' not in html:
        _fail("every table row must be selectable by element name")
    if "onclick=\"setMonthlySeries(this.dataset.series)\"" not in html:
        _fail("table row click must still switch the chart series")
    if "maxNumLen" not in html or "txt.length" not in html:
        _fail("month col width must come from fmtMonthlyCell text length")
    year_rule = html.find("table.monthly thead th.l")
    if year_rule < 0:
        _fail("year cell CSS missing (table.monthly thead th.l)")
    year_block = html[year_rule:year_rule + 280]
    if "var(--up)" not in year_block:
        _fail("year cell must be red fill")
    if "color:#fff" not in year_block.replace(" ", "") and "color:#ffffff" not in year_block.replace(" ", ""):
        _fail("year cell must be white text")
    if "font-weight:bold" not in year_block.replace(" ", "") and "font-weight:700" not in year_block.replace(" ", ""):
        _fail("year cell must be bold")
    if "font-size:11px" in year_block.replace(" ", ""):
        _fail("year cell must be larger than 11px month headers")
    if "font-size:13px" not in year_block.replace(" ", "") and "font-size:14px" not in year_block.replace(" ", ""):
        _fail("year cell must be slightly larger than month headers")
    month_hdr = html.find("background:#eceff2")
    if month_hdr < 0:
        month_hdr = html.find("background: #eceff2")
    if month_hdr < 0:
        _fail("month header cells must use light gray #eceff2")
    month_hdr_block = html[max(0, month_hdr - 180):month_hdr + 140]
    if "thead th:not(:first-child)" not in month_hdr_block:
        _fail("light gray must apply to month header cells, not the year cell")
    month_hdr_compact = month_hdr_block.replace(" ", "")
    if "font-weight:700" not in month_hdr_compact and "font-weight:bold" not in month_hdr_compact:
        _fail("month labels must be bold")
    if "text-align:center" not in month_hdr_compact:
        _fail("month labels must be center-aligned")
    if "データなし" not in html:
        _fail("empty copy データなし missing")
    chart_fn = html.find("function drawMonthlyChart")
    chart_end = html.find("function fmtMonthlyCell")
    if chart_fn < 0 or chart_end < 0 or chart_end <= chart_fn:
        _fail("drawMonthlyChart bounds")
    chart_body = html[chart_fn:chart_end]
    if "monthlyDisplayYears" not in chart_body:
        _fail("chart must use the same last-3-year window as the tables")
    if "monthlyYearColor(year)" not in chart_body:
        _fail("bars and legend must use monthlyYearColor(year)")
    if "MONTHLY_YEAR_COLORS[yi" in chart_body:
        _fail("chart colors must not follow display order")
    if "fill-opacity" in chart_body or 'fill="#d32f2f"' in chart_body:
        _fail("no red-below-100 overlay in JS")
    if "v<100" in chart_body.replace(" ", ""):
        _fail("must not special-case yoy under 100 for bar fill")
    if "y100" not in chart_body or "stroke-dasharray" not in chart_body:
        _fail("keep yoy 100 horizontal line")
    if "n+100" in chart_body.replace(" ", "") or "+=100" in chart_body.replace(" ", ""):
        _fail("chart must plot stored values, not leftover yoy_pct+100")
    if "monthly-year-chart" not in chart_body:
        _fail("drawMonthlyChart must emit .monthly-year-chart year panels")
    if "monthly-bar-v" not in chart_body:
        _fail("drawMonthlyChart must label stored bar values with monthly-bar-v")
    compact_chart = chart_body.replace(" ", "").replace("\n", "")
    if "(slot*0.72)/nY" in compact_chart:
        _fail("chart must not use clustered bar width (slot*0.72)/nY")
    if re.search(r"W\s*=\s*[^;]*:\s*720\b", chart_body):
        _fail("PC chart width must not be fixed 720")
    if re.search(r"if\s*\(\s*!?\s*phone\s*\)\s*\{[^}]*monthly-bar-v", chart_body.replace("\n", " ")):
        _fail("bar labels must not be wrapped in if(phone) / if(!phone)")
    if re.search(r"(?:!phone|phone)\s*\?[^;]*monthly-bar-v", compact_chart):
        _fail("bar labels must not be hidden behind a phone ternary")
    color_fn = html.find("function monthlyYearColor")
    color_end = html.find("function uniqueMonthlyIssuers")
    if color_fn < 0 or color_end <= color_fn:
        _fail("monthlyYearColor / uniqueMonthlyIssuers bounds")
    color_body = html[color_fn:color_end]
    if "Number(year)" not in color_body:
        _fail("monthlyYearColor must key off the calendar year")
    if "% n" not in color_body and "%n" not in color_body.replace(" ", ""):
        _fail("monthlyYearColor must map year % palette length")
    if 'text-anchor="end"' not in chart_body:
        _fail("Y-axis tick numbers must be drawn (text-anchor=end in the left gutter)")
    if "monthlyYTicks" not in chart_body and "function monthlyYTicks" not in html:
        _fail("Y-axis ticks must be generated, not left as empty L pad")
    if "monthlyIsPhone" not in html and 'matchMedia("(max-width:640px)")' not in chart_body:
        _fail("chart must use max-width:640px (or similar) so phone fonts/gutters differ from PC")
    if "fsAxis" not in chart_body and 'font-size="11"' in chart_body and "280" not in chart_body:
        _fail("phone chart text must be larger than desktop font-size 11")
    if "fsTitle" in chart_body:
        _fail("chart must not draw a series title; #monthly-series is the label")
    if "spec.label)}</text>" in chart_body:
        _fail("chart SVG must not render spec.label as a title")
    mq = html.find("@media (max-width:640px)")
    if mq < 0:
        _fail("phone/PC split must use @media (max-width:640px)")
    mq_block = html[mq:mq + 1400]
    if "table.monthly" not in mq_block:
        _fail("phone monthly table rules must live in max-width:640px")
    mq_compact = mq_block.replace(" ", "").replace("\n", "")
    if "position:static" not in mq_compact:
        _fail("phone must turn off first-column sticky (or equivalent static position)")
    if "min-width:8em" not in mq_compact and "width:8em" not in mq_compact:
        _fail("phone name column must be much shorter than 16em so months are visible")
    if "right:0" in mq_compact:
        _fail("do not sticky-fix the rightmost month column on phone")
    monthly_css_start = html.find("table.monthly")
    monthly_css = html[monthly_css_start:mq] if mq > monthly_css_start else html[monthly_css_start:monthly_css_start + 2500]
    if "right:0" in monthly_css.replace(" ", ""):
        _fail("do not sticky-fix the rightmost month column")
    if "right:auto" not in monthly_css.replace(" ", ""):
        _fail("month columns must not be right-pinned (right:auto)")
    if 'id="monthly-value-tabs"' in html or "setMonthlyValueMode" in html:
        _fail("value tabs are gone; paired rows always show amount + yoy")
    if "MONTHLY_VALUE_MODE_KEY" in html or "monthlyValueMode" in html:
        _fail("value mode localStorage is gone")
    if 'class="tl-sub"' not in html:
        _fail("paired cells must still render yoy under amount as .tl-sub")
    for u in ("/static/monthly_row_order.json", "/static/ir_monthly_codes.json"):
        if f'fetch("{u}", {{cache: "no-store"}})' not in html:
            _fail(f"SPA must fetch {u} with cache: no-store (iOS Safari reuses stale JSON)")
    # 横持ちは API の tree だけを描く（#434）。親子の推測を JS に戻さない。
    for gone in ("breakdown_parent", "function monthlyTlParents", "function monthlyTimelineSections"):
        if gone in html:
            _fail(f"SPA must draw the timeline from the API tree only, found {gone!r}")
    row_order = json.loads((ROOT / "config" / "monthly" / "monthly_row_order.json").read_text(encoding="utf-8"))
    for code, cfg in row_order.items():
        if isinstance(cfg, dict) and "breakdown_parent" in cfg:
            _fail(f"monthly_row_order.json {code}: breakdown_parent is gone, write the nesting in tree")


def _assert_monthly_layout_js() -> None:
    """横持ちの木・既定の系列・グラフ。Python で書き直さず SPA の関数を node で呼ぶ。"""
    import json
    import subprocess
    import tempfile

    js_path = ROOT / "static" / "js" / "monthly.js"
    if not js_path.is_file():
        _fail("static/js/monthly.js missing")

    def _vals() -> dict:
        return {str(m): (1 if m == 1 else None) for m in range(1, 13)}

    def _el(name: str, unit: str = "yoy_index") -> dict:
        return {
            "element": name,
            "unit": unit,
            "label": monthly_element_label(name, unit),
            "values": _vals(),
        }

    fixtures = {
        "matsukiyo": [{
            "year": 2026,
            "code": "30880",
            "elements": [
                _el("全店 売上高 ドラッグ(前年比)"),
                _el("MKG ドラッグ 全店 売上高(前年比)"),
                _el("MKG 全店 売上高 調剤(前年比)"),
                _el("MKG 全店 客数(前年比)"),
                _el("MKG 全店 売上高(前年比)"),
                _el("全店 売上高(前年比)"),
                _el("既存 売上高(前年比)"),
            ],
        }],
        "leftoverMkg": _el("MKG ドラッグ 全店 売上高(前年比)"),
        # 60360: 海外 is unsegmented (country only, empty scope) and used to beat 全社.
        "keeper": [{
            "year": 2026,
            "code": "60360",
            "elements": [
                _el("海外 売上高(前年比)"),
                _el("キーパー製品等関連事業 売上高(前年比)"),
                _el("キーパーLABO運営事業 直営 全店 売上高(前年比)"),
                _el("全社 売上高(前年比)"),
            ],
        }],
        "spot138A": [{
            "year": 2026,
            "code": "138A0",
            "elements": [
                _el("既存 売上高(前年比)"),
                _el("全店 客数(前年比)"),
                _el("全店 売上高(前年比)"),
                _el("店舗数", "number"),
            ],
        }],
        "spot3086": [{
            "year": 2026,
            "code": "30860",
            "elements": [
                _el("百貨店事業 総額 売上高(前年比)"),
                _el("大丸 心斎橋店 売上高(前年比)"),
                _el("総額 連結売上高(前年比)"),
            ],
        }],
        "spot9997": [{
            "year": 2026,
            "code": "99970",
            "elements": [
                _el("グロース領域 売上高(前年比)"),
                _el("連結売上高(前年比)"),
            ],
        }],
        "alpenBoth": [{
            "year": 2026,
            "code": "30280",
            "elements": [
                _el("全社 売上高(前年比)"),
                _el("全店 売上高(前年比)"),
            ],
        }],
        "chartTables": [
            {
                "year": 2026,
                "code": "72030",
                "elements": [{
                    "element": "全店 売上高(前年比)",
                    "unit": "yoy_index",
                    "label": monthly_element_label("全店 売上高(前年比)", "yoy_index"),
                    "values": {str(m): (110 if m == 1 else None) for m in range(1, 13)},
                }],
            },
            {
                "year": 2025,
                "code": "72030",
                "elements": [{
                    "element": "全店 売上高(前年比)",
                    "unit": "yoy_index",
                    "label": monthly_element_label("全店 売上高(前年比)", "yoy_index"),
                    "values": {str(m): (100 if m == 1 else None) for m in range(1, 13)},
                }],
            },
        ],
    }
    from app.monthly.tree import assign_tree_rows, auto_tree_payload, build_auto_tree, load_monthly_trees, tree_payload

    # 神戸物産（config の木）: 前年比だけの会社の行は (前年比) を残す。単独の指標は「節名 葉」
    kobe_rows = [
        ("売上高", "number"), ("売上高(前年比)", "yoy_index"), ("店舗数", "number"), ("FC 店舗数", "number"),
        ("直轄エリア 店舗数", "number"), ("直轄エリア 仕入れ 既存 売上高(前年比)", "yoy_index"),
        ("直轄エリア 仕入れ 全店 売上高(前年比)", "yoy_index"), ("全国 仕入れ 全店 売上高(前年比)", "yoy_index"),
    ]
    kobe_trees = load_monthly_trees()
    kobe_assign = assign_tree_rows("30380", [e for e, _ in kobe_rows], kobe_trees)
    fixtures["kobeTree"] = [{
        "year": 2026, "code": "30380",
        "elements": [{**_el(e, u), "tree": kobe_assign[e]} for e, u in kobe_rows],
        **tree_payload("30380", [e for e, _ in kobe_rows], kobe_trees),
    }]

    store_rows = [
        ("直営+FC 既存 店舗数", "number"), ("直営 既存 店舗数", "number"),
        ("焼肉 直営+FC 既存 店舗数", "number"), ("焼肉 直営 既存 店舗数", "number"),
        ("日本+海外 直営+FC 全店 店舗数", "number"), ("日本 直営+FC 全店 店舗数", "number"),
        ("日本 直営 全店 店舗数", "number"), ("日本 FC 全店 店舗数", "number"), ("海外 全店 店舗数", "number"),
        ("焼肉 直営+FC 全店 店舗数", "number"), ("焼肉 直営 全店 店舗数", "number"), ("焼肉 FC 全店 店舗数", "number"),
    ]
    auto = build_auto_tree("30970", {2026: store_rows}, group_metric={"直営+FC 全店 店舗数": "日本 直営+FC 全店 店舗数"})
    fixtures["storeTree"] = [{
        "year": 2026, "code": "30970",
        "elements": [{**_el(e, u), "tree": auto.rows[e]} for e, u in store_rows],
        **auto_tree_payload(auto, [e for e, _ in store_rows]),
    }]
    brand_rows = [
        ("トライアル 既存 売上高(前年比)", "yoy_index"), ("トライアル 全店 売上高(前年比)", "yoy_index"),
        ("西友 既存 売上高(前年比)", "yoy_index"), ("西友 全店 売上高(前年比)", "yoy_index"),
    ]
    brand_auto = build_auto_tree("141A0", {2026: brand_rows})
    fixtures["brandTree"] = [{
        "year": 2026, "code": "141A0",
        "elements": [{**_el(e, u), "tree": brand_auto.rows[e]} for e, u in brand_rows],
        **auto_tree_payload(brand_auto, [e for e, _ in brand_rows]),
    }]
    # ハードオフ: 業態が 直営 と FC の両方を持ち、業態合計の 店舗数 は無い。庄やは店舗数だけの節
    hardoff_rows = [
        ("店舗数", "number"), ("直営 店舗数", "number"), ("海外 直営 店舗数", "number"),
        ("FC 店舗数", "number"), ("海外 FC 店舗数", "number"),
        ("ハードオフ 直営 店舗数", "number"), ("ハードオフ FC 店舗数", "number"),
        ("オフハウス 直営 店舗数", "number"), ("オフハウス FC 店舗数", "number"),
        ("ブックオフ 直営 店舗数", "number"), ("庄や 店舗数", "number"),
    ]
    hardoff_auto = build_auto_tree("26740", {2026: hardoff_rows})
    fixtures["hardoffTree"] = [{
        "year": 2026, "code": "26740",
        "elements": [{**_el(e, u), "tree": hardoff_auto.rows[e]} for e, u in hardoff_rows],
        **auto_tree_payload(hardoff_auto, [e for e, _ in hardoff_rows]),
    }]
    sanyo_rows = [
        "全社 売上高(前年比)",
        "ブランド別 MACKINTOSH LONDON 売上高(前年比)",
        "ブランド別 MACKINTOSH PHILOSOPHY 売上高(前年比)",
        "ブランド別 Paul Stuart 売上高(前年比)",
        "ブランド別 EPOCA 売上高(前年比)",
        "ブランド別 婦人服*1 売上高(前年比)",
        "ブランド別 コーポレート*2 売上高(前年比)",
        "ブランド別 LOVELESS 売上高(前年比)",
        "販路別 百貨店 売上高(前年比)",
        "販路別 直営 売上高(前年比)",
        "販路別 EC・通販 売上高(前年比)",
        "販路別 アウトレット 売上高(前年比)",
    ]
    sanyo_trees = load_monthly_trees()
    sanyo_assign = assign_tree_rows("80110", sanyo_rows, sanyo_trees)
    fixtures["sanyoTree"] = [{
        "year": 2026, "code": "80110",
        "elements": [{**_el(e), "tree": sanyo_assign[e]} for e in sanyo_rows],
        **tree_payload("80110", sanyo_rows, sanyo_trees),
    }]
    valor_rows = [
        "グループ総 店舗数",
        "スーパーマーケット 既存 売上高(前年比)",
        "スーパーマーケット 既存 客数(前年比)",
        "スーパーマーケット 既存 客単価(前年比)",
        "スーパーマーケット 全店 売上高(前年比)",
        "ドラッグストア 既存 売上高(前年比)",
        "ドラッグストア 既存 客数(前年比)",
        "ドラッグストア 既存 客単価(前年比)",
        "ドラッグストア 全店 売上高(前年比)",
        "ホームセンター 既存 売上高(前年比)",
        "ホームセンター 既存 客数(前年比)",
        "ホームセンター 既存 客単価(前年比)",
        "ホームセンター 全店 売上高(前年比)",
    ]
    valor_assign = assign_tree_rows("99560", valor_rows, sanyo_trees)
    fixtures["valorTree"] = [{
        "year": 2026, "code": "99560",
        "elements": [
            {**_el(e, "number" if e == "グループ総 店舗数" else "yoy_index"), "tree": valor_assign[e]}
            for e in valor_rows
        ],
        **tree_payload("99560", valor_rows, sanyo_trees),
    }]
    # ワタミ: 既存店は 国内外食 の行が無く サブウェイ除く / サブウェイ だけ
    watami_rows = [
        ("国内外食 全店 売上高(前年比)", "yoy_index"),
        ("国内外食 全店 店舗数", "number"),
        ("国内外食 サブウェイ除く 全店 店舗数", "number"),
        ("国内外食 サブウェイ除く 既存 売上高(前年比)", "yoy_index"),
        ("国内外食 サブウェイ 全店 店舗数", "number"),
        ("国内外食 サブウェイ 既存 売上高(前年比)", "yoy_index"),
    ]
    watami_assign = assign_tree_rows("75220", [e for e, _ in watami_rows], sanyo_trees)
    fixtures["watamiTree"] = [{
        "year": 2026, "code": "75220",
        "elements": [{**_el(e, u), "tree": watami_assign[e]} for e, u in watami_rows],
        **tree_payload("75220", [e for e, _ in watami_rows], sanyo_trees),
    }]
    # オートバックスセブン: 車検・整備はサービスの子。商品群は既存売上高の下
    ab_rows = [
        ("日本 全店 売上高(前年比)", "yoy_index"),
        ("日本 既存 売上高(前年比)", "yoy_index"),
        ("日本 全店 客数(前年比)", "yoy_index"),
        ("日本 既存 客数(前年比)", "yoy_index"),
        ("既存 客単価(前年比)", "yoy_index"),
        ("土日祝日前年差異", "number"),
        ("タイヤ 既存 売上高(前年比)", "yoy_index"),
        ("サービス 既存 売上高(前年比)", "yoy_index"),
        ("車検・整備 既存 売上高(前年比)", "yoy_index"),
        ("車販売 既存 売上高(前年比)", "yoy_index"),
    ]
    ab_assign = assign_tree_rows("98320", [e for e, _ in ab_rows], sanyo_trees)
    fixtures["autobacsTree"] = [{
        "year": 2026, "code": "98320",
        "elements": [{**_el(e, u), "tree": ab_assign[e]} for e, u in ab_rows],
        **tree_payload("98320", [e for e, _ in ab_rows], sanyo_trees),
    }]
    # TABAYA: 小売は小売＋ネット通販の下。既存店カウントは根で、開くと小売 / ネット通販が直下
    tabaya_rows = [
        ("全社 売上高(前年比)", "yoy_index"),
        ("ビジネスユニット計 売上高(前年比)", "yoy_index"),
        ("小売＋ネット通販 売上高(前年比)", "yoy_index"),
        ("小売 売上高(前年比)", "yoy_index"),
        ("ネット通販 売上高(前年比)", "yoy_index"),
        ("アウトレット その他 売上高(前年比)", "yoy_index"),
        ("小売＋ネット通販 既存 売上高(前年比)", "yoy_index"),
        ("小売 既存 売上高(前年比)", "yoy_index"),
        ("ネット通販 既存 売上高(前年比)", "yoy_index"),
        ("カウント 既存 店舗数", "number"),
        ("小売 既存 店舗数", "number"),
        ("ネット通販 既存 店舗数", "number"),
        ("店舗数", "number"),
        ("小売 店舗数", "number"),
        ("ネット通販 店舗数", "number"),
        ("アウトレット 店舗数", "number"),
    ]
    tabaya_assign = assign_tree_rows("76060", [e for e, _ in tabaya_rows], sanyo_trees)
    fixtures["tabayaTree"] = [{
        "year": 2026, "code": "76060",
        "elements": [{**_el(e, u), "tree": tabaya_assign[e]} for e, u in tabaya_rows],
        **tree_payload("76060", [e for e, _ in tabaya_rows], sanyo_trees),
    }]
    lom_canon = [
        "ストック型 売上高",
        "電気 売上高",
        "宅配水 売上高",
        "インターネット 売上高",
        "その他 売上高",
        "電気 燃料調整費",
    ]
    lom_assign = assign_tree_rows("92520", lom_canon, sanyo_trees)
    aeon_rows = [
        "GMS事業 既存 売上高(前年比)",
        "GMS事業 既存 客数(前年比)",
        "イオンリテール 既存 売上高(前年比)",
        "イオンリテール 全店 売上高(前年比)",
        "イオン北海道 既存 売上高(前年比)",
        "イオン北海道 全店 売上高(前年比)",
        "イオン九州 既存 売上高(前年比)",
        "イオン九州 全店 売上高(前年比)",
        "スーパーマーケット事業 既存 売上高(前年比)",
        "スーパーマーケット事業 既存 客数(前年比)",
        "マックスバリュ東海 既存 売上高(前年比)",
        "マックスバリュ東海 全店 売上高(前年比)",
        "フジ 既存 売上高(前年比)",
        "フジ 全店 売上高(前年比)",
        "いなげや 既存 売上高(前年比)",
        "いなげや 全店 売上高(前年比)",
        "ディスカウントストア事業 既存 売上高(前年比)",
        "ディスカウントストア事業 既存 客数(前年比)",
        "ヘルス＆ウエルネス事業 既存 売上高(前年比)",
        "ヘルス＆ウエルネス事業 既存 客数(前年比)",
        "ディベロッパー事業 既存 売上高(前年比)",
        "ディベロッパー事業 既存 客数(前年比)",
        "イオンモール 既存 売上高(前年比)",
        "イオンモール 全店 売上高(前年比)",
        "エンターテイメント事業 既存 売上高(前年比)",
        "エンターテイメント事業 既存 客数(前年比)",
        "サービス・専門店事業 既存 売上高(前年比)",
        "サービス・専門店事業 既存 客数(前年比)",
        "コックス 既存 売上高(前年比)",
        "コックス 全店 売上高(前年比)",
        "ジーフット 既存 売上高(前年比)",
        "ジーフット 全店 売上高(前年比)",
        "サービス事業 既存 売上高(前年比)",
        "サービス事業 既存 客数(前年比)",
        "専門店事業 既存 売上高(前年比)",
        "専門店事業 既存 客数(前年比)",
        "キャンドゥ 全店 売上高(前年比)",
        "キャンドゥ 既存 売上高(前年比)",
        "ミニストップ 全店 売上高(前年比)",
        "ミニストップ 既存 売上高(前年比)",
    ]
    aeon_assign = assign_tree_rows("82670", aeon_rows, sanyo_trees)
    fixtures["aeonTree"] = [{
        "year": 2026, "code": "82670",
        "elements": [{**_el(e), "tree": aeon_assign.get(e)} for e in aeon_rows],
        **tree_payload("82670", aeon_rows, sanyo_trees),
    }]
    fixtures["lomTree"] = [{
        "year": 2026, "code": "92520",
        "elements": [{**_el(e, "number"), "tree": lom_assign[e]} for e in lom_canon],
        **tree_payload("92520", lom_canon, sanyo_trees),
    }]
    # アークランズ: 国内総店舗数は外食事業の店舗数。かつや / その他はその子（#484）
    arc_rows = [
        ("小売事業 既存 売上高(前年比)", "yoy_index"),
        ("小売事業 全店 売上高(前年比)", "yoy_index"),
        ("外食事業 かつや 既存 売上高(前年比)", "yoy_index"),
        ("外食事業 全店 売上高(前年比)", "yoy_index"),
        ("外食事業 かつや 店舗数", "number"),
        ("外食事業 その他 店舗数", "number"),
        ("日本 外食事業 店舗数", "number"),
    ]
    arc_assign = assign_tree_rows("98420", [e for e, _ in arc_rows], sanyo_trees)
    fixtures["arclandsTree"] = [{
        "year": 2026, "code": "98420",
        "elements": [{**_el(e, u), "tree": arc_assign[e]} for e, u in arc_rows],
        **tree_payload("98420", [e for e, _ in arc_rows], sanyo_trees),
    }]
    bookoff_rows = [
        "国内ブックオフ 直営 既存 売上高(前年比)",
        "国内ブックオフ 直営 全店 売上高(前年比)",
        "国内ブックオフ 直営 店舗数",
        "国内ブックオフ 加盟店 店舗数",
        "国内ブックオフ事業計 店舗数",
        "プレミアムサービス事業 店舗数",
        "海外事業 直営 店舗数",
        "海外事業 加盟店 店舗数",
        "海外事業計 店舗数",
        "その他 店舗数",
        "チェーン 店舗数",
    ]
    bookoff_assign = assign_tree_rows("92780", bookoff_rows, sanyo_trees)
    fixtures["bookoffTree"] = [{
        "year": 2026, "code": "92780",
        "elements": [
            {**_el(e, "number" if e.endswith("店舗数") else "yoy_index"), "tree": bookoff_assign[e]}
            for e in bookoff_rows
        ],
        **tree_payload("92780", bookoff_rows, sanyo_trees),
    }]

    # 小田急: 見出しの鉄道・箱根を開いたときの横持ち（#482）
    oda_rows = [
        ("鉄道 定期+定期外 輸送人員", "number"),
        ("鉄道 定期計 輸送人員", "number"),
        ("鉄道 通勤定期 輸送人員", "number"),
        ("鉄道 通学定期 輸送人員", "number"),
        ("鉄道 定期外 輸送人員", "number"),
        ("鉄道 定期+定期外 運輸収入", "number"),
        ("鉄道 定期計 運輸収入", "number"),
        ("鉄道 通勤定期 運輸収入", "number"),
        ("鉄道 通学定期 運輸収入", "number"),
        ("鉄道 定期外 運輸収入", "number"),
        ("百貨店 取扱高", "number"),
        ("百貨店 新宿店 取扱高", "number"),
        ("百貨店 町田店 取扱高", "number"),
        ("百貨店 ふじさわ 取扱高", "number"),
        ("ストア 既存 取扱高", "number"),
        ("ストア 既存 売上高(前年比)", "yoy_index"),
        ("ストア 全店 取扱高", "number"),
        ("ストア 全店 売上高(前年比)", "yoy_index"),
        ("ホテル センチュリー 客室稼働率", "number"),
        ("ホテル 山のホテル 客室稼働率", "number"),
        ("ホテル はつはな 客室稼働率", "number"),
        ("ホテル 箱根ゆとわ 客室稼働率", "number"),
        ("ホテル RETONA 客室稼働率", "number"),
        ("ホテル CLAD 客室稼働率", "number"),
        ("ホテル 箱根ハイランド 客室稼働率", "number"),
        ("ホテル センチュリー 稼働率前年差", "number"),
        ("ホテル 山のホテル 稼働率前年差", "number"),
    ]
    oda_assign = assign_tree_rows("90070", [e for e, _ in oda_rows], sanyo_trees)
    fixtures["odakyuTree"] = [{
        "year": 2026, "code": "90070",
        "elements": [{**_el(e, u), "tree": oda_assign[e]} for e, u in oda_rows],
        **tree_payload("90070", [e for e, _ in oda_rows], sanyo_trees),
    }]
    # ピープル: 裸の前年比は国内カテゴリの節。旧名は同じ節（#480）
    people_rows = [
        ("全店 売上高", "number"),
        ("全店 売上高(前年比)", "yoy_index"),
        ("国内販売 全店 売上高", "number"),
        ("国内販売 全店 売上高(前年比)", "yoy_index"),
        ("海外販売 全店 売上高", "number"),
        ("海外販売 全店 売上高(前年比)", "yoy_index"),
        ("乳児・知育玩具 全店 売上高", "number"),
        ("乳児・知育玩具(前年比)", "yoy_index"),
        ("乳児・知育・構成玩具 全店 売上高", "number"),
        ("乳児・知育・構成玩具 全店 売上高(前年比)", "yoy_index"),
        ("メイキングトイ 全店 売上高", "number"),
        ("メイキングトイ(前年比)", "yoy_index"),
        ("屋内遊具・乗り物 全店 売上高", "number"),
        ("屋内遊具・乗り物(前年比)", "yoy_index"),
        ("その他 全店 売上高", "number"),
        ("その他(前年比)", "yoy_index"),
        ("海外販売・ロイヤリティ 全店 売上高", "number"),
        ("海外販売・ロイヤリティ 全店 売上高(前年比)", "yoy_index"),
    ]
    people_assign = assign_tree_rows("78650", [e for e, _ in people_rows], sanyo_trees)
    fixtures["peopleTree"] = [{
        "year": 2026, "code": "78650",
        "elements": [{**_el(e, u), "tree": people_assign[e]} for e, u in people_rows],
        **tree_payload("78650", [e for e, _ in people_rows], sanyo_trees),
    }]
    rakus_rows = [
        ("全社（連結） 売上高", "number"),
        ("全社（連結） 売上高(前年比)", "yoy_index"),
        ("クラウド事業 売上高", "number"),
        ("クラウド事業 売上高(前年比)", "yoy_index"),
        ("クラウド事業 ストック 売上高", "number"),
        ("クラウド事業 ストック 売上高(前年比)", "yoy_index"),
        ("楽楽精算 売上高", "number"),
        ("楽楽精算 売上高(前年比)", "yoy_index"),
        ("楽楽明細 売上高", "number"),
        ("楽楽明細 売上高(前年比)", "yoy_index"),
        ("IT人材事業 売上高", "number"),
        ("IT人材事業 売上高(前年比)", "yoy_index"),
    ]
    rakus_assign = assign_tree_rows("39230", [e for e, _ in rakus_rows], sanyo_trees)
    fixtures["rakusTree"] = [{
        "year": 2026, "code": "39230",
        "elements": [{**_el(e, u), "tree": rakus_assign[e]} for e, u in rakus_rows],
        **tree_payload("39230", [e for e, _ in rakus_rows], sanyo_trees),
    }]
    # Trailhead: 子会社は連結の見出しの下。業態の店舗数は全店店舗数（#476）
    th_rows = [
        ("連結売上高", "number"),
        ("連結売上高(前年比)", "yoy_index"),
        ("全店 売上高", "number"),
        ("売上高(前年比)", "yoy_index"),
        ("客数(前年比)", "yoy_index"),
        ("客単価(前年比)", "yoy_index"),
        ("全店 店舗数", "number"),
        ("店舗数", "number"),
        ("ワイエスフード 売上高", "number"),
        ("Yappa 売上高", "number"),
        ("KINKA FAMILY JAPAN 売上高", "number"),
        ("バーガーレボリューション 売上高", "number"),
        ("食べる 売上高", "number"),
        ("SBWorks 売上高", "number"),
        ("ラーメン 売上高", "number"),
        ("ラーメン 売上高(前年比)", "yoy_index"),
        ("ラーメン 客数(前年比)", "yoy_index"),
        ("ラーメン 客単価(前年比)", "yoy_index"),
        ("ラーメン 店舗数", "number"),
        ("焼肉 売上高", "number"),
        ("焼肉 店舗数", "number"),
        ("その他専門店 売上高", "number"),
        ("その他専門店 店舗数", "number"),
    ]
    th_assign = assign_tree_rows("33580", [e for e, _ in th_rows], sanyo_trees)
    fixtures["trailheadTree"] = [{
        "year": 2026, "code": "33580",
        "elements": [{**_el(e, u), "tree": th_assign[e]} for e, u in th_rows],
        **tree_payload("33580", [e for e, _ in th_rows], sanyo_trees),
    }]
    fit_rows = [
        ("会員数", "number"), ("会員数(前年比)", "yoy_index"),
        ("会員数 既存", "number"), ("会員数 既存(前年比)", "yoy_index"),
        ("全店 店舗数", "number"), ("増減 全店 店舗数", "number"),
        ("北海道 店舗数", "number"), ("九州沖縄 店舗数", "number"),
    ]
    fit_assign = assign_tree_rows("212A0", [e for e, _ in fit_rows], sanyo_trees)
    fixtures["fitTree"] = [{
        "year": 2026, "code": "212A0",
        "elements": [{**_el(e, u), "tree": fit_assign[e]} for e, u in fit_rows],
        **tree_payload("212A0", [e for e, _ in fit_rows], sanyo_trees),
    }]
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const jsPath = process.argv[process.argv.length - 1];
const fixtures = """ + json.dumps(fixtures, ensure_ascii=False) + r""";
const code = fs.readFileSync(jsPath, "utf8");
const el = {
  innerHTML: "",
  value: "",
  textContent: "",
  style: { display: "", setProperty() {}, removeProperty() {} },
  classList: { toggle() {} },
  dataset: {},
  clientWidth: 720,
  addEventListener() {},
};
const sandbox = {
  console,
  fetch: async () => ({ ok: false, json: async () => ({}) }),
  $: () => el,
  document: {
    querySelector: () => el,
    querySelectorAll: () => [],
    documentElement: { clientWidth: 720 },
  },
  window: {
    matchMedia: () => ({ matches: false, addEventListener() {} }),
    addEventListener() {},
  },
};
sandbox.window = sandbox.window;
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

const matsukiyo = fixtures.matsukiyo;
const drugRow = matsukiyo[0].elements.find((e) => e.element === "全店 売上高 ドラッグ(前年比)");
sandbox.drawMonthlyChart(fixtures.chartTables, "全店 売上高(前年比)");
const chartHtml = el.innerHTML;
// 物語の店舗数: 焼肉 の直営を選ぶと 全店 店舗数 ▸ 日本 ▸ 焼肉 ▸ 直営 まで開く
const tm = sandbox.monthlyTreeModel(fixtures.storeTree);
sandbox.monthlyTreeSeedOpen(tm);
sandbox.monthlyTreeReveal(tm, "焼肉 直営 全店 店舗数");
const treeHtml = sandbox.monthlyTreeHtml(tm, () => "<td></td>", [{y: 2026, m: 1}]);
const treeNames = [...treeHtml.matchAll(/<td class="tl-name[^"]*"[^>]*>(?:<span class="tl-tw">[^<]*<\/span>)?([^<]*)/g)].map((m) => m[1]);
// トライアル: 会社の行が無い指標は最初から開いて、ブランドの行が見える
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const btm = sandbox.monthlyTreeModel(fixtures.brandTree);
sandbox.monthlyTreeSeedOpen(btm);
const brandHtml = sandbox.monthlyTreeHtml(btm, () => "<td></td>", [{y: 2026, m: 1}]);
const brandNames = [...brandHtml.matchAll(/<td class="tl-name[^"]*"[^>]*>(?:<span class="tl-tw">[^<]*<\/span>)?([^<]*)/g)].map((m) => m[1]);
const nameRe = /<td class="tl-name[^"]*"[^>]*>(?:<span class="tl-tw">[^<]*<\/span>)?([^<]*)/g;
const namesOf = (html) => [...html.matchAll(nameRe)].map((m) => m[1]);
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const htm = sandbox.monthlyTreeModel(fixtures.hardoffTree);
sandbox.monthlyTreeSeedOpen(htm);
vm.runInContext('monthlyTlOpen.add("m:直営 店舗数"); monthlyTlOpen.add("m:FC 店舗数");', sandbox);
const hardoffNames = namesOf(sandbox.monthlyTreeHtml(htm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
sandbox.monthlyTreeSeedOpen(htm);
sandbox.monthlyTreeReveal(htm, "ハードオフ FC 店舗数");
const hardoffReveal = namesOf(sandbox.monthlyTreeHtml(htm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const ktm = sandbox.monthlyTreeModel(fixtures.kobeTree);
sandbox.monthlyTreeSeedOpen(ktm);
vm.runInContext('for(const k of ["店舗数", "仕入れ 全店 売上高"]){ monthlyTlOpen.add("m:" + k); monthlyTlOpen.add("m:" + k + "\\u203atree:全体\\u203aFC"); }', sandbox);
const kobeNames = namesOf(sandbox.monthlyTreeHtml(ktm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const stm = sandbox.monthlyTreeModel(fixtures.sanyoTree);
sandbox.monthlyTreeSeedOpen(stm);
vm.runInContext('monthlyTlOpen.add("m:売上高\\u203atree:全体\\u203aブランド別"); monthlyTlOpen.add("m:売上高\\u203atree:全体\\u203a販路別");', sandbox);
const sanyoNames = namesOf(sandbox.monthlyTreeHtml(stm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const vtm = sandbox.monthlyTreeModel(fixtures.valorTree);
sandbox.monthlyTreeSeedOpen(vtm);
const valorNames = namesOf(sandbox.monthlyTreeHtml(vtm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const wtm = sandbox.monthlyTreeModel(fixtures.watamiTree);
sandbox.monthlyTreeSeedOpen(wtm);
const watamiNames = namesOf(sandbox.monthlyTreeHtml(wtm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const abtm = sandbox.monthlyTreeModel(fixtures.autobacsTree);
sandbox.monthlyTreeSeedOpen(abtm);
vm.runInContext('monthlyTlOpen.add("m:既存 売上高\\u203atree:全体\\u203aサービス");', sandbox);
const autobacsNames = namesOf(sandbox.monthlyTreeHtml(abtm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const tbtm = sandbox.monthlyTreeModel(fixtures.tabayaTree);
sandbox.monthlyTreeSeedOpen(tbtm);
vm.runInContext('monthlyTlOpen.add("m:既存 売上高\\u203atree:全体\\u203a小売＋ネット通販"); monthlyTlOpen.add("m:既存 店舗数"); monthlyTlOpen.add("m:売上高"); monthlyTlOpen.add("m:売上高\\u203atree:全体\\u203a小売＋ネット通販"); monthlyTlOpen.add("m:店舗数");', sandbox);
const tabayaNames = namesOf(sandbox.monthlyTreeHtml(tbtm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const ltm = sandbox.monthlyTreeModel(fixtures.lomTree);
sandbox.monthlyTreeSeedOpen(ltm);
vm.runInContext('monthlyTlOpen.add("m:売上高\\u203atree:全体\\u203a電気");', sandbox);
const lomHtml = sandbox.monthlyTreeHtml(ltm, () => "<td></td>", [{y: 2026, m: 1}]);
const lomNames = namesOf(lomHtml);
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const artm = sandbox.monthlyTreeModel(fixtures.arclandsTree);
sandbox.monthlyTreeSeedOpen(artm);
vm.runInContext('monthlyTlOpen.add("m:店舗数\\u203atree:全体\\u203a外食事業");', sandbox);
const arclandsNames = namesOf(sandbox.monthlyTreeHtml(artm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const boftm = sandbox.monthlyTreeModel(fixtures.bookoffTree);
sandbox.monthlyTreeSeedOpen(boftm);
vm.runInContext('monthlyTlOpen.add("m:店舗数"); monthlyTlOpen.add("m:店舗数\\u203atree:全体\\u203a国内ブックオフ"); monthlyTlOpen.add("m:店舗数\\u203atree:全体\\u203a海外事業");', sandbox);
const bookoffNames = namesOf(sandbox.monthlyTreeHtml(boftm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const otm = sandbox.monthlyTreeModel(fixtures.odakyuTree);
sandbox.monthlyTreeSeedOpen(otm);
vm.runInContext(
  'monthlyTlOpen.add("m:輸送人員\\u203atree:全体\\u203a鉄道");'
  + 'monthlyTlOpen.add("m:輸送人員\\u203atree:全体\\u203a鉄道\\u203a定期+定期外");'
  + 'monthlyTlOpen.add("m:輸送人員\\u203atree:全体\\u203a鉄道\\u203a定期+定期外\\u203a定期計");'
  + 'monthlyTlOpen.add("m:取扱高\\u203atree:全体\\u203a百貨店");'
  + 'monthlyTlOpen.add("m:客室稼働率\\u203atree:全体\\u203aホテル");'
  + 'monthlyTlOpen.add("m:客室稼働率\\u203atree:全体\\u203aホテル\\u203a箱根");',
  sandbox);
const odakyuNames = namesOf(sandbox.monthlyTreeHtml(otm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const ptm = sandbox.monthlyTreeModel(fixtures.peopleTree);
sandbox.monthlyTreeSeedOpen(ptm);
vm.runInContext('monthlyTlOpen.add("m:全店 売上高\\u203atree:全体\\u203a国内販売");', sandbox);
const peopleNames = namesOf(sandbox.monthlyTreeHtml(ptm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const rktm = sandbox.monthlyTreeModel(fixtures.rakusTree);
sandbox.monthlyTreeSeedOpen(rktm);
vm.runInContext('monthlyTlOpen.add("m:売上高\\u203atree:全体\\u203aクラウド事業");', sandbox);
const rakusHtml = sandbox.monthlyTreeHtml(rktm, () => "<td></td>", [{y: 2026, m: 1}]);
const rakusNames = namesOf(rakusHtml);
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const thtm = sandbox.monthlyTreeModel(fixtures.trailheadTree);
sandbox.monthlyTreeSeedOpen(thtm);
vm.runInContext([
  'monthlyTlOpen.add("m:連結売上高\\u203atree:全体\\u203a主要子会社別売上高");',
  'monthlyTlOpen.add("m:全店 売上高");',
  'monthlyTlOpen.add("m:全店 店舗数");',
].join(""), sandbox);
const trailheadNames = namesOf(sandbox.monthlyTreeHtml(thtm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const ftm = sandbox.monthlyTreeModel(fixtures.fitTree);
sandbox.monthlyTreeSeedOpen(ftm);
const fitClosed = namesOf(sandbox.monthlyTreeHtml(ftm, () => "<td></td>", [{y: 2026, m: 1}]));
vm.runInContext('monthlyTlOpen.add("m:全店 店舗数");', sandbox);
const fitHtml = sandbox.monthlyTreeHtml(ftm, () => "<td></td>", [{y: 2026, m: 1}]);
const fitNames = namesOf(fitHtml);
vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
const atm = sandbox.monthlyTreeModel(fixtures.aeonTree);
sandbox.monthlyTreeSeedOpen(atm);
vm.runInContext(
  'for (const name of ["GMS事業", "スーパーマーケット事業", "ディベロッパー事業", "サービス・専門店事業"]) {'
  + ' monthlyTlOpen.add("m:既存 売上高\\u203atree:全体\\u203a" + name); }'
  + ' monthlyTlOpen.add("__more__");',
  sandbox,
);
const aeonNames = namesOf(sandbox.monthlyTreeHtml(atm, () => "<td></td>", [{y: 2026, m: 1}]));
const pairRec = {element: "売上高", unit: "number", yoy: "売上高(前年比)"};
const pairCell = sandbox.monthlyTlCell({
  "売上高": {unit: "number", values: {"8": 99}},
  "売上高(前年比)": {unit: "yoy_index", values: {"8": 108}},
}, pairRec, 8);
const pairYoyOnly = sandbox.monthlyTlCell({
  "売上高": {unit: "number", values: {}},
  "売上高(前年比)": {unit: "yoy_index", values: {"8": 108}},
}, pairRec, 8);
const unpairedCell = sandbox.monthlyTlCell({
  "店舗数": {unit: "number", values: {"8": 12}},
}, {element: "店舗数", unit: "number"}, 8);
const pairedRows = sandbox.monthlyPairYoyRows([
  {element: "売上高", unit: "number"},
  {element: "売上高(前年比)", unit: "yoy_index"},
]);
console.log(JSON.stringify({
  brandNames,
  bdCanon: fixtures.leftoverMkg.label.display,
  bdDefault: sandbox.pickDefaultMonthlySeries(matsukiyo),
  keeperDefault: sandbox.pickDefaultMonthlySeries(fixtures.keeper),
  spot138A: sandbox.pickDefaultMonthlySeries(fixtures.spot138A),
  spot3086: sandbox.pickDefaultMonthlySeries(fixtures.spot3086),
  spot9997: sandbox.pickDefaultMonthlySeries(fixtures.spot9997),
  alpenBoth: sandbox.pickDefaultMonthlySeries(fixtures.alpenBoth),
  bdCompanyKind: drugRow && drugRow.label.series_kind,
  chartHtml,
  treeNames,
  treeParent: tm.parent,
  hardoffNames,
  hardoffReveal,
  kobeNames,
  sanyoNames,
  valorNames,
  watamiNames,
  autobacsNames,
  tabayaNames,
  lomNames,
  arclandsNames,
  lomPart: lomHtml.includes("tl-part"),
  bookoffNames,
  odakyuNames,
  peopleNames,
  rakusNames,
  rakusPart: rakusHtml.includes('class="tl-part"'),
  trailheadNames,
  fitClosed,
  fitNames,
  fitPart: fitHtml.includes('class="tl-part"'),
  aeonNames,
  pairTxt: pairCell.txt,
  pairYoyOnlyTxt: pairYoyOnly.txt,
  unpairedTxt: unpairedCell.txt,
  pairedIds: pairedRows.map((r) => [r.element, r.yoy || ""]),
}));
"""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".js", delete=False, encoding="utf-8",
    ) as tmp:
        tmp.write(harness)
        harness_path = tmp.name
    try:
        proc = subprocess.run(
            ["node", harness_path, str(js_path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
    finally:
        Path(harness_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        _fail(f"monthly layout js: {proc.stderr.strip() or proc.stdout.strip() or proc.returncode}")
    try:
        got = json.loads(proc.stdout.strip().splitlines()[-1])
    except json.JSONDecodeError:
        _fail(f"monthly layout js stdout: {proc.stdout[:400]!r}")
    if got.get("bdCanon") != "MKG 全店 売上高 ドラッグ(前年比)":
        _fail(f"old leftover ドラッグ must display-canonicalize: {got.get('bdCanon')!r}")
    if got.get("bdDefault") != "全店 売上高(前年比)":
        _fail(f"TOP series must ignore 内訳: {got.get('bdDefault')!r}")
    if got.get("keeperDefault") != "全社 売上高(前年比)":
        _fail(f"60360 default must be 全社, not 海外: {got.get('keeperDefault')!r}")
    if got.get("spot138A") != "全店 売上高(前年比)":
        _fail(f"138A0 default must stay 全店 売上高(前年比): {got.get('spot138A')!r}")
    if got.get("spot3086") != "総額 連結売上高(前年比)":
        _fail(f"30860 default must stay 総額 連結売上高(前年比): {got.get('spot3086')!r}")
    if got.get("spot9997") != "連結売上高(前年比)":
        _fail(f"99970 default must stay 連結売上高(前年比): {got.get('spot9997')!r}")
    if got.get("alpenBoth") != "全店 売上高(前年比)":
        _fail(f"全店+全社 must still prefer 全店: {got.get('alpenBoth')!r}")
    if got.get("bdCompanyKind") is not None:
        _fail(f"会社 ドラッグ must not be all_store: {got.get('bdCompanyKind')!r}")
    chart_html = got.get("chartHtml") or ""
    if chart_html.count("<svg") != 2:
        _fail(f"year panels must draw two svgs: {chart_html[:400]!r}")
    if "2026年" not in chart_html or "2025年" not in chart_html:
        _fail(f"year panel labels missing: {chart_html[:400]!r}")
    if "monthly-bar-v" not in chart_html:
        _fail("year panels must label bars with monthly-bar-v")
    if "110" not in chart_html or "100" not in chart_html:
        _fail(f"bar labels must include 110 and 100: {chart_html[:400]!r}")
    # ブランドは 1 回だけ。直営 / FC はブランドの行の子。名前は 日本+海外 だけ外し（直営+FC は残す）、子は親との差だけ
    want_names = ["直営+FC 既存 店舗数", "直営", "焼肉", "直営+FC 全店 店舗数", "日本", "直営", "FC", "焼肉", "直営", "FC", "海外"]
    if got.get("treeNames") != want_names:
        _fail(f"物語 store tree rows: {got.get('treeNames')!r}")
    want_brand = ["既存 売上高", "トライアル", "西友", "全店 売上高", "トライアル", "西友"]
    if got.get("brandNames") != want_brand:
        _fail(f"トライアル: 会社の行が無い指標は開く: {got.get('brandNames')!r}")
    # 仕入れ 全店 の FC には行が無いので節を出さず 直轄 を同じ深さに
    want_kobe = ["売上高", "店舗数", "FC", "直轄", "直轄 仕入れ 既存 売上高(前年比)",
                 "全国 仕入れ 全店 売上高(前年比)", "直轄"]
    if got.get("kobeNames") != want_kobe:
        _fail(f"30380 config tree rows: {got.get('kobeNames')!r}")
    want_sanyo = [
        "全社 売上高(前年比)", "ブランド別",
        "MACKINTOSH LONDON", "MACKINTOSH PHILOSOPHY", "Paul Stuart", "EPOCA",
        "婦人服*1", "コーポレート*2", "LOVELESS",
        "販路別", "百貨店", "直営店", "EC・通販", "アウトレット",
    ]
    if got.get("sanyoNames") != want_sanyo:
        _fail(f"80110 config tree rows: {got.get('sanyoNames')!r}")
    want_valor = [
        "既存 売上高", "スーパーマーケット", "ドラッグストア", "ホームセンター",
        "既存 客数", "スーパーマーケット", "ドラッグストア", "ホームセンター",
        "既存 客単価", "スーパーマーケット", "ドラッグストア", "ホームセンター",
        "全店 売上高", "スーパーマーケット", "ドラッグストア", "ホームセンター",
        "グループ総 店舗数",
    ]
    if got.get("valorNames") != want_valor:
        _fail(f"99560 config tree rows: {got.get('valorNames')!r}")
    # 既存 売上高 は 国内外食 の行が無いので、数字の無い 国内外食 は出さず子を指標の直下に
    want_watami = ["既存 売上高", "サブウェイ除く", "サブウェイ",
                   "国内外食 全店 売上高(前年比)", "全店 店舗数", "国内外食"]
    if got.get("watamiNames") != want_watami:
        _fail(f"75220 config tree rows: {got.get('watamiNames')!r}")
    # 車検・整備はサービスを開いた下。既存売上高が先頭なので商品群が見える
    want_ab = [
        "日本 既存 売上高(前年比)", "タイヤ", "サービス", "車検・整備", "車販売",
        "日本 既存 客数(前年比)", "既存 客単価(前年比)", "日本 全店 売上高(前年比)",
        "日本 全店 客数(前年比)", "土日祝日前年差異",
    ]
    if got.get("autobacsNames") != want_ab:
        _fail(f"98320 config tree rows: {got.get('autobacsNames')!r}")
    # 既存売上は小売＋ネット通販の下に小売 / ネット通販。既存店舗数は根の直下（空の親は出さない）
    want_tabaya = [
        "既存 売上高", "小売＋ネット通販", "小売", "ネット通販",
        "既存 店舗数", "小売", "ネット通販",
        "全社 売上高(前年比)", "ビジネスユニット計", "小売＋ネット通販", "小売", "ネット通販", "アウトレット その他",
        "店舗数", "小売＋ネット通販", "アウトレット",
    ]
    if got.get("tabayaNames") != want_tabaya:
        _fail(f"76060 config tree rows: {got.get('tabayaNames')!r}")
    want_lom = ["ストック型 売上高", "電気", "内燃料調整費", "宅配水", "インターネット", "その他"]
    if got.get("lomNames") != want_lom:
        _fail(f"92520 config tree rows: {got.get('lomNames')!r}")
    want_oda = [
        "輸送人員", "鉄道", "定期+定期外", "定期計", "通勤", "通学", "定期外",
        "運輸収入", "鉄道",
        "取扱高", "百貨店", "新宿店", "町田店", "ふじさわ",
        "ストア 既存 取扱高", "ストア 既存 売上高(前年比)", "ストア 全店 取扱高", "ストア 全店 売上高(前年比)",
        "客室稼働率", "ホテル", "センチュリー", "箱根",
        "山のホテル", "はつはな", "箱根ゆとわ", "RETONA", "CLAD", "箱根ハイランド",
        "稼働率前年差", "ホテル",
    ]
    if got.get("odakyuNames") != want_oda:
        _fail(f"90070 config tree rows: {got.get('odakyuNames')!r}")
    want_people = [
        "全店 売上高", "国内販売", "乳児・知育玩具", "乳児・知育玩具(前年比)",
        "乳児・知育・構成玩具 全店 売上高", "メイキングトイ", "メイキングトイ(前年比)",
        "屋内遊具・乗り物", "屋内遊具・乗り物(前年比)", "その他", "その他(前年比)",
        "海外販売", "海外販売・ロイヤリティ 全店 売上高",
    ]
    if got.get("peopleNames") != want_people:
        _fail(f"78650 config tree rows: {got.get('peopleNames')!r}")
    if not got.get("lomPart"):
        _fail("92520 内燃料調整費 must render as tl-part")
    # 店舗数は会社の行が無いので開く。かつや / その他は外食事業を開いた下。国内総は指標名に出ない
    want_arc = [
        "既存 売上高", "小売事業", "外食事業",
        "全店 売上高", "小売事業", "外食事業",
        "店舗数", "外食事業", "かつや", "その他",
    ]
    if got.get("arclandsNames") != want_arc:
        _fail(f"98420 config tree rows: {got.get('arclandsNames')!r}")
    want_bookoff = [
        "国内ブックオフ 直営 既存 売上高(前年比)",
        "国内ブックオフ 直営 全店 売上高(前年比)",
        "チェーン 店舗数",
        "国内ブックオフ", "直営", "加盟店",
        "プレミアムサービス事業",
        "海外事業", "直営", "加盟店",
        "その他",
    ]
    if got.get("bookoffNames") != want_bookoff:
        _fail(f"92780 config tree rows: {got.get('bookoffNames')!r}")
    want_rakus = ["全社（連結） 売上高", "クラウド事業", "ストック", "楽楽精算", "楽楽明細", "IT人材事業"]
    if got.get("rakusNames") != want_rakus:
        _fail(f"39230 config tree rows: {got.get('rakusNames')!r}")
    if not got.get("rakusPart"):
        _fail("39230 ストック must render as tl-part")
    want_th = [
        "連結売上高", "主要子会社別売上高",
        "ワイエスフード", "Yappa", "KINKA FAMILY JAPAN", "バーガーレボリューション", "食べる", "SBWorks",
        "全店 売上高", "ラーメン", "焼肉", "その他専門店",
        "売上高(前年比)",
        "客数(前年比)", "客単価(前年比)",
        "全店 店舗数", "ラーメン", "焼肉", "その他専門店",
        "店舗数",
    ]
    if got.get("trailheadNames") != want_th:
        _fail(f"33580 config tree rows: {got.get('trailheadNames')!r}")
    if got.get("fitClosed") != ["会員数", "既存", "全店 店舗数", "増減 全店 店舗数"]:
        _fail(f"212A0 closed tree rows: {got.get('fitClosed')!r}")
    if got.get("fitNames") != [
        "会員数", "既存", "全店 店舗数", "北海道", "九州沖縄", "増減 全店 店舗数",
    ]:
        _fail(f"212A0 config tree rows: {got.get('fitNames')!r}")
    if not got.get("fitPart"):
        _fail("212A0 既存 must render as tl-part")
    want_aeon = [
        "既存 売上高", "GMS事業", "イオンリテール", "イオン北海道", "イオン九州",
        "スーパーマーケット事業", "マックスバリュ東海", "フジ", "いなげや",
        "ディスカウントストア事業", "ヘルス＆ウエルネス事業",
        "ディベロッパー事業", "イオンモール", "エンターテイメント事業",
        "サービス・専門店事業", "コックス", "ジーフット", "サービス事業", "専門店事業",
        "既存 客数", "GMS事業", "スーパーマーケット事業", "ディスカウントストア事業",
        "ヘルス＆ウエルネス事業", "ディベロッパー事業", "エンターテイメント事業",
        "サービス・専門店事業", "サービス事業", "専門店事業",
        "全店 売上高", "GMS事業", "スーパーマーケット事業", "ディベロッパー事業", "サービス・専門店事業",
        "その他の項目",
        "キャンドゥ 全店 売上高(前年比)", "キャンドゥ 既存 売上高(前年比)",
        "ミニストップ 全店 売上高(前年比)", "ミニストップ 既存 売上高(前年比)",
    ]
    if got.get("aeonNames") != want_aeon:
        _fail(f"82670 config tree rows: {got.get('aeonNames')!r}")
    # 直営と FC の両方に業態行があるときは両方の下に出す。FC の無い業態は直営だけ。店舗数だけの節は合計の下
    want_hardoff = [
        "店舗数", "直営", "海外", "ハードオフ", "オフハウス", "ブックオフ",
        "FC", "海外", "ハードオフ", "オフハウス", "庄や",
    ]
    if got.get("hardoffNames") != want_hardoff:
        _fail(f"ハードオフ store tree rows: {got.get('hardoffNames')!r}")
    want_reveal = ["店舗数", "直営", "FC", "海外", "ハードオフ", "オフハウス", "庄や"]
    if got.get("hardoffReveal") != want_reveal:
        _fail(f"ハードオフ FC reveal: {got.get('hardoffReveal')!r}")
    pair_txt = got.get("pairTxt") or ""
    if "99" not in pair_txt or 'class="tl-sub"' not in pair_txt or "(108)" not in pair_txt:
        _fail(f"paired cell must show amount and yoy: {pair_txt!r}")
    yoy_only = got.get("pairYoyOnlyTxt") or ""
    if 'class="tl-sub"' not in yoy_only or "(108)" not in yoy_only or "99" in yoy_only:
        _fail(f"amount-missing month must show yoy only: {yoy_only!r}")
    if got.get("unpairedTxt") != "12":
        _fail(f"unpaired number cell: {got.get('unpairedTxt')!r}")
    if got.get("pairedIds") != [["売上高", "売上高(前年比)"]]:
        _fail(f"pair yoy rows: {got.get('pairedIds')!r}")

def _assert_known_prod_units(c) -> None:
    from app.db import SessionLocal
    from app.monthly.maintenance import apply_known_monthly_element_units
    from app.models import MonthlyElement, MonthlyValue

    _ensure_stock("92790")
    _ensure_stock("262A0")
    db = SessionLocal()
    try:
        if (not db.query(MonthlyElement).filter(MonthlyElement.code == "92790").all()
                and not db.query(MonthlyElement).filter(MonthlyElement.code == "262A0").all()):
            return
    finally:
        db.close()

    r = c.post("/api/v1/tables/92790/2025/elements", json={"element": "全店売上高", "unit": "number"})
    if r.status_code not in (200, 201):
        _fail(f"seed 92790: {r.status_code} {r.text[:200]}")
    r = c.post("/api/v1/tables/92790/2025/elements", json={"element": "店舗数", "unit": "number"})
    _expect(r, 201, "seed 92790 stores")
    r = c.put("/api/v1/tables/92790/2025/values",
              json={"element": "全店売上高", "month": 11, "value": 125.8})
    _expect(r, 200, "seed gift 125.8")
    r = c.put("/api/v1/tables/92790/2025/values",
              json={"element": "店舗数", "month": 11, "value": 303})
    _expect(r, 200, "seed gift stores")

    r = c.get("/api/v1/tables/262A0/2024")
    if r.status_code == 200:
        r = c.post("/api/v1/tables/262A0/2024/elements", json={"element": "店舗数", "unit": "number"})
        if r.status_code not in (200, 201):
            _fail(f"seed 262A0 stores: {r.status_code} {r.text[:200]}")
        r = c.put("/api/v1/tables/262A0/2024/values",
                  json={"element": "売上高", "month": 1, "value": -7.1})
        _expect(r, 200, "seed intermestic -7.1")

    apply_known_monthly_element_units()

    db = SessionLocal()
    try:
        gift = db.query(MonthlyElement).filter(MonthlyElement.code == "92790").all()
        inter = db.query(MonthlyElement).filter(MonthlyElement.code == "262A0").all()
        if not gift and not inter:
            return
        for row in gift:
            want = "number" if "店舗数" in row.element else "yoy_index"
            if row.unit != want:
                _fail(f"92790 {row.element!r} unit {row.unit!r} != {want}")
        for row in inter:
            if "店舗数" in row.element and row.unit != "number":
                _fail(f"262A0 店舗数 unit {row.unit!r}")
            if row.unit == "yoy_pct":
                _fail(f"262A0 must not force yoy_pct: {row.element!r} {row.unit!r}")
        v = db.get(MonthlyValue, ("92790", 2025, "全店 売上高", 11))
        if v is None or float(v.value) != 125.8:
            _fail(f"gift value converted: {v}")
        v = db.get(MonthlyValue, ("262A0", 2024, "売上高", 1))
        if v is not None and float(v.value) != -7.1:
            _fail(f"intermestic value converted: {v.value}")
    finally:
        db.close()

    r = c.get("/api/v1/tables/92790/2025")
    _expect(r, 200, "GET 92790 after unit assign")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["全店 売上高"]["unit"] != "yoy_index" or float(by["全店 売上高"]["values"]["11"]) != 125.8:
        _fail(f"92790 GET: {by['全店 売上高']!r}")
    if format_monthly_cell(125.8, by["全店 売上高"]["unit"]) != "125.8":
        _fail("92790 display")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"92790 stores GET: {by['店舗数']!r}")

    r = c.get("/api/v1/tables/262A0/2024")
    if r.status_code != 200:
        return
    by = {e["element"]: e for e in r.json()["elements"]}
    if "売上高" in by:
        vals = by["売上高"]["values"]
        raw = vals.get("1", vals.get(1))
        if raw is not None and float(raw) != -7.1:
            _fail(f"262A0 value converted: {vals!r}")

if __name__ == "__main__":
    main()
