"""月次 SPA の JS（static/js/monthly.js）。node で関数を呼び、表示単位・表示年・色を Python と突き合わせる。"""
from __future__ import annotations

import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")


from app.monthly.ingest import (  # noqa: E402
    MONTHLY_DISPLAY_UNITS,
    monthly_element_label,
)

INVALID_VALUE_MSG = "value は数値のみ受け付けます。単位(%、円など)やカンマは除去してください。"
SIMILAR_MSG = "類似する要素が既に存在します。既存要素を使うか、force=true で強制登録してください。"


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


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


def test_monthly_layout_js() -> None:
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
    taka_rows = [
        ("全社 売上高(前年比)", "yoy_index"),
        ("小売+ネット通販 既存 売上高(前年比)", "yoy_index"),
        ("小売 既存 売上高(前年比)", "yoy_index"),
        ("ネット通販 既存 売上高(前年比)", "yoy_index"),
        ("小売 店舗数", "number"),
    ]
    taka_assign = assign_tree_rows("81660", [e for e, _ in taka_rows], sanyo_trees)
    fixtures["takaTree"] = [{
        "year": 2026, "code": "81660",
        "elements": [{**_el(e, u), "tree": taka_assign[e]} for e, u in taka_rows],
        **tree_payload("81660", [e for e, _ in taka_rows], sanyo_trees),
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
const tktm = sandbox.monthlyTreeModel(fixtures.takaTree);
sandbox.monthlyTreeSeedOpen(tktm);
vm.runInContext('monthlyTlOpen.add("m:既存 売上高\\u203atree:全体\\u203a小売+ネット通販"); monthlyTlOpen.add("m:店舗数");', sandbox);
const takaNames = namesOf(sandbox.monthlyTreeHtml(tktm, () => "<td></td>", [{y: 2026, m: 1}]));
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
  takaNames,
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
    want_names = ["直営+FC 既存 店舗数", "直営", "焼肉", "直営+FC 全店 店舗数", "日本", "直営", "FC", "焼肉", "直営", "FC", "海外"]
    if got.get("treeNames") != want_names:
        _fail(f"物語 store tree rows: {got.get('treeNames')!r}")
    want_brand = ["既存 売上高", "トライアル", "西友", "全店 売上高", "トライアル", "西友"]
    if got.get("brandNames") != want_brand:
        _fail(f"トライアル: 会社の行が無い指標は開く: {got.get('brandNames')!r}")
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
    want_watami = ["既存 売上高", "サブウェイ除く", "サブウェイ",
                   "国内外食 全店 売上高(前年比)", "全店 店舗数", "国内外食"]
    if got.get("watamiNames") != want_watami:
        _fail(f"75220 config tree rows: {got.get('watamiNames')!r}")
    want_ab = [
        "日本 既存 売上高(前年比)", "タイヤ", "サービス", "車検・整備", "車販売",
        "日本 既存 客数(前年比)", "既存 客単価(前年比)", "日本 全店 売上高(前年比)",
        "日本 全店 客数(前年比)", "土日祝日前年差異",
    ]
    if got.get("autobacsNames") != want_ab:
        _fail(f"98320 config tree rows: {got.get('autobacsNames')!r}")
    want_tabaya = [
        "既存 売上高", "小売＋ネット通販", "小売", "ネット通販",
        "既存 店舗数", "小売", "ネット通販",
        "全社 売上高(前年比)", "ビジネスユニット計", "小売＋ネット通販", "小売", "ネット通販", "アウトレット その他",
        "店舗数", "小売＋ネット通販", "アウトレット",
    ]
    if got.get("tabayaNames") != want_tabaya:
        _fail(f"76060 config tree rows: {got.get('tabayaNames')!r}")
    want_taka = [
        "既存 売上高", "小売+ネット通販", "小売", "ネット通販",
        "全社 売上高(前年比)",
        "小売 店舗数",
    ]
    if got.get("takaNames") != want_taka:
        _fail(f"81660 config tree rows: {got.get('takaNames')!r}")
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


def test_display_units_match_js() -> None:
    """表示単位は Python（MONTHLY_DISPLAY_UNITS）と SPA の JS の 2 か所にある。ずれたら落とす。"""
    js = (ROOT / "static" / "js" / "monthly.js").read_text(encoding="utf-8")
    js_fn = js.find("const MONTHLY_DISPLAY_UNITS")
    js_end = js.find("function monthlyDisplayUnit")
    if js_fn < 0 or js_end <= js_fn:
        _fail("static/js/monthly.js: MONTHLY_DISPLAY_UNITS / monthlyDisplayUnit が見つからない")
    js_rows = re.findall(r'\["([^"]+)", "([^"]+)", (\d+)\]', js[js_fn:js_end])
    py_rows = [(token, suffix, str(digits)) for token, suffix, digits in MONTHLY_DISPLAY_UNITS]
    if js_rows != py_rows:
        _fail(f"JS/Python display units drifted: {js_rows} vs {py_rows}")


def test_monthly_year_window() -> None:
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


def test_monthly_tree_html_kab87() -> None:
    """画面の指標 ▸ 区分（#582 / #585 / #586 / #587 / #588）。"""
    import json
    import subprocess
    import tempfile

    from app.monthly.tree import (
        assign_tree_rows, auto_tree_payload, build_auto_tree, load_monthly_trees, tree_payload,
    )

    js_path = ROOT / "static" / "js" / "monthly.js"

    def _vals() -> dict:
        return {str(m): (1 if m == 1 else None) for m in range(1, 13)}

    def _el(name: str, unit: str = "number") -> dict:
        return {
            "element": name,
            "unit": unit,
            "label": monthly_element_label(name, unit),
            "values": _vals(),
        }

    def _cfg(code: str, pairs: list[tuple[str, str]]) -> list[dict]:
        trees = load_monthly_trees()
        els = [e for e, _ in pairs]
        assign = assign_tree_rows(code, els, trees)
        payload = tree_payload(code, els, trees)
        return [{
            "year": 2026, "code": code,
            "elements": [{**_el(e, u), "tree": assign[e]} for e, u in pairs],
            **payload,
        }]

    def _auto(code: str, pairs: list[tuple[str, str]]) -> list[dict]:
        auto = build_auto_tree(code, {2026: pairs})
        return [{
            "year": 2026, "code": code,
            "elements": [{**_el(e, u), "tree": auto.rows[e]} for e, u in pairs],
            **auto_tree_payload(auto, [e for e, _ in pairs]),
        }]

    fixtures = {
        "sugi": _cfg("76490", [
            ("スギ薬局 全店 売上高(前年比)", "yoy_index"),
            ("ドラッグストア事業 全店 売上高(前年比)", "yoy_index"),
            ("調剤事業 全店 売上高(前年比)", "yoy_index"),
        ]),
        "life": _auto("71570", [
            ("保有契約年換算保険料", "number"),
            ("保有契約年換算保険料 個人保険", "number"),
            ("保有契約年換算保険料 団体信用生命保険", "number"),
        ]),
        "hirose": _auto("71850", [
            ("連結営業収益", "number"),
            ("単体 営業収益", "number"),
            ("連結顧客口座数", "number"),
            ("単体 顧客口座数", "number"),
        ]),
        "gmo": _cfg("71770", [
            ("営業収益", "number"),
            ("受入手数料", "number"),
            ("トレーディング損益", "number"),
            ("証券・FX事業 営業収益", "number"),
            ("クリック証券 一日平均売買代金", "number"),
            ("クリック証券 一日平均売買代金現物", "number"),
            ("クリック証券 一日平均売買代金信用", "number"),
        ]),
        "nissan": _cfg("87050", [
            ("純営業収益", "number"),
            ("受入手数料", "number"),
            ("預かり資産", "number"),
            ("リテール事業 預かり資産", "number"),
            ("ホールセール事業 預かり資産", "number"),
        ]),
    }
    harness = r"""
const fs = require("fs");
const vm = require("vm");
const jsPath = process.argv[process.argv.length - 1];
const fixtures = """ + json.dumps(fixtures, ensure_ascii=False) + r""";
const code = fs.readFileSync(jsPath, "utf8");
const el = {
  innerHTML: "", value: "", textContent: "",
  style: { display: "", setProperty() {}, removeProperty() {} },
  classList: { toggle() {} }, dataset: {}, clientWidth: 720,
  addEventListener() {},
};
const sandbox = {
  console, fetch: async () => ({ ok: false, json: async () => ({}) }),
  $: () => el,
  document: { querySelector: () => el, querySelectorAll: () => [], documentElement: { clientWidth: 720 } },
  window: { matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {} },
};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(code, sandbox);
const nameRe = /<td class="tl-name[^"]*"[^>]*>(?:<span class="tl-tw">[^<]*<\/span>)?([^<]*)/g;
const namesOf = (html) => [...html.matchAll(nameRe)].map((m) => m[1]);
const run = (tables, extra) => {
  vm.runInContext("monthlyTlOpen = new Set(); monthlyTlTreeSeeded = false;", sandbox);
  const tm = sandbox.monthlyTreeModel(tables);
  sandbox.monthlyTreeSeedOpen(tm);
  if (extra) extra();
  return namesOf(sandbox.monthlyTreeHtml(tm, () => "<td></td>", [{y: 2026, m: 1}]));
};
const sugi = run(fixtures.sugi, () => vm.runInContext(
  'monthlyTlOpen.add("m:全店 売上高"); monthlyTlOpen.add("m:全店 売上高\\u203atree:全体\\u203aスギ薬局");', sandbox));
const life = run(fixtures.life, () => vm.runInContext('monthlyTlOpen.add("m:保有契約年換算保険料");', sandbox));
const hirose = run(fixtures.hirose, () => vm.runInContext('monthlyTlOpen.add("m:営業収益");', sandbox));
const gmo = run(fixtures.gmo, () => vm.runInContext(
  'monthlyTlOpen.add("m:営業収益"); monthlyTlOpen.add("m:一日平均売買代金"); monthlyTlOpen.add("m:一日平均売買代金\\u203atree:全体\\u203aクリック証券");', sandbox));
const nissan = run(fixtures.nissan, () => vm.runInContext(
  'monthlyTlOpen.add("m:純営業収益"); monthlyTlOpen.add("m:預かり資産");', sandbox));
console.log(JSON.stringify({sugi, life, hirose, gmo, nissan}));
"""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as tmp:
        tmp.write(harness)
        harness_path = tmp.name
    try:
        proc = subprocess.run(
            ["node", harness_path, str(js_path)],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
        )
    finally:
        Path(harness_path).unlink(missing_ok=True)
    if proc.returncode != 0:
        _fail(f"kab87 tree html: {proc.stderr.strip() or proc.stdout.strip() or proc.returncode}")
    got = json.loads(proc.stdout.strip().splitlines()[-1])
    if got.get("sugi") != ["全店 売上高", "スギ薬局", "ドラッグストア事業", "調剤事業"]:
        _fail(f"76490 screen: {got.get('sugi')!r}")
    if got.get("life") != ["保有契約年換算保険料", "個人保険", "団体信用生命保険"]:
        _fail(f"71570 screen: {got.get('life')!r}")
    if got.get("hirose") != ["連結顧客口座数", "単体", "連結営業収益", "単体"]:
        _fail(f"71850 screen: {got.get('hirose')!r}")
    if "受入手数料" not in (got.get("gmo") or []) or "現物" not in (got.get("gmo") or []):
        _fail(f"71770 screen: {got.get('gmo')!r}")
    if got.get("nissan") != ["純営業収益", "受入手数料", "預かり資産", "リテール事業", "ホールセール事業"]:
        _fail(f"87050 screen: {got.get('nissan')!r}")


def test_monthly_year_color_stable() -> None:
    """色は暦年。2025 は 2026 の有無で変わらない。"""
    if _monthly_year_color(2025) != _MONTHLY_YEAR_COLORS[2025 % 4]:
        _fail("2025 must use year % 4, not display index")
    colors_with = {y: _monthly_year_color(y) for y in _monthly_display_years([2024, 2025, 2026])}
    colors_without = {y: _monthly_year_color(y) for y in _monthly_display_years([2023, 2024, 2025])}
    if colors_with[2025] != colors_without[2025]:
        _fail(f"2025 color moved when 2026 dropped: {colors_with[2025]!r} vs {colors_without[2025]!r}")
    if colors_with[2024] != colors_without[2024]:
        _fail("2024 color must stay when the window slides")


def main() -> None:
    test_monthly_layout_js()
    test_display_units_match_js()
    test_monthly_year_window()
    test_monthly_year_color_stable()
    test_monthly_tree_html_kab87()


if __name__ == "__main__":
    main()
