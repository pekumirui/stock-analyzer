"""月次 ingest: PDF列順→暦年の純関数と API。手元の stock.db は触らない。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-monthly-ingest-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
import app.monthly.ingest as monthly_ingest_mod  # noqa: E402
from app.monthly.ingest import (  # noqa: E402
    MonthlyIngestError,
    apply_issuer_aliases,
    canonicalize_element_label,
    format_monthly_cell,
    load_issuer_aliases,
    load_issuer_metrics,
    map_pdf_columns,
    monthly_breakdown_depth,
    monthly_element_label,
    monthly_row_breakdown_rank,
    monthly_row_segment,
    monthly_row_unit_rank,
    monthly_series_kind,
    parse_as_of,
    parse_element_layers,
    parse_monthly_unit,
    plan_column_years,
    plan_issuer_alias_cases,
    plan_yoy_duplicate_merges,
    reject_prior_period_element,
    resolve_fy_end_month,
    same_yoy_element,
    uncovered_yoy_number_twins,
    union_monthly_elements,
)

GIFT_MONTHS = [11, 12, 1, 2, 3, 4, 5, 6, 7]
MARCH_MONTHS = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]
CAL_MONTHS = list(range(1, 13))
# Genky 20250630: Jun2024 … Jun2025 (13 cols, month 6 twice)
GENKY_MONTHS = [6, 7, 8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6]


def _map_dict(mapping: list[tuple[int, int]]) -> dict[int, int]:
    """一意月の比較用。重複がある mapping では使わない。"""
    return {month: year for month, year in mapping}


def _fail(msg: object) -> None:
    raise SystemExit(str(msg))


def _present_months(vals: dict) -> dict[str, float]:
    return {str(k): float(v) for k, v in vals.items() if v is not None}


def _err(resp) -> dict:
    body = resp.json()
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict) or "code" not in err or "message" not in err:
        _fail(f"error shape: {resp.status_code} {body!r}")
    return err


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        _fail(f"{label}: {resp.status_code} {resp.text[:400]}")


def _expect_ingest_error(fn, code: str, needle: str | None = None) -> MonthlyIngestError:
    try:
        fn()
    except MonthlyIngestError as exc:
        if exc.code != code:
            _fail(f"expected {code}, got {exc.code}: {exc.message}")
        if needle is not None and needle not in exc.message:
            _fail(f"{code} message {exc.message!r} missing {needle!r}")
        return exc
    _fail(f"expected {code}")
    raise AssertionError  # unreachable


def test_alias_rename_takes_unit_from_names(tmp: Path) -> None:
    path = tmp / "issuer_aliases.json"
    path.write_text(json.dumps({"99990": [{
        "until": "2027-01-01",
        "rename": {"売上高前年比": "全店 売上高", "客数": "全店 客数"},
    }]}, ensure_ascii=False), encoding="utf-8")
    rule = load_issuer_aliases(path)["99990"][0]
    if rule.until != date(2027, 1, 1):
        _fail(f"until {rule.until}")
    # 片方だけ前年比でも両側を yoy の表示名にそろえる。実数行は接尾辞を付けない
    if rule.rename != {"売上高(前年比)": "全店 売上高(前年比)", "客数": "全店 客数"}:
        _fail(f"alias rename {rule.rename}")


def test_union_orders_country_then_channel() -> None:
    def order(names: list[str]) -> list[str]:
        table = {"code": "99990", "year": 2026,
                 "elements": [{"element": n, "unit": "yoy_index"} for n in names]}
        return [row["element"] for row in union_monthly_elements([table])]

    got = order(["海外 全店 売上高(前年比)", "国内 全店 売上高(前年比)", "全店 売上高(前年比)"])
    if got != ["全店 売上高(前年比)", "日本 全店 売上高(前年比)", "海外 全店 売上高(前年比)"]:
        _fail(f"country order (none → 日本 → 海外): {got}")
    got = order(["FC 売上高(前年比)", "直営 売上高(前年比)", "直営+FC 売上高(前年比)"])
    if got != ["直営+FC 売上高(前年比)", "直営 売上高(前年比)", "FC 売上高(前年比)"]:
        _fail(f"channel order (直営+FC → 直営 → FC): {got}")


def test_canonicalize_element_label() -> None:
    if canonicalize_element_label("売上高前年同月比", "yoy_index") != "売上高(前年比)":
        _fail("strip 前年同月比 + suffix")
    if canonicalize_element_label("売上高前年比", "yoy_pct") != "売上高(前年比)":
        _fail("strip 前年比 + suffix")
    if canonicalize_element_label("国内全店 売上高前年対比", "yoy_pct") != "日本 全店 売上高(前年比)":
        _fail("strip 前年対比 from 国内全店 売上高")
    if canonicalize_element_label("那須パラダイスヴィレッジ 前年対比", "yoy_pct") != "那須パラダイスヴィレッジ(前年比)":
        _fail("strip leftover 前年対比; do not invent 全店 売上高")
    if canonicalize_element_label("米国子会社 前年対比", "yoy_index") != "米国子会社(前年比)":
        _fail("strip leftover 前年対比 from 米国子会社")
    if canonicalize_element_label("売上高前期対比", "yoy_pct") != "売上高(前年比)":
        _fail("strip 前期対比 + suffix")
    if canonicalize_element_label("売上高前年同期対比", "yoy_index") != "売上高(前年比)":
        _fail("strip 前年同期対比 + suffix")
    if canonicalize_element_label("売上高(前期比)", "yoy_pct") != "売上高(前年比)":
        _fail("strip (前期比) + suffix")
    if canonicalize_element_label("売上高（前期比）", "yoy_index") != "売上高(前年比)":
        _fail("fullwidth (前期比) to ascii suffix")
    if canonicalize_element_label("既存売上高(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("no double suffix")
    if canonicalize_element_label("既存売上高（前年比）", "yoy_index") != "既存 売上高(前年比)":
        _fail("fullwidth parens to ascii suffix")
    if canonicalize_element_label("全店 売上高", "yoy_index") != "全店 売上高(前年比)":
        _fail("yoy append even without 前年比")
    if canonicalize_element_label(" 売上高 前年同月比 ", "yoy_index") != "売上高(前年比)":
        _fail("trim leftover spaces after strip")
    if canonicalize_element_label("売上高", "number") != "売上高":
        _fail("number 売上高 stays unlabeled")
    if canonicalize_element_label("売上高前年同月比", "number") != "売上高":
        _fail("number token-normalize still skips yoy suffix")
    if canonicalize_element_label("売上高前年対比", "number") != "売上高":
        _fail("number drops leftover 前年対比 without yoy suffix")
    if canonicalize_element_label("店舗数", "number") != "店舗数":
        _fail("number 店舗数")
    if canonicalize_element_label("売上高前年同月比", None) != "売上高":
        _fail("omitted unit is number: tokens only")
    if canonicalize_element_label("合計 店舗数", "number") != "店舗数":
        _fail("合計 店舗数 drops 合計")
    if canonicalize_element_label("百貨店事業合計 売上高(前年比)", "yoy_index") != "百貨店事業合計 売上高(前年比)":
        _fail("百貨店事業合計 売上高 keeps compound 合計")
    if canonicalize_element_label("百貨店事業合計売上高(前年比)", "yoy_index") != "百貨店事業合計 売上高(前年比)":
        _fail("glued 百貨店事業合計売上高 keeps compound")
    if canonicalize_element_label("百貨店事業合計 入店 客数(前年比)", "yoy_index") != "百貨店事業合計 入店 客数(前年比)":
        _fail("百貨店事業合計 入店 客数 keeps 合計 and 入店")
    if canonicalize_element_label("全社合計 売上高(前年比)", "yoy_index") != "全社 売上高(前年比)":
        _fail("全社合計 still drops 合計")
    if canonicalize_element_label("対象店舗数", "number") != "店舗数":
        _fail("対象店舗数 is 店舗数")
    if canonicalize_element_label("当年 全店 店舗数", "number") != "全店 店舗数":
        _fail("当年 全店 店舗数 drops 当年")
    if canonicalize_element_label("当年店舗数", "number") != "店舗数":
        _fail("当年店舗数 drops 当年")
    if monthly_element_label("当年 全店 店舗数", "number")["group"] != "":
        _fail("当年 is not a group")
    if monthly_element_label("当年 全店 店舗数", "number")["display"] != "全店 店舗数":
        _fail("当年 全店 店舗数 display")
    if canonicalize_element_label("全業態 対象 店舗数", "number") != "全業態 店舗数":
        _fail("対象 leftover drops from 全業態 店舗数")
    if canonicalize_element_label("全業態 対象 既存 店舗数", "number") != "全業態 既存 店舗数":
        _fail("対象 leftover drops from 全業態 既存 店舗数")
    if monthly_row_segment("全業態 対象 店舗数") != "全業態":
        _fail("全業態 対象 stores group is 全業態")
    if monthly_element_label("てけてけ 対象 店舗数", "number")["group"] != "てけてけ":
        _fail("てけてけ 対象 stores group is てけてけ")
    if canonicalize_element_label("既存店 売上高(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("既存店 shortens")
    if canonicalize_element_label("既存店全店 売上高(％)", "yoy_index") != "既存 売上高(前年比)":
        _fail("既存店全店 売上高 stays 既存")
    if canonicalize_element_label("既存店全店 客単価(％)", "yoy_index") != "既存 客単価(前年比)":
        _fail("既存店全店 客単価 stays 既存")
    if canonicalize_element_label("既存店全店 店舗数", "number") != "既存 店舗数":
        _fail("既存店全店 店舗数 stays 既存")
    if canonicalize_element_label("既存店全店舗 客数", "yoy_index") != "既存 客数(前年比)":
        _fail("既存店全店舗 客数 stays 既存")
    if canonicalize_element_label("全店 売上高(％)", "yoy_index") != "全店 売上高(前年比)":
        _fail("全店 売上高 unchanged")
    if canonicalize_element_label("全店 店舗数", "number") != "全店 店舗数":
        _fail("全店 店舗数 unchanged")
    glued_scope = canonicalize_element_label("全店既存店合算(前年比)", "yoy_index")
    if "既存" not in glued_scope:
        _fail(f"全店既存店合算 keeps 既存: {glued_scope!r}")
    if parse_element_layers("全店既存店合算(前年比)")["scope"] != "既存":
        _fail("全店既存店合算 scope is 既存")
    if canonicalize_element_label("全社 売上高(前年比)", "yoy_index") != "全社 売上高(前年比)":
        _fail("全社 stays 全社 (not 全店)")
    if canonicalize_element_label("既存店計 単月", "yoy_index") != "既存 売上高(前年比)":
        _fail("Alpen 既存店計 単月 → 既存 売上高")
    if canonicalize_element_label("全店計 単月", "yoy_index") != "全店 売上高(前年比)":
        _fail("Alpen 全店計 単月 → 全店 売上高")
    if canonicalize_element_label("全社計 ※ 単月", "yoy_index") != "全社 売上高(前年比)":
        _fail("Alpen 全社計 ※ 単月 → 全社 売上高")
    if canonicalize_element_label("全店 売上高(1)", "yoy_index") != "全店 売上高(前年比)":
        _fail("SRS footnote (1) dropped")
    if (
        canonicalize_element_label("(2) ＊M&A除く 全店 売上高(前年比)", "yoy_index")
        != "＊M&A除く 全店 売上高(前年比)"
    ):
        _fail("SRS footnote (2) dropped")
    if canonicalize_element_label("全店 売上高①", "yoy_index") != "全店 売上高(前年比)":
        _fail("SRS circled footnote ① dropped")
    if (
        canonicalize_element_label("全店 売上高② ＊M&A除く", "yoy_index")
        != "＊M&A除く 全店 売上高(前年比)"
    ):
        _fail("SRS circled footnote ② dropped")
    if canonicalize_element_label("① 全店 売上高(前年比)", "yoy_index") != "全店 売上高(前年比)":
        _fail("SRS leading ① dropped")
    if (
        canonicalize_element_label("前年比 売上高 ※ 2026年（令和8年）度", "yoy_index")
        != "売上高(前年比)"
    ):
        _fail("Daikokuten 令和年度帯 dropped")
    if canonicalize_element_label("ゼビオ 単月 既存", "yoy_index") != "ゼビオ 既存 売上高(前年比)":
        _fail("Xevo 単月 既存 → ゼビオ 既存 売上高")
    if canonicalize_element_label("直営+FC 既存店 焼肉 売上高(前年比)", "yoy_index") != "焼肉 直営+FC 既存 売上高(前年比)":
        _fail("channel stays a layer")
    if canonicalize_element_label("売上高合計(直営・FC)", "number") != "直営+FC 売上高":
        _fail("71340 売上高合計(直営・FC) → 直営+FC 売上高")
    if canonicalize_element_label("直営・FC 全店 売上高", "number") != "直営+FC 全店 売上高":
        _fail("71340 直営・FC middle-dot → 直営+FC")
    if canonicalize_element_label("売上高合計(直営・FC)", "yoy_index") != "直営+FC 売上高(前年比)":
        _fail("71340 直営・FC yoy → 直営+FC 売上高(前年比)")
    if canonicalize_element_label("直営･FC 全店 売上高", "number") != "直営+FC 全店 売上高":
        _fail("71340 halfwidth middle-dot 直営･FC")
    if canonicalize_element_label("直営・ＦＣ 全店 売上高", "number") != "直営+FC 全店 売上高":
        _fail("71340 fullwidth ＦＣ with middle-dot")
    if canonicalize_element_label("国内FC店舗数", "number") != "日本 FC 店舗数":
        _fail("国内 to 日本, FC channel")
    if canonicalize_element_label("日本 合計 売上高(前年比)", "yoy_index") != "日本 売上高(前年比)":
        _fail("合計 on sales drops, no 全店 fill")
    if canonicalize_element_label("国内リテール 既存店 売上高(前年比)", "yoy_index") != "国内リテール 既存 売上高(前年比)":
        _fail("国内リテール stays one segment")
    if canonicalize_element_label("月末店舗数", "number") != "店舗数":
        _fail("月末店舗数 drops 月末")
    if canonicalize_element_label("期末店舗数", "number") != "店舗数":
        _fail("期末店舗数 drops 期末")
    if canonicalize_element_label("全店舗数", "number") != "店舗数":
        _fail("全店舗数 is 店舗数, not 全店 店舗数")
    if canonicalize_element_label("総店舗数", "number") != "店舗数":
        _fail("総店舗数 is 店舗数")
    if canonicalize_element_label("実店舗数", "number") != "実店舗 店舗数":
        _fail("実店舗数 keeps 実店舗 as segment")
    if canonicalize_element_label("実 店舗数", "number") != "実店舗 店舗数":
        _fail("実 leftover stitches to 実店舗")
    if canonicalize_element_label("実店舗 店舗数", "number") != "実店舗 店舗数":
        _fail("実店舗 店舗数 is idempotent")
    if canonicalize_element_label("稼働店舗数", "number") != "稼働店舗数":
        _fail("稼働店舗数 stays one metric")
    if canonicalize_element_label("稼働 店舗数", "number") != "稼働店舗数":
        _fail("稼働 leftover stitches to 稼働店舗数")
    if canonicalize_element_label("昨対増減率 全店 売上高", "yoy_index") != "全店 売上高(前年比)":
        _fail("昨対増減率 drops")
    if canonicalize_element_label("昨対増減率 既存 客数", "yoy_index") != "既存 客数(前年比)":
        _fail("昨対増減率 既存 客数")
    if canonicalize_element_label("昨年比 全店 売上高", "yoy_index") != "全店 売上高(前年比)":
        _fail("昨年比 drops")
    if canonicalize_element_label("内数 FC 既存 店舗数", "number") != "FC 既存 店舗数":
        _fail("内数 drops like うち")
    if canonicalize_element_label("内数 FC 稼働店舗数", "number") != "FC 稼働店舗数":
        _fail("内数 drops, 稼働店舗数 stays metric")
    if canonicalize_element_label("取引店舗数", "number") != "取引店舗数":
        _fail("取引店舗数 stays one token")
    active = parse_element_layers("店舗流通ネット 稼働店舗数 管理テナント数")
    if (
        active["segment"] != "店舗流通ネット"
        or active["metric"] != "稼働店舗数"
        or active["breakdown"] != "管理テナント数"
    ):
        _fail(f"稼働店舗数 peels 管理テナント数 {active}")
    if canonicalize_element_label(
        "店舗流通ネット 稼働店舗数 取引店舗数", "number",
    ) != "店舗流通ネット 稼働店舗数 取引店舗数":
        _fail("稼働店舗数 取引店舗数 is idempotent")
    if monthly_breakdown_depth("店舗流通ネット 稼働店舗数") != 0:
        _fail("稼働店舗数 parent depth")
    if monthly_breakdown_depth("店舗流通ネット 稼働店舗数 増加") != 1:
        _fail("稼働店舗数 child depth")
    if monthly_row_breakdown_rank("店舗流通ネット 稼働店舗数 減少") != 4:
        _fail("減少 breakdown rank")
    if monthly_element_label("店舗流通ネット 稼働店舗数", "number")["group"] != "店舗流通ネット":
        _fail("店舗流通ネット is the group")
    if canonicalize_element_label("LOWYA 対(前年比)", "yoy_index") != "LOWYA 売上高(前年比)":
        _fail("対 leftover is 売上高")
    if canonicalize_element_label("LOWYA対前年比", "yoy_index") != "LOWYA 売上高(前年比)":
        _fail("glued 対前年比 is 売上高")
    if canonicalize_element_label("LOWYA 売上高(前年比)", "yoy_index") != "LOWYA 売上高(前年比)":
        _fail("LOWYA 売上高 stays unlabeled")
    if canonicalize_element_label("100円ショップ 直営 全店(前年比)", "yoy_index") != "100円ショップ 直営 全店(前年比)":
        _fail("empty-metric 直営 全店 yoy stays")
    if canonicalize_element_label("直営店舗 全店 売上高", "number") != "直営 全店 売上高":
        _fail("直営店舗 全店 売上高 (9251)")
    if "舗" in canonicalize_element_label("直営店舗 全店 売上高", "number"):
        _fail("直営店舗 must not leave 舗")
    if canonicalize_element_label("直営店舗 売上高", "number") != "直営 売上高":
        _fail("直営店舗 売上高")
    if canonicalize_element_label("既存直営店舗 売上高", "number") != "直営 既存 売上高":
        _fail("既存直営店舗 売上高")
    if "舗" in canonicalize_element_label("既存直営店舗 売上高", "number"):
        _fail("既存直営店舗 must not leave 舗")
    if canonicalize_element_label("直営店 全店 売上高", "number") != "直営 全店 売上高":
        _fail("直営店 全店 売上高")
    if canonicalize_element_label("直営店 売上高", "number") != "直営 売上高":
        _fail("直営店 売上高")
    if canonicalize_element_label("既存直営店 売上高", "number") != "直営 既存 売上高":
        _fail("既存直営店 売上高")
    if canonicalize_element_label("直営店数", "number") != "直営 店舗数":
        _fail("直営店数")
    if canonicalize_element_label("焼肉 直営店舗数", "number") != "焼肉 直営 店舗数":
        _fail("直営店舗数 stays 直営 店舗数")
    if canonicalize_element_label("FC店舗 全店 売上高", "number") != "FC 全店 売上高":
        _fail("FC店舗 全店 売上高")
    if canonicalize_element_label("ＦＣ店舗 全店 売上高", "number") != "FC 全店 売上高":
        _fail("ＦＣ店舗 全店 売上高")
    if canonicalize_element_label("新規出店数", "number") != "出店":
        _fail("出店数 is 出店 metric, not 店舗数")
    if canonicalize_element_label("全店 新規出店数", "number") != "全店 出店":
        _fail("出店 keeps 全店")
    if canonicalize_element_label("トライアル 新規 全店 出店", "number") != "トライアル 全店 出店":
        _fail("出店 keeps 全店")
    if canonicalize_element_label("西友 全店 出店", "number") != "西友 全店 出店":
        _fail("西友 全店 出店 stays")
    if canonicalize_element_label("西友 業態転換数 全店", "number") != "西友 全店 業態転換数":
        _fail("業態転換数 is the metric")
    if canonicalize_element_label("出店店舗数", "number") != "出店":
        _fail("出店店舗数 is 出店")
    if canonicalize_element_label("100円ショップ 出店 店舗数", "number") != "100円ショップ 出店":
        _fail("出店 stays metric, not a segment")
    if canonicalize_element_label("100円ショップ 閉鎖 店舗数", "number") != "100円ショップ 退店":
        _fail("閉鎖 folds to 退店")
    if canonicalize_element_label("閉店", "number") != "退店":
        _fail("閉店 folds to 退店")
    if canonicalize_element_label("開店", "number") != "出店":
        _fail("開店 folds to 出店")
    if canonicalize_element_label("開店数", "number") != "出店":
        _fail("開店数 folds to 出店")
    if canonicalize_element_label("新規開設数", "number") != "出店":
        _fail("開設 folds to 出店")
    if canonicalize_element_label("開設", "number") != "出店":
        _fail("開設 token folds to 出店")
    if canonicalize_element_label("海外 転換出店数", "number") != "海外 転換出店":
        _fail("転換出店 stays distinct")
    if canonicalize_element_label("海外 転換 出店", "number") != "海外 転換出店":
        _fail("転換 leftover stitches to 転換出店")
    if canonicalize_element_label("海外 撤退数", "number") != "海外 退店":
        _fail("撤退 folds to 退店")
    if canonicalize_element_label("拠点数 調理済み商品お届け数(※)（千食）", "number") != "調理済み商品お届け数（千食）":
        _fail("拠点数 heading and ※ drop")
    if canonicalize_element_label("宅食 調理済み商品お届け数（千食）", "number") != "宅食 調理済み商品お届け数（千食）":
        _fail("宅食 お届け数 stays leftover + metric")
    if canonicalize_element_label("宅食調理済み商品お届け数（千食）", "number") != "宅食 調理済み商品お届け数（千食）":
        _fail("unglue 宅食 お届け数")
    meals = parse_element_layers("宅食 調理済み商品お届け数（千食）")
    if meals["segment"] != "宅食" or meals["metric"] != "調理済み商品お届け数（千食）" or meals["scope"]:
        _fail(f"parse お届け数 layers: {meals!r}")
    if monthly_row_segment("宅食 調理済み商品お届け数（千食）") != "宅食":
        _fail("gate leftover peels 調理済み商品お届け数")
    if canonicalize_element_label("宅食 営業所数", "number") != "宅食 営業所数":
        _fail("宅食 営業所数 stays leftover + metric")
    if canonicalize_element_label("宅食営業所数", "number") != "宅食 営業所数":
        _fail("unglue 宅食 営業所数")
    if canonicalize_element_label("宅食 営業所数合計", "number") != "宅食 営業所数":
        _fail("営業所数合計 drops 合計")
    offices = parse_element_layers("宅食 営業所数")
    if offices["segment"] != "宅食" or offices["metric"] != "営業所数" or offices["scope"]:
        _fail(f"parse 営業所数 layers: {offices!r}")
    if monthly_row_segment("宅食 営業所数") != "宅食":
        _fail("gate leftover peels 営業所数")
    if canonicalize_element_label("営業所数", "number") != "営業所数":
        _fail("bare 営業所数 stays metric without 全店")
    if canonicalize_element_label("退 店舗数", "number") != "退店":
        _fail("退 店舗数 stitches to 退店")
    if canonicalize_element_label("開 店舗数", "number") != "出店":
        _fail("開 店舗数 stitches to 出店")
    if canonicalize_element_label("閉 店舗数", "number") != "退店":
        _fail("閉 店舗数 stitches to 退店")
    if canonicalize_element_label("店舗 閉店", "number") != "退店":
        _fail("dummy 店舗 on 閉店 drops")
    if canonicalize_element_label("閉店数", "number") != "退店":
        _fail("閉店数 stays 退店 flow")
    if canonicalize_element_label("出店", "number") != "出店":
        _fail("出店 stays flow")
    status_store_cases = (
        ("転換準備による一時閉店中の店舗数", "転換準備による一時閉店中 店舗数"),
        ("転換準備による一時閉店中 店舗数", "転換準備による一時閉店中 店舗数"),
        ("閉店中店舗数", "閉店中 店舗数"),
        ("閉店予定店舗数", "閉店予定 店舗数"),
        ("一時閉店店舗数", "一時閉店 店舗数"),
        ("改装閉店中店舗数", "改装閉店中 店舗数"),
        ("開店準備中店舗数", "開店準備中 店舗数"),
        ("出店予定店舗数", "出店予定 店舗数"),
        ("閉鎖店舗数", "閉鎖店舗数"),
        ("休業中店舗数", "休業中 店舗数"),
    )
    for src, want in status_store_cases:
        got = canonicalize_element_label(src, "number")
        if got != want:
            _fail(f"status+店舗数 {src!r} → {got!r}, want {want!r}")
        if canonicalize_element_label(got, "number") != got:
            _fail(f"status+店舗数 not idempotent {src!r} → {got!r}")
        layers = parse_element_layers(src)
        if layers["metric"] != "店舗数":
            _fail(f"status+店舗数 metric {src!r}: {layers}")
    closed_mid = monthly_element_label("転換準備による一時閉店中の店舗数", "number")
    if (
        closed_mid["segment"] != "転換準備による一時閉店中"
        or closed_mid["metric"] != "店舗数"
        or closed_mid["metric_kind"] != "stores"
    ):
        _fail(f"一時閉店中の店舗数 stays stores: {closed_mid}")
    closed_stem = monthly_element_label("閉鎖店舗数", "number")
    if (
        closed_stem["segment"] != "閉鎖"
        or closed_stem["metric"] != "店舗数"
        or closed_stem["metric_kind"] != "stores"
    ):
        _fail(f"閉鎖店舗数 stays stores: {closed_stem}")
    if canonicalize_element_label("純増 店舗数", "number") != "純増 店舗数":
        _fail("純増 stays")
    if canonicalize_element_label("新設店舗", "number") != "新設店舗":
        _fail("新設 stays")
    if canonicalize_element_label("改装", "number") != "改装":
        _fail("改装 stays")
    if canonicalize_element_label("業態転換", "number") != "業態転換":
        _fail("業態転換 stays")
    if canonicalize_element_label("退店数", "number") != "退店":
        _fail("退店数")
    if canonicalize_element_label("中国 店舗数", "number") != "中国 店舗数":
        _fail("中国 stays a segment")
    if canonicalize_element_label("海外 中国 店舗数", "number") != "海外 中国 店舗数":
        _fail("海外 + 中国")
    if canonicalize_element_label("売上変化", "yoy_pct") != "売上変化(前年比)":
        _fail("売上変化 is not 売上高")
    if canonicalize_element_label("全店売上", "yoy_index") != "全店 売上高(前年比)":
        _fail("全店売上 endswith 売上")
    if canonicalize_element_label("既存店 単月売上", "yoy_pct") != "既存 売上高(前年比)":
        _fail("単月売上 drops 単月 time-axis")
    if canonicalize_element_label("全店 単月売上", "yoy_pct") != "全店 売上高(前年比)":
        _fail("全店 単月売上 drops 単月")
    if canonicalize_element_label("単月売上", "yoy_pct") != "売上高(前年比)":
        _fail("bare 単月売上 does not fill 全店")
    if canonicalize_element_label("単月 全店 売上高(前年比)", "yoy_pct") != "全店 売上高(前年比)":
        _fail("stored 単月 segment drops")
    if canonicalize_element_label("ゼビオ 単月 既存(前年比)", "yoy_index") != "ゼビオ 既存 売上高(前年比)":
        _fail("empty-metric 単月 with segment fills 売上高")
    if canonicalize_element_label("単月 既存(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("bare 単月 既存 fills 売上高 (Alpen; 累計列はパーサが落とす)")
    if (
        canonicalize_element_label("All stores 単月Monthly 全店 売上高(前年比)", "yoy_pct")
        != "All stores 全店 売上高(前年比)"
    ):
        _fail("unglue 単月Monthly then drop when metric set")
    if canonicalize_element_label("累計 店舗数", "number") != "店舗数":
        _fail("累計 time-axis drops")
    if canonicalize_element_label("運用戸数 単月増加", "number") != "運用戸数 単月増加":
        _fail("単月増加 stays one token")
    if canonicalize_element_label("既存店舗 客 数", "yoy_index") != "既存 客数(前年比)":
        _fail("客 数 shards stitch to 客数")
    if canonicalize_element_label("売上 （％） 既存店", "yoy_index") != "既存 売上高(前年比)":
        _fail("（％） unit noise drops")
    if canonicalize_element_label("（％） 全店 売上高(前年比)", "yoy_index") != "全店 売上高(前年比)":
        _fail("leading （％） drops")
    if canonicalize_element_label("店舗状況 店舗数", "number") != "店舗数":
        _fail("店舗状況 section drops")
    if canonicalize_element_label("店舗状況 （内 出店", "number") != "出店":
        _fail("broken inner open stitches to 出店")
    if canonicalize_element_label("全店", "yoy_index") != "全店 売上高(前年比)":
        _fail("bare scope yoy fills 売上高")
    if canonicalize_element_label("既存(前年比)", "yoy_index") != "既存 売上高(前年比)":
        _fail("bare 既存 yoy fills 売上高")
    if canonicalize_element_label("直営 全店(前年比)", "yoy_index") != "直営 全店(前年比)":
        _fail("channel+scope empty metric stays")
    if canonicalize_element_label("客数 既存(前年比)", "yoy_index") != "既存 客数(前年比)":
        _fail("客数 既存 yoy stays 客数 metric")
    if canonicalize_element_label("会員数 既存(前年比)", "yoy_index") != "会員数 既存(前年比)":
        _fail("会員数 既存 yoy does not fill 売上高")
    if canonicalize_element_label("会員数(前年比)", "yoy_index") != "会員数(前年比)":
        _fail("bare 会員数 yoy stays")
    if canonicalize_element_label("会員数 既存", "number") != "会員数 既存":
        _fail("会員数 既存 number stays")
    if canonicalize_element_label("既存店会員数前年同月比", "yoy_index") != "会員数 既存(前年比)":
        _fail("既存店会員数 yoy is 会員数 既存, not 売上高")
    twice = canonicalize_element_label("焼肉 直営+FC 既存 売上高(前年比)", "yoy_index")
    if twice != "焼肉 直営+FC 既存 売上高(前年比)":
        _fail("canonicalize idempotent")
    if canonicalize_element_label("全店 内)ドラッグ店舗", "yoy_index") != "全店 売上高 ドラッグ(前年比)":
        _fail("内)ドラッグ店舗 peels to 内訳")
    if canonicalize_element_label("MKG ドラッグ 全店 売上高(前年比)", "yoy_index") != "MKG 全店 売上高 ドラッグ(前年比)":
        _fail("leftover ドラッグ moves after 売上高")
    if canonicalize_element_label("MKG 全店 売上高 ドラッグ(前年比)", "yoy_index") != "MKG 全店 売上高 ドラッグ(前年比)":
        _fail("内訳 canonicalize is idempotent")
    if canonicalize_element_label("（内 物販） 既存 売上高(前年比)", "yoy_index") != "既存 売上高 物販(前年比)":
        _fail("（内 物販） peels to 内訳")
    if canonicalize_element_label("内)調剤店舗 全店 売上高", "number") != "全店 売上高 調剤":
        _fail("内)調剤店舗 peels to 内訳")
    if canonicalize_element_label("調剤事業 全店 売上高(前年比)", "yoy_index") != "調剤事業 全店 売上高(前年比)":
        _fail("調剤事業 stays a segment")
    if canonicalize_element_label("内アパレル 全店 売上高(前年比)", "yoy_index") != "内アパレル 全店 売上高(前年比)":
        _fail("内アパレル stays a segment")
    if canonicalize_element_label("グロース領域 売上高 プロパティ", "number") != "グロース領域 売上高 プロパティ":
        _fail("グロース領域 売上高 プロパティ peels to 内訳")
    if canonicalize_element_label("グロース領域 売上高 ホテル", "number") != "グロース領域 売上高 ホテル":
        _fail("グロース領域 売上高 ホテル peels to 内訳")
    area = parse_element_layers("グロース領域 売上高 ホテル")
    if (
        area["segment"] != "グロース領域"
        or area["scope"]
        or area["metric"] != "売上高"
        or area["breakdown"] != "ホテル"
    ):
        _fail(f"parse area 内訳 layers: {area!r}")
    if canonicalize_element_label("グロース領域 全店 売上高 インカム、太陽光", "number") != "グロース領域 全店 売上高 インカム太陽光":
        _fail("インカム、太陽光 aliases to インカム太陽光")
    if canonicalize_element_label("サステナブル領域 売上高 その他", "number") != "サステナブル領域 売上高 その他":
        _fail("サステナブル領域 その他 peels to 内訳")
    if canonicalize_element_label("サステナブル領域 全店 売上高 アパレル・雑貨", "number") != "サステナブル領域 全店 売上高 アパレル雑貨":
        _fail("アパレル・雑貨 aliases to アパレル雑貨")
    if canonicalize_element_label("グロース領域 売上高 ホテル", "yoy_index") != "グロース領域 売上高 ホテル(前年比)":
        _fail("area 内訳 yoy suffix")
    if canonicalize_element_label("グロース領域 全店 ホテル", "yoy_index") != "グロース領域 全店 売上高 ホテル(前年比)":
        _fail("area 内訳 empty-metric yoy peels")
    if canonicalize_element_label("ホテル 全店 売上高", "number") != "ホテル 全店 売上高":
        _fail("ホテル without parent stays segment")
    if canonicalize_element_label("その他 全店 売上高", "number") != "その他 全店 売上高":
        _fail("その他 without parent stays segment")
    area_twice = canonicalize_element_label("グロース領域 全店 売上高 ホテル", "number")
    if area_twice != canonicalize_element_label(area_twice, "number"):
        _fail("area 内訳 canonicalize is idempotent")
    if len(canonicalize_element_label("サステナブル領域 売上高 アパレル・雑貨", "yoy_index")) > 50:
        _fail("area 内訳 name exceeds 50")
    if canonicalize_element_label("連結売上高", "number") != "連結売上高":
        _fail("連結売上高 is a metric, not a segment")
    if canonicalize_element_label("連結 売上高", "number") != "連結売上高":
        _fail("連結 売上高 glues to 連結売上高")
    if canonicalize_element_label("連結 全店 売上高", "number") != "全店 連結売上高":
        _fail("連結 全店 売上高 → 全店 連結売上高")
    if canonicalize_element_label("連結売上高", "yoy_index") != "連結売上高(前年比)":
        _fail("連結売上高 yoy stays unlabeled")
    if canonicalize_element_label("連結 全店", "yoy_index") != "全店 連結売上高(前年比)":
        _fail("連結 全店 yoy fills 連結売上高")
    if canonicalize_element_label("クラウド事業 全店 売上高", "number") != "クラウド事業 全店 売上高":
        _fail("named leftover stays a segment")
    if canonicalize_element_label("連結営業利益", "number") != "連結営業利益":
        _fail("連結営業利益 is a metric")
    if canonicalize_element_label("連結 営業収益", "number") != "連結営業収益":
        _fail("連結 営業収益 glues to a metric")
    if canonicalize_element_label("連結 顧客口座数", "number") != "連結顧客口座数":
        _fail("連結 leftover KPI is a metric")
    if canonicalize_element_label("CPAソリューション事業 取扱高", "number") != "CPAソリューション事業 取扱高":
        _fail("取扱高 is a metric, no 全店 fill")
    if canonicalize_element_label("CPAソリューション事業取扱高", "number") != "CPAソリューション事業 取扱高":
        _fail("unglue 取扱高")
    if canonicalize_element_label("CPAソリューション事業 取扱高(前年比)", "yoy_index") != "CPAソリューション事業 取扱高(前年比)":
        _fail("取扱高 yoy keeps leftover")
    if canonicalize_element_label("取扱高", "number") != "取扱高":
        _fail("bare 取扱高 stays metric without 全店")
    if canonicalize_element_label("戦略事業 取扱高", "number") != "戦略事業 取扱高":
        _fail("named leftover + 取扱高")
    if canonicalize_element_label("連結 取扱高", "number") != "連結取扱高":
        _fail("連結 取扱高 glues like 連結営業収益")
    gmv = parse_element_layers("CPAソリューション事業 取扱高")
    if gmv["segment"] != "CPAソリューション事業" or gmv["metric"] != "取扱高" or gmv["scope"]:
        _fail(f"parse 取扱高 layers: {gmv!r}")
    if monthly_row_segment("CPAソリューション事業 取扱高") != "CPAソリューション事業":
        _fail("gate leftover peels 取扱高")
    if canonicalize_element_label("ホテル 客室収入", "number") != "ホテル 客室収入":
        _fail("客室収入 is a metric, no 全店 fill")
    if canonicalize_element_label("ホテル客室収入", "number") != "ホテル 客室収入":
        _fail("unglue 客室収入")
    if canonicalize_element_label("ホテル 客室収入(前年比)", "yoy_index") != "ホテル 客室収入(前年比)":
        _fail("客室収入 yoy keeps leftover")
    if canonicalize_element_label("客室収入", "number") != "客室収入":
        _fail("bare 客室収入 stays metric without 全店")
    room = parse_element_layers("ホテル 客室収入")
    if room["segment"] != "ホテル" or room["metric"] != "客室収入" or room["scope"]:
        _fail(f"parse 客室収入 layers: {room!r}")
    if monthly_row_segment("ホテル 客室収入") != "ホテル":
        _fail("gate leftover peels 客室収入")
    if monthly_row_segment("ホテル 客室収入(前年比)") != "ホテル":
        _fail("gate leftover peels 客室収入 yoy")
    if canonicalize_element_label("ASKUL事業 購入お客様 客単価(前年比)", "yoy_index") != "ASKUL事業 客単価(前年比)":
        _fail("購入お客様 drops, 客単価 stays unlabeled")
    if canonicalize_element_label("ASKUL事業 購入お客様 全店 客単価(前年比)", "yoy_index") != "ASKUL事業 全店 客単価(前年比)":
        _fail("購入お客様 already-scoped 客単価")
    if canonicalize_element_label("ASKUL事業 購入お客様数(前年比)", "yoy_index") != "ASKUL事業 購入お客様数(前年比)":
        _fail("購入お客様数 stays metric")
    if canonicalize_element_label("BtoB事業 購入お客様数(前年比)", "yoy_index") != "BtoB事業 購入お客様数(前年比)":
        _fail("BtoB 購入お客様数 stays metric")
    if canonicalize_element_label("ASKUL事業 稼働日修正後(前年比)", "yoy_index") != "ASKUL事業 稼働日修正後(前年比)":
        _fail("稼働日修正後 is a metric, no 全店 fill")
    if canonicalize_element_label("ASKUL事業稼働日修正後", "yoy_index") != "ASKUL事業 稼働日修正後(前年比)":
        _fail("unglue 稼働日修正後")
    kado = parse_element_layers("ASKUL事業 稼働日修正後")
    if kado["segment"] != "ASKUL事業" or kado["metric"] != "稼働日修正後" or kado["scope"]:
        _fail(f"parse 稼働日修正後 layers: {kado!r}")
    if monthly_row_segment("ASKUL事業 稼働日修正後(前年比)") != "ASKUL事業":
        _fail("gate leftover peels 稼働日修正後")
    if monthly_row_segment("ASKUL事業 購入お客様数(前年比)") != "ASKUL事業":
        _fail("gate leftover peels 購入お客様数")
    if monthly_row_segment("ASKUL事業 客単価(前年比)") != "ASKUL事業":
        _fail("gate leftover peels 客単価")
    if canonicalize_element_label("内 MM 全店 売上高", "number") != "内 MM 全店 売上高":
        _fail("内 MM stays leftover, not 内訳")
    if canonicalize_element_label("店舗状況 （内 出店", "number") != "出店":
        _fail("（内 出店 stays flow, not 内訳")
    layers = parse_element_layers("MKG ドラッグ 全店 売上高")
    if layers["segment"] != "MKG" or layers["breakdown"] != "ドラッグ" or layers["metric"] != "売上高":
        _fail(f"parse 内訳 layers: {layers!r}")
    if monthly_row_segment("MKG 全店 売上高 ドラッグ(前年比)") != "MKG":
        _fail("gate leftover stays MKG after 内訳 peel")
    if monthly_row_breakdown_rank("MKG 全店 売上高(前年比)") != 0:
        _fail("parent 売上高 bd rank 0")
    if monthly_row_breakdown_rank("MKG 全店 売上高 ドラッグ(前年比)") != 1:
        _fail("ドラッグ bd rank 1")
    if monthly_row_breakdown_rank("MKG 全店 売上高 調剤(前年比)") != 2:
        _fail("調剤 bd rank 2")
    if monthly_row_breakdown_rank("既存 売上高 物販(前年比)") != 3:
        _fail("物販 bd rank 3")
    if monthly_row_unit_rank("number") != 0:
        _fail("number unit rank 0")
    if monthly_row_unit_rank("yoy_index") != 1:
        _fail("yoy unit rank 1")
    if monthly_row_unit_rank("yoy_pct") != 1:
        _fail("yoy_pct unit rank 1")
    if monthly_row_breakdown_rank("グロース領域 全店 売上高") != 0:
        _fail("area parent bd rank 0")
    if monthly_row_breakdown_rank("グロース領域 全店 売上高 プロパティ") != 1:
        _fail("プロパティ bd rank 1")
    if monthly_row_breakdown_rank("グロース領域 全店 売上高 ホテル") != 2:
        _fail("ホテル bd rank 2")
    if monthly_breakdown_depth("グロース領域 全店 売上高") != 0:
        _fail("area parent depth 0")
    if monthly_breakdown_depth("グロース領域 全店 売上高 プロパティ") != 1:
        _fail("プロパティ depth 1")
    if monthly_breakdown_depth("グロース領域 全店 売上高 ホテル") != 2:
        _fail("ホテル depth 2")
    if monthly_breakdown_depth("グロース領域 全店 売上高 インカム太陽光") != 2:
        _fail("インカム太陽光 depth 2")
    if monthly_breakdown_depth("MKG 全店 売上高 ドラッグ") != 1:
        _fail("ドラッグ depth 1")
    if monthly_series_kind("全店 売上高 ドラッグ(前年比)", "yoy_index") is not None:
        _fail("会社 ドラッグ must not be TOP 全店")
    if monthly_series_kind("MKG 全店 売上高 ドラッグ(前年比)", "yoy_index") is not None:
        _fail("ブランド 内訳 is not a series")
    if monthly_series_kind("グロース領域 全店 売上高 ホテル(前年比)", "yoy_index") is not None:
        _fail("area 内訳 yoy is not a series")
    if monthly_series_kind("MKG 全店 売上高(前年比)", "yoy_index") != "all_store":
        _fail("ブランド 売上高 parent stays all_store")
    bd_order = [row["element"] for row in union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "MKG 全店 売上高 調剤(前年比)", "unit": "yoy_index"},
            {"element": "MKG 全店 売上高 物販(前年比)", "unit": "yoy_index"},
            {"element": "MKG ドラッグ 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "MKG 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "MKG 既存 売上高(前年比)", "unit": "yoy_index"},
        ],
    }])]
    if bd_order != [
        "MKG 全店 売上高(前年比)",
        "MKG 全店 売上高 ドラッグ(前年比)",
        "MKG 全店 売上高 調剤(前年比)",
        "MKG 全店 売上高 物販(前年比)",
        "MKG 既存 売上高(前年比)",
    ]:
        _fail(f"売上高 内訳 order: {bd_order!r}")
    area_order = [row["element"] for row in union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "グロース領域 全店 売上高 専門通販", "unit": "number"},
            {"element": "グロース領域 全店 売上高 ホテル", "unit": "number"},
            {"element": "グロース領域 全店 売上高 化粧品健康食品", "unit": "number"},
            {"element": "グロース領域 全店 売上高", "unit": "number"},
            {"element": "グロース領域 全店 売上高 インカム太陽光", "unit": "number"},
            {"element": "グロース領域 全店 売上高 プロパティ", "unit": "number"},
        ],
    }])]
    if area_order != [
        "グロース領域 全店 売上高",
        "グロース領域 全店 売上高 プロパティ",
        "グロース領域 全店 売上高 ホテル",
        "グロース領域 全店 売上高 インカム太陽光",
        "グロース領域 全店 売上高 専門通販",
        "グロース領域 全店 売上高 化粧品健康食品",
    ]:
        _fail(f"area 小枝 order: {area_order!r}")
    mixed_area = [row["element"] for row in union_monthly_elements([{
        "year": 2026,
        "elements": [
            {"element": "グロース領域 全店 売上高 ホテル", "unit": "number"},
            {"element": "グロース領域 全店 売上高 ホテル(前年比)", "unit": "yoy_index"},
            {"element": "グロース領域 全店 売上高 プロパティ", "unit": "number"},
            {"element": "グロース領域 全店 売上高 プロパティ(前年比)", "unit": "yoy_index"},
            {"element": "グロース領域 全店 売上高", "unit": "number"},
            {"element": "グロース領域 全店 売上高(前年比)", "unit": "yoy_index"},
            {"element": "グロース領域 全店 売上高 インカム太陽光(前年比)", "unit": "yoy_index"},
            {"element": "グロース領域 全店 売上高 インカム太陽光", "unit": "number"},
        ],
    }])]
    if mixed_area != [
        "グロース領域 全店 売上高",
        "グロース領域 全店 売上高 プロパティ",
        "グロース領域 全店 売上高 ホテル",
        "グロース領域 全店 売上高 インカム太陽光",
        "グロース領域 全店 売上高(前年比)",
        "グロース領域 全店 売上高 プロパティ(前年比)",
        "グロース領域 全店 売上高 ホテル(前年比)",
        "グロース領域 全店 売上高 インカム太陽光(前年比)",
    ]:
        _fail(f"area number then yoy: {mixed_area!r}")
    if same_yoy_element("売上高", "yoy_index", "全店 売上高(前年比)", "yoy_index"):
        _fail("売上高 yoy does not fold into 全店 売上高")
    if not same_yoy_element("売上高", "yoy_index", "売上高(前年比)", "yoy_index"):
        _fail("short yoy name matches unlabeled canonical")
    if not same_yoy_element("カラオケ全店 売上高", "yoy_index", "カラオケ 全店 売上高(前年比)", "yoy_index"):
        _fail("karaoke short matches canonical")
    if same_yoy_element("売上高", "number", "売上高(前年比)", "number"):
        _fail("number 売上高 is not the yoy row")
    if same_yoy_element("売上高", "number", "売上高(前年比)", "yoy_index"):
        _fail("number 売上高 stays separate from yoy")
    planned = plan_yoy_duplicate_merges([
        ("60590", 2026, "カラオケ全店 売上高", "yoy_index"),
        ("60590", 2026, "カラオケ全店 売上高(前年比)", "yoy_index"),
        ("72030", 2024, "売上高", "number"),
        ("72030", 2024, "売上高(前年比)", "number"),
        ("44440", 2025, "売上高(前年比)", "yoy_index"),
        ("44440", 2025, "売上高", "number"),
        ("44440", 2025, "売上高前年同月比", "number"),
        ("29260", 2026, "全店 売上高前期比", "yoy_index"),
        ("29260", 2026, "店舗数", "number"),
        ("29260", 2026, "売上高", "number"),
    ])
    if planned != [
        ("29260", 2026, "全店 売上高(前年比)", ["全店 売上高前期比"]),
        ("44440", 2025, "売上高", ["売上高前年同月比"]),
        ("60590", 2026, "カラオケ 全店 売上高(前年比)",
         ["カラオケ全店 売上高", "カラオケ全店 売上高(前年比)"]),
        ("72030", 2024, "売上高", ["売上高(前年比)"]),
    ]:
        _fail(f"plan_yoy_duplicate_merges: {planned!r}")


def test_issuer_alias_cases() -> None:
    cases = plan_issuer_alias_cases()
    if not cases:
        _fail("issuer_aliases.json の rename が空")
    from app.monthly.ingest import ISSUER_ELEMENT_ALIASES
    codes = {code for code, _, _, _ in cases}
    missing = sorted(set(ISSUER_ELEMENT_ALIASES) - codes)
    if missing:
        _fail(f"rename が無い銘柄: {missing!r}")
    for code, incoming, stored, ctx in cases:
        got = apply_issuer_aliases(
            code, incoming, year=ctx.get("year"), as_of=ctx.get("as_of"),  # type: ignore[arg-type]
        )
        if got != stored:
            _fail(f"{code} {incoming!r} {ctx} → {got!r}, want {stored!r}")
        if apply_issuer_aliases("72030", incoming, year=ctx.get("year"), as_of=ctx.get("as_of")) != incoming:  # type: ignore[arg-type]
            _fail(f"other issuer must not alias {incoming!r}")

    sample_34150 = apply_issuer_aliases(
        "34150", canonicalize_element_label("全社 国内 合計 売上高(前年比)", "yoy_index"), year=2099,
    )
    if sample_34150 != "日本 売上高(前年比)":
        _fail(f"34150 sample: {sample_34150!r}")
    sample_26980 = apply_issuer_aliases(
        "26980", canonicalize_element_label("全社(前年比)", "yoy_index"), year=2099,
    )
    if sample_26980 != "全社 売上高(前年比)":
        _fail(f"26980 sample: {sample_26980!r}")

    # 262A0: 2025 無印月次 → Zoff。2026 多セグメントはメガネスーパー等を触らない。
    sample_262a0_2025 = apply_issuer_aliases("262A0", "全店", year=2025)
    if sample_262a0_2025 != "Zoff 全店":
        _fail(f"262A0 year=2025 全店: {sample_262a0_2025!r}")
    sample_262a0_2026_mega = apply_issuer_aliases("262A0", "メガネスーパー 全店", year=2026)
    if sample_262a0_2026_mega != "メガネスーパー 全店":
        _fail(f"262A0 year=2026 メガネスーパー: {sample_262a0_2026_mega!r}")
    sample_262a0_2026_bare = apply_issuer_aliases("262A0", "全店", year=2026)
    if sample_262a0_2026_bare != "全店":
        _fail(f"262A0 year=2026 must not force-prefix 全店: {sample_262a0_2026_bare!r}")

    # 27800: 売上高 に コメ兵 prefix を付けない。canonicalize 済みの全店ラベルはそのまま。
    sample_27800 = apply_issuer_aliases("27800", "全店 売上高", year=2026)
    if sample_27800 != "全店 売上高":
        _fail(f"27800 year=2026 全店 売上高: {sample_27800!r}")
    sample_27800_yoy = apply_issuer_aliases("27800", "全店 売上高(前年比)", year=2026)
    if sample_27800_yoy != "全店 売上高(前年比)":
        _fail(f"27800 year=2026 全店 売上高(前年比): {sample_27800_yoy!r}")

    # 76830: 内 MM → 内アパレル（Lane B / re-ingest）。canonicalize 後キーで照合。
    sample_76830 = apply_issuer_aliases(
        "76830", canonicalize_element_label("内 MM 全店 売上高", "number"), year=2026,
    )
    sample_76830 = canonicalize_element_label(sample_76830, "number")
    if sample_76830 != "内アパレル 全店 売上高":
        _fail(f"76830 内 MM 全店 売上高: {sample_76830!r}")
    sample_35570 = apply_issuer_aliases(
        "35570",
        canonicalize_element_label("てけてけ業態 既存 売上高(前年比)", "yoy_index"),
        year=2099,
    )
    if sample_35570 != "てけてけ 既存 売上高(前年比)":
        _fail(f"35570 てけてけ業態: {sample_35570!r}")
    sample_35570_stores = apply_issuer_aliases(
        "35570",
        canonicalize_element_label("the 3rd Burger業態 対象 既存 店舗数", "number"),
        year=2099,
    )
    if sample_35570_stores != "the 3rd Burger 既存 店舗数":
        _fail(f"35570 対象店舗数: {sample_35570_stores!r}")
    sample_76830_yoy = apply_issuer_aliases(
        "76830", canonicalize_element_label("内 MM 全店 売上高(前年比)", "yoy_index"), year=2026,
    )
    sample_76830_yoy = canonicalize_element_label(sample_76830_yoy, "yoy_index")
    if sample_76830_yoy != "内アパレル 全店 売上高(前年比)":
        _fail(f"76830 内 MM 全店 売上高(前年比): {sample_76830_yoy!r}")

    # 71340: 壊れた ・ FC ラベル → 直営+FC（Lane B / 既存格納・再ingest）。
    for incoming, stored, unit in (
        ("・ FC 全店 売上高", "直営+FC 全店 売上高", "number"),
        ("・ FC(前年比)", "直営+FC 全店 売上高(前年比)", "yoy_index"),
        ("・ FC 全店(前年比)", "直営+FC 全店 売上高(前年比)", "yoy_index"),
        ("・ FC 全店 売上高(前年比)", "直営+FC 全店 売上高(前年比)", "yoy_index"),
    ):
        got = apply_issuer_aliases(
            "71340", canonicalize_element_label(incoming, unit), year=2099,
        )
        if got != stored:
            _fail(f"71340 year=2099 {incoming!r}: {got!r}, want {stored!r}")

    # 30880: FC は別表（FC 単月売上高）で 全店 を付けない。合計表の FC も同じ数字なので寄せる。子会社化等は空白入り
    for incoming, stored, unit in (
        ("MKG FC 売上高前年比", "マツモトキヨシグループ FC 売上高(前年比)", "yoy_pct"),
        ("合計 FC 売上高前年比", "マツモトキヨシグループ FC 売上高(前年比)", "yoy_pct"),
        ("MKG FC 全店 売上高(前年比)", "マツモトキヨシグループ FC 売上高(前年比)", "yoy_index"),
        ("マツモトキヨシグループ FC 全店 売上高(前年比)", "マツモトキヨシグループ FC 売上高(前年比)", "yoy_index"),
        ("マツモトキヨシグループ FC 売上高(前年比)", "マツモトキヨシグループ FC 売上高(前年比)", "yoy_index"),
        ("グループ子会社化等", "グループ 子会社化等", "number"),
        ("グループ 子会社化等", "グループ 子会社化等", "number"),
    ):
        got = canonicalize_element_label(
            apply_issuer_aliases("30880", canonicalize_element_label(incoming, unit), year=2026), unit,
        )
        if got != stored:
            _fail(f"30880 {incoming!r}: {got!r}, want {stored!r}")

    # 46660: PDF の生ラベル「直営 タイムズパーキング 件数/台数」は 直営 なしの保存名へ寄せる。
    # ■タイムズパーキング数 の「累計」も同じ保存名のまま
    for incoming, stored in (
        ("直営 タイムズパーキング 件数", "タイムズパーキング 件数"),
        ("直営 タイムズパーキング 台数", "タイムズパーキング 台数"),
        ("タイムズパーキング 累計 件数", "タイムズパーキング 件数"),
    ):
        got = canonicalize_element_label(
            apply_issuer_aliases("46660", canonicalize_element_label(incoming, "number"), year=2026), "number",
        )
        if got != stored:
            _fail(f"46660 {incoming!r}: {got!r}, want {stored!r}")

    # 38170: 株式会社SRA/AIT → 短名（year=2026）。短名からは戻さない。
    for incoming, stored in (
        ("株式会社SRA 全店 売上高", "SRA 全店 売上高"),
        ("株式会社SRA 全店 売上高(前年比)", "SRA 全店 売上高(前年比)"),
        ("株式会社AIT 全店 売上高", "AIT 全店 売上高"),
        ("株式会社AIT 全店 売上高(前年比)", "AIT 全店 売上高(前年比)"),
    ):
        unit = "yoy_index" if incoming.endswith("(前年比)") else "number"
        got = apply_issuer_aliases(
            "38170", canonicalize_element_label(incoming, unit), year=2026,
        )
        if got != stored:
            _fail(f"38170 year=2026 {incoming!r}: {got!r}, want {stored!r}")
        reverse = apply_issuer_aliases(
            "38170", canonicalize_element_label(stored, unit), year=2026,
        )
        if reverse != stored:
            _fail(f"38170 year=2026 reverse {stored!r}: {reverse!r}")

    # 30820: 中黒+半角スペースの「モール・郊外型 レストラン業態」へ。HTML の業態行（既存店見出しなし）は既存へ、店舗数はスコープなし。
    mall = "モール・郊外型 レストラン業態"
    urban = "都市型ダイニング業態"
    p_lines_30820 = (
        (" 全店 売上高(前年比)", " 全店 売上高(前年比)"),
        (" 全店 客数(前年比)", " 全店 客数(前年比)"),
        (" 全店 客単価(前年比)", " 全店 客単価(前年比)"),
        (" 全店 店舗数", " 全店 店舗数"),
        (" 既存 売上高(前年比)", " 既存 売上高(前年比)"),
        (" 既存 客数(前年比)", " 既存 客数(前年比)"),
        (" 既存 客単価(前年比)", " 既存 客単価(前年比)"),
        (" 売上高(前年比)", " 既存 売上高(前年比)"),
        (" 客数(前年比)", " 既存 客数(前年比)"),
        (" 客単価(前年比)", " 既存 客単価(前年比)"),
        (" 店舗数", " 店舗数"),
        (" 既存 店舗数", " 店舗数"),
        ("", ""),
    )
    pairs_30820 = []
    for src_p, dst_p in (
        ("モール郊外型", mall),
        ("モール郊外型レストラン業態", mall),
        ("モール・郊外型レストラン業態", mall),
        ("都市型ダイニング", urban),
    ):
        for src_suf, dst_suf in p_lines_30820:
            pairs_30820.append((src_p + src_suf, dst_p + dst_suf if dst_suf else dst_p))
    for prefix in (mall, urban):
        pairs_30820.extend((
            (f"{prefix} 売上高(前年比)", f"{prefix} 既存 売上高(前年比)"),
            (f"{prefix} 客数(前年比)", f"{prefix} 既存 客数(前年比)"),
            (f"{prefix} 客単価(前年比)", f"{prefix} 既存 客単価(前年比)"),
            (f"{prefix} 既存 店舗数", f"{prefix} 店舗数"),
        ))
    for incoming, stored in pairs_30820:
        unit = "yoy_index" if "(前年比)" in incoming else "number"
        got = apply_issuer_aliases(
            "30820", canonicalize_element_label(incoming, unit), year=2026,
        )
        got = canonicalize_element_label(got, unit)
        if got != stored:
            _fail(f"30820 year=2026 {incoming!r}: {got!r}, want {stored!r}")
        dst_unit = "yoy_index" if "(前年比)" in stored else "number"
        reverse = apply_issuer_aliases(
            "30820", canonicalize_element_label(stored, dst_unit), year=2026,
        )
        reverse = canonicalize_element_label(reverse, dst_unit)
        if reverse != stored:
            _fail(f"30820 year=2026 reverse {stored!r}: {reverse!r}")
    for incoming, unit, stored in (
        ("既存店 モール・郊外型レストラン業態 売上高", "yoy_index", f"{mall} 既存 売上高(前年比)"),
        ("モール・郊外型 レストラン業態 売上高(％)", "yoy_index", f"{mall} 既存 売上高(前年比)"),
        ("既存店 モール・郊外型レストラン業態 店舗数", "number", f"{mall} 店舗数"),
        ("都市型ダイニング業態 客数(％)", "yoy_index", f"{urban} 既存 客数(前年比)"),
        ("都市型ダイニング業態 店舗数", "number", f"{urban} 店舗数"),
        (f"{mall} 全店 店舗数", "number", f"{mall} 全店 店舗数"),
        ("全店 売上高(前年比)", "yoy_index", "全店 売上高(前年比)"),
        ("全店 店舗数", "number", "全店 店舗数"),
    ):
        got = apply_issuer_aliases(
            "30820", canonicalize_element_label(incoming, unit), year=2026,
        )
        got = canonicalize_element_label(got, unit)
        if got != stored:
            _fail(f"30820 raw {incoming!r}: {got!r}, want {stored!r}")


def test_mapping_examples() -> None:
    layout, mapping = plan_column_years(GIFT_MONTHS, 10, date(2026, 8, 5))
    if layout != "fiscal":
        _fail(f"gift layout: {layout}")
    expect = {11: 2025, 12: 2025, 1: 2026, 2: 2026, 3: 2026, 4: 2026, 5: 2026, 6: 2026, 7: 2026}
    if _map_dict(mapping) != expect:
        _fail(f"gift mapping: {mapping}")

    fy, layout, mapping = map_pdf_columns(
        GIFT_MONTHS,
        [
            (date(2026, 5, 8), date(2025, 10, 31)),
            (date(2025, 5, 9), date(2024, 10, 31)),
        ],
        date(2026, 8, 5),
    )
    if fy != 10 or layout != "fiscal" or _map_dict(mapping) != expect:
        _fail(f"gift map_pdf_columns: {fy} {layout} {mapping}")

    layout, mapping = plan_column_years(MARCH_MONTHS, 3, date(2026, 4, 10))
    if layout != "fiscal":
        _fail(f"march layout: {layout}")
    # April is fiscal start after March-end → completed FY（4/2025…3/2026）
    expect = {m: 2025 for m in range(4, 13)}
    expect.update({1: 2026, 2: 2026, 3: 2026})
    if _map_dict(mapping) != expect:
        _fail(f"march mapping: {mapping}")

    layout, mapping = plan_column_years(CAL_MONTHS, 12, date(2027, 1, 10))
    if layout != "calendar":
        _fail(f"dec layout: {layout}")
    if _map_dict(mapping) != {m: 2026 for m in CAL_MONTHS}:
        _fail(f"dec mapping: {mapping}")

    # 先頭が1でも会計年度開始でもない列。暦年は最終列 + as_of。拒否しない。
    as_of_aug = date(2026, 8, 5)
    layout, mapping = plan_column_years([7], 10, as_of_aug)
    if layout != "partial":
        _fail(f"single July layout: {layout}")
    if _map_dict(mapping) != {7: 2026}:
        _fail(f"single July mapping: {mapping}")

    layout, mapping = plan_column_years([7, 8], 3, as_of_aug)
    if layout != "partial":
        _fail(f"mid-start layout: {layout}")
    if _map_dict(mapping) != {7: 2026, 8: 2026}:
        _fail(f"mid-start mapping: {mapping}")

    fy, layout, mapping = map_pdf_columns(
        [7],
        [(date(2026, 5, 8), date(2025, 10, 31))],
        as_of_aug,
    )
    if fy != 10 or layout != "partial" or _map_dict(mapping) != {7: 2026}:
        _fail(f"single July map_pdf_columns: {fy} {layout} {mapping}")

    # Genky 13列: 月6が2回。右端 as_of=2025-06-30 → 先頭6は2024、末尾6は2025。
    layout, mapping = plan_column_years(GENKY_MONTHS, 6, date(2025, 6, 30))
    if layout != "partial":
        _fail(f"genky layout: {layout}")
    expect_years = [2024] * 7 + [2025] * 6
    got_years = [y for _, y in mapping]
    if [m for m, _ in mapping] != GENKY_MONTHS or got_years != expect_years:
        _fail(f"genky mapping: {mapping}")
    if mapping[0] != (6, 2024) or mapping[-1] != (6, 2025):
        _fail(f"genky June split: {mapping[0]} {mapping[-1]}")

    # 60400 型: 7月期末・as_of が期末月より後（9月）→ 完了した FY（8/2025…7/2026）
    july_months = [8, 9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7]
    layout, mapping = plan_column_years(july_months, 7, date(2026, 9, 11))
    if layout != "fiscal":
        _fail(f"july post-end layout: {layout}")
    expect_july = {m: 2025 for m in (8, 9, 10, 11, 12)}
    expect_july.update({m: 2026 for m in (1, 2, 3, 4, 5, 6, 7)})
    if _map_dict(mapping) != expect_july:
        _fail(f"july post-end mapping: {mapping}")

    # 同 FY・as_of が期末月より前（6月）→ 現行 FY は当年7月期末
    layout, mapping = plan_column_years(july_months, 7, date(2026, 6, 15))
    if layout != "fiscal":
        _fail(f"july mid-fy layout: {layout}")
    expect_july_mid = {m: 2025 for m in (8, 9, 10, 11, 12)}
    expect_july_mid.update({m: 2026 for m in (1, 2, 3, 4, 5, 6, 7)})
    if _map_dict(mapping) != expect_july_mid:
        _fail(f"july mid-fy mapping: {mapping}")

    # HTML の当期途中（期首から as_of 月まで）。3月期 4〜8月、2月期 3〜8月はどちらも当年
    for months, fy_end in (([4, 5, 6, 7, 8], 3), ([3, 4, 5, 6, 7, 8], 2)):
        layout, mapping = plan_column_years(months, fy_end, date(2026, 8, 31))
        if layout != "fiscal" or _map_dict(mapping) != {m: 2026 for m in months}:
            _fail(f"in-progress fy={fy_end} {layout} {mapping}")

    # 27360 / 99790 型: 8月期末・12列(9〜8月)。新FY先頭月だけでなく翌々月以降も完了FY
    aug_months = [9, 10, 11, 12, 1, 2, 3, 4, 5, 6, 7, 8]
    expect_aug = {m: 2025 for m in (9, 10, 11, 12)}
    expect_aug.update({m: 2026 for m in (1, 2, 3, 4, 5, 6, 7, 8)})
    for as_of in (date(2026, 9, 14), date(2026, 9, 15), date(2026, 10, 1)):
        layout, mapping = plan_column_years(aug_months, 8, as_of)
        if layout != "fiscal":
            _fail(f"aug post-end layout as_of={as_of}: {layout}")
        if _map_dict(mapping) != expect_aug:
            _fail(f"aug post-end mapping as_of={as_of}: {mapping}")

    # 同 FY・as_of が期末月内（8月）→ まだ当期 FY（9/2025…7/2026）
    layout, mapping = plan_column_years(aug_months[:-1], 8, date(2026, 8, 10))
    if layout != "fiscal":
        _fail(f"aug mid-fy layout: {layout}")
    expect_aug_mid = {m: 2025 for m in (9, 10, 11, 12)}
    expect_aug_mid.update({m: 2026 for m in (1, 2, 3, 4, 5, 6, 7)})
    if _map_dict(mapping) != expect_aug_mid:
        _fail(f"aug mid-fy mapping: {mapping}")


def test_fy_end_and_layout_errors() -> None:
    as_of = date(2026, 8, 5)
    _expect_ingest_error(lambda: resolve_fy_end_month([], as_of), "NEED_FY_END")
    _expect_ingest_error(lambda: resolve_fy_end_month([(None, None)], as_of), "NEED_FY_END")
    _expect_ingest_error(
        lambda: resolve_fy_end_month([(None, date(2025, 10, 31))], as_of),
        "NEED_FY_END",
    )

    oct_rows = [
        (date(2026, 5, 8), date(2025, 10, 31)),
        (date(2025, 5, 9), date(2024, 10, 31)),
    ]
    if resolve_fy_end_month(oct_rows, as_of) != 10:
        _fail("same fy_end month should be accepted")

    # 2種の期末月があっても as_of 時点の最新開示の1か月。FY_END_CHANGED にはしない。
    changed = [
        (date(2026, 6, 1), date(2026, 3, 31)),
        (date(2026, 5, 1), date(2025, 10, 31)),
    ]
    if resolve_fy_end_month(changed, as_of) != 3:
        _fail("as-of after both disclosures should use latest month 3")
    if resolve_fy_end_month(changed, date(2026, 5, 15)) != 10:
        _fail("as-of between disclosures should use Oct")
    _expect_ingest_error(
        lambda: resolve_fy_end_month(changed, date(2026, 4, 30)),
        "NEED_FY_END",
    )

    same_day = [
        (date(2026, 6, 1), date(2026, 2, 28)),
        (date(2026, 6, 1), date(2026, 11, 30)),
    ]
    if resolve_fy_end_month(same_day, as_of) != 11:
        _fail("same disclosed_date should pick later fiscal_year_end")

    # 実在の期変更銘柄: 後の開示の月末を使う（live DB は見ない）
    for older_m, newer_m in ((2, 11), (1, 6), (2, 3), (11, 3), (8, 11), (1, 2)):
        rows = [
            (date(2020, 6, 1), date(2020, older_m, 1)),
            (date(2021, 6, 1), date(2021, newer_m, 1)),
        ]
        got = resolve_fy_end_month(rows, date(2021, 7, 1))
        if got != newer_m:
            _fail(f"{older_m}->{newer_m}: expected {newer_m}, got {got}")

    fy, layout, mapping = map_pdf_columns(
        MARCH_MONTHS, changed, date(2026, 8, 5),
    )
    if fy != 3 or layout != "fiscal":
        _fail(f"changed-fy map_pdf_columns: {fy} {layout} {mapping}")

    layout, mapping = plan_column_years([7, 8, 9, 10, 11, 12, 1], 10, date(2026, 8, 5))
    if layout != "partial":
        _fail(f"wrap mid-start layout: {layout}")
    expect_wrap = {m: 2025 for m in (7, 8, 9, 10, 11, 12)}
    expect_wrap[1] = 2026
    if _map_dict(mapping) != expect_wrap:
        _fail(f"wrap mid-start mapping: {mapping}")

    # 期末後の as_of で、右端列が as_of より未来なら書かない
    exc = _expect_ingest_error(
        lambda: plan_column_years([9, 10, 11], 8, date(2026, 10, 1)),
        "FUTURE_COLUMN",
        "未来",
    )
    if exc.extra.get("month") != 11 or exc.extra.get("year") != 2026:
        _fail(f"FUTURE_COLUMN extra {exc.extra}")
    if exc.status != 400:
        _fail(f"FUTURE_COLUMN status {exc.status}")


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


def test_api_gift_ingest() -> None:
    c = TestClient(app)
    code = "92790"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 10, 31), "gift-fy-1")
    _add_fy_end(code, date(2025, 5, 9), date(2024, 10, 31), "gift-fy-2")

    months = GIFT_MONTHS
    values = {str(m): 100.0 + m for m in months}
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": months,
        "rows": [{"element": "全店 売上高", "values": values, "unit": "number"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "gift ingest")
    body = r.json()
    if body.get("code") != code or body.get("fy_end_month") != 10 or body.get("layout") != "fiscal":
        _fail(f"gift ingest header: {body!r}")
    if body.get("as_of") != "2026-08-05":
        _fail(f"as_of: {body!r}")
    mapping = body.get("mapping")
    expect_map = {str(m): (2025 if m >= 11 else 2026) for m in months}
    if mapping != expect_map:
        _fail(f"mapping: {mapping!r}")

    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET 2025")
    els = r.json()["elements"]
    if [e["element"] for e in els] != ["全店 売上高"]:
        _fail(f"2025 elements: {els!r}")
    if els[0].get("unit") != "number":
        _fail(f"gift unit: {els!r}")
    v2025 = _present_months(els[0]["values"])
    if set(v2025) != {"11", "12"}:
        _fail(f"GET 2025 months: {v2025!r}")

    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET 2026")
    v2026 = _present_months(r.json()["elements"][0]["values"])
    if set(v2026) != {"1", "2", "3", "4", "5", "6", "7"}:
        _fail(f"GET 2026 months: {v2026!r}")


def test_api_errors() -> None:
    c = TestClient(app)

    r = c.post("/api/v1/tables/30320/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "売上高", "unit": "number", "values": {"11": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 404, "ingest without master")
    if _err(r)["code"] != "NOT_IN_MASTER":
        _fail(f"NOT_IN_MASTER: {r.json()!r}")

    _ensure_stock("99990")
    r = c.post("/api/v1/tables/99990/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "売上高", "unit": "number", "values": {"11": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "NEED_FY_END")
    if _err(r)["code"] != "NEED_FY_END":
        _fail(f"NEED_FY_END: {r.json()!r}")

    _add_fy_end("88880", date(2026, 6, 1), date(2026, 3, 31), "chg-1")
    _add_fy_end("88880", date(2026, 5, 1), date(2025, 10, 31), "chg-2")
    r = c.post("/api/v1/tables/88880/ingest", json={
        "months": MARCH_MONTHS,
        "rows": [{"element": "売上高", "unit": "number", "values": {"4": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "fy change as-of")
    body = r.json()
    if body.get("fy_end_month") != 3 or body.get("layout") != "fiscal":
        _fail(f"as-of after fy change: {body!r}")

    r = c.post("/api/v1/tables/88880/ingest", json={
        "months": MARCH_MONTHS,
        "rows": [{"element": "売上高", "unit": "number", "values": {"4": 1}}],
        "as_of": "2026-04-30",
    })
    _expect(r, 400, "NEED_FY_END before statements")
    if _err(r)["code"] != "NEED_FY_END":
        _fail(f"as_of before fy_end rows: {r.json()!r}")

    # 2月と11月: 後の開示が11月。FY_END_CHANGED にはしない。
    _add_fy_end("26980", date(2020, 4, 10), date(2020, 2, 29), "candu-feb")
    _add_fy_end("26980", date(2020, 12, 15), date(2020, 11, 30), "candu-nov")
    r = c.post("/api/v1/tables/26980/ingest", json={
        "months": CAL_MONTHS,
        "rows": [{"element": "売上高", "unit": "number", "values": {"1": 1}}],
        "as_of": "2021-06-01",
    })
    _expect(r, 200, "2 and 11 as-of")
    if r.json().get("fy_end_month") != 11 or r.json().get("layout") != "calendar":
        _fail(f"2/11 as-of: {r.json()!r}")

    # 同日開示は fiscal_year_end の新しい方
    _add_fy_end("36120", date(2021, 6, 1), date(2021, 2, 28), "world-feb")
    _add_fy_end("36120", date(2021, 6, 1), date(2021, 3, 31), "world-mar")
    r = c.post("/api/v1/tables/36120/ingest", json={
        "months": MARCH_MONTHS,
        "rows": [{"element": "売上高", "unit": "number", "values": {"4": 1}}],
        "as_of": "2021-06-01",
    })
    _expect(r, 200, "same-day later fy_end")
    if r.json().get("fy_end_month") != 3:
        _fail(f"same-day later fy_end: {r.json()!r}")

    _add_fy_end("77770", date(2026, 5, 1), date(2025, 10, 31), "amb-1")
    r = c.post("/api/v1/tables/77770/ingest", json={
        "months": [7, 8],
        "rows": [{"element": "売上高", "unit": "number", "values": {"7": 1, "8": 2}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "mid-start ingest")
    body = r.json()
    if body.get("layout") != "partial" or body.get("fy_end_month") != 10:
        _fail(f"mid-start ingest header: {body!r}")
    if body.get("mapping") != {"7": 2026, "8": 2026}:
        _fail(f"mid-start ingest mapping: {body.get('mapping')!r}")
    r = c.get("/api/v1/tables/77770/2026")
    _expect(r, 200, "GET 2026 mid-start")
    v2026 = _present_months(r.json()["elements"][0]["values"])
    if set(v2026) != {"7", "8"}:
        _fail(f"GET 2026 mid-start months: {v2026!r}")
    r = c.get("/api/v1/tables/77770/2025")
    _expect(r, 404, "GET 2025 mid-start")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"2025 should not exist: {r.json()!r}")

    _add_fy_end("33330", date(2026, 5, 1), date(2026, 3, 31), "single-jul-1")
    r = c.post("/api/v1/tables/33330/ingest", json={
        "months": [7],
        "rows": [{"element": "売上高", "unit": "number", "values": {"7": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "single July ingest")
    body = r.json()
    if body.get("layout") != "partial" or body.get("mapping") != {"7": 2026}:
        _fail(f"single July ingest: {body!r}")

    r = c.post("/api/v1/tables/77770/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "売上高", "unit": "number", "values": {"8": 1.0}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "month not in months")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"extra month: {r.json()!r}")


def test_all_or_nothing_similar() -> None:
    c = TestClient(app)
    code = "66660"
    _add_fy_end(code, date(2026, 5, 1), date(2025, 10, 31), "sim-1")
    r = c.post(f"/api/v1/tables/{code}/2025/elements", json={"element": "世界観ビジネス", "unit": "number"})
    _expect(r, 201, "seed similar")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "世界観 ビジネス", "values": {"11": 1, "1": 2}, "unit": "number"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 409, "similar ingest")
    if _err(r)["code"] != "ELEMENT_SIMILAR_EXISTS":
        _fail(f"similar ingest: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 404, "2026 rolled back")
    if _err(r)["code"] != "TABLE_NOT_FOUND":
        _fail(f"2026 should not exist: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GIFT_MONTHS,
        "rows": [{"element": "全店 売上高", "values": {"11": 1, "1": 2}, "unit": "number", "force": True}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "force ingest")
    r = c.get(f"/api/v1/tables/{code}/2025")
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["全店 売上高"]:
        _fail(f"force elements omit empty seed: {names!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET 2026 after force")
    if [e["element"] for e in r.json()["elements"]] != ["全店 売上高"]:
        _fail(f"2026 after force: {r.json()!r}")


def test_tdnet_4digit_fy_end_lookup() -> None:
    """TDnet 4桁パスは J-Quants 5桁の fiscal_year_end を使い、保存キーも 5桁。"""
    c = TestClient(app)
    _add_fy_end("92790", date(2026, 5, 8), date(2025, 10, 31), "tdnet-pad-fy-1")
    months = GIFT_MONTHS
    values = {str(m): 100.0 + m for m in months}
    r = c.post("/api/v1/tables/9279/ingest", json={
        "months": months,
        "rows": [{"element": "全店 売上高", "values": values, "unit": "number"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "9279 ingest with 92790 fy_end")
    if r.json().get("code") != "92790":
        _fail(f"9279 ingest code: {r.json()!r}")
    r = c.get("/api/v1/tables/92790/2025")
    _expect(r, 200, "GET 92790 after 9279 ingest")
    if r.json()["code"] != "92790":
        _fail(f"9279 save key: {r.json()!r}")


def test_api_alnum_code() -> None:
    c = TestClient(app)
    _add_fy_end("262A0", date(2026, 5, 8), date(2025, 10, 31), "alnum-fy-1")
    months = GIFT_MONTHS
    values = {str(m): 100.0 + m for m in months}
    r = c.post("/api/v1/tables/262A/ingest", json={
        "months": months,
        "rows": [{"element": "全店 売上高", "values": values, "unit": "number"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "alnum ingest")
    if r.json().get("code") != "262A0":
        _fail(f"alnum ingest code: {r.json()!r}")
    r = c.get("/api/v1/tables/262A0/2025")
    _expect(r, 200, "alnum GET 2025")
    if r.json()["code"] != "262A0":
        _fail(f"alnum GET code: {r.json()!r}")
    r = c.post("/api/v1/tables/abc/ingest", json={
        "months": months,
        "rows": [{"element": "売上高", "unit": "number", "values": {"11": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "abc ingest")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"abc ingest: {r.json()!r}")


def test_unit_display_and_ingest() -> None:
    if format_monthly_cell(125.8, "yoy_index") != "125.8":
        _fail(f"yoy_index display: {format_monthly_cell(125.8, 'yoy_index')!r}")
    if format_monthly_cell(105.2, "yoy_index") != "105.2":
        _fail(f"yoy_index 105.2: {format_monthly_cell(105.2, 'yoy_index')!r}")
    if format_monthly_cell(92.9, "yoy_index") != "92.9":
        _fail(f"yoy_index 92.9: {format_monthly_cell(92.9, 'yoy_index')!r}")
    if format_monthly_cell(303, "number") != "303":
        _fail(f"number display: {format_monthly_cell(303, 'number')!r}")
    if format_monthly_cell(None, "number") != "-":
        _fail("empty cell")
    if format_monthly_cell(29296.646549, "number", "受注高") != "29297":
        _fail(f"受注高 display: {format_monthly_cell(29296.646549, 'number', '受注高')!r}")
    if format_monthly_cell(125.8, "yoy_index", "受注高") != "125.8":
        _fail("yoy_index must ignore display units")
    try:
        parse_monthly_unit("percent")
    except MonthlyIngestError as exc:
        if exc.status != 400 or exc.code != "INVALID_PARAM":
            _fail(f"invalid unit: {exc.status} {exc.code}")
    else:
        _fail("invalid unit should 400")
    _expect_ingest_error(lambda: parse_monthly_unit(None), "UNIT_REQUIRED")
    _expect_ingest_error(lambda: parse_as_of(None), "AS_OF_REQUIRED")
    if parse_as_of(None, default=date(2026, 8, 5)) != date(2026, 8, 5):
        _fail("parse_as_of default")

    c = TestClient(app)
    code = "55550"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 10, 31), "unit-fy-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11, 12, 1],
        "rows": [
            {"element": "全店売上高", "values": {"11": 125.8}, "unit": "yoy_index"},
            {"element": "既存店売上高", "values": {"11": 103.4}, "unit": "yoy_index"},
            {"element": "客数前年比", "values": {"11": -7.1}, "unit": "yoy_pct"},
            {"element": "店舗数", "values": {"11": 303}, "unit": "number"},
        ],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest with units")
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET ingest units")
    by = {e["element"]: e for e in r.json()["elements"]}
    if "全店 売上高(前年比)" not in by:
        _fail(f"ingest yoy_index name: {list(by)!r}")
    if by["全店 売上高(前年比)"]["unit"] != "yoy_index" or float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"ingest yoy_index stored: {by['全店 売上高(前年比)']!r}")
    if format_monthly_cell(float(by["全店 売上高(前年比)"]["values"]["11"]), "yoy_index") != "125.8":
        _fail("ingest yoy_index display")
    if "既存 売上高(前年比)" not in by:
        _fail(f"ingest yoy_index 103.4 name: {list(by)!r}")
    if by["既存 売上高(前年比)"]["unit"] != "yoy_index" or float(by["既存 売上高(前年比)"]["values"]["11"]) != 103.4:
        _fail(f"ingest yoy_index 103.4 stored: {by['既存 売上高(前年比)']!r}")
    if "客数(前年比)" not in by:
        _fail(f"ingest yoy_pct name: {list(by)!r}")
    if by["客数(前年比)"]["unit"] != "yoy_index" or float(by["客数(前年比)"]["values"]["11"]) != 92.9:
        _fail(f"ingest yoy_pct stored as yoy_index 92.9: {by['客数(前年比)']!r}")
    if format_monthly_cell(float(by["客数(前年比)"]["values"]["11"]), "yoy_index") != "92.9":
        _fail("ingest yoy_index 92.9 display")
    if by["店舗数"]["unit"] != "number" or float(by["店舗数"]["values"]["11"]) != 303:
        _fail(f"ingest number stored: {by['店舗数']!r}")
    if "客数前年比" in by:
        _fail("yoy_pct must not keep PDF 前年比 wording")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11, 12, 1],
        "rows": [{"element": "客数前年比", "values": {"11": -7.1}, "unit": "yoy_pct"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "re-ingest yoy_pct -7.1")
    r = c.get(f"/api/v1/tables/{code}/2025")
    by = {e["element"]: e for e in r.json()["elements"]}
    if by["客数(前年比)"]["unit"] != "yoy_index" or float(by["客数(前年比)"]["values"]["11"]) != 92.9:
        _fail(f"re-ingest must not add 100 twice: {by['客数(前年比)']!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11, 12, 1],
        "rows": [{"element": "x", "values": {"11": 1}, "unit": "percent"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "ingest invalid unit")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"ingest invalid unit: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11, 12, 1],
        "rows": [{"element": "大きい指数", "values": {"11": 125.8}, "unit": "yoy_pct"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 125.8 as yoy_pct")
    r = c.get(f"/api/v1/tables/{code}/2025")
    by = {e["element"]: e for e in r.json()["elements"]}
    if "大きい指数(前年比)" not in by:
        _fail(f"yoy_pct name missing suffix: {list(by)!r}")
    if by["大きい指数(前年比)"]["unit"] != "yoy_index" or float(by["大きい指数(前年比)"]["values"]["11"]) != 225.8:
        _fail(f"yoy_pct 125.8 must become yoy_index 225.8: {by['大きい指数(前年比)']!r}")


def test_ingest_yoy_label_and_number_separate() -> None:
    c = TestClient(app)
    code = "44440"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 10, 31), "label-fy-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11],
        "rows": [{"element": "売上高前年同月比", "values": {"11": 125.8}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 前年同月比")
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET after 前年同月比")
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["売上高(前年比)"]:
        _fail(f"save 売上高前年同月比 as 売上高(前年比): {names!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11],
        "rows": [{"element": "売上高前年比", "values": {"11": 126.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest canonical same row")
    r = c.get(f"/api/v1/tables/{code}/2025")
    els = r.json()["elements"]
    if [e["element"] for e in els] != ["売上高(前年比)"]:
        _fail(f"canonical similar must be same row: {[e['element'] for e in els]!r}")
    if float(els[0]["values"]["11"]) != 126.0:
        _fail(f"canonical ingest overwrite: {els[0]!r}")

    r = c.post(f"/api/v1/tables/{code}/{2025}/elements", json={"element": "売上高", "unit": "number"})
    _expect(r, 201, "number 売上高 separate")
    r = c.put(f"/api/v1/tables/{code}/2025/values",
              json={"element": "売上高", "month": 11, "value": 1.0})
    _expect(r, 200, "number 売上高 value")
    r = c.get(f"/api/v1/tables/{code}/2025")
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["売上高(前年比)", "売上高"]:
        _fail(f"number and yoy stay separate: {names!r}")

    r = c.post(f"/api/v1/tables/{code}/{2025}/elements",
               json={"element": "売上高前年同月比", "unit": "number"})
    _expect(r, 200, "number 前年同月比 folds to 売上高")
    if r.json()["element"] != "売上高":
        _fail(f"number token-normalize: {r.json()!r}")


def test_uncovered_yoy_number_twins() -> None:
    lone = uncovered_yoy_number_twins(
        [("売上高(前年比)", "yoy_index")],
        "売上高", "number",
        [("売上高", "number")],
    )
    if lone != ["売上高(前年比)"]:
        _fail(f"missing counterpart: {lone!r}")
    both = uncovered_yoy_number_twins(
        [("全店 売上高(前年比)", "yoy_index"), ("全店 売上高", "number")],
        "全店 売上高", "number",
        [("全店 売上高", "number"), ("全店 売上高(前年比)", "yoy_index")],
    )
    if both:
        _fail(f"both rows in the post must pass: {both!r}")
    same = uncovered_yoy_number_twins(
        [("売上高(前年比)", "yoy_index")],
        "売上高(前年比)", "yoy_index",
        [("売上高(前年比)", "yoy_index")],
    )
    if same:
        _fail(f"same kind is not a twin: {same!r}")
    other = uncovered_yoy_number_twins(
        [("店舗数(前年比)", "yoy_index")],
        "売上高", "number",
        [("売上高", "number")],
    )
    if other:
        _fail(f"different metric: {other!r}")
    blank = uncovered_yoy_number_twins(
        [("前年比", "yoy_index")],
        "", "number",
        [],
    )
    if blank:
        _fail(f"empty base: {blank!r}")
    reverse = uncovered_yoy_number_twins(
        [("客数", "number")],
        "客数(前年比)", "yoy_index",
        [("客数(前年比)", "yoy_index")],
    )
    if reverse != ["客数"]:
        _fail(f"number existing vs yoy incoming: {reverse!r}")


def test_ingest_yoy_number_conflict() -> None:
    """unit を取り違えると行名が変わり、既存の反対側が rows に無いと 409。"""
    c = TestClient(app)
    code = "31710"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 10, 31), "twin-fy-1")
    body = {
        "months": [11],
        "rows": [{"element": "全店 売上高", "values": {"11": 125.8}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    }
    r = c.post(f"/api/v1/tables/{code}/ingest", json=body)
    _expect(r, 200, "seed yoy")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11],
        "rows": [
            {"element": "店舗数", "values": {"11": 10}, "unit": "number"},
            {"element": "全店 売上高", "values": {"11": 1000}, "unit": "number"},
        ],
        "as_of": "2026-08-05",
    })
    _expect(r, 409, "number without existing yoy row")
    err = _err(r)
    if err["code"] != "YOY_NUMBER_CONFLICT" or err.get("existing") != ["全店 売上高(前年比)"]:
        _fail(f"twin conflict: {r.json()!r}")
    if err.get("incoming") != "全店 売上高":
        _fail(f"incoming: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET after conflict")
    els = r.json()["elements"]
    if [e["element"] for e in els] != ["全店 売上高(前年比)"]:
        _fail(f"409 must roll back the sibling row: {[e['element'] for e in els]!r}")
    if float(els[0]["values"]["11"]) != 125.8:
        _fail(f"yoy value changed: {els[0]!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11],
        "rows": [
            {"element": "全店 売上高", "values": {"11": 1000}, "unit": "number"},
            {"element": "全店 売上高", "values": {"11": 125.8}, "unit": "yoy_index"},
        ],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "both rows in one post")
    r = c.get(f"/api/v1/tables/{code}/2025")
    by = {e["element"]: e for e in r.json()["elements"]}
    if set(by) != {"全店 売上高(前年比)", "全店 売上高"}:
        _fail(f"coexist: {list(by)!r}")
    if by["全店 売上高"]["unit"] != "number" or float(by["全店 売上高"]["values"]["11"]) != 1000:
        _fail(f"number stored: {by['全店 売上高']!r}")
    if float(by["全店 売上高(前年比)"]["values"]["11"]) != 125.8:
        _fail(f"yoy kept: {by['全店 売上高(前年比)']!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [11],
        "rows": [{"element": "全店 売上高", "values": {"11": 126.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "update existing yoy while number exists")
    r = c.get(f"/api/v1/tables/{code}/2025")
    by = {e["element"]: e for e in r.json()["elements"]}
    if float(by["全店 売上高(前年比)"]["values"]["11"]) != 126.0:
        _fail(f"yoy update: {by['全店 売上高(前年比)']!r}")

    code2 = "31720"
    _add_fy_end(code2, date(2026, 5, 8), date(2025, 10, 31), "twin-fy-2")
    r = c.post(f"/api/v1/tables/{code2}/ingest", json={
        "months": [11],
        "rows": [{"element": "客数", "values": {"11": 50}, "unit": "number"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "seed number")
    r = c.post(f"/api/v1/tables/{code2}/ingest", json={
        "months": [11],
        "rows": [{"element": "客数", "values": {"11": 110.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 409, "yoy without existing number row")
    if _err(r)["code"] != "YOY_NUMBER_CONFLICT" or _err(r).get("existing") != ["客数"]:
        _fail(f"reverse twin: {r.json()!r}")
    r = c.post(f"/api/v1/tables/{code2}/ingest", json={
        "months": [11],
        "rows": [{
            "element": "客数", "values": {"11": 110.0}, "unit": "yoy_index", "force": True,
        }],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "force creates the yoy twin")
    r = c.get(f"/api/v1/tables/{code2}/2025")
    names = [e["element"] for e in r.json()["elements"]]
    if names != ["客数", "客数(前年比)"]:
        _fail(f"force both: {names!r}")


def test_ingest_yoy_short_and_canonical_same_row() -> None:
    c = TestClient(app)
    # 88880 は test_api_errors で 3月期末の売上高を ingest 済み（現行 FY は 2026 年列）と衝突するため別コード。
    code = "88770"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 12, 31), "yoy-same-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "売上高", "values": {"1": 101.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 売上高 yoy_index")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "売上高(前年比)", "values": {"1": 102.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 売上高(前年比) yoy_index")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET after yoy short+canonical")
    els = r.json()["elements"]
    names = [e["element"] for e in els]
    if names != ["売上高(前年比)"]:
        _fail(f"ingest 売上高 then 売上高(前年比) must be one row: {names!r}")
    if els[0]["unit"] != "yoy_index" or float(els[0]["values"]["1"]) != 102.0:
        _fail(f"second ingest upsert: {els[0]!r}")


def test_existing_short_yoy_renamed_on_canonical() -> None:
    c = TestClient(app)
    code = "88890"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 12, 31), "yoy-rename-1")
    r = c.post(f"/api/v1/tables/{code}/2026/elements", json={"element": "カラオケ全店 売上高", "unit": "number"})
    _expect(r, 201, "seed short name")
    if r.json()["element"] != "カラオケ 全店 売上高":
        _fail(f"number short stored: {r.json()!r}")
    r = c.put(f"/api/v1/tables/{code}/2026/elements",
              json={"element": "カラオケ全店 売上高", "unit": "yoy_index"})
    _expect(r, 200, "PUT unit yoy on short")
    # unit を変えた行はその場で新しい unit の正規名へ（起動時には作り直さない。#545）
    if r.json()["element"] != "カラオケ 全店 売上高(前年比)":
        _fail(f"PUT unit must rename to yoy canonical: {r.json()!r}")
    r = c.put(f"/api/v1/tables/{code}/2026/values",
              json={"element": "カラオケ全店 売上高(前年比)", "month": 1, "value": 110.0})
    _expect(r, 200, "PUT value on renamed")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "カラオケ全店 売上高(前年比)", "values": {"1": 111.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest canonical onto renamed")
    r = c.get(f"/api/v1/tables/{code}/2026")
    els = r.json()["elements"]
    names = [e["element"] for e in els]
    if names != ["カラオケ 全店 売上高(前年比)"]:
        _fail(f"one canonical row: {names!r}")
    if els[0]["unit"] != "yoy_index" or float(els[0]["values"]["1"]) != 111.0:
        _fail(f"rename+upsert: {els[0]!r}")

    r = c.post(f"/api/v1/tables/{code}/2026/elements",
               json={"element": "カラオケ全店 売上高(前年比)", "unit": "yoy_index"})
    _expect(r, 200, "POST canonical after rename")
    if r.json()["created"] is not False or r.json()["element"] != "カラオケ 全店 売上高(前年比)":
        _fail(f"POST after rename: {r.json()!r}")


def test_number_stores_not_yoy_suffix() -> None:
    c = TestClient(app)
    code = "88900"
    _add_fy_end(code, date(2026, 5, 8), date(2025, 12, 31), "stores-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "店舗数", "values": {"1": 42}, "unit": "number"}],
        "as_of": "2026-08-05",
    })
    _expect(r, 200, "ingest 店舗数")
    r = c.get(f"/api/v1/tables/{code}/2026")
    els = r.json()["elements"]
    if [e["element"] for e in els] != ["店舗数"]:
        _fail(f"number 店舗数 must not get (前年比): {[e['element'] for e in els]!r}")
    if els[0]["unit"] != "number" or float(els[0]["values"]["1"]) != 42:
        _fail(f"店舗数 stored: {els[0]!r}")


def test_merge_uchiyama_like_yoy_duplicates() -> None:
    from app.db import SessionLocal
    from app.monthly.maintenance import merge_yoy_duplicate_elements
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    code, year = "60590", 2026
    short = "カラオケ全店 売上高"
    canon = "カラオケ 全店 売上高(前年比)"
    _ensure_stock(code, "ウチヤマ")
    db = SessionLocal()
    try:
        db.add(MonthlyTable(code=code, year=year))
        db.add(MonthlyElement(code=code, year=year, element=short, sort_order=1, unit="yoy_index"))
        db.add(MonthlyElement(code=code, year=year, element=canon, sort_order=2, unit="yoy_index"))
        db.add(MonthlyValue(code=code, year=year, element=short, month=1, value=101.0))
        db.add(MonthlyValue(code=code, year=year, element=canon, month=1, value=101.0))
        db.add(MonthlyValue(code=code, year=year, element=short, month=2, value=102.0))
        db.add(MonthlyValue(code=code, year=year, element=canon, month=3, value=103.0))
        db.add(MonthlyValue(code=code, year=year, element=short, month=4, value=104.0))
        db.add(MonthlyValue(code=code, year=year, element=canon, month=4, value=105.0))
        db.commit()
    finally:
        db.close()

    reports = merge_yoy_duplicate_elements()
    print(f"merge 60590 reports: {reports!r}")
    if any(r.get("code") == "44440" for r in reports):
        _fail(f"must not merge number 売上高 into yoy: {reports!r}")
    hit = [r for r in reports if r.get("code") == code and r.get("year") == year]
    if not hit:
        _fail(f"60590 2026 merge missing: {reports!r}")
    one = hit[0]
    if one.get("canonical") != canon or short not in (one.get("deleted") or []):
        _fail(f"60590 merge target: {one!r}")
    conflicts = one.get("conflicts") or []
    if not any(c.get("month") == 4 and c.get("kept") == 105.0 and c.get("dropped") == 104.0
               for c in conflicts):
        _fail(f"60590 month 4 conflict not reported: {conflicts!r}")

    db = SessionLocal()
    try:
        els = (db.query(MonthlyElement)
               .filter(MonthlyElement.code == code, MonthlyElement.year == year)
               .all())
        names = sorted(e.element for e in els)
        if names != [canon]:
            _fail(f"60590 after merge names: {names!r}")
        if els[0].unit != "yoy_index":
            _fail(f"60590 unit: {els[0].unit!r}")
        vals = {
            v.month: float(v.value)
            for v in db.query(MonthlyValue).filter(
                MonthlyValue.code == code, MonthlyValue.year == year,
            )
        }
        if vals != {1: 101.0, 2: 102.0, 3: 103.0, 4: 105.0}:
            _fail(f"60590 after merge values: {vals!r}")
        if db.get(MonthlyElement, (code, year, short)) is not None:
            _fail("short row must be deleted")
    finally:
        db.close()

    again = merge_yoy_duplicate_elements()
    if any(r.get("code") == code and r.get("year") == year for r in again):
        _fail(f"merge must be idempotent: {again!r}")


def test_rename_29260_style_cross_year_yoy() -> None:
    """年Aは短い yoy 名、年Bは正規名。migrate で短い名を正規名へ。number は触らない。"""
    from app.db import SessionLocal
    from app.monthly.maintenance import merge_yoy_duplicate_elements
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue
    from app.monthly.ingest import union_monthly_elements

    c = TestClient(app)
    code = "29260"
    short = "全店 売上高前期比"
    canon = "全店 売上高(前年比)"
    _ensure_stock(code, "Action")
    db = SessionLocal()
    try:
        db.add(MonthlyTable(code=code, year=2026))
        db.add(MonthlyTable(code=code, year=2025))
        db.add(MonthlyElement(code=code, year=2026, element=short, sort_order=1, unit="yoy_index"))
        db.add(MonthlyElement(code=code, year=2026, element="店舗数", sort_order=2, unit="number"))
        db.add(MonthlyElement(code=code, year=2026, element="売上高", sort_order=3, unit="number"))
        db.add(MonthlyElement(code=code, year=2025, element=canon, sort_order=1, unit="yoy_index"))
        db.add(MonthlyElement(code=code, year=2025, element="店舗数", sort_order=2, unit="number"))
        db.add(MonthlyElement(code=code, year=2025, element="売上高", sort_order=3, unit="number"))
        db.add(MonthlyValue(code=code, year=2026, element=short, month=1, value=110.0))
        db.add(MonthlyValue(code=code, year=2026, element="店舗数", month=1, value=50.0))
        db.add(MonthlyValue(code=code, year=2026, element="売上高", month=1, value=1000.0))
        db.add(MonthlyValue(code=code, year=2025, element=canon, month=1, value=105.0))
        db.add(MonthlyValue(code=code, year=2025, element="店舗数", month=1, value=48.0))
        db.add(MonthlyValue(code=code, year=2025, element="売上高", month=1, value=900.0))
        db.commit()
    finally:
        db.close()

    reports = merge_yoy_duplicate_elements()
    print(f"rename 29260 reports: {reports!r}")
    hit = [r for r in reports if r.get("code") == code and r.get("year") == 2026
           and r.get("canonical") == canon]
    if not hit:
        _fail(f"29260 2026 rename missing: {reports!r}")
    one = hit[0]
    moved = (one.get("renamed") or []) + (one.get("deleted") or [])
    if short not in moved:
        _fail(f"29260 short not moved: {one!r}")
    if any(r.get("canonical") == "店舗数" for r in reports if r.get("code") == code):
        _fail(f"must not rename 店舗数: {reports!r}")

    db = SessionLocal()
    try:
        els = (db.query(MonthlyElement)
               .filter(MonthlyElement.code == code, MonthlyElement.year == 2026)
               .all())
        names = sorted(e.element for e in els)
        if short in names:
            _fail(f"29260 2026 short still present: {names!r}")
        if canon not in names:
            _fail(f"29260 2026 missing canonical: {names!r}")
        if "店舗数" not in names or "売上高" not in names:
            _fail(f"number rows must stay: {names!r}")
        if "売上高(前年比)" in names:
            _fail(f"number 売上高 must not gain (前年比): {names!r}")
        v = db.get(MonthlyValue, (code, 2026, canon, 1))
        if v is None or float(v.value) != 110.0:
            _fail(f"2026 values must come from the renamed short row: {v}")
        if db.get(MonthlyValue, (code, 2026, short, 1)) is not None:
            _fail("old short values must move with the rename")
        stores = db.get(MonthlyValue, (code, 2026, "店舗数", 1))
        if stores is None or float(stores.value) != 50.0:
            _fail(f"number 店舗数 values: {stores}")
        sales = db.get(MonthlyValue, (code, 2026, "売上高", 1))
        if sales is None or float(sales.value) != 1000.0:
            _fail(f"number 売上高 values: {sales}")
        if db.get(MonthlyElement, (code, 2025, canon)) is None:
            _fail("2025 canonical row must stay")
    finally:
        db.close()

    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET 29260 2026")
    y2026 = r.json()
    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET 29260 2025")
    y2025 = r.json()
    order = [row["element"] for row in union_monthly_elements([y2026, y2025])]
    if order != ["売上高", canon, "店舗数"]:
        _fail(f"29260 union must be one yoy row: {order!r}")
    by = {e["element"]: e for e in y2026["elements"]}
    if canon not in by or float(by[canon]["values"]["1"]) != 110.0:
        _fail(f"GET 2026 values from renamed short: {by!r}")
    if by.get("店舗数", {}).get("unit") != "number" or float(by["店舗数"]["values"]["1"]) != 50.0:
        _fail(f"GET 2026 店舗数: {by.get('店舗数')!r}")
    if by.get("売上高", {}).get("unit") != "number" or float(by["売上高"]["values"]["1"]) != 1000.0:
        _fail(f"GET 2026 number 売上高: {by.get('売上高')!r}")

    leftover = [
        {"year": 2026, "elements": [
            {"element": short, "unit": "yoy_index", "values": {"1": 110.0}},
            {"element": "店舗数", "unit": "number"},
            {"element": "売上高", "unit": "number"},
        ]},
        {"year": 2025, "elements": [
            {"element": canon, "unit": "yoy_index", "values": {"1": 105.0}},
            {"element": "店舗数", "unit": "number"},
            {"element": "売上高", "unit": "number"},
        ]},
    ]
    leftover_order = [row["element"] for row in union_monthly_elements(leftover)]
    if leftover_order != ["売上高", canon, "店舗数"]:
        _fail(f"leftover short names must collapse by display: {leftover_order!r}")

    again = merge_yoy_duplicate_elements()
    if any(r.get("code") == code for r in again):
        _fail(f"29260 rename must be idempotent: {again!r}")


def test_issuer_alias_ingest_and_migrate() -> None:
    from app.db import SessionLocal
    from app.monthly.maintenance import apply_issuer_element_aliases
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    cases = plan_issuer_alias_cases()
    by_code: dict[str, list[tuple[str, str, date]]] = {}
    after_until: dict[str, date] = {}
    for code, incoming, stored, ctx in cases:
        as_of = ctx.get("as_of")
        if not isinstance(as_of, date):
            continue
        if incoming != stored:
            by_code.setdefault(code, []).append((incoming, stored, as_of))
        else:
            # 複数 until では最早の境界を「新 as_of」側にする
            prev = after_until.get(code)
            if prev is None or as_of < prev:
                after_until[code] = as_of

    # 複数 until の銘柄は period を混ぜると同一 ingest で衝突するため、最早 as_of だけ使う
    for code, pairs in list(by_code.items()):
        min_as = min(p[2] for p in pairs)
        by_code[code] = [p for p in pairs if p[2] == min_as]

    c = TestClient(app)
    for i, (code, pairs) in enumerate(sorted(by_code.items())):
        as_of = pairs[0][2]
        year = as_of.year if as_of.month >= 6 else as_of.year - 1
        _add_fy_end(code, date(year, 6, 1), date(year, 6, 30), f"alias-fy-{code}-{year}")
        # many-to-one rename（複数 before → 同一 after）は同一 ingest で衝突するため stored で畳む
        rows = []
        seen_stored: set[str] = set()
        for j, (incoming, stored, _) in enumerate(pairs):
            if stored in seen_stored:
                continue
            seen_stored.add(stored)
            unit = "yoy_index" if stored.endswith("(前年比)") else "number"
            rows.append({
                "element": incoming,
                "values": {"6": 100.0 + j},
                "unit": unit,
            })
        r = c.post(f"/api/v1/tables/{code}/ingest", json={
            "months": [6],
            "rows": rows,
            "as_of": as_of.isoformat(),
        })
        _expect(r, 200, f"{code} ingest old as_of")
        mapped_year = r.json()["updated"][0]["year"]
        r = c.get(f"/api/v1/tables/{code}/{mapped_year}")
        _expect(r, 200, f"GET {code} {mapped_year}")
        names = [e["element"] for e in r.json()["elements"]]

        def _folded_stored(
            stored: str,
            *,
            year: int | None = None,
            as_of: date | None = None,
            code: str = code,
        ) -> str:
            # fold_aliased_element: dest が別 src なら保存名はもう一段 alias 後
            unit = "yoy_index" if stored.endswith("(前年比)") else "number"
            return canonicalize_element_label(
                apply_issuer_aliases(code, stored, year=year, as_of=as_of),
                unit,
            )

        for incoming, stored, _ in pairs:
            expect = _folded_stored(stored, year=mapped_year)
            if expect not in names:
                _fail(f"{code} stored missing {expect!r}: {names!r}")
            if incoming != expect and incoming in names:
                _fail(f"{code} incoming leftover {incoming!r}: {names!r}")

        # migrate / POST は一段 hop。stored が最終名の pair を使う
        incoming, stored, _ = next(
            (p for p in pairs if _folded_stored(p[1], as_of=as_of) == p[1]),
            pairs[0],
        )
        unit = "yoy_index" if stored.endswith("(前年比)") else "number"
        new_as_of = after_until.get(code)
        if new_as_of is not None:
            r = c.post(f"/api/v1/tables/{code}/ingest", json={
                "months": [6],
                "rows": [{"element": incoming, "values": {"6": 1.0}, "unit": unit}],
                "as_of": new_as_of.isoformat(),
            })
            _expect(r, 200, f"{code} ingest new as_of")
            new_name = r.json()["updated"][0]["element"]
            if new_name not in (incoming, stored) and incoming.endswith("(前年比)"):
                _fail(f"{code} as_of>={new_as_of} must keep {incoming!r}, got {new_name!r}")
            if incoming.endswith("(前年比)") is False:
                canon = canonicalize_element_label(incoming, unit)
                kept = r.json()["updated"][0]["element"]
                if kept not in (canon, stored):
                    _fail(f"{code} new as_of must keep {canon!r}, got {kept!r}")

        old_year = 2008 + i
        merge_year = 2007 + i
        db = SessionLocal()
        try:
            db.add(MonthlyTable(code=code, year=old_year))
            db.add(MonthlyElement(
                code=code, year=old_year, element=incoming,
                sort_order=1, unit=unit,
            ))
            db.add(MonthlyValue(
                code=code, year=old_year, element=incoming, month=5, value=105.2,
            ))
            db.add(MonthlyTable(code=code, year=merge_year))
            db.add(MonthlyElement(
                code=code, year=merge_year, element=stored,
                sort_order=1, unit=unit,
            ))
            db.add(MonthlyElement(
                code=code, year=merge_year, element=incoming,
                sort_order=2, unit=unit,
            ))
            db.add(MonthlyValue(
                code=code, year=merge_year, element=stored, month=8, value=98.3,
            ))
            db.add(MonthlyValue(
                code=code, year=merge_year, element=incoming, month=7, value=101.4,
            ))
            db.commit()
        finally:
            db.close()

        reports = apply_issuer_element_aliases()
        if not any(
            r.get("code") == code and r.get("year") == old_year and r.get("canonical") == stored
            for r in reports
        ):
            _fail(f"{code} {old_year} rename missing: {reports!r}")
        db = SessionLocal()
        try:
            names_old = sorted(
                e.element for e in db.query(MonthlyElement).filter_by(code=code, year=old_year)
            )
            if names_old != [stored]:
                _fail(f"{code} {old_year} after migrate: {names_old!r}")
            v = db.get(MonthlyValue, (code, old_year, stored, 5))
            if v is None or float(v.value) != 105.2:
                _fail(f"{code} {old_year} value not moved: {v}")
            names_merge = sorted(
                e.element for e in db.query(MonthlyElement).filter_by(code=code, year=merge_year)
            )
            if names_merge != [stored]:
                _fail(f"{code} {merge_year} merge leftover: {names_merge!r}")
            vals_merge = {
                v.month: float(v.value)
                for v in db.query(MonthlyValue).filter_by(code=code, year=merge_year)
            }
            if vals_merge != {7: 101.4, 8: 98.3}:
                _fail(f"{code} {merge_year} merged values: {vals_merge!r}")
        finally:
            db.close()

        later = (after_until[code].year + 1) if code in after_until else 2026 + i
        r = c.post(f"/api/v1/tables/{code}/{later}/elements", json={
            "element": incoming, "unit": unit,
        })
        _expect(r, 201, f"{code} POST after until.year")
        created = r.json()["element"]
        expect_kept = canonicalize_element_label(incoming, unit)
        if created != expect_kept:
            _fail(f"{code} year {later} must keep {expect_kept!r}, got {created!r}")

        again = apply_issuer_element_aliases()
        if any(r.get("code") == code and r.get("year") in {old_year, merge_year} for r in again):
            _fail(f"{code} alias migrate must be idempotent: {again!r}")


def test_reject_prior_period_element() -> None:
    blocked = (
        "前期計店舗数",
        "店舗数（前期）",
        "前期店舗数",
        "店舗数 前期",
        "国内リテール 前年同月店舗数",
        "ディスカウント 前年同月店舗数",
        "UNY 前年同月店舗数",
        "前年同期 店舗数",
        "前 年",
        "前年",
        "前年店舗数",
        "前年 全店 店舗数",
        "店舗数 前年",
    )
    allowed = (
        "当期計店舗数",
        "全店 売上高(前年比)",
        "売上高前年同月比",
        "前期比",
        "売上高前期対比",
        "売上高前年同期対比",
        "前期対比",
        "既存店 店舗数",
        "当年 全店 店舗数",
        "当年店舗数",
        "前年差",
        "MRR前年増減率",
        "NJSS MRR前年同期比(前年比)",
    )
    for name in blocked:
        _expect_ingest_error(
            lambda n=name: reject_prior_period_element(n),
            "PRIOR_PERIOD_ELEMENT",
            "前期の実数",
        )
    for name in allowed:
        reject_prior_period_element(name)

    c = TestClient(app)
    code = "88880"
    _add_fy_end(code, date(2026, 5, 1), date(2026, 3, 31), "prior-fy-1")
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [7],
        "rows": [
            {"element": "当期計店舗数", "values": {"7": 68}, "unit": "number"},
            {"element": "前期計店舗数", "values": {"7": 66}, "unit": "number"},
        ],
        "as_of": "2026-08-14",
    })
    _expect(r, 400, "ingest prior period")
    if _err(r)["code"] != "PRIOR_PERIOD_ELEMENT":
        _fail(f"ingest prior: {r.json()!r}")
    if "前期計店舗数" not in _err(r)["message"]:
        _fail(f"message missing name: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    if r.status_code == 200:
        names = [e["element"] for e in r.json()["elements"]]
        if "前期計店舗数" in names or "当期計店舗数" in names:
            _fail(f"prior ingest leftover: {names!r}")
    elif r.status_code != 404:
        _fail(f"prior ingest GET: {r.status_code} {r.text[:200]}")

    r = c.post(f"/api/v1/tables/{code}/2026/elements", json={
        "element": "店舗数（前期）", "unit": "number",
    })
    _expect(r, 400, "POST prior period")
    if _err(r)["code"] != "PRIOR_PERIOD_ELEMENT":
        _fail(f"POST prior: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [7],
        "rows": [
            {"element": "当年 全店 店舗数", "values": {"7": 85}, "unit": "number"},
            {"element": "前年 全店 店舗数", "values": {"7": 77}, "unit": "number"},
        ],
        "as_of": "2026-08-14",
    })
    _expect(r, 400, "ingest prior 前年店舗数")
    if _err(r)["code"] != "PRIOR_PERIOD_ELEMENT":
        _fail(f"ingest 前年 全店 店舗数: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [7],
        "rows": [
            {"element": "当年 全店 店舗数", "values": {"7": 85}, "unit": "number"},
        ],
        "as_of": "2026-08-14",
    })
    _expect(r, 200, "ingest 当年")
    r = c.get(f"/api/v1/tables/{code}/2026")
    _expect(r, 200, "GET after 当年 ingest")
    names = [e["element"] for e in r.json()["elements"]]
    if "全店 店舗数" not in names:
        _fail(f"当年 must save as 全店 店舗数: {names!r}")
    if any("当年" in n for n in names):
        _fail(f"当年 leftover: {names!r}")
    by = {e["element"]: e for e in r.json()["elements"]}
    if float(by["全店 店舗数"]["values"]["7"]) != 85.0:
        _fail(f"当年 values: {by['全店 店舗数']!r}")
    if by["全店 店舗数"]["label"]["group"] != "":
        _fail(f"当年 group: {by['全店 店舗数']['label']!r}")


def test_api_genky_year_wrap_ingest() -> None:
    """Genky 20250630 型: 13列で月6が2回。配列 values で暦年分割して両年に残る。"""
    c = TestClient(app)
    code = "92670"
    _add_fy_end(code, date(2025, 5, 1), date(2025, 6, 30), "genky-fy-1")
    _add_fy_end(code, date(2024, 5, 1), date(2024, 6, 30), "genky-fy-2")

    values = [
        104.6, 107.5, 106.7, 106.2, 110.4, 104.5, 109.4,
        111.1, 109.1, 106.5, 113.0, 108.8, 110.1,
    ]
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GENKY_MONTHS,
        "rows": [{"element": "売上高前年比（全店）", "unit": "yoy_index", "values": values}],
        "as_of": "2025-06-30",
    })
    _expect(r, 200, "genky ingest")
    body = r.json()
    if body.get("fy_end_month") != 6 or body.get("layout") != "partial":
        _fail(f"genky header: {body!r}")
    mapping = body.get("mapping")
    if not isinstance(mapping, list) or len(mapping) != 13:
        _fail(f"genky mapping shape: {mapping!r}")
    if mapping[0] != {"month": 6, "year": 2024} or mapping[-1] != {"month": 6, "year": 2025}:
        _fail(f"genky mapping ends: {mapping[0]!r} {mapping[-1]!r}")
    expect_years = [2024] * 7 + [2025] * 6
    if [item["year"] for item in mapping] != expect_years:
        _fail(f"genky mapping years: {mapping!r}")

    r = c.get(f"/api/v1/tables/{code}/2024")
    _expect(r, 200, "GET genky 2024")
    els = r.json()["elements"]
    if len(els) != 1:
        _fail(f"2024 elements: {els!r}")
    v2024 = _present_months(els[0]["values"])
    if set(v2024) != {"6", "7", "8", "9", "10", "11", "12"}:
        _fail(f"GET 2024 months: {v2024!r}")
    if abs(v2024["6"] - 104.6) > 1e-6:
        _fail(f"2024 June value: {v2024!r}")

    r = c.get(f"/api/v1/tables/{code}/2025")
    _expect(r, 200, "GET genky 2025")
    v2025 = _present_months(r.json()["elements"][0]["values"])
    if set(v2025) != {"1", "2", "3", "4", "5", "6"}:
        _fail(f"GET 2025 months: {v2025!r}")
    if abs(v2025["6"] - 110.1) > 1e-6:
        _fail(f"2025 June value: {v2025!r}")

    # 重複月なのに月キー object は拒否
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GENKY_MONTHS,
        "rows": [{"element": "客数", "unit": "yoy_index", "values": {"6": 1.0}}],
        "as_of": "2025-06-30",
    })
    _expect(r, 400, "genky ambiguous values")
    if _err(r)["code"] != "INVALID_PARAM":
        _fail(f"ambiguous: {r.json()!r}")

    # 列番号キーは許可
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": GENKY_MONTHS,
        "rows": [{"element": "客数前年比（既存店）", "unit": "yoy_index",
                  "values": {"0": 102.5, "12": 102.2}}],
        "as_of": "2025-06-30",
    })
    _expect(r, 200, "genky col-index values")
    r = c.get(f"/api/v1/tables/{code}/2024")
    names = [e["element"] for e in r.json()["elements"]]
    if "客数前年比（既存店）(前年比)" not in names and "客数前年比（既存店）" not in names:
        # canonicalize may add (前年比)
        if not any("客数" in n for n in names):
            _fail(f"col-index 2024 missing: {names!r}")


def test_fail_loud_required_and_conflicts() -> None:
    c = TestClient(app)
    code = "81110"
    _ensure_stock(code)
    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "店舗数", "values": {"1": 1}, "unit": "number"}],
    })
    _expect(r, 400, "AS_OF_REQUIRED")
    if _err(r)["code"] != "AS_OF_REQUIRED":
        _fail(f"AS_OF_REQUIRED: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/ingest", json={
        "months": [1],
        "rows": [{"element": "店舗数", "values": {"1": 1}}],
        "as_of": "2026-08-05",
    })
    _expect(r, 400, "UNIT_REQUIRED ingest")
    if _err(r)["code"] != "UNIT_REQUIRED":
        _fail(f"UNIT_REQUIRED ingest: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/2026/elements", json={"element": "店舗数"})
    _expect(r, 400, "UNIT_REQUIRED POST")
    if _err(r)["code"] != "UNIT_REQUIRED":
        _fail(f"UNIT_REQUIRED POST: {r.json()!r}")

    r = c.post(f"/api/v1/tables/{code}/2026/elements", json={"element": "店舗数", "unit": None})
    _expect(r, 400, "UNIT_REQUIRED null")
    if _err(r)["code"] != "UNIT_REQUIRED":
        _fail(f"UNIT_REQUIRED null: {r.json()!r}")

    r = c.post(
        f"/api/v1/tables/{code}/2026/elements",
        json={"element": "売上高前年比", "unit": "yoy_index"},
    )
    _expect(r, 201, "seed yoy_index")
    r = c.post(
        f"/api/v1/tables/{code}/2026/elements",
        json={"element": "売上高前年比", "unit": "yoy_pct"},
    )
    _expect(r, 200, "yoy_pct is stored yoy_index, not UNIT_CONFLICT")
    if r.json().get("unit") != "yoy_index":
        _fail(f"POST yoy_pct must store yoy_index: {r.json()!r}")
    r = c.get(f"/api/v1/tables/{code}/2026")
    if r.status_code == 200:
        by = {e["element"]: e for e in r.json()["elements"]}
        yoy = by.get("全店 売上高(前年比)")
        if yoy is not None and yoy.get("unit") != "yoy_index":
            _fail(f"stored unit after yoy_pct POST: {yoy!r}")

    r = c.post(
        f"/api/v1/tables/{code}/2026/elements",
        json={"element": "売上高前年比", "unit": "yoy_pct", "force": True},
    )
    _expect(r, 200, "force yoy_pct")
    if r.json().get("unit") != "yoy_index":
        _fail(f"force yoy_pct must return yoy_index: {r.json()!r}")

    fold_code = "81120"
    _add_fy_end(fold_code, date(2026, 5, 8), date(2025, 12, 31), "fold-fy-1")
    from app.db import SessionLocal
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    db = SessionLocal()
    try:
        db.add(MonthlyTable(code=fold_code, year=2026))
        db.add(MonthlyElement(
            code=fold_code, year=2026, element="全店 売上高(前年比)",
            sort_order=1, unit="yoy_index",
        ))
        db.add(MonthlyElement(
            code=fold_code, year=2026, element="全店 売上高前期比",
            sort_order=2, unit="yoy_index",
        ))
        db.add(MonthlyValue(
            code=fold_code, year=2026, element="全店 売上高(前年比)",
            month=1, value=100.0,
        ))
        db.add(MonthlyValue(
            code=fold_code, year=2026, element="全店 売上高前期比",
            month=1, value=200.0,
        ))
        db.commit()
    finally:
        db.close()

    r = c.post(f"/api/v1/tables/{fold_code}/ingest", json={
        "months": [1],
        "rows": [{"element": "全店 売上高(前年比)", "values": {"1": 100.0}, "unit": "yoy_index"}],
        "as_of": "2026-08-05",
    })
    # 保存名は凍結（#545）。ingest は完全一致の行にだけ書き、ずれた既存行は寄せない（付け替えは jobs/monthly/monthly_names.py）
    _expect(r, 200, "ingest leaves drifted row alone")
    db = SessionLocal()
    try:
        names = sorted(
            e.element for e in db.query(MonthlyElement).filter_by(code=fold_code, year=2026)
        )
        if names != ["全店 売上高(前年比)", "全店 売上高前期比"]:
            _fail(f"FOLD_CONFLICT must not delete src: {names!r}")
        kept = db.get(MonthlyValue, (fold_code, 2026, "全店 売上高(前年比)", 1))
        dropped = db.get(MonthlyValue, (fold_code, 2026, "全店 売上高前期比", 1))
        if kept is None or float(kept.value) != 100.0:
            _fail(f"FOLD_CONFLICT kept: {kept}")
        if dropped is None or float(dropped.value) != 200.0:
            _fail(f"FOLD_CONFLICT dropped row gone: {dropped}")
    finally:
        db.close()


def test_monthly_element_label() -> None:
    leftover = monthly_element_label("売上高", "yoy_index")
    want_left = {
        "display": "売上高(前年比)",
        "group": "",
        "metric_kind": "sales",
        "series_kind": "all_store",
        "country": "",
        "segment": "",
        "time_axis": "",
        "channel": "",
        "scope": "",
        "metric": "売上高",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if leftover != want_left:
        _fail(f"leftover 売上高: {leftover!r}")
    mkg = monthly_element_label("MKG 全店 売上高 ドラッグ(前年比)", "yoy_index")
    want_mkg = {
        "display": "MKG 全店 売上高 ドラッグ(前年比)",
        "group": "MKG",
        "metric_kind": "sales",
        "series_kind": None,
        "country": "",
        "segment": "MKG",
        "time_axis": "",
        "channel": "",
        "scope": "全店",
        "metric": "売上高",
        "breakdown": "ドラッグ",
        "breakdown_depth": 1,
    }
    if mkg != want_mkg:
        _fail(f"MKG: {mkg!r}")
    consol = monthly_element_label("連結売上高", "yoy_index")
    want_consol = {
        "display": "連結売上高(前年比)",
        "group": "",
        "metric_kind": "sales",
        "series_kind": "all_store",
        "country": "",
        "segment": "",
        "time_axis": "",
        "channel": "",
        "scope": "",
        "metric": "連結売上高",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if consol != want_consol:
        _fail(f"連結売上高: {consol!r}")
    profit = monthly_element_label("連結営業利益", "number")
    if profit["group"] != "" or profit["metric"] != "連結営業利益" or profit["display"] != "連結営業利益":
        _fail(f"連結営業利益: {profit!r}")
    hotel = monthly_element_label("グロース領域 全店 売上高 ホテル", "number")
    if hotel["breakdown"] != "ホテル" or hotel["breakdown_depth"] != 2 or hotel["group"] != "グロース領域":
        _fail(f"ホテル 小枝: {hotel!r}")
    prop = monthly_element_label("グロース領域 全店 売上高 プロパティ", "number")
    if prop["breakdown"] != "プロパティ" or prop["breakdown_depth"] != 1:
        _fail(f"プロパティ 中節: {prop!r}")
    gmv = monthly_element_label("CPAソリューション事業 取扱高", "number")
    want_gmv = {
        "display": "CPAソリューション事業 取扱高",
        "group": "CPAソリューション事業",
        "metric_kind": "other",
        "series_kind": None,
        "country": "",
        "segment": "CPAソリューション事業",
        "time_axis": "",
        "channel": "",
        "scope": "",
        "metric": "取扱高",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if gmv != want_gmv:
        _fail(f"取扱高 label: {gmv!r}")
    gmv_yoy = monthly_element_label("CPAソリューション事業 取扱高(前年比)", "yoy_index")
    if (
        gmv_yoy["group"] != "CPAソリューション事業"
        or gmv_yoy["metric"] != "取扱高"
        or gmv_yoy["display"] != "CPAソリューション事業 取扱高(前年比)"
    ):
        _fail(f"取扱高 yoy label: {gmv_yoy!r}")
    bare_gmv = monthly_element_label("取扱高", "number")
    if bare_gmv["group"] or bare_gmv["metric"] != "取扱高" or bare_gmv["scope"]:
        _fail(f"bare 取扱高 label: {bare_gmv!r}")
    room = monthly_element_label("ホテル 客室収入", "number")
    want_room = {
        "display": "ホテル 客室収入",
        "group": "ホテル",
        "metric_kind": "other",
        "series_kind": None,
        "country": "",
        "segment": "ホテル",
        "time_axis": "",
        "channel": "",
        "scope": "",
        "metric": "客室収入",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if room != want_room:
        _fail(f"客室収入 label: {room!r}")
    room_yoy = monthly_element_label("ホテル 客室収入(前年比)", "yoy_index")
    if (
        room_yoy["group"] != "ホテル"
        or room_yoy["metric"] != "客室収入"
        or room_yoy["scope"]
        or room_yoy["metric_kind"] != "other"
        or room_yoy["series_kind"] is not None
        or room_yoy["display"] != "ホテル 客室収入(前年比)"
    ):
        _fail(f"客室収入 yoy label: {room_yoy!r}")
    kado_yoy = monthly_element_label("ASKUL事業 稼働日修正後(前年比)", "yoy_index")
    if (
        kado_yoy["group"] != "ASKUL事業"
        or kado_yoy["metric"] != "稼働日修正後"
        or kado_yoy["scope"]
        or kado_yoy["metric_kind"] != "sales"
        or kado_yoy["series_kind"] is not None
        or kado_yoy["display"] != "ASKUL事業 稼働日修正後(前年比)"
    ):
        _fail(f"稼働日修正後 yoy label: {kado_yoy!r}")
    cust = monthly_element_label("ASKUL事業 購入お客様数(前年比)", "yoy_index")
    if (
        cust["group"] != "ASKUL事業"
        or cust["metric"] != "購入お客様数"
        or cust["scope"]
        or cust["metric_kind"] != "customers"
        or cust["series_kind"] is not None
        or cust["display"] != "ASKUL事業 購入お客様数(前年比)"
    ):
        _fail(f"購入お客様数 label: {cust!r}")
    spend = monthly_element_label("ASKUL事業 購入お客様 客単価(前年比)", "yoy_index")
    if (
        spend["group"] != "ASKUL事業"
        or spend["metric"] != "客単価"
        or spend["scope"]
        or spend["display"] != "ASKUL事業 客単価(前年比)"
    ):
        _fail(f"購入お客様 客単価 label: {spend!r}")
    meals = monthly_element_label("宅食 調理済み商品お届け数（千食）", "number")
    want_meals = {
        "display": "宅食 調理済み商品お届け数（千食）",
        "group": "宅食",
        "metric_kind": "other",
        "series_kind": None,
        "country": "",
        "segment": "宅食",
        "time_axis": "",
        "channel": "",
        "scope": "",
        "metric": "調理済み商品お届け数（千食）",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if meals != want_meals:
        _fail(f"お届け数 label: {meals!r}")
    offices = monthly_element_label("宅食 営業所数", "number")
    want_offices = {
        "display": "宅食 営業所数",
        "group": "宅食",
        "metric_kind": "other",
        "series_kind": None,
        "country": "",
        "segment": "宅食",
        "time_axis": "",
        "channel": "",
        "scope": "",
        "metric": "営業所数",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if offices != want_offices:
        _fail(f"営業所数 label: {offices!r}")
    bare_offices = monthly_element_label("営業所数", "number")
    if bare_offices["group"] or bare_offices["metric"] != "営業所数" or bare_offices["scope"]:
        _fail(f"bare 営業所数 label: {bare_offices!r}")
    members = monthly_element_label("会員数 既存(前年比)", "yoy_index")
    want_members = {
        "display": "会員数 既存(前年比)",
        "group": "会員数",
        "metric_kind": "other",
        "series_kind": None,
        "country": "",
        "segment": "会員数",
        "time_axis": "",
        "channel": "",
        "scope": "既存",
        "metric": "",
        "breakdown": "",
        "breakdown_depth": 0,
    }
    if members != want_members:
        _fail(f"会員数 既存 yoy label: {members!r}")
    bare_members = monthly_element_label("会員数(前年比)", "yoy_index")
    if (
        bare_members["display"] != "会員数(前年比)"
        or bare_members["group"] != "会員数"
        or bare_members["metric"]
        or bare_members["scope"]
        or bare_members["metric_kind"] != "other"
        or bare_members["series_kind"] is not None
    ):
        _fail(f"会員数 yoy label: {bare_members!r}")
    guests = monthly_element_label("客数 既存(前年比)", "yoy_index")
    if (
        guests["display"] != "既存 客数(前年比)"
        or guests["metric"] != "客数"
        or guests["scope"] != "既存"
        or guests["metric_kind"] != "customers"
        or guests["series_kind"] != "existing"
    ):
        _fail(f"客数 既存 yoy label: {guests!r}")


def test_department_store_layers() -> None:
    """3086 形の行。表示名は変えず、店舗/区分の group だけ揃える。銘柄分岐は置かない。"""
    ifrs = monthly_element_label("百貨店事業 売上収益(IFRS)(前年比)", "yoy_index")
    if (
        ifrs["display"] != "百貨店事業 売上収益(IFRS)(前年比)"
        or ifrs["group"] != "百貨店事業"
        or ifrs["segment"] != "百貨店事業"
        or ifrs["metric"] != "売上収益(IFRS)"
        or ifrs["metric_kind"] != "sales"
    ):
        _fail(f"売上収益(IFRS) label: {ifrs!r}")
    gross = monthly_element_label("百貨店事業 総額 売上高(前年比)", "yoy_index")
    if (
        gross["display"] != "百貨店事業 総額 売上高(前年比)"
        or gross["group"] != "百貨店事業"
        or gross["segment"] != "百貨店事業 総額"
        or gross["metric"] != "売上高"
        or gross["metric_kind"] != "sales"
    ):
        _fail(f"総額 売上高 label: {gross!r}")
    if monthly_row_segment("SC事業 売上収益(IFRS)(前年比)") != "SC事業":
        _fail("SC事業 IFRS group")
    if monthly_row_segment("SC事業 総額 売上高(前年比)") != "SC事業":
        _fail("SC事業 総額 group")
    store_sales = monthly_element_label("大丸 心斎橋店 売上高(前年比)", "yoy_index")
    store_in = monthly_element_label("大丸 心斎橋店 入店 客数(前年比)", "yoy_index")
    if store_sales["display"] != "大丸 心斎橋店 売上高(前年比)":
        _fail(f"store sales display: {store_sales!r}")
    if (
        store_in["display"] != "大丸 心斎橋店 入店 客数(前年比)"
        or store_in["group"] != "大丸 心斎橋店"
        or store_in["segment"] != "大丸 心斎橋店 入店"
        or store_in["metric"] != "客数"
        or store_in["metric_kind"] != "customers"
        or store_sales["group"] != store_in["group"]
    ):
        _fail(f"store 入店 group: sales={store_sales!r} in={store_in!r}")
    total_sales = monthly_element_label("百貨店事業合計 売上高(前年比)", "yoy_index")
    total_in = monthly_element_label("百貨店事業合計 入店 客数(前年比)", "yoy_index")
    if total_sales["display"] != "百貨店事業合計 売上高(前年比)":
        _fail(f"百貨店事業合計 display: {total_sales!r}")
    if total_sales["group"] != "百貨店事業合計" or total_in["group"] != "百貨店事業合計":
        _fail(f"百貨店事業合計 group: {total_sales['group']!r} {total_in['group']!r}")
    if total_sales["group"] == gross["group"]:
        _fail("百貨店事業合計 must not collapse onto 百貨店事業 総額")
    if canonicalize_element_label("百貨店事業 総額 売上高(前年比)", "yoy_index") == canonicalize_element_label(
        "百貨店事業合計 売上高(前年比)", "yoy_index",
    ):
        _fail("表て1 総額 and 表て2 合計 must stay distinct names")
    consol_ifrs = monthly_element_label("連結売上収益(IFRS)(前年比)", "yoy_index")
    consol_gross = monthly_element_label("総額 連結売上高(前年比)", "yoy_index")
    if consol_ifrs["display"] != "連結売上収益(IFRS)(前年比)" or consol_ifrs["group"]:
        _fail(f"連結売上収益(IFRS): {consol_ifrs!r}")
    if (
        consol_gross["display"] != "総額 連結売上高(前年比)"
        or consol_gross["group"]
        or consol_gross["segment"] != "総額"
        or consol_gross["metric"] != "連結売上高"
    ):
        _fail(f"総額 連結売上高: {consol_gross!r}")
    parco = monthly_element_label("パルコ テナント 全店 取扱高(前年比)", "yoy_index")
    parco_ex = monthly_element_label("パルコ テナント 既存 取扱高(前年比)", "yoy_index")
    if parco["group"] != "パルコ テナント" or parco_ex["group"] != "パルコ テナント":
        _fail(f"パルコ テナント group: {parco!r} {parco_ex!r}")
    if monthly_row_segment("パルコ 札幌PARCO テナント 取扱高(前年比)") != "パルコ 札幌PARCO テナント":
        _fail("PARCO store leftover stays store-level")
    other_canon = (
        ("大阪店 売上高(前年比)", "yoy_index", "大阪店 売上高(前年比)"),
        ("大阪店 入店 客数(前年比)", "yoy_index", "大阪店 入店 客数(前年比)"),
        ("髙島屋各店 既存 売上高(前年比)", "yoy_index", "髙島屋各店 既存 売上高(前年比)"),
        ("髙島屋各店 入店 既存 客数(前年比)", "yoy_index", "髙島屋各店 入店 既存 客数(前年比)"),
        ("日本 合計 売上高(前年比)", "yoy_index", "日本 売上高(前年比)"),
        ("合計 店舗数", "number", "店舗数"),
        ("全社合計 売上高(前年比)", "yoy_index", "全社 売上高(前年比)"),
        ("売上高合計(直営・FC)", "number", "直営+FC 売上高"),
        ("宅食 営業所数合計", "number", "宅食 営業所数"),
    )
    for src, unit, want in other_canon:
        got = canonicalize_element_label(src, unit)
        if got != want:
            _fail(f"other-issuer canon {src!r} -> {got!r} want {want!r}")
    if monthly_row_segment("大阪店 入店 客数(前年比)") != "大阪店":
        _fail("大阪店 入店 groups with 大阪店")
    if monthly_row_segment("髙島屋各店 入店 既存 客数(前年比)") != "髙島屋各店":
        _fail("髙島屋各店 入店 groups with 髙島屋各店")
    layers = parse_element_layers("髙島屋各店 入店 既存 客数")
    if layers["segment"] != "髙島屋各店 入店" or layers["scope"] != "既存" or layers["metric"] != "客数":
        _fail(f"髙島屋各店 入店 既存 layers must keep 入店 before 既存: {layers!r}")


_COUNT_CODE = "90000"  # 架空の銘柄。辞書の仕組みだけを見る（銘柄の分岐は書かない）
_COUNT_WORDS = frozenset({
    "受託施設数", "訪問介護事業所数", "居宅介護支援事業所数", "有料老人ホーム数",
    "総レセプト数(鍼灸込み)", "拠点数", "件数",
})


@contextmanager
def _issuer_count_words(code: str, words: frozenset[str]):
    saved = dict(monthly_ingest_mod.ISSUER_METRICS)
    monthly_ingest_mod.ISSUER_METRICS[code] = words
    try:
        yield
    finally:
        monthly_ingest_mod.ISSUER_METRICS.clear()
        monthly_ingest_mod.ISSUER_METRICS.update(saved)


def test_issuer_count_metrics() -> None:
    """件数指標は config/monthly/issuer_metrics.json の銘柄だけ。層だけに効き、保存名は並べ替えない。"""
    cases = (
        (
            "アスモフードサービス 受託施設数",
            {"segment": "アスモフードサービス", "channel": "", "scope": "",
             "metric": "受託施設数"},
            "count",
        ),
        (
            "アスモ介護サービス 訪問介護事業所数",
            {"segment": "アスモ介護サービス", "channel": "", "scope": "",
             "metric": "訪問介護事業所数"},
            "count",
        ),
        (
            "アスモ介護サービス 居宅介護支援事業所数",
            {"segment": "アスモ介護サービス", "channel": "", "scope": "",
             "metric": "居宅介護支援事業所数"},
            "count",
        ),
        (
            "アスモ介護サービス 有料老人ホーム数",
            {"segment": "アスモ介護サービス", "channel": "", "scope": "",
             "metric": "有料老人ホーム数"},
            "count",
        ),
        (
            "マッサージ事業 総レセプト数(鍼灸込み) 直営",
            {"segment": "マッサージ事業", "channel": "直営", "scope": "",
             "metric": "総レセプト数(鍼灸込み)"},
            "count",
        ),
        (
            "メディカルケア事業 拠点数",
            {"segment": "メディカルケア事業", "channel": "", "scope": "",
             "metric": "拠点数"},
            "count",
        ),
        (
            "数 出店",
            {"segment": "数", "channel": "", "scope": "", "metric": "出店"},
            "open",
        ),
        (
            "数 退店",
            {"segment": "数", "channel": "", "scope": "", "metric": "退店"},
            "close",
        ),
    )
    code_re = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]$")
    for code, words in load_issuer_metrics().items():
        if not code_re.fullmatch(code):
            _fail(f"issuer_metrics.json key must be a 5-char code: {code!r}")
        for word in words:
            # 共通の語彙（_METRIC_PREFIX）が先に剥がす語は辞書に置いても効かない
            if word.split() != [word] or monthly_ingest_mod._peel_metric(word)[0]:
                _fail(f"issuer_metrics.json {code} {word!r}: one token, not a common metric")

    with _issuer_count_words(_COUNT_CODE, _COUNT_WORDS):
        _check_count_cases(cases)
        # 辞書に無い銘柄では件数語は指標にならない（ほかの銘柄に波及しない）
        for src in ("アスモフードサービス 受託施設数", "メディカルケア事業 拠点数"):
            for other in (None, "90010"):
                lab = monthly_element_label(src, "number", other)
                if lab["metric"] or lab["metric_kind"] != "other" or lab["group"] != src:
                    _fail(f"count word leaked to code={other!r} {src!r}: {lab!r}")
        # 保存名は辞書を読まない
        if canonicalize_element_label("直営 タイムズパーキング 件数", "number") != "タイムズパーキング 件数 直営":
            _fail("issuer count words must not reorder stored names")
        # 完全一致だけ（売上個数 は 個数 に当たらない。辞書に無い …数 も拾わない）
        if parse_element_layers("ZAC 追加ライセンス数", code=_COUNT_CODE)["metric"]:
            _fail("count words must match whole tokens listed in the dictionary only")

    regressions = (
        ("店舗数", {"segment": "", "scope": "", "metric": "店舗数"}, "stores"),
        ("既存 店舗数", {"segment": "", "scope": "既存", "metric": "店舗数"}, "stores"),
        ("全店 店舗数", {"segment": "", "scope": "全店", "metric": "店舗数"}, "stores"),
        ("休業中 店舗数", {"segment": "休業中", "scope": "", "metric": "店舗数"}, "stores"),
        ("稼働店舗数", {"segment": "", "scope": "", "metric": "稼働店舗数"}, "stores"),
        ("出店", {"segment": "", "scope": "", "metric": "出店"}, "open"),
        ("退店", {"segment": "", "scope": "", "metric": "退店"}, "close"),
        ("転換出店", {"segment": "", "scope": "", "metric": "転換出店"}, "open"),
        ("売上高", {"segment": "", "scope": "", "metric": "売上高"}, "sales"),
        ("全店 売上高", {"segment": "", "scope": "全店", "metric": "売上高"}, "sales"),
        ("既存 売上高", {"segment": "", "scope": "既存", "metric": "売上高"}, "sales"),
        ("全社 売上高", {"segment": "", "scope": "全社", "metric": "売上高"}, "sales"),
        ("客数", {"segment": "", "scope": "", "metric": "客数"}, "customers"),
        ("既存 客数", {"segment": "", "scope": "既存", "metric": "客数"}, "customers"),
        ("全店 客数", {"segment": "", "scope": "全店", "metric": "客数"}, "customers"),
        ("客単価", {"segment": "", "scope": "", "metric": "客単価"}, "spend"),
        ("既存 客単価", {"segment": "", "scope": "既存", "metric": "客単価"}, "spend"),
        ("宅食 営業所数", {"segment": "宅食", "scope": "", "metric": "営業所数"}, "other"),
        ("会員数 既存", {"segment": "会員数", "scope": "既存", "metric": ""}, "other"),
    )
    for src, want_layers, kind in regressions:
        layers = parse_element_layers(src)
        for key, want in want_layers.items():
            if layers[key] != want:
                _fail(f"count regression {src!r} {key}: {layers[key]!r} want {want!r} in {layers!r}")
        lab = monthly_element_label(src, "number")
        if lab["metric_kind"] != kind:
            _fail(f"count regression metric_kind {src!r}: {lab['metric_kind']!r} want {kind!r}")
        if lab["scope"] != want_layers["scope"] or lab["metric"] != want_layers["metric"]:
            _fail(f"count regression label {src!r}: {lab!r}")

    yoy_reg = (
        ("全店 売上高(前年比)", "yoy_index", "売上高", "全店", "sales"),
        ("既存 客数(前年比)", "yoy_index", "客数", "既存", "customers"),
        ("会員数 既存(前年比)", "yoy_index", "", "既存", "other"),
        ("会員数(前年比)", "yoy_index", "", "", "other"),
    )
    for src, unit, metric, scope, kind in yoy_reg:
        lab = monthly_element_label(src, unit)
        if lab["metric"] != metric or lab["scope"] != scope or lab["metric_kind"] != kind:
            _fail(f"count yoy regression {src!r}: {lab!r}")

    flow_canon = (
        ("出店数", "出店"),
        ("新規出店数", "出店"),
        ("転換出店数", "転換出店"),
        ("退店数", "退店"),
        ("閉鎖 店舗数", "退店"),
    )
    for src, want in flow_canon:
        got = canonicalize_element_label(src, "number")
        if got != want:
            _fail(f"count flow regression {src!r} -> {got!r} want {want!r}")
        if parse_element_layers(src)["metric"] != want:
            _fail(f"count flow metric {src!r}: {parse_element_layers(src)!r}")

    if canonicalize_element_label("拠点数 調理済み商品お届け数(※)（千食）", "number") != "調理済み商品お届け数（千食）":
        _fail("拠点数 heading still yields お届け数")
    if monthly_element_label("会員数 既存(前年比)", "yoy_index")["display"] != "会員数 既存(前年比)":
        _fail("会員数 yoy display must stay leftover + scope")
    shop_var = parse_element_layers("店舗流通ネット 店舖リースの 取引店舖数")
    if shop_var["metric"] == "取引店舖数":
        _fail(f"店舖数 variant must not peel as count: {shop_var!r}")
    if canonicalize_element_label("西友 業態転換数 全店", "number") != "西友 全店 業態転換数":
        _fail("業態転換数 still reorders as adj metric")
    if canonicalize_element_label("既存 空室数", "number") != "空室数 既存":
        _fail("空室数 stays leftover so 既存 reorders after it")


def _check_count_cases(cases) -> None:
    for src, want_layers, kind in cases:
        if canonicalize_element_label(src, "number") != src:
            _fail(f"count metric must not rename {src!r} -> {canonicalize_element_label(src, 'number')!r}")
        layers = parse_element_layers(src, code=_COUNT_CODE)
        for key, want in want_layers.items():
            if layers[key] != want:
                _fail(f"count peel {src!r} {key}: {layers[key]!r} want {want!r} in {layers!r}")
        lab = monthly_element_label(src, "number", _COUNT_CODE)
        if lab["display"] != src:
            _fail(f"count peel display {src!r}: {lab['display']!r}")
        if lab["metric"] != want_layers["metric"] or lab["segment"] != want_layers["segment"]:
            _fail(f"count peel label layers {src!r}: {lab!r}")
        if lab["channel"] != want_layers["channel"] or lab["scope"] != want_layers["scope"]:
            _fail(f"count peel label channel/scope {src!r}: {lab!r}")
        if lab["metric_kind"] != kind:
            _fail(f"count peel metric_kind {src!r}: {lab['metric_kind']!r} want {kind!r}")
        if lab["series_kind"] is not None:
            _fail(f"count peel series_kind {src!r}: {lab!r}")
        if lab["group"] != want_layers["segment"]:
            _fail(f"count peel group {src!r}: {lab['group']!r}")


def test_count_metric_keeps_pre534_stored_names() -> None:
    """#534 の件数 peel は層だけ。保存名と起動時の寄せは #534 前と同じ。"""
    stored = (
        ("直営 タイムズパーキング 件数", "number", "タイムズパーキング 件数 直営"),
        ("直営 タイムズパーキング 台数", "number", "タイムズパーキング 台数 直営"),
        ("直営 タイムズパーキング 累計 件数", "number", "タイムズパーキング 件数 直営"),
        ("ZAC 既存追加ライセンス数", "number", "ZAC 追加ライセンス数 既存"),
        ("ZAC 既存 追加ライセンス数", "number", "ZAC 追加ライセンス数 既存"),
        ("売上個数 既存 売上高(前年比)", "yoy_index", "売上個数 既存 売上高(前年比)"),
        ("買上点数 全店 売上高(前年比)", "yoy_index", "買上点数 全店 売上高(前年比)"),
        ("撮影件数 既存 売上高(前年比)", "yoy_index", "撮影件数 既存 売上高(前年比)"),
        ("撮影件数 全店 売上高(前年比)", "yoy_index", "撮影件数 全店 売上高(前年比)"),
        ("全店 撮影件数", "yoy_index", "撮影件数 全店 売上高(前年比)"),
        ("直営 受託施設数", "number", "受託施設数 直営"),
        ("拠点数 調理済み商品お届け数(※)（千食）", "number", "調理済み商品お届け数（千食）"),
        ("拠点数 出店", "number", "出店"),
        ("メディカルケア事業 拠点数", "number", "メディカルケア事業 拠点数"),
    )
    for src, unit, want in stored:
        got = canonicalize_element_label(src, unit)
        if got != want:
            _fail(f"pre534 stored {src!r} {unit} -> {got!r} want {want!r}")

    already = (
        ("タイムズパーキング 件数 直営", "number"),
        ("ZAC 追加ライセンス数 既存", "number"),
        ("売上個数 既存 売上高(前年比)", "yoy_index"),
        ("買上点数 全店 売上高(前年比)", "yoy_index"),
        ("撮影件数 既存 売上高(前年比)", "yoy_index"),
        ("撮影件数 全店 売上高(前年比)", "yoy_index"),
        ("既存 売上高(前年比)", "yoy_index"),
        ("全店 売上高(前年比)", "yoy_index"),
        ("アスモフードサービス 受託施設数", "number"),
        ("マッサージ事業 総レセプト数(鍼灸込み) 直営", "number"),
    )
    for src, unit in already:
        got = canonicalize_element_label(src, unit)
        if got != src:
            _fail(f"saved name must stay {src!r} -> {got!r}")

    if canonicalize_element_label("買上点数 全店 売上高(前年比)", "yoy_index") == (
        canonicalize_element_label("全店 売上高(前年比)", "yoy_index")
    ):
        _fail("81980 買上点数 must not collide with 全店 売上高(前年比)")
    if canonicalize_element_label("売上個数 既存 売上高(前年比)", "yoy_index") == (
        canonicalize_element_label("既存 売上高(前年比)", "yoy_index")
    ):
        _fail("31740 売上個数 must not collide with 既存 売上高(前年比)")

    rows = [
        ("23050", 2026, "撮影件数 既存 売上高(前年比)", "yoy_index"),
        ("23050", 2026, "既存 売上高(前年比)", "yoy_index"),
        ("23050", 2026, "撮影件数 全店 売上高(前年比)", "yoy_index"),
        ("23050", 2026, "全店 売上高(前年比)", "yoy_index"),
        ("31740", 2026, "売上個数 既存 売上高(前年比)", "yoy_index"),
        ("31740", 2026, "既存 売上高(前年比)", "yoy_index"),
        ("81980", 2026, "買上点数 全店 売上高(前年比)", "yoy_index"),
        ("81980", 2026, "全店 売上高(前年比)", "yoy_index"),
        ("46660", 2026, "タイムズパーキング 件数 直営", "number"),
        ("39830", 2026, "ZAC 追加ライセンス数 既存", "number"),
    ]
    plans = plan_yoy_duplicate_merges(rows)
    if plans:
        _fail(f"startup merge must not fold count/sales rows: {plans!r}")

    from app.db import SessionLocal
    from app.monthly.maintenance import merge_yoy_duplicate_elements
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue

    code, year = "23050", 2026
    count_el = "撮影件数 既存 売上高(前年比)"
    sales_el = "既存 売上高(前年比)"
    _ensure_stock(code, "スタジオアリス")
    db = SessionLocal()
    try:
        db.add(MonthlyTable(code=code, year=year))
        db.add(MonthlyElement(
            code=code, year=year, element=count_el, sort_order=1, unit="yoy_index",
        ))
        db.add(MonthlyElement(
            code=code, year=year, element=sales_el, sort_order=2, unit="yoy_index",
        ))
        db.add(MonthlyValue(code=code, year=year, element=count_el, month=7, value=93.9))
        db.add(MonthlyValue(code=code, year=year, element=sales_el, month=7, value=101.2))
        db.commit()
    finally:
        db.close()

    reports = merge_yoy_duplicate_elements()
    if any(r.get("code") == code and r.get("year") == year for r in reports):
        _fail(f"23050 startup merge must not rewrite count yoy: {reports!r}")
    db = SessionLocal()
    try:
        names = {
            e.element for e in db.query(MonthlyElement).filter(
                MonthlyElement.code == code, MonthlyElement.year == year,
            )
        }
        if names != {sales_el, count_el}:
            _fail(f"23050 rows after merge: {names!r}")
        vals = {
            (v.element, v.month): float(v.value)
            for v in db.query(MonthlyValue).filter(
                MonthlyValue.code == code, MonthlyValue.year == year,
            )
        }
        if vals != {(count_el, 7): 93.9, (sales_el, 7): 101.2}:
            _fail(f"23050 values after merge: {vals!r}")
    finally:
        db.close()

    with _issuer_count_words(_COUNT_CODE, _COUNT_WORDS):
        asumo = monthly_element_label("アスモフードサービス 受託施設数", "number", _COUNT_CODE)
        park = monthly_element_label("直営 タイムズパーキング 件数", "number", _COUNT_CODE)
    if (
        asumo["metric"] != "受託施設数"
        or asumo["segment"] != "アスモフードサービス"
        or asumo["metric_kind"] != "count"
        or asumo["display"] != "アスモフードサービス 受託施設数"
    ):
        _fail(f"asmo count layers: {asumo!r}")
    if (
        park["display"] != "タイムズパーキング 件数 直営"
        or park["metric"] != "件数"
        or park["channel"] != "直営"
        or park["segment"] != "タイムズパーキング"
        or park["metric_kind"] != "count"
    ):
        _fail(f"park24 layers from raw: {park!r}")


def main() -> None:
    test_canonicalize_element_label()
    test_union_orders_country_then_channel()
    with tempfile.TemporaryDirectory() as d:
        test_alias_rename_takes_unit_from_names(Path(d))
    test_monthly_element_label()
    test_issuer_count_metrics()
    test_count_metric_keeps_pre534_stored_names()
    test_department_store_layers()
    test_issuer_alias_cases()
    test_mapping_examples()
    test_fy_end_and_layout_errors()
    test_api_gift_ingest()
    test_api_genky_year_wrap_ingest()
    test_tdnet_4digit_fy_end_lookup()
    test_api_errors()
    test_all_or_nothing_similar()
    test_api_alnum_code()
    test_unit_display_and_ingest()
    test_ingest_yoy_label_and_number_separate()
    test_uncovered_yoy_number_twins()
    test_ingest_yoy_number_conflict()
    test_ingest_yoy_short_and_canonical_same_row()
    test_existing_short_yoy_renamed_on_canonical()
    test_number_stores_not_yoy_suffix()
    test_merge_uchiyama_like_yoy_duplicates()
    test_rename_29260_style_cross_year_yoy()
    test_issuer_alias_ingest_and_migrate()
    test_reject_prior_period_element()
    test_fail_loud_required_and_conflicts()
    print("ok: monthly ingest")


if __name__ == "__main__":
    main()
