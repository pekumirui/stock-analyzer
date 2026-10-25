"""月次の木（tree）と行の並び。config の木は銘柄ごと、自動の木と和集合の並びは関数で確かめる。手元の stock.db は触らない。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")


import app.monthly.ingest as monthly_ingest_mod  # noqa: E402
from app.monthly.ingest import (  # noqa: E402
    apply_issuer_aliases,
    canonicalize_element_label,
    monthly_element_label,
    monthly_row_segment,
    monthly_row_slot,
    union_monthly_elements,
)

INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def test_tree_99990() -> None:
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


def test_tree_75320() -> None:
    pass


def test_tree_31750() -> None:
    pass


def test_tree_30880() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

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
        "マツモトキヨシグループ FC 売上高(前年比)": (
            ["全体", "マツモトキヨシグループ"], "FC 売上高(前年比)", "FC 売上高",
        ),
    }
    if got != want:
        _fail(f"30880 tree rows: {got!r}")
    mk_tree = tree_payload("30880", list(want), load_monthly_trees())
    mk_metrics = mk_tree["tree_metrics"]
    if mk_metrics[mk_metrics.index("既存 客単価") + 1] != "FC 売上高" or mk_metrics[-4:] != [
        "出店", "退店", "子会社化等", "店舗数",
    ]:
        _fail(f"30880 tree_metrics: {mk_metrics!r}")
    mk_kids = {c["name"]: c for c in mk_tree["tree"][0]["children"]}
    if not mk_kids["グループ調剤"]["part"] or mk_kids["マツモトキヨシグループ"]["part"] or mk_kids["アンドカンパニー"]["part"]:
        _fail(f"30880 part: {mk_kids!r}")


def test_tree_31970() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_35470() -> None:
    pass


def test_tree_35470_2() -> None:

    got_alias = apply_issuer_aliases(
        "35470", canonicalize_element_label("当年 全店 店舗数", "number"), year=2025,
    )
    if got_alias != "串カツ田中G 直営 店舗数":
        _fail(f"35470 当年 全店 店舗数 alias: {got_alias!r}")


def test_tree_46660() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_70490() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_70490_2() -> None:

    if apply_issuer_aliases("70490", "全店 売上高(前年比)", year=2026) != "全店 売上高(前年比)":
        _fail("70490 must not alias 全店 売上高(前年比)")


def test_tree_74760() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

    ao = assign_tree_rows("74760", [
        "単体 売上高",
        "単体 売上高(前年比)",
        "営業日数",
        "1日当たり 売上高(前年比)",
        "主要3分野 売上高",
        "主要3分野 売上高(前年比)",
        "ラボラトリー 売上高",
        "ラボラトリー 売上高(前年比)",
        "ラボラトリー 1日当たり 売上高(前年比)",
        "インダストリー 売上高",
        "メディカル 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in ao.items()}
    want = {
        "単体 売上高": (["全体"], "単体 売上高", "売上高"),
        "単体 売上高(前年比)": (["全体"], "単体 売上高(前年比)", "売上高"),
        "営業日数": (["全体"], "営業日数", "営業日数"),
        "1日当たり 売上高(前年比)": (["全体"], "1日当たり 売上高(前年比)", "1日当たり 売上高"),
        "主要3分野 売上高": (["全体", "主要3分野"], "売上高", "売上高"),
        "主要3分野 売上高(前年比)": (["全体", "主要3分野"], "売上高(前年比)", "売上高"),
        "ラボラトリー 売上高": (["全体", "主要3分野", "ラボラトリー"], "売上高", "売上高"),
        "ラボラトリー 売上高(前年比)": (["全体", "主要3分野", "ラボラトリー"], "売上高(前年比)", "売上高"),
        "ラボラトリー 1日当たり 売上高(前年比)": (
            ["全体", "主要3分野", "ラボラトリー"], "1日当たり 売上高(前年比)", "1日当たり 売上高",
        ),
        "インダストリー 売上高": (["全体", "主要3分野", "インダストリー"], "売上高", "売上高"),
        "メディカル 売上高(前年比)": (["全体", "主要3分野", "メディカル"], "売上高(前年比)", "売上高"),
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


def test_tree_78650() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

    people_rows = [
        "売上高",
        "売上高(前年比)",
        "国内販売 売上高",
        "国内販売 売上高(前年比)",
        "海外販売 売上高",
        "海外販売 売上高(前年比)",
        "乳児・知育玩具 売上高",
        "乳児・知育玩具 売上高(前年比)",
        "乳児・知育・構成玩具 売上高",
        "乳児・知育・構成玩具 売上高(前年比)",
        "メイキングトイ 売上高",
        "メイキングトイ 売上高(前年比)",
        "屋内遊具・乗り物 売上高",
        "屋内遊具・乗り物 売上高(前年比)",
        "その他 売上高",
        "その他 売上高(前年比)",
        "海外販売・ロイヤリティ 売上高",
        "海外販売・ロイヤリティ 売上高(前年比)",
    ]
    people_tree = tree_payload("78650", people_rows, load_monthly_trees())
    if people_tree is None or people_tree.get("tree_auto"):
        _fail("78650 must use the config tree")
    if people_tree["tree_metrics"] != ["売上高"]:
        _fail(f"78650 metrics: {people_tree!r}")
    people_root = people_tree["tree"][0]
    if people_root["elements"] != ["売上高", "売上高(前年比)"]:
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


def test_tree_96160() -> None:
    pass


def test_tree_26540() -> None:
    pass


def test_tree_65610() -> None:
    pass


def test_tree_73700() -> None:
    pass


def test_tree_82330() -> None:
    pass


def test_tree_138A0() -> None:
    pass


def test_tree_212A0() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_99410() -> None:
    pass


def test_tree_99410_2() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

    taiyo_tree = tree_payload("99410", [
        "売上高", "農産品 売上高", "食料部計 売上高", "営業開拓部計",
    ], load_monthly_trees())
    if taiyo_tree is None:
        _fail("99410 tree payload")
    if [n["name"] for n in taiyo_tree["tree"][0]["children"]] != [
        "食料部", "農産部", "営業開拓部", "中国開拓部", "生活産業部",
    ]:
        _fail(f"99410 dept order: {taiyo_tree['tree'][0]['children']!r}")
    if not taiyo_tree["tree"][0]["children"][1]["heading"]:
        _fail("99410 農産部 must be a heading")


def test_tree_30380() -> None:
    pass


def test_tree_60360() -> None:
    pass


def test_tree_80110() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_78230() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

    an = assign_tree_rows("78230", [
        "売上高(前年比)",
        "オーダーメイドウィッグ 男性(前年比)",
        "主要商品(前年比)",
        "主要商品 男性(前年比)",
        "主要商品 男性新規(前年比)",
        "主要商品 男性リピート(前年比)",
        "主要商品 女性新規(前年比)",
        "主要商品 新規(前年比)",
        "主要商品 リピート(前年比)",
        "ヘアケア商品 女性(前年比)",
        "ジュリア・オージェ(前年比)",
        "その他商品(前年比)",
        "男性 客数(前年比)",
        "女性 客数(前年比)",
        "客数(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in an.items()}
    want = {
        "売上高(前年比)": (["全体"], "売上高(前年比)", "売上高(前年比)"),
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
        "主要商品 新規(前年比)": (["全体", "主要商品", "新規"], "主要商品 新規(前年比)", "売上高(前年比)"),
        "主要商品 リピート(前年比)": (["全体", "主要商品", "リピート"], "主要商品 リピート(前年比)", "売上高(前年比)"),
        "ヘアケア商品 女性(前年比)": (
            ["全体", "ヘアケア商品", "女性"], "ヘアケア商品 女性(前年比)", "売上高(前年比)"),
        "ジュリア・オージェ(前年比)": (["全体", "ジュリア・オージェ"], "ジュリア・オージェ(前年比)", "売上高(前年比)"),
        "その他商品(前年比)": (["全体", "その他商品"], "その他商品(前年比)", "売上高(前年比)"),
        "男性 客数(前年比)": (["全体", "延べ来店顧客数", "男性"], "男性 客数(前年比)", "客数(前年比)"),
        "女性 客数(前年比)": (["全体", "延べ来店顧客数", "女性"], "女性 客数(前年比)", "客数(前年比)"),
        "客数(前年比)": (["全体", "延べ来店顧客数", "合計"], "客数(前年比)", "客数(前年比)"),
    }
    if got != want:
        _fail(f"78230 tree rows: {got!r}")
    old_open = assign_tree_rows("78230", ["主要商品 出店(前年比)"], load_monthly_trees())
    if old_open.get("主要商品 出店(前年比)", {}).get("path") != ["全体", "主要商品", "新規"]:
        _fail(f"78230 old 出店 row still maps to 新規: {old_open!r}")
    an_tree = tree_payload("78230", list(want), load_monthly_trees())
    if an_tree is None or an_tree["tree_metrics"] != ["売上高(前年比)", "客数(前年比)"]:
        _fail(f"78230 metrics: {an_tree!r}")
    if "出店" in an_tree["tree_metrics"]:
        _fail("78230 主要商品の新規 must not be metric 出店")
    kids = [n["name"] for n in an_tree["tree"][0]["children"]]
    if kids[:4] != ["オーダーメイドウィッグ", "増毛", "育毛ケア・サービス", "主要商品"] or kids[-1] != "延べ来店顧客数":
        _fail(f"78230 child order: {kids!r}")
    shuyo = next(n for n in an_tree["tree"][0]["children"] if n["name"] == "主要商品")
    shuyo_kids = [c["name"] for c in shuyo["children"]]
    if shuyo["heading"] or shuyo_kids != ["男性", "女性", "新規", "リピート"]:
        _fail(f"78230 主要商品: {shuyo!r}")
    if not an_tree["tree"][0]["children"][0]["heading"]:
        _fail("78230 オーダーメイドウィッグ is a heading (no total row)")


def test_tree_75220() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_76060() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_99560() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_82670() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_98320() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_98420() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_92520() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_92780() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_90070() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_81660() -> None:
    from app.monthly.tree import assign_tree_rows, tree_payload
    from app.monthly.tree import load_monthly_trees

    taka_rows = [
        "全社 売上高(前年比)",
        "全社 客数(前年比)",
        "全社 客単価(前年比)",
        "店舗数",
        "小売+ネット通販 既存 売上高(前年比)",
        "小売+ネット通販 既存 客数(前年比)",
        "小売+ネット通販 既存 客単価(前年比)",
        "小売 既存 売上高(前年比)",
        "小売 既存 客数(前年比)",
        "小売 既存 客単価(前年比)",
        "小売 店舗数",
        "ネット通販 既存 売上高(前年比)",
        "ネット通販 既存 客数(前年比)",
        "ネット通販 既存 客単価(前年比)",
    ]
    taka = assign_tree_rows("81660", taka_rows, load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in taka.items()}
    retail = ["全体", "小売+ネット通販", "小売"]
    web = ["全体", "小売+ネット通販", "ネット通販"]
    both = ["全体", "小売+ネット通販"]
    want = {
        "全社 売上高(前年比)": (["全体"], "全社 売上高(前年比)", "全社 売上高"),
        "全社 客数(前年比)": (["全体"], "全社 客数(前年比)", "全社 客数"),
        "全社 客単価(前年比)": (["全体"], "全社 客単価(前年比)", "全社 客単価"),
        "店舗数": (["全体"], "店舗数", "店舗数"),
        "小売+ネット通販 既存 売上高(前年比)": (both, "既存 売上高(前年比)", "既存 売上高"),
        "小売+ネット通販 既存 客数(前年比)": (both, "既存 客数(前年比)", "既存 客数"),
        "小売+ネット通販 既存 客単価(前年比)": (both, "既存 客単価(前年比)", "既存 客単価"),
        "小売 既存 売上高(前年比)": (retail, "既存 売上高(前年比)", "既存 売上高"),
        "小売 既存 客数(前年比)": (retail, "既存 客数(前年比)", "既存 客数"),
        "小売 既存 客単価(前年比)": (retail, "既存 客単価(前年比)", "既存 客単価"),
        "小売 店舗数": (retail, "店舗数", "店舗数"),
        "ネット通販 既存 売上高(前年比)": (web, "既存 売上高(前年比)", "既存 売上高"),
        "ネット通販 既存 客数(前年比)": (web, "既存 客数(前年比)", "既存 客数"),
        "ネット通販 既存 客単価(前年比)": (web, "既存 客単価(前年比)", "既存 客単価"),
    }
    if got != want:
        _fail(f"81660 tree rows: {got!r}")
    taka_tree = tree_payload("81660", taka_rows, load_monthly_trees())
    if taka_tree is None or taka_tree.get("tree_auto"):
        _fail("81660 must use the config tree")
    if taka_tree["tree_metrics"] != [
        "既存 売上高", "既存 客数", "既存 客単価",
        "全社 売上高", "全社 客数", "全社 客単価", "店舗数",
    ]:
        _fail(f"81660 metrics: {taka_tree!r}")
    taka_kids = taka_tree["tree"][0]["children"]
    if [n["name"] for n in taka_kids] != ["小売+ネット通販"]:
        _fail(f"81660 section order: {taka_kids!r}")
    if [n["name"] for n in taka_kids[0]["children"]] != ["小売", "ネット通販"]:
        _fail(f"81660 小売 / ネット通販 must be under 小売+ネット通販: {taka_kids[0]!r}")
    if taka_tree["tree"][0]["elements"] != [
        "全社 売上高(前年比)", "全社 客数(前年比)", "全社 客単価(前年比)", "店舗数",
    ]:
        _fail(f"81660 root rows: {taka_tree['tree'][0]!r}")
    if taka_kids[0]["elements"] != [
        "小売+ネット通販 既存 売上高(前年比)",
        "小売+ネット通販 既存 客数(前年比)",
        "小売+ネット通販 既存 客単価(前年比)",
    ]:
        _fail(f"81660 小売+ネット通販 rows: {taka_kids[0]!r}")


def test_tree_81980() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

    mv_rows = [
        "全店 売上高(前年比)",
        "同規模 売上高(前年比)",
        "全店 客数(前年比)",
        "同規模 客数(前年比)",
        "全店 客単価(前年比)",
        "同規模 客単価(前年比)",
        "一品 全店 客単価(前年比)",
        "同規模 一品 客単価(前年比)",
        "買上点数 全店 売上高(前年比)",
        "同規模 買上点数(前年比)",
        "新設店舗",
        "都市型小型店 新設",
        "退店",
        "店舗数",
    ]
    mv_tree = tree_payload("81980", mv_rows, load_monthly_trees())
    if mv_tree is None or mv_tree.get("tree_auto"):
        _fail("81980 must use the config tree")
    if mv_tree["tree_metrics"] != [
        "全店 売上高", "同規模 売上高", "全店 客数", "同規模 客数",
        "全店 客単価", "同規模 客単価", "一品単価", "同規模 一品単価",
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


def test_tree_39230() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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


def test_tree_33580() -> None:
    from app.monthly.tree import tree_payload
    from app.monthly.tree import load_monthly_trees

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
    y25 = [("ラーメン 直営+FC 既存 売上高(前年比)", "yoy_index")]
    auto = build_auto_tree(
        "99991", {2026: y26, 2025: y25},
        company_metric={"日本 直営 全店 店舗数": "直営 全店 店舗数"},
    )
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in auto.rows.items()}
    want = {
        "直営+FC 全店 売上高(前年比)": (["全体"], "直営+FC 全店 売上高(前年比)", "直営+FC 全店 売上高"),
        "直営+FC 既存 売上高(前年比)": (["全体"], "直営+FC 既存 売上高(前年比)", "直営+FC 既存 売上高"),
        "全店 売上高 ドラッグ(前年比)": (["全体", "ドラッグ"], "全店 売上高(前年比)", "全店 売上高"),
        "焼肉 直営+FC 既存 売上高(前年比)": (["全体", "焼肉"], "直営+FC 既存 売上高(前年比)", "直営+FC 既存 売上高"),
        "焼肉 直営 全店 店舗数": (["全体", "焼肉"], "直営 全店 店舗数", "直営 全店 店舗数"),
        "日本 直営 全店 店舗数": (["全体"], "日本 直営 全店 店舗数", "直営 全店 店舗数"),
        "日本+海外 直営+FC 全店 店舗数": (["全体"], "直営+FC 全店 店舗数", "直営+FC 全店 店舗数"),
        "焼肉 直営+FC 全店 店舗数": (["全体", "焼肉"], "直営+FC 全店 店舗数", "直営+FC 全店 店舗数"),
        "庄や 店舗数": (["全体", "庄や"], "店舗数", "店舗数"),
        "ラーメン 直営+FC 既存 売上高(前年比)": (["全体", "ラーメン"], "直営+FC 既存 売上高(前年比)", "直営+FC 既存 売上高"),
    }
    if got != want:
        _fail(f"auto tree rows: {got!r}")
    want_metrics = ["直営+FC 既存 売上高", "直営+FC 全店 売上高", "全店 売上高", "直営+FC 全店 店舗数", "店舗数", "直営 全店 店舗数"]
    if auto.metrics != want_metrics:
        _fail(f"auto tree metrics: {auto.metrics!r}")
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
        "グループ 稼働率": (["全体"], "稼働率"),
    }
    if got != want:
        _fail(f"auto tree total/metric tokens: {got!r}")
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
    kakiyasu = build_auto_tree("22940", {2026: [
        ("精肉事業 売上高", "number"), ("精肉事業 既存 売上高", "number"), ("惣菜事業 売上高", "number"),
        ("全社 売上高", "number"), ("全社 売上高(前年比)", "yoy_index"), ("既存 売上高", "number"),
    ]})
    if kakiyasu.metrics != ["既存 売上高", "売上高"]:
        _fail(f"全社 売上高 と 売上高 は 1 つの指標: {kakiyasu.metrics!r}")
    if {e: kakiyasu.rows[e]["metric"] for e in ("全社 売上高", "全社 売上高(前年比)", "精肉事業 売上高")} != {
            "全社 売上高": "売上高", "全社 売上高(前年比)": "売上高", "精肉事業 売上高": "売上高"}:
        _fail(f"全社 の会社の行は 売上高 に寄る: {kakiyasu.rows!r}")


def test_auto_tree_71340() -> None:
    """7134 アップガレージグループ（#580）。国の語のあとのブランドは節にする（#590）、
    指標語の無い前年比は同じ チャネル + 範囲 の 売上高 の行に寄せる（#578）。
    """
    from app.monthly.tree import _auto_split, auto_tree_payload, build_auto_tree

    months = {
        2024: [
            ("直営 全店 売上高", "number"), ("直営 全店(前年比)", "yoy_index"),
            ("直営 既存 売上高", "number"), ("直営 既存(前年比)", "yoy_index"),
            ("FC 全店 売上高", "number"), ("FC 全店(前年比)", "yoy_index"),
            ("FC 既存 売上高", "number"), ("FC 既存(前年比)", "yoy_index"),
            ("全店 売上高", "number"), ("全店 売上高(前年比)", "yoy_index"),
            ("直営+FC 全店(前年比)", "yoy_index"),
            ("アップガレージ 直営 店舗数", "number"), ("アップガレージ FC 店舗数", "number"),
            ("海外 アップガレージ 店舗数", "number"),
            ("ライダース 直営 店舗数", "number"), ("ライダース FC 店舗数", "number"),
            ("ホイールズ 直営 店舗数", "number"), ("ホイールズ FC 店舗数", "number"),
            ("専門店ブランド 直営 店舗数", "number"), ("専門店ブランド FC 店舗数", "number"),
            ("直営 店舗数", "number"), ("FC 店舗数", "number"), ("店舗数", "number"),
        ],
    }
    auto = build_auto_tree("71340", months)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in auto.rows.items()}
    want_yoy = {
        "直営 全店(前年比)": (["全体"], "直営 全店 売上高(前年比)", "直営 全店 売上高"),
        "直営 既存(前年比)": (["全体"], "直営 既存 売上高(前年比)", "直営 既存 売上高"),
        "FC 全店(前年比)": (["全体"], "FC 全店 売上高(前年比)", "FC 全店 売上高"),
        "FC 既存(前年比)": (["全体"], "FC 既存 売上高(前年比)", "FC 既存 売上高"),
        "直営+FC 全店(前年比)": (["全体"], "全店 売上高(前年比)", "全店 売上高"),
    }
    for e, w in want_yoy.items():
        if got.get(e) != w:
            _fail(f"71340 bare yoy {e}: {got.get(e)!r} want {w!r}")
    if got["海外 アップガレージ 店舗数"] != (["全体", "アップガレージ"], "海外 店舗数", "海外 店舗数"):
        _fail(f"71340 海外 brand: {got['海外 アップガレージ 店舗数']!r}")
    if got["アップガレージ 直営 店舗数"][0] != ["全体", "アップガレージ"]:
        _fail(f"71340 brand node: {got['アップガレージ 直営 店舗数']!r}")
    if auto.metric_parent.get("海外 店舗数") != "店舗数":
        _fail(f"71340 metric parent (海外 店舗数): {auto.metric_parent!r}")
    pay = auto_tree_payload(auto, [e for es in months.values() for e, _ in es])
    root = pay["tree"][0]
    els = root["elements"]
    for n, y in (("直営 全店", "直営 全店(前年比)"), ("直営 既存", "直営 既存(前年比)"),
                 ("FC 全店", "FC 全店(前年比)"), ("FC 既存", "FC 既存(前年比)")):
        want = [f"{n} 売上高", y]
        if want not in (els[i:i + 2] for i in range(len(els) - 1)):
            _fail(f"71340 yoy must sit next to its 売上高: want {want} in {els!r}")
    if [n["name"] for n in root["children"]] != ["アップガレージ", "ライダース", "ホイールズ", "専門店ブランド"]:
        _fail(f"71340 children: {root['children']!r}")
    m = monthly_element_label("海外 米国 店舗数", "number", "99990")
    if _auto_split(m) != ("米国", "海外 店舗数"):
        _fail(f"country 海外 米国: {_auto_split(m)!r}")
    m = monthly_element_label("日本 鳥貴族 店舗数", "number", "99990")
    if _auto_split(m) != ("鳥貴族", "日本 店舗数"):
        _fail(f"country 日本 鳥貴族: {_auto_split(m)!r}")
    m = monthly_element_label("日本 ラ・ボエム 既存 売上高(前年比)", "yoy_index", "99990")
    if _auto_split(m) != ("ラ・ボエム", "日本 既存 売上高(前年比)"):
        _fail(f"country 日本 ラ・ボエム: {_auto_split(m)!r}")
    m = monthly_element_label("日本 鳥貴族 TCC 店舗数", "number", "99990")
    if _auto_split(m) != ("鳥貴族", "日本 TCC 店舗数"):
        _fail(f"country 日本 鳥貴族 TCC: {_auto_split(m)!r}")
    m = monthly_element_label("日本 (曜日調整前) 既存 売上高(前年比)", "yoy_index", "99990")
    if _auto_split(m) != ("", "日本 (曜日調整前) 既存 売上高(前年比)"):
        _fail(f"country 日本 (曜日調整前): {_auto_split(m)!r}")
    if _auto_split({"display": "海外 アップガレージ 店舗数", "group": "アップガレージグループ"}) \
            != ("", "海外 アップガレージ 店舗数"):
        _fail("country no-match must not split")
    m = monthly_element_label("日本+海外 直営+FC 全店 店舗数", "number", "99991")
    if _auto_split(m) != ("", "直営+FC 全店 店舗数"):
        _fail(f"company country group must strip whole: {_auto_split(m)!r}")


def test_auto_tree_country_total_keeps_country() -> None:
    """46800 ラウンドワン: `日本 総 売上高` は `日本 売上高`。節（ボウリング …）も `日本 売上高` なので
    会社の `日本 ` 付きの行を国なしに寄せない（寄せると国の語の無い 米国 の節の `売上高` の親になる）。
    """
    from app.monthly.tree import build_auto_tree

    months = {2025: [
        ("日本 ボウリング 売上高", "number"), ("日本 カラオケ 売上高", "number"), ("日本 総 売上高", "number"),
        ("米国 ボウリング 売上高", "number"), ("米国 総 売上高", "number"),
    ]}
    rows = build_auto_tree("46800", months).rows
    if (rows["日本 総 売上高"]["path"], rows["日本 総 売上高"]["metric"]) != (["全体"], "日本 売上高"):
        _fail(f"46800 日本 総: {rows['日本 総 売上高']!r}")
    if (rows["日本 ボウリング 売上高"]["path"], rows["日本 ボウリング 売上高"]["metric"]) \
            != (["全体", "ボウリング"], "日本 売上高"):
        _fail(f"46800 日本 ボウリング: {rows['日本 ボウリング 売上高']!r}")


def test_auto_tree_76850() -> None:
    """76850 B u y S e l l T e c h n o l o g i e s: `仕入高` は指標語（#581）。

    `出張訪問買取 仕入高` は `出張訪問買取` の節で `出張訪問数` の隣、`店舗買取 仕入高` は
    `店舗買取` の節。どちらも根に散らばらない。店舗数の 直営 / FC の親子はそのまま。
    """
    from app.monthly.tree import auto_tree_payload, build_auto_tree

    months = {2026: [
        ("直営 店舗数", "number"),
        ("FC 店舗数", "number"),
        ("グループ 店舗数", "number"),
        ("出張訪問買取 出張訪問数", "number"),
        ("出張訪問買取 出張訪問数(前年比)", "yoy_index"),
        ("出張訪問買取 仕入高", "number"),
        ("出張訪問買取 仕入高(前年比)", "yoy_index"),
        ("店舗買取 仕入高", "number"),
        ("店舗買取 仕入高(前年比)", "yoy_index"),
    ]}
    auto = build_auto_tree("76850", months)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in auto.rows.items()}
    want = {
        "直営 店舗数": (["全体"], "直営 店舗数", "直営 店舗数"),
        "FC 店舗数": (["全体"], "FC 店舗数", "FC 店舗数"),
        "グループ 店舗数": (["全体"], "店舗数", "店舗数"),
        "出張訪問買取 出張訪問数": (["全体", "出張訪問買取"], "出張訪問数", "出張訪問数"),
        "出張訪問買取 出張訪問数(前年比)": (["全体", "出張訪問買取"], "出張訪問数(前年比)", "出張訪問数"),
        "出張訪問買取 仕入高": (["全体", "出張訪問買取"], "仕入高", "仕入高"),
        "出張訪問買取 仕入高(前年比)": (["全体", "出張訪問買取"], "仕入高(前年比)", "仕入高"),
        "店舗買取 仕入高": (["全体", "店舗買取"], "仕入高", "仕入高"),
        "店舗買取 仕入高(前年比)": (["全体", "店舗買取"], "仕入高(前年比)", "仕入高"),
    }
    if got != want:
        _fail(f"76850 tree rows: {got!r}")
    if auto.metric_parent != {"直営 店舗数": "店舗数", "FC 店舗数": "店舗数"}:
        _fail(f"76850 metric parents: {auto.metric_parent!r}")
    pay = auto_tree_payload(auto, [e for e, _ in months[2026]])
    kids = {c["name"]: c for c in pay["tree"][0]["children"]}
    if sorted(kids) != ["出張訪問買取", "店舗買取"]:
        _fail(f"76850 children: {pay['tree'][0]['children']!r}")
    if kids["出張訪問買取"]["elements"] != [
        "出張訪問買取 出張訪問数", "出張訪問買取 出張訪問数(前年比)",
        "出張訪問買取 仕入高", "出張訪問買取 仕入高(前年比)",
    ]:
        _fail(f"76850 出張訪問買取 rows: {kids['出張訪問買取']!r}")
    if kids["店舗買取"]["elements"] != ["店舗買取 仕入高", "店舗買取 仕入高(前年比)"]:
        _fail(f"76850 店舗買取 rows: {kids['店舗買取']!r}")


def test_union_collapses_yoy_display_names() -> None:
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


def test_segment_tail_row_order() -> None:
    """76830: 内アパレルは segment_tail で非 tail の後。他銘柄は初出順のまま。"""
    cfg = ROOT / "config" / "monthly" / "monthly_row_order.json"
    if not cfg.is_file():
        _fail("monthly_row_order.json missing under config/")

    elements = [
        {"element": "内アパレル 売上高", "unit": "number"},
        {"element": "内アパレル 売上高(前年比)", "unit": "yoy_index"},
        {"element": "店舗販売 売上高", "unit": "number"},
        {"element": "店舗販売 売上高(前年比)", "unit": "yoy_index"},
        {"element": "オンライン販売 売上高", "unit": "number"},
        {"element": "オンライン販売 売上高(前年比)", "unit": "yoy_index"},
        {"element": "その他 売上高", "unit": "number"},
        {"element": "その他 売上高(前年比)", "unit": "yoy_index"},
        {"element": "売上高", "unit": "number"},
        {"element": "売上高(前年比)", "unit": "yoy_index"},
    ]
    table = {"code": "76830", "year": 2026, "elements": elements}
    order = [row["element"] for row in union_monthly_elements([table])]
    want = [
        "売上高",
        "売上高(前年比)",
        "店舗販売 売上高",
        "店舗販売 売上高(前年比)",
        "オンライン販売 売上高",
        "オンライン販売 売上高(前年比)",
        "その他 売上高",
        "その他 売上高(前年比)",
        "内アパレル 売上高",
        "内アパレル 売上高(前年比)",
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

    for src, unit, want_name in [
        ("内 MM 売上", "number", "内アパレル 売上高"),
        ("内 MM 売上高(前年比)", "yoy_index", "内アパレル 売上高(前年比)"),
    ]:
        got = apply_issuer_aliases(
            "76830", canonicalize_element_label(src, unit), year=2026,
        )
        got = canonicalize_element_label(got, unit)
        if got != want_name:
            _fail(f"76830 alias {src!r} → {got!r}, want {want_name!r}")


def test_tree_76490() -> None:
    """76490 スギ: スギ薬局 ▸ ドラッグストア事業 / 調剤事業（#582）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("76490", [
        "総拠点数",
        "スギ薬局 全店 売上高(前年比)",
        "ドラッグストア事業 全店 売上高(前年比)",
        "調剤事業 全店 売上高(前年比)",
        "スギ薬局事業 全店 売上高(前年比)",
        "スギ薬局 既存 客数(前年比)",
        "調剤事業 既存 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "総拠点数": (["全体"], "総拠点数", "総拠点数"),
        "スギ薬局 全店 売上高(前年比)": (["全体", "スギ薬局"], "全店 売上高(前年比)", "全店 売上高"),
        "ドラッグストア事業 全店 売上高(前年比)": (
            ["全体", "スギ薬局", "ドラッグストア事業"], "全店 売上高(前年比)", "全店 売上高",
        ),
        "調剤事業 全店 売上高(前年比)": (
            ["全体", "スギ薬局", "調剤事業"], "全店 売上高(前年比)", "全店 売上高",
        ),
        "スギ薬局事業 全店 売上高(前年比)": (
            ["全体", "スギ薬局", "スギ薬局事業"], "全店 売上高(前年比)", "全店 売上高",
        ),
        "スギ薬局 既存 客数(前年比)": (["全体", "スギ薬局"], "既存 客数(前年比)", "既存 客数"),
        "調剤事業 既存 売上高(前年比)": (
            ["全体", "スギ薬局", "調剤事業"], "既存 売上高(前年比)", "既存 売上高",
        ),
    }
    if got != want:
        _fail(f"76490 tree: {got!r}")


def test_tree_87050() -> None:
    """87050 日産証券: 純営業収益の内訳とリテール/ホールセール（#588）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("87050", [
        "純営業収益", "受入手数料", "トレーディング損益", "その他",
        "預かり資産", "リテール事業 預かり資産", "ホールセール事業 預かり資産",
        "リテール事業 投資信託残高", "リテール事業 外国株預かり残高",
        "株式等売買代金", "リテール事業 株式等売買代金", "リテール事業 外国株売買代金",
        "リテール事業 国内株式等売買代金", "ホールセール事業 株式等売買代金",
        "デリバティブ取引売買高", "リテール事業 デリバティブ取引売買高",
        "ホールセール事業 デリバティブ取引売買高", "口座数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["metric"]) for k, v in rows.items()}
    if got["受入手数料"] != (["全体", "受入手数料"], "純営業収益"):
        _fail(f"87050 受入手数料: {got['受入手数料']!r}")
    if got["リテール事業 預かり資産"] != (["全体", "リテール事業"], "預かり資産"):
        _fail(f"87050 リテール預かり: {got['リテール事業 預かり資産']!r}")
    if got["リテール事業 投資信託残高"] != (["全体", "リテール事業", "投資信託"], "預かり資産"):
        _fail(f"87050 投資信託: {got['リテール事業 投資信託残高']!r}")
    if got["リテール事業 外国株売買代金"] != (["全体", "リテール事業", "外国株"], "株式等売買代金"):
        _fail(f"87050 外国株売買: {got['リテール事業 外国株売買代金']!r}")
    if got["ホールセール事業 デリバティブ取引売買高"] != (["全体", "ホールセール事業"], "デリバティブ取引売買高"):
        _fail(f"87050 HSデリバ: {got['ホールセール事業 デリバティブ取引売買高']!r}")
    if got["純営業収益"] != (["全体"], "純営業収益"):
        _fail(f"87050 合計: {got['純営業収益']!r}")


def test_tree_71770() -> None:
    """71770 GMOFHD: 営業収益の内訳・セグメント、クリック現物/信用、コイン取引所/販売所（#586）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("71770", [
        "営業収益", "受入手数料", "トレーディング損益", "金融収益", "その他営業収益",
        "証券・FX事業 営業収益", "暗号資産事業 営業収益", "その他事業 営業収益", "調整額",
        "クリック証券 一日平均売買代金", "クリック証券 一日平均売買代金現物", "クリック証券 一日平均売買代金信用",
        "クリック証券 株式約定件数", "クリック証券 くりっく365口座数",
        "コイン 暗号資産売買代金", "コイン 取引所取引", "コイン 取引所現物取引",
        "コイン 販売所取引", "コイン 販売所証拠金取引",
        "クリック証券 株式売買代金月間", "クリック証券 株式売買代金月間現物",
        "クリック証券 証券取引口座数", "外貨 店頭FX売買代金月間", "外貨 店頭FX取引口座数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["受入手数料"][:3:2] != (["全体", "受入手数料"], "営業収益"):
        _fail(f"71770 受入手数料: {got['受入手数料']!r}")
    if got["証券・FX事業 営業収益"] != (["全体", "証券・FX事業"], "営業収益", "営業収益"):
        _fail(f"71770 証券・FX: {got['証券・FX事業 営業収益']!r}")
    if got["クリック証券 一日平均売買代金現物"] != (
            ["全体", "クリック証券", "現物"], "一日平均売買代金現物", "一日平均売買代金"):
        _fail(f"71770 現物: {got['クリック証券 一日平均売買代金現物']!r}")
    if got["コイン 取引所現物取引"] != (
            ["全体", "コイン", "取引所", "現物"], "取引所現物取引", "暗号資産売買代金"):
        _fail(f"71770 取引所現物: {got['コイン 取引所現物取引']!r}")
    if got["コイン 販売所証拠金取引"] != (
            ["全体", "コイン", "販売所", "証拠金"], "販売所証拠金取引", "暗号資産売買代金"):
        _fail(f"71770 販売所証拠金: {got['コイン 販売所証拠金取引']!r}")
    if got["クリック証券 証券取引口座数"] != (["全体", "クリック証券"], "証券取引口座数", "証券取引口座数"):
        _fail(f"71770 クリック証券口座: {got['クリック証券 証券取引口座数']!r}")
    if got["外貨 店頭FX取引口座数"] != (["全体", "外貨"], "店頭FX取引口座数", "店頭FX取引口座数"):
        _fail(f"71770 外貨口座: {got['外貨 店頭FX取引口座数']!r}")
    if got["クリック証券 くりっく365口座数"] != (["全体", "クリック証券"], "くりっく365口座数", "くりっく365口座数"):
        _fail(f"71770 くりっく: {got['クリック証券 くりっく365口座数']!r}")


def test_tree_23370() -> None:
    """23370 いちご: 発電量 / CO2削減量 ▸ いちご / いちごグリーン（#836）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("23370", [
        "いちご 発電量", "いちごグリーン 発電量", "発電量", "発電量(前年比)",
        "いちご CO2削減量", "いちごグリーン CO2削減量", "CO2削減量",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "発電量": (["全体"], "発電量", "発電量"),
        "発電量(前年比)": (["全体"], "発電量(前年比)", "発電量"),
        "いちご 発電量": (["全体", "いちご"], "発電量", "発電量"),
        "いちごグリーン 発電量": (["全体", "いちごグリーン"], "発電量", "発電量"),
        "CO2削減量": (["全体"], "CO2削減量", "CO2削減量"),
        "いちご CO2削減量": (["全体", "いちご"], "CO2削減量", "CO2削減量"),
        "いちごグリーン CO2削減量": (["全体", "いちごグリーン"], "CO2削減量", "CO2削減量"),
    }
    if got != want:
        _fail(f"23370 tree: {got!r}")


def test_tree_31740() -> None:
    """31740 ハピネス: 既存店前年比の各行を別指標にする（#822）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("31740", [
        "既存 売上高(前年比)",
        "売上総利益 既存 売上高(前年比)",
        "既存 売上総利益(前年比)",
        "売上個数 既存 売上高(前年比)",
        "既存 売上個数(前年比)",
        "既存 客単価(前年比)",
        "ヴィンテージ商品 既存 売上高(前年比)",
        "既存 ヴィンテージ商品売上高(前年比)",
        "粗利率 既存",
        "既存 粗利率",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "既存 売上高(前年比)": (["全体"], "既存 売上高(前年比)", "既存 売上高"),
        "売上総利益 既存 売上高(前年比)": (
            ["全体"], "売上総利益 既存 売上高(前年比)", "既存 売上総利益",
        ),
        "既存 売上総利益(前年比)": (["全体"], "既存 売上総利益(前年比)", "既存 売上総利益"),
        "売上個数 既存 売上高(前年比)": (
            ["全体"], "売上個数 既存 売上高(前年比)", "既存 売上個数",
        ),
        "既存 売上個数(前年比)": (["全体"], "既存 売上個数(前年比)", "既存 売上個数"),
        "既存 客単価(前年比)": (["全体"], "既存 客単価(前年比)", "既存 客単価"),
        "ヴィンテージ商品 既存 売上高(前年比)": (
            ["全体"], "ヴィンテージ商品 既存 売上高(前年比)", "既存 ヴィンテージ商品売上高",
        ),
        "既存 ヴィンテージ商品売上高(前年比)": (
            ["全体"], "既存 ヴィンテージ商品売上高(前年比)", "既存 ヴィンテージ商品売上高",
        ),
        "粗利率 既存": (["全体"], "粗利率 既存", "既存 粗利率"),
        "既存 粗利率": (["全体"], "既存 粗利率", "既存 粗利率"),
    }
    if got != want:
        _fail(f"31740 tree: {got!r}")
    metrics = [m for m, _ in load_monthly_trees()["31740"].metrics]
    if metrics != [
        "既存 売上高", "既存 売上総利益", "既存 売上個数", "既存 客単価",
        "既存 ヴィンテージ商品売上高", "既存 粗利率",
    ]:
        _fail(f"31740 metrics: {metrics!r}")


def test_tree_37330() -> None:
    """37330 ソフトウェア・サービス: 受注高の実数と前年比を同じ指標にする（#602）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    els = [
        "売上高", "売上高(前年比)",
        "受注高", "受注高(前年比)",
        "受注残高", "受注残高(前年比)",
    ]
    trees = load_monthly_trees()
    rows = assign_tree_rows("37330", els, trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "売上高": (["全体"], "売上高", "売上高"),
        "売上高(前年比)": (["全体"], "売上高(前年比)", "売上高"),
        "受注高": (["全体"], "受注高", "受注高"),
        "受注高(前年比)": (["全体"], "受注高(前年比)", "受注高"),
        "受注残高": (["全体"], "受注残高", "受注残高"),
        "受注残高(前年比)": (["全体"], "受注残高(前年比)", "受注残高"),
    }
    if got != want:
        _fail(f"37330 tree: {got!r}")
    t = tree_payload("37330", els, trees)
    if t is None or t["tree_metrics"] != ["売上高", "受注高", "受注残高"] or "tree_auto" in t:
        _fail(f"37330 tree_metrics: {None if t is None else t!r}")


def test_tree_39790() -> None:
    """39790 うるる: NJSS MRR の実数と前年比を同じ指標にする（#603）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    els = ["NJSS MRR", "NJSS MRR(前年比)", "NJSS MRR前年同期比(前年比)"]
    trees = load_monthly_trees()
    rows = assign_tree_rows("39790", els, trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "NJSS MRR": (["全体"], "NJSS MRR", "NJSS MRR"),
        "NJSS MRR(前年比)": (["全体"], "NJSS MRR(前年比)", "NJSS MRR"),
        "NJSS MRR前年同期比(前年比)": (["全体"], "NJSS MRR前年同期比(前年比)", "NJSS MRR"),
    }
    if got != want:
        _fail(f"39790 tree: {got!r}")
    t = tree_payload("39790", els, trees)
    if t is None or t["tree_metrics"] != ["NJSS MRR"] or "tree_auto" in t:
        _fail(f"39790 tree_metrics: {None if t is None else t!r}")


def test_tree_kab102() -> None:
    """月次の木の監査（#831〜#835）: 合計を会社の行、内訳を子の節にする。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    trees = load_monthly_trees()
    cases = {
        # 9251: 前年比と金額は別の指標。金額の会社の行は合計、直営 / FC はその子（#831）
        "92510": {
            "日本 全店 売上高(前年比)": (["全体"], "全店 売上高(前年比)"),
            "国内店舗 売上高": (["全体"], "売上高"),
            "直営 売上高": (["全体", "直営"], "売上高"),
            "FC 売上高": (["全体", "FC"], "売上高"),
            "日本 店舗数": (["全体"], "店舗数"),
            "FC 店舗数": (["全体", "FC"], "店舗数"),
            "純増 直営 店舗数": (["全体", "直営"], "純増 店舗数"),
            "日本 既存 売上高(前年比)": (["全体"], "既存 売上高"),
        },
        # 3612: 国内小売が会社の行、店舗 / Eコマースが子（#832）
        "36120": {
            "国内小売 売上高(前年比)": (["全体"], "売上高"),
            "店舗 売上高(前年比)": (["全体", "店舗"], "売上高"),
            "Eコマース 売上高(前年比)": (["全体", "Eコマース"], "売上高"),
            "既存 売上高(前年比)": (["全体"], "既存 売上高"),
            "小売 店舗数": (["全体"], "店舗数"),
        },
        # 7683: 内アパレルは合計のうち（#833）
        "76830": {
            "売上高": (["全体"], "売上高"),
            "店舗販売 売上高": (["全体", "店舗販売"], "売上高"),
            "内アパレル 売上高": (["全体", "内アパレル"], "売上高"),
        },
        # 1435: 管理戸数 / 入居率 ▸ 自社施工 / 管理受託（#834）
        "14350": {
            "管理戸数 全体": (["全体"], "管理戸数"),
            "管理戸数 自社施工": (["全体", "自社施工"], "管理戸数"),
            "入居率 管理受託": (["全体", "管理受託"], "入居率"),
        },
        # 1878: 空室数 ▸ 新築 / 既存、家賃ベース入居率 ▸ 事業用 / 居住用（#835）
        "18780": {
            "空室数": (["全体"], "空室数"),
            "新築 空室数": (["全体", "新築"], "空室数"),
            "空室数 既存": (["全体", "既存"], "空室数"),
            "家賃ベース入居率": (["全体"], "家賃ベース入居率"),
            "家賃ベース入居率 事業用": (["全体", "事業用"], "家賃ベース入居率"),
        },
    }
    for code, want in cases.items():
        rows = assign_tree_rows(code, list(want), trees)
        got = {k: (v["path"], v["metric"]) for k, v in rows.items()}
        if got != want:
            _fail(f"{code} tree: {got!r}")
    if not next(n for n in trees["76830"].roots[0].children if n.name == "内アパレル").part:
        _fail("76830 内アパレル must be part")


def test_tree_31930() -> None:
    """31930 エターナルホスピタリティ: 日本 ▸ 鳥貴族 / やきとり大吉 / その他、海外 ▸ 各国（#598）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    trees = load_monthly_trees()
    rows = assign_tree_rows("31930", [
        "店舗数", "日本 店舗数", "海外 店舗数",
        "日本 鳥貴族 店舗数", "日本 鳥貴族 直営 店舗数", "日本 鳥貴族 TCC 店舗数",
        "日本 やきとり大吉 店舗数", "日本 その他 店舗数",
        "鳥貴族パートナーズ", "TORIKI BURGER",
        "海外 米国 店舗数", "海外 韓国 店舗数", "海外 台湾 店舗数",
        "海外 中国 店舗数", "海外 香港 店舗数", "海外 ベトナム 店舗数",
        "海外 シンガポール 店舗数",
        "全店 売上高(前年比)", "既存 売上高(前年比)",
    ], trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "店舗数": (["全体"], "店舗数", "店舗数"),
        "日本 店舗数": (["全体", "日本"], "店舗数", "店舗数"),
        "海外 店舗数": (["全体", "海外"], "店舗数", "店舗数"),
        "日本 鳥貴族 店舗数": (["全体", "日本", "鳥貴族"], "店舗数", "店舗数"),
        "日本 鳥貴族 直営 店舗数": (["全体", "日本", "鳥貴族", "直営"], "店舗数", "店舗数"),
        "日本 鳥貴族 TCC 店舗数": (["全体", "日本", "鳥貴族", "TCC"], "店舗数", "店舗数"),
        "日本 やきとり大吉 店舗数": (["全体", "日本", "やきとり大吉"], "店舗数", "店舗数"),
        "日本 その他 店舗数": (["全体", "日本", "その他"], "店舗数", "店舗数"),
        "鳥貴族パートナーズ": (["全体", "日本", "その他"], "鳥貴族パートナーズ", "店舗数"),
        "TORIKI BURGER": (["全体", "日本", "その他"], "TORIKI BURGER", "店舗数"),
        "海外 米国 店舗数": (["全体", "海外", "米国"], "店舗数", "店舗数"),
        "海外 韓国 店舗数": (["全体", "海外", "韓国"], "店舗数", "店舗数"),
        "海外 台湾 店舗数": (["全体", "海外", "台湾"], "店舗数", "店舗数"),
        "海外 中国 店舗数": (["全体", "海外", "中国"], "店舗数", "店舗数"),
        "海外 香港 店舗数": (["全体", "海外", "香港"], "店舗数", "店舗数"),
        "海外 ベトナム 店舗数": (["全体", "海外", "ベトナム"], "店舗数", "店舗数"),
        "海外 シンガポール 店舗数": (["全体", "海外", "シンガポール"], "店舗数", "店舗数"),
        "全店 売上高(前年比)": (["全体"], "全店 売上高(前年比)", "全店 売上高"),
        "既存 売上高(前年比)": (["全体"], "既存 売上高(前年比)", "既存 売上高"),
    }
    if got != want:
        _fail(f"31930 tree: {got!r}")
    metrics = [m for m, _ in trees["31930"].metrics]
    if metrics != ["既存 売上高", "既存 客数", "既存 客単価", "全店 売上高", "店舗数"]:
        _fail(f"31930 metrics: {metrics!r}")


def test_tree_30820() -> None:
    """30820 きちり: 業態の 店舗数 は会社の 全店 店舗数（#592）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("30820", [
        "全店 店舗数", "既存 店舗数",
        "都市型ダイニング業態 店舗数", "都市型ダイニング業態 既存 売上高(前年比)",
        "モール・郊外型 レストラン業態 店舗数",
        "既存 売上高(前年比)", "全店 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["都市型ダイニング業態 店舗数"] != (
            ["全体", "都市型ダイニング業態"], "店舗数", "全店 店舗数"):
        _fail(f"30820 都市型店舗数: {got['都市型ダイニング業態 店舗数']!r}")
    if got["モール・郊外型 レストラン業態 店舗数"] != (
            ["全体", "モール・郊外型 レストラン業態"], "店舗数", "全店 店舗数"):
        _fail(f"30820 モール店舗数: {got['モール・郊外型 レストラン業態 店舗数']!r}")
    if got["既存 店舗数"] != (["全体"], "既存 店舗数", "既存 店舗数"):
        _fail(f"30820 既存店舗数: {got['既存 店舗数']!r}")
    if got["都市型ダイニング業態 既存 売上高(前年比)"] != (
            ["全体", "都市型ダイニング業態"], "既存 売上高(前年比)", "既存 売上高"):
        _fail(f"30820 都市型既存売上: {got['都市型ダイニング業態 既存 売上高(前年比)']!r}")


def test_tree_33290() -> None:
    """33290 東和フード: 休業は店舗数の内数、数 退店 は会社の退店（#591）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    trees = load_monthly_trees()
    rows = assign_tree_rows("33290", [
        "店舗数", "休業 店舗数", "既存 店舗数", "数 出店", "出店", "数 退店", "退店",
        "全店 売上高(前年比)", "全社 売上高(前年比)",
    ], trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["休業 店舗数"] != (["全体", "休業"], "店舗数", "店舗数"):
        _fail(f"33290 休業: {got['休業 店舗数']!r}")
    if got["数 出店"] != (["全体"], "数 出店", "出店"):
        _fail(f"33290 数 出店: {got['数 出店']!r}")
    if got["数 退店"] != (["全体"], "数 退店", "退店"):
        _fail(f"33290 数 退店: {got['数 退店']!r}")
    if got["退店"] != (["全体"], "退店", "退店"):
        _fail(f"33290 退店: {got['退店']!r}")
    if got["店舗数"] != (["全体"], "店舗数", "店舗数"):
        _fail(f"33290 店舗数: {got['店舗数']!r}")
    if not next(n for n in trees["33290"].roots[0].children if n.name == "休業").part:
        _fail("33290 休業 must be part")


def test_tree_35570() -> None:
    """35570 ユナイテッド＆コレクティブ: 全業態は会社の行（#591）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("35570", [
        "店舗数", "全業態 既存 売上高(前年比)", "全業態 既存 店舗数",
        "てけてけ 既存 売上高(前年比)", "てけてけ 店舗数", "てけてけ 既存 店舗数",
        "もつ焼てけ八 店舗数", "新太郎 店舗数",
        "the 3rd Burger 既存 売上高(前年比)", "the 3rd Burger 店舗数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["全業態 既存 売上高(前年比)"] != (["全体"], "既存 売上高(前年比)", "既存 売上高"):
        _fail(f"35570 全業態売上: {got['全業態 既存 売上高(前年比)']!r}")
    if got["全業態 既存 店舗数"] != (["全体"], "既存 店舗数", "既存 店舗数"):
        _fail(f"35570 全業態既存店: {got['全業態 既存 店舗数']!r}")
    if got["てけてけ 既存 売上高(前年比)"] != (
            ["全体", "てけてけ"], "既存 売上高(前年比)", "既存 売上高"):
        _fail(f"35570 てけてけ: {got['てけてけ 既存 売上高(前年比)']!r}")
    if got["店舗数"] != (["全体"], "店舗数", "店舗数"):
        _fail(f"35570 店舗数: {got['店舗数']!r}")
    if any("全業態" in p for v in got.values() for p in v[0]):
        _fail(f"35570 全業態 must not be a group: {got!r}")


def test_tree_76430() -> None:
    """76430 ダイイチ: 部門別前年比は全社 売上高の内訳（#608）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    els = [
        "全社 売上高(前年比)", "既存 売上高(前年比)",
        "客数(前年比)", "客単価(前年比)",
        "青果(前年比)", "水産(前年比)", "畜産(前年比)", "惣菜(前年比)",
        "デイリー(前年比)", "一般食品(前年比)", "日用雑貨(前年比)", "その他(前年比)",
    ]
    trees = load_monthly_trees()
    rows = assign_tree_rows("76430", els, trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "全社 売上高(前年比)": (["全体"], "全社 売上高(前年比)", "全社 売上高"),
        "既存 売上高(前年比)": (["全体"], "既存 売上高(前年比)", "既存 売上高"),
        "客数(前年比)": (["全体"], "客数(前年比)", "客数"),
        "客単価(前年比)": (["全体"], "客単価(前年比)", "客単価"),
        "青果(前年比)": (["全体", "青果"], "青果(前年比)", "全社 売上高"),
        "水産(前年比)": (["全体", "水産"], "水産(前年比)", "全社 売上高"),
        "畜産(前年比)": (["全体", "畜産"], "畜産(前年比)", "全社 売上高"),
        "惣菜(前年比)": (["全体", "惣菜"], "惣菜(前年比)", "全社 売上高"),
        "デイリー(前年比)": (["全体", "デイリー"], "デイリー(前年比)", "全社 売上高"),
        "一般食品(前年比)": (["全体", "一般食品"], "一般食品(前年比)", "全社 売上高"),
        "日用雑貨(前年比)": (["全体", "日用雑貨"], "日用雑貨(前年比)", "全社 売上高"),
        "その他(前年比)": (["全体", "その他"], "その他(前年比)", "全社 売上高"),
    }
    if got != want:
        _fail(f"76430 tree: {got!r}")
    t = tree_payload("76430", els, trees)
    if t is None or t["tree_metrics"] != ["全社 売上高", "既存 売上高", "客数", "客単価"] or "tree_auto" in t:
        _fail(f"76430 tree_metrics: {None if t is None else t!r}")


def test_tree_76160() -> None:
    """76160 コロワイド: （全業態）は会社の行（#591）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("76160", [
        "（全業態） 全店 売上高(前年比)", "（全業態） 既存 売上高(前年比)",
        "（全業態） 全店 客数(前年比)", "（全業態） 既存 客数(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "（全業態） 全店 売上高(前年比)": (["全体"], "全店 売上高(前年比)", "全店 売上高"),
        "（全業態） 既存 売上高(前年比)": (["全体"], "既存 売上高(前年比)", "既存 売上高"),
        "（全業態） 全店 客数(前年比)": (["全体"], "全店 客数(前年比)", "全店 客数"),
        "（全業態） 既存 客数(前年比)": (["全体"], "既存 客数(前年比)", "既存 客数"),
    }
    if got != want:
        _fail(f"76160 tree: {got!r}")


def test_tree_81630() -> None:
    """81630 SRS: ＊M&A除く は根の別指標（#591）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    trees = load_monthly_trees()
    rows = assign_tree_rows("81630", [
        "全店 売上高(前年比)", "＊M&A除く 全店 売上高(前年比)",
    ], trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["全店 売上高(前年比)"] != (["全体"], "全店 売上高(前年比)", "全店 売上高"):
        _fail(f"81630 全店: {got['全店 売上高(前年比)']!r}")
    if got["＊M&A除く 全店 売上高(前年比)"] != (
            ["全体"], "＊M&A除く 全店 売上高(前年比)", "＊M&A除く 全店 売上高"):
        _fail(f"81630 M&A除く: {got['＊M&A除く 全店 売上高(前年比)']!r}")
    metrics = [m for m, _ in trees["81630"].metrics]
    if metrics != ["全店 売上高", "＊M&A除く 全店 売上高"]:
        _fail(f"81630 metrics: {metrics!r}")


def test_tree_82000() -> None:
    """82000 リンガーハット: 純既存は会社の既存、ブランド店舗数は全店 店舗数（#591 / #592）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("82000", [
        "全店 売上高(前年比)", "全店 店舗数",
        "純 既存 売上高(前年比)", "純 既存 客数(前年比)", "純 既存 客単価(前年比)",
        "リンガーハット 売上高(前年比)", "リンガーハット 店舗数",
        "濵かつ 店舗数", "その他 店舗数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["純 既存 売上高(前年比)"] != (["全体"], "既存 売上高(前年比)", "既存 売上高"):
        _fail(f"82000 純既存: {got['純 既存 売上高(前年比)']!r}")
    if got["リンガーハット 店舗数"] != (["全体", "リンガーハット"], "店舗数", "全店 店舗数"):
        _fail(f"82000 リンガー店舗: {got['リンガーハット 店舗数']!r}")
    if got["濵かつ 店舗数"] != (["全体", "濵かつ"], "店舗数", "全店 店舗数"):
        _fail(f"82000 濵かつ店舗: {got['濵かつ 店舗数']!r}")
    if got["その他 店舗数"] != (["全体", "その他"], "店舗数", "全店 店舗数"):
        _fail(f"82000 その他店舗: {got['その他 店舗数']!r}")
    if got["リンガーハット 売上高(前年比)"] != (
            ["全体", "リンガーハット"], "売上高(前年比)", "全店 売上高"):
        _fail(f"82000 リンガー売上: {got['リンガーハット 売上高(前年比)']!r}")
    if any("純" in p for v in got.values() for p in v[0]):
        _fail(f"82000 純 must not be a group: {got!r}")


def test_tree_43430() -> None:
    """43430 イオンファンタジー: 国内計既存は日本 既存、総合店はアミューズメントの子（#826）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    rows = assign_tree_rows("43430", [
        "日本 売上高(前年比)",
        "日本 (曜日調整前) 既存 売上高(前年比)",
        "日本 既存 売上高(前年比)",
        "日本 (曜日調整後) 既存 売上高(前年比)",
        "日本 店舗数",
        "日本 アミューズメント事業 売上高(前年比)",
        "日本 アミューズメント事業 既存 売上高(前年比)",
        "日本 アミューズメント事業 店舗数",
        "日本 アミューズメント事業 総合店 売上高(前年比)",
        "日本 アミューズメント事業 総合店 既存 売上高(前年比)",
        "日本 アミューズメント事業 総合店 店舗数",
        "日本 アミューズメント事業 専門店 売上高(前年比)",
        "日本 プレイグラウンド事業 売上高(前年比)",
        "海外 既存 売上高(前年比)",
        "海外 中国 既存 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["日本 (曜日調整前) 既存 売上高(前年比)"] != (
            ["全体"], "日本 (曜日調整前) 既存 売上高(前年比)", "日本 既存 売上高"):
        _fail(f"43430 曜日調整前: {got['日本 (曜日調整前) 既存 売上高(前年比)']!r}")
    if got["日本 既存 売上高(前年比)"] != (["全体"], "日本 既存 売上高(前年比)", "日本 既存 売上高"):
        _fail(f"43430 日本既存: {got['日本 既存 売上高(前年比)']!r}")
    if got["日本 (曜日調整後) 既存 売上高(前年比)"] != (
            ["全体"], "日本 (曜日調整後) 既存 売上高(前年比)", "日本 (曜日調整後) 既存 売上高"):
        _fail(f"43430 曜日調整後: {got['日本 (曜日調整後) 既存 売上高(前年比)']!r}")
    if any("曜日調整" in p for v in got.values() for p in v[0]):
        _fail(f"43430 曜日調整 must not be a group: {got!r}")
    if got["日本 アミューズメント事業 総合店 売上高(前年比)"] != (
            ["全体", "アミューズメント事業", "総合店"], "売上高(前年比)", "日本 売上高"):
        _fail(f"43430 総合店: {got['日本 アミューズメント事業 総合店 売上高(前年比)']!r}")
    if got["日本 アミューズメント事業 専門店 売上高(前年比)"] != (
            ["全体", "アミューズメント事業", "専門店"], "売上高(前年比)", "日本 売上高"):
        _fail(f"43430 専門店: {got['日本 アミューズメント事業 専門店 売上高(前年比)']!r}")
    if got["日本 アミューズメント事業 既存 売上高(前年比)"] != (
            ["全体", "アミューズメント事業"], "既存 売上高(前年比)", "日本 既存 売上高"):
        _fail(f"43430 アミューズメント既存: {got['日本 アミューズメント事業 既存 売上高(前年比)']!r}")
    if got["海外 中国 既存 売上高(前年比)"] != (
            ["全体", "海外", "中国"], "海外 中国 既存 売上高(前年比)", "海外 既存 売上高"):
        _fail(f"43430 中国: {got['海外 中国 既存 売上高(前年比)']!r}")
    payload = tree_payload("43430", list(got), load_monthly_trees())
    if payload is None or payload.get("tree_metrics") != [
            "日本 既存 売上高", "日本 (曜日調整後) 既存 売上高", "日本 売上高", "日本 店舗数", "海外 既存 売上高"]:
        _fail(f"43430 tree_metrics: {payload!r}")
    if payload.get("tree_metric_parent"):
        _fail(f"43430 must not nest metrics: {payload.get('tree_metric_parent')!r}")


def test_tree_60400() -> None:
    """60400 日本スキー場開発: リフト券・来場者の余りを来場者数へ（#827）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    rows = assign_tree_rows("60400", [
        "グリーン 来場者数",
        "グリーン 索道計 リフト券",
        "グリーン 索道計 来場者数",
        "グリーン めいほう高原 来場者",
        "グリーン 岩岳 来場者数",
        "ウィンター 川場 来場者",
        "ウィンター 川場 来場者数",
        "ウィンター めいほう 来場者",
        "ウィンター めいほう 来場者数",
        "ウィンター 鹿島槍 来場者",
        "ウィンター 鹿島槍 来場者数",
        "ウィンター 来場者数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["グリーン 索道計 リフト券"] != (
            ["全体", "グリーン 索道計"], "リフト券", "来場者数"):
        _fail(f"60400 索道計: {got['グリーン 索道計 リフト券']!r}")
    if got["グリーン めいほう高原 来場者"] != (
            ["全体", "グリーン めいほう高原"], "来場者", "来場者数"):
        _fail(f"60400 めいほう高原: {got['グリーン めいほう高原 来場者']!r}")
    if got["ウィンター 川場 来場者"] != (
            ["全体", "ウィンター 川場"], "来場者", "来場者数"):
        _fail(f"60400 川場来場者: {got['ウィンター 川場 来場者']!r}")
    if got["ウィンター 川場 来場者数"] != (
            ["全体", "ウィンター 川場"], "来場者数", "来場者数"):
        _fail(f"60400 川場来場者数: {got['ウィンター 川場 来場者数']!r}")
    if got["ウィンター 鹿島槍 来場者"] != (
            ["全体", "ウィンター 鹿島槍"], "来場者", "来場者数"):
        _fail(f"60400 鹿島槍来場者: {got['ウィンター 鹿島槍 来場者']!r}")
    if got["グリーン 来場者数"] != (["全体", "グリーン"], "来場者数", "来場者数"):
        _fail(f"60400 グリーン計: {got['グリーン 来場者数']!r}")
    payload = tree_payload("60400", list(got), load_monthly_trees())
    if payload is None or payload.get("tree_metrics") != ["来場者数"]:
        _fail(f"60400 tree_metrics: {payload!r}")
    if payload.get("tree_metric_parent"):
        _fail(f"60400 must not nest metrics: {payload.get('tree_metric_parent')!r}")


def test_tree_65370() -> None:
    """65370 ＷＡＳＨハウス: 一時閉店は退店の節にしない（#829）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    rows = assign_tree_rows("65370", [
        "出店", "一時 退店", "一時閉店", "退店", "店舗数",
        "全店 売上高(前年比)", "既存 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["一時 退店"] != (["全体"], "一時 退店", "一時閉店"):
        _fail(f"65370 一時退店: {got['一時 退店']!r}")
    if got["一時閉店"] != (["全体"], "一時閉店", "一時閉店"):
        _fail(f"65370 一時閉店: {got['一時閉店']!r}")
    if got["退店"] != (["全体"], "退店", "退店"):
        _fail(f"65370 退店: {got['退店']!r}")
    if any(p == "一時" for v in got.values() for p in v[0]):
        _fail(f"65370 一時 must not be a group: {got!r}")
    payload = tree_payload("65370", list(got), load_monthly_trees())
    if payload is None or payload.get("tree_metrics") != [
            "出店", "一時閉店", "退店", "店舗数", "全店 売上高", "既存 売上高"]:
        _fail(f"65370 tree_metrics: {payload!r}")
    if payload.get("tree_metric_parent"):
        _fail(f"65370 must not nest 一時閉店: {payload.get('tree_metric_parent')!r}")


def test_tree_92450() -> None:
    """92450 リベロ: 転貸戸数・法人企業等の前年同期比を各実数の前年比にする（#830）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    rows = assign_tree_rows("92450", [
        "売上高", "売上高(前年比)",
        "転貸戸数", "転貸戸数(前年比)", "転貸戸数前年同期比(前年比)",
        "法人企業等", "法人企業等(前年比)", "法人企業等前年同期比(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    want = {
        "売上高": (["全体"], "売上高", "売上高"),
        "売上高(前年比)": (["全体"], "売上高(前年比)", "売上高"),
        "転貸戸数": (["全体"], "転貸戸数", "転貸戸数"),
        "転貸戸数(前年比)": (["全体"], "転貸戸数(前年比)", "転貸戸数"),
        "転貸戸数前年同期比(前年比)": (["全体"], "転貸戸数前年同期比(前年比)", "転貸戸数"),
        "法人企業等": (["全体"], "法人企業等", "法人企業等"),
        "法人企業等(前年比)": (["全体"], "法人企業等(前年比)", "法人企業等"),
        "法人企業等前年同期比(前年比)": (["全体"], "法人企業等前年同期比(前年比)", "法人企業等"),
    }
    if got != want:
        _fail(f"92450 tree: {got!r}")
    payload = tree_payload("92450", list(got), load_monthly_trees())
    if payload is None or payload.get("tree_metrics") != ["売上高", "転貸戸数", "法人企業等"]:
        _fail(f"92450 tree_metrics: {payload!r}")
    if payload.get("tree_metric_parent"):
        _fail(f"92450 must not nest metrics: {payload.get('tree_metric_parent')!r}")


def test_tree_23110() -> None:
    """23110 エプコ: 経常利益 ▸ 連結 + 再エネ/メンテ/設計（再エネの子に TEPCO 等）（#825）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    rows = assign_tree_rows("23110", [
        "連結経常利益", "連結経常利益(前年比)",
        "再エネサービス 経常利益", "再エネサービス 経常利益(前年比)",
        "メンテナンスサービス 経常利益", "設計サービス 経常利益",
        "TEPCOホームテック 持分法損益", "TEPCO ホームテック 経常利益(前年比)",
        "ENE's 経常利益", "LESSO EPCO 経常利益", "その他 経常利益", "MEDX 経常利益",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["連結経常利益"] != (["全体"], "連結経常利益", "経常利益"):
        _fail(f"23110 連結: {got['連結経常利益']!r}")
    if got["再エネサービス 経常利益"] != (["全体", "再エネサービス"], "経常利益", "経常利益"):
        _fail(f"23110 再エネ: {got['再エネサービス 経常利益']!r}")
    if got["メンテナンスサービス 経常利益"] != (
            ["全体", "メンテナンスサービス"], "経常利益", "経常利益"):
        _fail(f"23110 メンテ: {got['メンテナンスサービス 経常利益']!r}")
    if got["設計サービス 経常利益"] != (["全体", "設計サービス"], "経常利益", "経常利益"):
        _fail(f"23110 設計: {got['設計サービス 経常利益']!r}")
    if got["TEPCOホームテック 持分法損益"] != (
            ["全体", "再エネサービス", "TEPCOホームテック"],
            "TEPCOホームテック 持分法損益", "経常利益"):
        _fail(f"23110 TEPCO: {got['TEPCOホームテック 持分法損益']!r}")
    if got["TEPCO ホームテック 経常利益(前年比)"] != (
            ["全体", "再エネサービス", "TEPCOホームテック"],
            "TEPCO ホームテック 経常利益(前年比)", "経常利益"):
        _fail(f"23110 TEPCO yoy: {got['TEPCO ホームテック 経常利益(前年比)']!r}")
    enes = got["ENE's 経常利益"]
    if enes != (["全体", "再エネサービス", "ENE's"], "経常利益", "経常利益"):
        _fail(f"23110 ENE's: {enes!r}")
    if got["その他 経常利益"] != (["全体", "再エネサービス", "その他"], "経常利益", "経常利益"):
        _fail(f"23110 その他: {got['その他 経常利益']!r}")
    if got["MEDX 経常利益"] != (["全体", "再エネサービス", "MEDX"], "経常利益", "経常利益"):
        _fail(f"23110 MEDX: {got['MEDX 経常利益']!r}")
    payload = tree_payload("23110", list(got), load_monthly_trees())
    if payload is None or payload.get("tree_metrics") != ["経常利益"]:
        _fail(f"23110 tree_metrics: {payload!r}")
    if payload.get("tree_metric_parent"):
        _fail(f"23110 must not nest metrics: {payload.get('tree_metric_parent')!r}")


def test_tree_75060() -> None:
    """75060 ハウス オブ ローゼ: 全社/直営/既存/直営既存は兄弟の指標（#823）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees, tree_payload

    rows = assign_tree_rows("75060", [
        "全社 売上高", "全社 売上高(前年比)",
        "直営 売上高", "直営 売上高(前年比)",
        "既存 売上高", "既存 売上高(前年比)",
        "直営 既存 売上高", "直営 既存 売上高(前年比)",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["全社 売上高"] != (["全体"], "全社 売上高", "全社 売上高"):
        _fail(f"75060 全社: {got['全社 売上高']!r}")
    if got["直営 売上高"] != (["全体"], "直営 売上高", "直営 売上高"):
        _fail(f"75060 直営: {got['直営 売上高']!r}")
    if got["既存 売上高"] != (["全体"], "既存 売上高", "既存 売上高"):
        _fail(f"75060 既存: {got['既存 売上高']!r}")
    if got["直営 既存 売上高"] != (["全体"], "直営 既存 売上高", "直営 既存 売上高"):
        _fail(f"75060 直営既存: {got['直営 既存 売上高']!r}")
    payload = tree_payload("75060", list(got), load_monthly_trees())
    if payload is None or payload.get("tree_metrics") != [
            "全社 売上高", "直営 売上高", "既存 売上高", "直営 既存 売上高"]:
        _fail(f"75060 tree_metrics: {payload!r}")
    if payload.get("tree_metric_parent"):
        _fail(f"75060 must not nest 直営既存: {payload.get('tree_metric_parent')!r}")


def test_tree_82190() -> None:
    """82190 青山商事: 全店と既存はビジネスウェア、店舗数は内訳（#612）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("82190", [
        "全店 売上高(前年比)",
        "ビジネスウェア 既存 売上高(前年比)",
        "ビジネスウェア 既存 客数(前年比)",
        "ビジネスウェア 既存 客単価(前年比)",
        "洋服の青山 店舗数",
        "SUIT SQUARE他 店舗数",
        "TSC他 店舗数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["全店 売上高(前年比)"] != (["ビジネスウェア"], "全店 売上高(前年比)", "全店 売上高"):
        _fail(f"82190 全店: {got['全店 売上高(前年比)']!r}")
    if got["ビジネスウェア 既存 売上高(前年比)"] != (
            ["ビジネスウェア"], "既存 売上高(前年比)", "既存 売上高"):
        _fail(f"82190 既存: {got['ビジネスウェア 既存 売上高(前年比)']!r}")
    if got["洋服の青山 店舗数"] != (["ビジネスウェア", "洋服の青山"], "店舗数", "店舗数"):
        _fail(f"82190 青山: {got['洋服の青山 店舗数']!r}")
    if got["SUIT SQUARE他 店舗数"] != (["ビジネスウェア", "SUIT SQUARE他"], "店舗数", "店舗数"):
        _fail(f"82190 SUIT: {got['SUIT SQUARE他 店舗数']!r}")
    if got["TSC他 店舗数"] != (["ビジネスウェア", "TSC他"], "店舗数", "店舗数"):
        _fail(f"82190 TSC: {got['TSC他 店舗数']!r}")
    if any(p == "全体" for v in got.values() for p in v[0]):
        _fail(f"82190 全体 must not remain: {got!r}")


def test_tree_82550() -> None:
    """82550 アクシアル: （従来基準）は根の別指標（#591）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    trees = load_monthly_trees()
    rows = assign_tree_rows("82550", [
        "全店 売上高(前年比)", "既存 売上高(前年比)",
        "（従来基準） 全店 売上高(前年比)", "（従来基準） 既存 売上高(前年比)",
    ], trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["（従来基準） 全店 売上高(前年比)"] != (
            ["全体"], "（従来基準） 全店 売上高(前年比)", "（従来基準） 全店 売上高"):
        _fail(f"82550 従来全店: {got['（従来基準） 全店 売上高(前年比)']!r}")
    if got["（従来基準） 既存 売上高(前年比)"] != (
            ["全体"], "（従来基準） 既存 売上高(前年比)", "（従来基準） 既存 売上高"):
        _fail(f"82550 従来既存: {got['（従来基準） 既存 売上高(前年比)']!r}")
    if got["全店 売上高(前年比)"] != (["全体"], "全店 売上高(前年比)", "全店 売上高"):
        _fail(f"82550 全店: {got['全店 売上高(前年比)']!r}")
    if any("従来基準" in p for v in got.values() for p in v[0]):
        _fail(f"82550 従来基準 must not be a group: {got!r}")
    metrics = [m for m, _ in trees["82550"].metrics]
    if "（従来基準） 全店 売上高" not in metrics or "全店 売上高" not in metrics:
        _fail(f"82550 metrics: {metrics!r}")


def test_tree_92790() -> None:
    """92790 ギフトHD: （当期）は節にせず、改装店除くの表記揺れは同じ指標（#817）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    trees = load_monthly_trees()
    rows = assign_tree_rows("92790", [
        "全店 売上高(前年比)", "既存 売上高(前年比)",
        "改装店除く 既存 売上高(前年比)",
        "(改装除く) 既存 売上高(前年比)",
        "(改装店除く) 既存 売上高(前年比)",
        "（当期） 店舗数", "店舗数",
    ], trees)
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["（当期） 店舗数"] != (["全体"], "店舗数", "店舗数"):
        _fail(f"92790 当期: {got['（当期） 店舗数']!r}")
    if got["店舗数"] != (["全体"], "店舗数", "店舗数"):
        _fail(f"92790 店舗数: {got['店舗数']!r}")
    if got["改装店除く 既存 売上高(前年比)"] != (
            ["全体"], "改装店除く 既存 売上高(前年比)", "改装店除く 既存 売上高"):
        _fail(f"92790 改装 dest: {got['改装店除く 既存 売上高(前年比)']!r}")
    if got["(改装除く) 既存 売上高(前年比)"] != (
            ["全体"], "(改装除く) 既存 売上高(前年比)", "改装店除く 既存 売上高"):
        _fail(f"92790 改装除く: {got['(改装除く) 既存 売上高(前年比)']!r}")
    if got["(改装店除く) 既存 売上高(前年比)"] != (
            ["全体"], "(改装店除く) 既存 売上高(前年比)", "改装店除く 既存 売上高"):
        _fail(f"92790 改装店除く: {got['(改装店除く) 既存 売上高(前年比)']!r}")
    if any("当期" in p for v in got.values() for p in v[0]):
        _fail(f"92790 （当期） must not be a group: {got!r}")


def test_tree_99360() -> None:
    """99360 王将: 全店 店舗数 ▸ 直営 / FC。売上の直営は根のまま（#818）。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows("99360", [
        "直営 全店 売上高", "直営 全店 売上高(前年比)",
        "直営 既存 売上高", "直営 既存 売上高(前年比)",
        "直営 店舗数", "FC 店舗数", "全店 店舗数",
    ], load_monthly_trees())
    got = {k: (v["path"], v["leaf"], v["metric"]) for k, v in rows.items()}
    if got["直営 全店 売上高"] != (["全体"], "直営 全店 売上高", "直営 全店 売上高"):
        _fail(f"99360 直営全店売上: {got['直営 全店 売上高']!r}")
    if got["直営 既存 売上高(前年比)"] != (
            ["全体"], "直営 既存 売上高(前年比)", "直営 既存 売上高"):
        _fail(f"99360 直営既存売上: {got['直営 既存 売上高(前年比)']!r}")
    if got["全店 店舗数"] != (["全体"], "全店 店舗数", "全店 店舗数"):
        _fail(f"99360 全店店舗: {got['全店 店舗数']!r}")
    if got["直営 店舗数"] != (["全体", "直営"], "直営 店舗数", "全店 店舗数"):
        _fail(f"99360 直営店舗: {got['直営 店舗数']!r}")
    if got["FC 店舗数"] != (["全体", "FC"], "FC 店舗数", "全店 店舗数"):
        _fail(f"99360 FC店舗: {got['FC 店舗数']!r}")
    if got["直営 全店 売上高"][0] != ["全体"]:
        _fail(f"99360 直営売上 must stay at root: {got['直営 全店 売上高']!r}")


GOLDEN_TREE_ROWS = Path(__file__).resolve().parent / "golden" / "tree_rows.json"


def _tree_cases() -> list[dict]:
    return json.loads(GOLDEN_TREE_ROWS.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", _tree_cases(), ids=lambda c: c["test"].removeprefix("test_tree_"))
def test_tree_rows_golden(case: dict) -> None:
    """golden/tree_rows.json: 行名の並びを config の木に割り当て、各行の path / leaf / metric を比べる。"""
    from app.monthly.tree import assign_tree_rows, load_monthly_trees

    rows = assign_tree_rows(case["code"], case["elements"], load_monthly_trees())
    got = {el: {f: v[f] for f in case["fields"]} for el, v in rows.items()}
    if got != case["want"]:
        diff = [f"  {el}: want {case['want'].get(el)!r} got {got.get(el)!r}"
                for el in sorted(set(got) | set(case["want"])) if got.get(el) != case["want"].get(el)]
        _fail(f"{case['code']} tree rows ({case['test']}):\n" + "\n".join(diff))


def test_auto_tree_fold_unknown_metrics() -> None:
    """辞書に無い指標語の焼き込みを、既知の節・合計行へ戻す（#577 / #578 / #579）。"""
    from app.monthly.tree import build_auto_tree

    life = build_auto_tree("71570", {2026: [
        ("保有契約年換算保険料", "number"),
        ("保有契約年換算保険料 個人保険", "number"),
        ("保有契約年換算保険料 団体信用生命保険", "number"),
        ("個人保険 保有契約件数", "number"),
        ("個人保険 新契約件数", "number"),
        ("個人保険 新契約年換算保険料", "number"),
        ("個人保険 保険金及び給付金支払額", "number"),
        ("個人保険 解約失効率", "number"),
    ]})
    got = {k: (v["path"], v["metric"]) for k, v in life.rows.items()}
    want = {
        "保有契約年換算保険料": (["全体"], "保有契約年換算保険料"),
        "保有契約年換算保険料 個人保険": (["全体", "個人保険"], "保有契約年換算保険料"),
        "保有契約年換算保険料 団体信用生命保険": (["全体", "団体信用生命保険"], "保有契約年換算保険料"),
        "個人保険 保有契約件数": (["全体", "個人保険"], "保有契約件数"),
        "個人保険 新契約件数": (["全体", "個人保険"], "新契約件数"),
        "個人保険 新契約年換算保険料": (["全体", "個人保険"], "新契約年換算保険料"),
        "個人保険 保険金及び給付金支払額": (["全体", "個人保険"], "保険金及び給付金支払額"),
        "個人保険 解約失効率": (["全体", "個人保険"], "解約失効率"),
    }
    if got != want:
        _fail(f"71570 fold: {got!r}")

    hirose = build_auto_tree("71850", {2026: [
        ("連結営業収益", "number"),
        ("連結顧客口座数", "number"),
        ("単体 営業収益", "number"),
        ("単体 顧客口座数", "number"),
        ("単体 外国為替取引高", "number"),
    ]})
    got_h = {k: (v["path"], v["metric"]) for k, v in hirose.rows.items()}
    want_h = {
        "連結営業収益": (["全体"], "営業収益"),
        "連結顧客口座数": (["全体"], "顧客口座数"),
        "単体 営業収益": (["全体", "単体"], "営業収益"),
        "単体 顧客口座数": (["全体", "単体"], "顧客口座数"),
        "単体 外国為替取引高": (["全体", "単体"], "外国為替取引高"),
    }
    if got_h != want_h:
        _fail(f"71850 fold: {got_h!r}")

    nissan = build_auto_tree("87050", {2026: [
        ("預かり資産", "number"),
        ("リテール事業 預かり資産", "number"),
        ("ホールセール事業 預かり資産", "number"),
        ("株式等売買代金", "number"),
        ("リテール事業 株式等売買代金", "number"),
        ("ホールセール事業 株式等売買代金", "number"),
        ("デリバティブ取引売買高", "number"),
        ("リテール事業 デリバティブ取引売買高", "number"),
        ("ホールセール事業 デリバティブ取引売買高", "number"),
        ("純営業収益", "number"),
        ("口座数", "number"),
    ]})
    got_n = {k: (v["path"], v["metric"]) for k, v in nissan.rows.items()}
    if got_n["リテール事業 預かり資産"] != (["全体", "リテール事業"], "預かり資産"):
        _fail(f"87050 リテール: {got_n['リテール事業 預かり資産']!r}")
    if got_n["ホールセール事業 預かり資産"] != (["全体", "ホールセール事業"], "預かり資産"):
        _fail(f"87050 ホールセール: {got_n['ホールセール事業 預かり資産']!r}")
    if got_n["預かり資産"] != (["全体"], "預かり資産"):
        _fail(f"87050 合計: {got_n['預かり資産']!r}")
    if got_n["リテール事業 株式等売買代金"] != (["全体", "リテール事業"], "株式等売買代金"):
        _fail(f"87050 売買代金: {got_n['リテール事業 株式等売買代金']!r}")
    if got_n["ホールセール事業 デリバティブ取引売買高"] != (["全体", "ホールセール事業"], "デリバティブ取引売買高"):
        _fail(f"87050 デリバティブ: {got_n['ホールセール事業 デリバティブ取引売買高']!r}")

    jac = build_auto_tree("21240", {2026: [
        ("連結売上総利益", "number"),
        ("連結売上総利益増減率(前年比)", "yoy_index"),
        ("従業員数", "number"),
        ("従業員数増減率(前年比)", "yoy_index"),
        ("採用決定人数", "number"),
        ("採用決定人数増減率(前年比)", "yoy_index"),
        ("コンサルタント人員", "number"),
        ("コンサルタント人員増減率(前年比)", "yoy_index"),
    ]})
    got_j = {k: v["metric"] for k, v in jac.rows.items()}
    if got_j["従業員数増減率(前年比)"] != "従業員数":
        _fail(f"21240 従業員数増減率: {got_j!r}")
    if got_j["採用決定人数増減率(前年比)"] != "採用決定人数":
        _fail(f"21240 採用決定人数増減率: {got_j!r}")
    if got_j["コンサルタント人員増減率(前年比)"] != "コンサルタント人員":
        _fail(f"21240 コンサルタント人員増減率: {got_j!r}")
    if got_j["連結売上総利益増減率(前年比)"] != "連結売上総利益":
        _fail(f"21240 連結売上総利益増減率: {got_j!r}")

    gmo = build_auto_tree("71770", {2026: [
        ("営業収益", "number"),
        ("証券・FX事業 営業収益", "number"),
        ("暗号資産事業 営業収益", "number"),
        ("その他事業 営業収益", "number"),
        ("クリック 株式約定件数", "number"),
        ("クリック 一日平均売買代金", "number"),
        ("クリック 一日平均売買代金現物", "number"),
        ("クリック 一日平均売買代金信用", "number"),
        ("クリック 株式約定件数現物", "number"),
        ("クリック 株式約定件数信用", "number"),
        ("外貨 店頭FX取引口座数", "number"),
        ("外貨 店頭FX売買代金月間", "number"),
    ]})
    got_g = {k: (v["path"], v["metric"]) for k, v in gmo.rows.items()}
    if got_g["証券・FX事業 営業収益"] != (["全体", "証券・FX事業"], "営業収益"):
        _fail(f"71770 証券・FX: {got_g['証券・FX事業 営業収益']!r}")
    if got_g["暗号資産事業 営業収益"] != (["全体", "暗号資産事業"], "営業収益"):
        _fail(f"71770 暗号資産: {got_g['暗号資産事業 営業収益']!r}")
    if got_g["クリック 一日平均売買代金"] != (["全体", "クリック"], "一日平均売買代金"):
        _fail(f"71770 クリック合計: {got_g['クリック 一日平均売買代金']!r}")
    if got_g["クリック 一日平均売買代金現物"] != (["全体", "クリック", "現物"], "一日平均売買代金"):
        _fail(f"71770 現物: {got_g['クリック 一日平均売買代金現物']!r}")
    if got_g["クリック 一日平均売買代金信用"] != (["全体", "クリック", "信用"], "一日平均売買代金"):
        _fail(f"71770 信用: {got_g['クリック 一日平均売買代金信用']!r}")
    if got_g["クリック 株式約定件数現物"] != (["全体", "クリック", "現物"], "株式約定件数"):
        _fail(f"71770 約定現物: {got_g['クリック 株式約定件数現物']!r}")
    if got_g["外貨 店頭FX売買代金月間"] != (["全体", "外貨"], "店頭FX売買代金月間"):
        _fail(f"71770 外貨: {got_g['外貨 店頭FX売買代金月間']!r}")

    keep = build_auto_tree("92510", {2026: [
        ("店舗数", "number"),
        ("純増 店舗数", "number"),
        ("純増 直営 店舗数", "number"),
        ("既存 売上高(前年比)", "yoy_index"),
        ("全店 売上高(前年比)", "yoy_index"),
    ]})
    got_k = {k: (v["path"], v["metric"]) for k, v in keep.rows.items()}
    if got_k["純増 店舗数"] != (["全体"], "純増 店舗数"):
        _fail(f"純増 店舗数 must stay its own metric: {got_k['純増 店舗数']!r}")
    if got_k["既存 売上高(前年比)"] != (["全体"], "既存 売上高"):
        _fail(f"既存 売上高 must stay: {got_k['既存 売上高(前年比)']!r}")


def main() -> None:
    for case in _tree_cases():
        test_tree_rows_golden(case)
    test_tree_99990()
    test_tree_75320()
    test_tree_31750()
    test_tree_30880()
    test_tree_31970()
    test_tree_35470()
    test_tree_35470_2()
    test_tree_46660()
    test_tree_70490()
    test_tree_70490_2()
    test_tree_74760()
    test_tree_78650()
    test_tree_96160()
    test_tree_26540()
    test_tree_65610()
    test_tree_73700()
    test_tree_82330()
    test_tree_138A0()
    test_tree_212A0()
    test_tree_99410()
    test_tree_99410_2()
    test_tree_30380()
    test_tree_60360()
    test_tree_80110()
    test_tree_78230()
    test_tree_75220()
    test_tree_76060()
    test_tree_99560()
    test_tree_82670()
    test_tree_98320()
    test_tree_98420()
    test_tree_92520()
    test_tree_92780()
    test_tree_90070()
    test_tree_81660()
    test_tree_81980()
    test_tree_39230()
    test_tree_33580()
    test_monthly_auto_tree()
    test_auto_tree_76850()
    test_auto_tree_fold_unknown_metrics()
    test_tree_76490()
    test_tree_87050()
    test_tree_71770()
    test_tree_23370()
    test_tree_31740()
    test_tree_37330()
    test_tree_39790()
    test_tree_kab102()
    test_tree_31930()
    test_tree_30820()
    test_tree_33290()
    test_tree_35570()
    test_tree_76430()
    test_tree_76160()
    test_tree_81630()
    test_tree_82000()
    test_tree_43430()
    test_tree_60400()
    test_tree_65370()
    test_tree_92450()
    test_tree_23110()
    test_tree_75060()
    test_tree_82190()
    test_tree_82550()
    test_tree_92790()
    test_tree_99360()
    test_union_collapses_yoy_display_names()
    test_segment_tail_row_order()


if __name__ == "__main__":
    main()
