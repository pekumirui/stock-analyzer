"""9989 の宣言は i9989.parse。html_recipe は無い。"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jobs"))

from html_issuers import PARSERS  # noqa: E402
from html_issuers.i9989 import parse as parse_9989  # noqa: E402
from ir_monthly_html_r2 import ingest_path, load_html_registry, write_ingest  # noqa: E402

CONFIG = ROOT / "config" / "monthly" / "ir_monthly.json"


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _sales(header: str, april: str, months: list[int]) -> str:
    heads = "".join(f"<th>{m}月</th>" for m in months)
    whole = "".join(f"<td>{april if m == months[0] else '1.0'}%</td>" for m in months)
    exist = "".join("<td>1.0%</td>" for _ in months)
    return (
        "<table>"
        f"<tr><th>{header}</th>{heads}</tr>"
        f"<tr><td>全店 売上高</td>{whole}</tr>"
        f"<tr><td>既存店 売上高</td>{exist}</tr>"
        "</table>"
    )


def _stores() -> str:
    months = [4, 5, 6, 7, 8]
    vals = [1592, 1594, 1597, 1599, 1596]
    heads = "".join(f"<th>{m}月</th>" for m in months)
    cells = "".join(f"<td>{v}</td>" for v in vals)
    return (
        "<table>"
        f"<tr><th>店舗数</th>{heads}</tr>"
        f"<tr><td>総店舗数</td>{cells}</tr>"
        "</table>"
    )


def _filler() -> str:
    return (
        "<table><tr><th>備考</th><th>4月</th></tr>"
        "<tr><td>注</td><td>1</td></tr></table>"
    )


def sundrug_html() -> str:
    """12 表。2026年度の 12 か月表は、見出し無しの後続部分月に負ける。2025年度も負ける。"""
    partial = [4, 5, 6, 7, 8]
    full = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
    pad = "あ" * 1200
    # 備考表は事業見出しより前。後に置くと直前表の見出しが section に残り未請求になる。
    parts = [
        "<html><body>",
        _filler(),
        _filler(),
        _filler(),
        _filler(),
        "<h2>1. 2026年度 売上高前年同月比較表</h2>",
        "<p>（単位：%）</p>",
        _sales("グループ合計", "99.0", full),
        f"<p>{pad}</p>",
        _sales("グループ合計", "5.9", partial),
        _sales("ドラッグストア事業", "4.0", partial),
        _sales("ディスカウントストア事業", "8.0", partial),
        _stores(),
        "<h2>1. 2025年度 売上高前年同月比較表</h2>",
        _sales("グループ合計", "50.0", partial),
        _sales("ドラッグストア事業", "11.0", partial),
        _sales("ディスカウントストア事業", "70.0", partial),
        "</body></html>",
    ]
    return "\n".join(parts)


def _row(payload: dict, element: str) -> dict:
    hits = [r for r in payload["rows"] if r["element"] == element]
    if len(hits) != 1:
        _fail(f"element {element!r}: {[r['element'] for r in payload['rows']]}")
    return hits[0]


def test_sundrug_prefixes_latest_and_units() -> None:
    html = sundrug_html()
    if html.count("<table") < 12:
        _fail("fixture needs 12 tables")
    payload = parse_9989(html)
    if set(payload) != {"code", "months", "rows", "as_of"}:
        _fail(f"keys {payload.keys()}")
    if "year" in payload:
        _fail("year leaked")
    if payload["code"] != "9989":
        _fail(payload["code"])
    if payload["as_of"] != "2026-08-31":
        _fail(payload["as_of"])
    if payload["months"] != [4, 5, 6, 7, 8]:
        _fail(str(payload["months"]))
    group = _row(payload, "全店 売上高")
    if group["unit"] != "yoy_pct" or group["values"]["4"] != 5.9:
        _fail(str(group))
    if group["values"]["4"] in (50.0, 99.0):
        _fail("picked older or complete group")
    drug = _row(payload, "ドラッグストア事業 全店 売上高")
    if drug["unit"] != "yoy_pct" or drug["values"]["4"] != 4.0:
        _fail(str(drug))
    if drug["values"]["4"] == 11.0:
        _fail("picked 2025 drug")
    disc = _row(payload, "ディスカウントストア事業 全店 売上高")
    if disc["unit"] != "yoy_pct" or disc["values"]["4"] != 8.0:
        _fail(str(disc))
    _row(payload, "ドラッグストア事業 既存店 売上高")
    _row(payload, "ディスカウントストア事業 既存店 売上高")
    stores = _row(payload, "総店舗数")
    if stores["unit"] != "number" or stores["values"]["8"] != 1596:
        _fail(str(stores))


def test_missing_drug_raises_and_writes_no_sidecar() -> None:
    html = sundrug_html().replace("ドラッグストア事業", "別名事業")
    try:
        parse_9989(html)
    except ValueError:
        pass
    else:
        _fail("expected ValueError")
    iss = next(i for i in load_html_registry(CONFIG) if i.code == "9989")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        fresh = tmp / "20260922_9989_サンドラッグ_月次.html"
        fresh.write_text(html, encoding="utf-8")
        try:
            write_ingest(fresh, html, iss)
        except ValueError:
            pass
        else:
            _fail("write_ingest should raise")
        if ingest_path(fresh).exists():
            _fail("sidecar written on mismatch")
        kept = tmp / "kept.html"
        kept.write_text(html, encoding="utf-8")
        prior = ingest_path(kept)
        prior.write_text('{"kept": true}\n', encoding="utf-8")
        try:
            write_ingest(kept, html, iss)
        except ValueError:
            pass
        else:
            _fail("write_ingest should raise")
        if prior.read_text(encoding="utf-8") != '{"kept": true}\n':
            _fail("existing sidecar removed")


def test_unclaimed_business_header_is_mismatch() -> None:
    html = sundrug_html().replace(
        "</body>",
        "<table><tr><th>参考 ドラッグストア事業</th><th>4月</th><th>5月</th><th>6月</th></tr>"
        "<tr><td>全店 売上高</td><td>1.0%</td><td>1.0%</td><td>1.0%</td></tr></table></body>",
    )
    try:
        parse_9989(html)
    except ValueError as e:
        if "unclaimed" not in str(e):
            _fail(str(e))
        return
    _fail("expected ValueError")


def test_short_page_is_mismatch() -> None:
    html = """
    <html><body>
    <h2>2026年度</h2>
    <table>
    <tr><th>グループ合計</th><th>4月</th><th>5月</th></tr>
    <tr><td>全店 売上高</td><td>5.9%</td><td>7.0%</td></tr>
    </table>
    </body></html>
    """
    try:
        parse_9989(html)
    except ValueError as e:
        if "tables" not in str(e):
            _fail(str(e))
        return
    _fail("expected ValueError")


def test_explain_and_html_recipe_are_gone() -> None:
    try:
        importlib.import_module("html_recipe")
    except ModuleNotFoundError:
        pass
    else:
        _fail("html_recipe remains")
    if hasattr(importlib.import_module("html_issuers.i9989"), "explain"):
        _fail("explain remains")


def test_write_ingest_sundrug_sidecar() -> None:
    html = sundrug_html()
    iss = next(i for i in load_html_registry(CONFIG) if i.code == "9989")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        html_path = tmp / "20260922_9989_サンドラッグ_月次.html"
        html_path.write_text(html, encoding="utf-8")
        write_ingest(html_path, html, iss)
        side = json.loads(ingest_path(html_path).read_text(encoding="utf-8"))
        if side["code"] != "9989" or side["as_of"] != "2026-08-31":
            _fail(str(side))
        if not any(r["element"] == "ドラッグストア事業 全店 売上高" for r in side["rows"]):
            _fail(str(side["rows"]))
        drug = next(r for r in side["rows"] if r["element"] == "ドラッグストア事業 全店 売上高")
        if drug["unit"] != "yoy_pct" or drug["values"]["4"] != 4.0:
            _fail(str(drug))


def test_config_9989_has_no_recipes() -> None:
    raw = json.loads(CONFIG.read_text(encoding="utf-8"))
    iss = next(i for i in raw["html"] if i["code"] == "9989")
    if "recipes" in iss or "drift" in iss:
        _fail(f"9989 still has recipe keys {iss.keys()}")
    if "9989" not in PARSERS or "9993" not in PARSERS:
        _fail(str(sorted(PARSERS)))
    live = load_html_registry(CONFIG)
    if any(getattr(i, "recipes", None) is not None for i in live):
        _fail("recipes field remains")
    yamazawa = next(i for i in live if i.code == "9993")
    if yamazawa.code != "9993":
        _fail("9993")


def main() -> None:
    test_sundrug_prefixes_latest_and_units()
    test_missing_drug_raises_and_writes_no_sidecar()
    test_unclaimed_business_header_is_mismatch()
    test_short_page_is_mismatch()
    test_explain_and_html_recipe_are_gone()
    test_write_ingest_sundrug_sidecar()
    test_config_9989_has_no_recipes()
    print("ok: html_recipe")


if __name__ == "__main__":
    main()
