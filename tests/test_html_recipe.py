from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "jobs"))

from html_recipe import (  # noqa: E402
    RecipeDrift,
    compile_recipes,
    execute_recipes,
    explain_recipes,
)
from ir_monthly_html_r2 import (  # noqa: E402
    HtmlIssuer,
    html_to_ingest,
    ingest_path,
    load_html_registry,
    write_ingest,
)

CONFIG = ROOT / "config" / "ir_monthly.json"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


SUNDRUG_HTML = """
<html><body>
<h2>1. 2026年度 売上高前年同月比較表</h2>
<p>（単位：%）</p>
<table>
<tr><th>グループ合計</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>全店 売上高</td><td>5.9%</td><td>7.3%</td><td>-1.9%</td><td>2.7%</td><td>2.7%</td></tr>
<tr><td>既存店 売上高</td><td>2.9%</td><td>4.6%</td><td>-4.4%</td><td>1.1%</td><td>1.5%</td></tr>
</table>
<table>
<tr><th>ドラッグストア事業</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>全店 売上高</td><td>4.0%</td><td>5.0%</td><td>1.0%</td><td>2.0%</td><td>3.0%</td></tr>
<tr><td>既存店 売上高</td><td>1.0%</td><td>2.0%</td><td>0.5%</td><td>1.5%</td><td>2.5%</td></tr>
</table>
<table>
<tr><th>ディスカウントストア事業</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>全店 売上高</td><td>8.0%</td><td>9.0%</td><td>-3.0%</td><td>4.0%</td><td>4.0%</td></tr>
<tr><td>既存店 売上高</td><td>3.0%</td><td>4.0%</td><td>-1.0%</td><td>2.0%</td><td>2.0%</td></tr>
</table>
<h2>2. 2026年度 出店・退店</h2>
<table>
<tr><th>店舗数</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>店舗数</td><td>1592</td><td>1594</td><td>1597</td><td>1599</td><td>1596</td></tr>
</table>
<h2>1. 2025年度 売上高前年同月比較表</h2>
<table>
<tr><th>グループ合計</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>全店 売上高</td><td>50.0%</td><td>50.0%</td><td>50.0%</td><td>50.0%</td><td>50.0%</td></tr>
<tr><td>既存店 売上高</td><td>40.0%</td><td>40.0%</td><td>40.0%</td><td>40.0%</td><td>40.0%</td></tr>
</table>
<table>
<tr><th>ドラッグストア事業</th><th>4月</th><th>5月</th><th>6月</th><th>7月</th><th>8月</th></tr>
<tr><td>全店 売上高</td><td>11.0%</td><td>11.0%</td><td>11.0%</td><td>11.0%</td><td>11.0%</td></tr>
</table>
</body></html>
"""

SUNDRUG_RECIPES = {
    "recipes": [
        {
            "id": "group",
            "match": {"header_re": "^グループ合計"},
            "role": "primary",
            "expect": {"tables": [1, 4], "rows": ["^全店 売上高", "^既存店 売上高"]},
        },
        {
            "id": "drug",
            "match": {"header_re": "^ドラッグストア事業"},
            "segment": "ドラッグストア事業",
            "expect": {"tables": [1, 4], "rows": ["^ドラッグストア事業 全店 売上高"]},
        },
        {
            "id": "discount",
            "match": {"header_re": "^ディスカウントストア事業"},
            "segment": "ディスカウントストア事業",
            "expect": {
                "tables": [1, 4],
                "rows": ["^ディスカウントストア事業 全店 売上高"],
            },
        },
        {
            "id": "stores",
            "match": {"header_re": "店舗数"},
            "unit": "number",
            "expect": {"tables": [1, 4]},
        },
    ],
    "drift": {"claim_re": "事業|合計|店舗数"},
}


def _row(payload: dict, element: str) -> dict:
    hits = [r for r in payload["rows"] if r["element"] == element]
    if len(hits) != 1:
        _fail(f"element {element!r}: {[r['element'] for r in payload['rows']]}")
    return hits[0]


def test_compile_rejects_two_primaries() -> None:
    try:
        compile_recipes(
            {
                "recipes": [
                    {"id": "a", "match": {"index": 0}, "role": "primary"},
                    {"id": "b", "match": {"index": 1}, "role": "primary"},
                ]
            }
        )
    except ValueError as e:
        if "primary" not in str(e):
            _fail(str(e))
        return
    _fail("expected ValueError")


def test_compile_rejects_bad_regex_and_table_plus_recipes() -> None:
    try:
        compile_recipes({"recipes": [{"id": "a", "match": {"header_re": "("}}]})
    except ValueError:
        pass
    else:
        _fail("bad regex")
    try:
        compile_recipes(
            {
                "table": 2,
                "recipes": [
                    {"id": "a", "match": {"index": 0}, "role": "primary"},
                ],
            }
        )
    except ValueError:
        pass
    else:
        _fail("table+recipes")


def test_compile_auto_when_no_recipes_key() -> None:
    auto_all = compile_recipes({"table": None})
    if auto_all.explicit or not auto_all.primary.match.auto:
        _fail(f"auto {auto_all}")
    by_index = compile_recipes({"table": 0})
    if by_index.explicit or by_index.primary.match.index != 0:
        _fail(f"index {by_index}")


def test_sundrug_recipes_prefix_dg_ds() -> None:
    recipes = compile_recipes(SUNDRUG_RECIPES)
    result = execute_recipes(SUNDRUG_HTML, "99890", recipes)
    payload = result.sidecar
    if set(payload) != {"code", "months", "rows", "as_of"}:
        _fail(f"keys {payload.keys()}")
    if payload["code"] != "9989":
        _fail(payload["code"])
    if payload["as_of"] != "2026-08-31":
        _fail(payload["as_of"])
    if payload["months"] != [4, 5, 6, 7, 8]:
        _fail(str(payload["months"]))
    group = _row(payload, "全店 売上高")
    if group["unit"] != "yoy_pct" or group["values"]["4"] != 5.9:
        _fail(str(group))
    if _row(payload, "全店 売上高")["values"]["4"] == 50.0:
        _fail("picked 2025 group")
    drug = _row(payload, "ドラッグストア事業 全店 売上高")
    if drug["unit"] != "yoy_pct" or drug["values"]["4"] != 4.0:
        _fail(str(drug))
    if drug["values"]["4"] == 11.0:
        _fail("picked 2025 drug")
    disc = _row(payload, "ディスカウントストア事業 全店 売上高")
    if disc["values"]["4"] != 8.0:
        _fail(str(disc))
    _row(payload, "ドラッグストア事業 既存店 売上高")
    _row(payload, "ディスカウントストア事業 既存店 売上高")
    stores = _row(payload, "店舗数")
    if stores["unit"] != "number" or stores["values"]["8"] != 1596:
        _fail(str(stores))


def test_no_recipes_path_drops_brand_tables() -> None:
    payload = html_to_ingest(SUNDRUG_HTML, "9989", table=None)
    names = [r["element"] for r in payload["rows"]]
    if any("ドラッグストア" in n or "ディスカウント" in n for n in names):
        _fail(f"legacy leaked brands {names}")
    if "全店 売上高" not in names:
        _fail(names)


def test_missing_claimed_table_is_recipe_drift() -> None:
    html = SUNDRUG_HTML.replace("ドラッグストア事業", "別名事業")
    recipes = compile_recipes(SUNDRUG_RECIPES)
    try:
        execute_recipes(html, "9989", recipes)
    except RecipeDrift as e:
        kinds = {f.kind for f in e.findings if f.level == "error"}
        if "no_match" not in kinds and "missing_row" not in kinds:
            _fail(str(e.findings))
        return
    _fail("expected RecipeDrift")


def test_unclaimed_claim_re_is_recipe_drift() -> None:
    html = SUNDRUG_HTML.replace(
        "</body>",
        "<table><tr><th>新ブランド事業</th><th>4月</th></tr>"
        "<tr><td>全店 売上高</td><td>1.0%</td></tr></table></body>",
    )
    recipes = compile_recipes(SUNDRUG_RECIPES)
    try:
        execute_recipes(html, "9989", recipes)
    except RecipeDrift as e:
        kinds = [f.kind for f in e.findings if f.level == "error"]
        if "unclaimed" not in kinds:
            _fail(str(e.findings))
        return
    _fail("expected RecipeDrift")


def test_explain_does_not_raise_on_drift() -> None:
    html = SUNDRUG_HTML.replace("ドラッグストア事業", "別名事業")
    recipes = compile_recipes(SUNDRUG_RECIPES)
    report = explain_recipes(html, "9989", recipes)
    text = report.render()
    if "9989" not in text or "no_match" not in text and "missing_row" not in text:
        _fail(text)


def test_write_ingest_recipes_and_drift(tmp_path: Path) -> None:
    recipes = compile_recipes(SUNDRUG_RECIPES)
    iss = HtmlIssuer(
        code="9989",
        name="サンドラッグ",
        index_url="https://www.sundrug.co.jp/ir/irdata/monthly",
        title="月次",
        source="html",
        table=None,
        eir_code="9989",
        eir_n=0,
        eir_title_re="月次",
        recipes=recipes,
    )
    html_path = tmp_path / "20260922_9989_サンドラッグ_月次.html"
    html_path.write_text(SUNDRUG_HTML, encoding="utf-8")
    write_ingest(html_path, SUNDRUG_HTML, iss)
    side = json.loads(ingest_path(html_path).read_text(encoding="utf-8"))
    if side["code"] != "9989" or side["as_of"] != "2026-08-31":
        _fail(str(side))
    if not any(r["element"] == "ドラッグストア事業 全店 売上高" for r in side["rows"]):
        _fail(str(side["rows"]))

    bad = SUNDRUG_HTML.replace("ドラッグストア事業", "別名事業")
    html_path.write_text(bad, encoding="utf-8")
    ingest_path(html_path).unlink()
    try:
        write_ingest(html_path, bad, iss)
    except RecipeDrift:
        pass
    else:
        _fail("write_ingest should raise RecipeDrift")
    if ingest_path(html_path).exists():
        _fail("sidecar written on drift")


def test_config_9989_compiles() -> None:
    live = load_html_registry(CONFIG)
    iss = next(i for i in live if i.code == "9989")
    if iss.recipes is None or not iss.recipes.explicit:
        _fail("9989 recipes missing")
    ids = [r.id for r in iss.recipes.recipes]
    if ids != ["group", "drug", "discount", "stores"]:
        _fail(str(ids))
    if iss.recipes.primary.id != "group":
        _fail(iss.recipes.primary.id)
    if iss.recipes.drift.claim_re is None:
        _fail("claim_re")
    no_recipes = next(i for i in live if i.code == "9993")
    if no_recipes.recipes is not None:
        _fail("9993 should stay on html_to_ingest")
