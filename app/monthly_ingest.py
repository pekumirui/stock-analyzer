"""PDF 月次列順と事業年度末月から暦年を割り当てる。year は呼び出し側が渡さない。"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

# 保存値は PDF 原文。unit は表示だけ。値の大きさから推測しない。
MONTHLY_UNITS = ("number", "yoy_index", "yoy_pct")
_YOY_UNITS = frozenset({"yoy_index", "yoy_pct"})
# 長い語を先に。前年同月比 を 前年同月 より先、括弧付きを 前年比 より先。
_YOY_FRAGMENTS = ("前年同月比", "前年同月", "(前年比)", "（前年比）", "前年比")
_YOY_SUFFIX = "(前年比)"
_UNIT_MSG = "unit は number / yoy_index / yoy_pct です。"


class MonthlyIngestError(Exception):
    """API の error.code に載せる。DB には触らない。"""

    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def parse_monthly_unit(raw: Any, *, omitted_ok: bool = False) -> str | None:
    """許可文字列だけ。省略時は omitted_ok なら None。大きさでは決めない。"""
    if raw is None and omitted_ok:
        return None
    if not isinstance(raw, str) or raw not in MONTHLY_UNITS:
        raise MonthlyIngestError(400, "INVALID_PARAM", _UNIT_MSG)
    return raw


def strip_yoy_fragments(name: str) -> str:
    """PDF 由来の前年比語を繰り返し除去し、残り空白を畳む。"""
    s = name.strip()
    while True:
        prev = s
        for frag in _YOY_FRAGMENTS:
            s = s.replace(frag, "")
        s = re.sub(r"\s+", " ", s).strip()
        if s == prev:
            return s


def canonicalize_element_label(element: str, unit: str | None) -> str:
    """保存名・表示名。yoy は語を剥がして (前年比) を1回。number はトリムのみ。"""
    trimmed = element.strip()
    if (unit or "number") not in _YOY_UNITS:
        return trimmed
    base = strip_yoy_fragments(trimmed)
    if base.endswith(_YOY_SUFFIX):
        return base
    return f"{base}{_YOY_SUFFIX}"


class IssuerPeriodRename:
    """until 未満の as_of、または until.year 以下の year に rename / delete を掛ける。"""

    def __init__(
        self,
        until: date,
        rename: dict[str, str],
        delete: frozenset[str] = frozenset(),
    ) -> None:
        self.until = until
        self.rename = rename
        self.delete = delete


_ALIAS_PATH = Path(__file__).with_name("issuer_aliases.json")


def load_issuer_aliases(path: Path | None = None) -> dict[str, tuple[IssuerPeriodRename, ...]]:
    raw = json.loads((path or _ALIAS_PATH).read_text(encoding="utf-8"))
    out: dict[str, tuple[IssuerPeriodRename, ...]] = {}
    if not isinstance(raw, dict):
        return out
    for code, periods in raw.items():
        if not isinstance(periods, list):
            continue
        rules: list[IssuerPeriodRename] = []
        for item in periods:
            if not isinstance(item, dict):
                continue
            until_raw = item.get("until")
            if not isinstance(until_raw, str):
                continue
            rename_raw = item.get("rename")
            if rename_raw is None:
                rename_raw = {}
            if not isinstance(rename_raw, dict):
                continue
            delete_raw = item.get("delete")
            if delete_raw is None:
                delete_raw = []
            if not isinstance(delete_raw, list):
                continue
            rename = {
                str(src).strip(): str(dst).strip()
                for src, dst in rename_raw.items()
                if str(src).strip() and str(dst).strip()
                and str(src).strip() != str(dst).strip()
            }
            delete = frozenset(
                str(name).strip()
                for name in delete_raw
                if str(name).strip()
            )
            if not rename and not delete:
                continue
            rules.append(IssuerPeriodRename(date.fromisoformat(until_raw), rename, delete))
        rules.sort(key=lambda rule: rule.until)
        if rules:
            out[str(code)] = tuple(rules)
    return out


ISSUER_ELEMENT_ALIASES = load_issuer_aliases()


def _issuer_rule_active(
    rule: IssuerPeriodRename,
    *,
    year: int | None = None,
    as_of: date | None = None,
) -> bool:
    if as_of is not None:
        return as_of < rule.until
    if year is not None:
        return year <= rule.until.year
    return False


def apply_issuer_aliases(
    code: str,
    element: str,
    *,
    year: int | None = None,
    as_of: date | None = None,
) -> str:
    """canonicalize 後の行名。ingest は as_of、POST/PUT/migrate は year。"""
    name = element.strip()
    for rule in ISSUER_ELEMENT_ALIASES.get(code, ()):
        if _issuer_rule_active(rule, year=year, as_of=as_of):
            name = rule.rename.get(name, name)
    return name


def is_issuer_element_deleted(
    code: str,
    element: str,
    *,
    year: int | None = None,
    as_of: date | None = None,
) -> bool:
    """canonicalize 済みの保存名が delete リストにあれば真。ゲートは rename と同じ。"""
    name = element.strip()
    if not name:
        return False
    for rule in ISSUER_ELEMENT_ALIASES.get(code, ()):
        if _issuer_rule_active(rule, year=year, as_of=as_of) and name in rule.delete:
            return True
    return False


def plan_issuer_alias_cases() -> list[tuple[str, str, str, dict[str, date | int]]]:
    """テスト用。(code, incoming, stored, ctx)。rename 表から as_of / year を生成。"""
    cases: list[tuple[str, str, str, dict[str, date | int]]] = []
    for code, rules in sorted(ISSUER_ELEMENT_ALIASES.items()):
        for rule in rules:
            before = rule.until - timedelta(days=1)
            after = rule.until
            for src, dst in rule.rename.items():
                cases.append((code, src, dst, {"as_of": before}))
                cases.append((code, src, src, {"as_of": after}))
                cases.append((code, src, dst, {"year": rule.until.year}))
                cases.append((code, src, src, {"year": rule.until.year + 1}))
                cases.append((code, dst, dst, {"year": rule.until.year}))
    return cases


def plan_issuer_delete_cases() -> list[tuple[str, str, bool, dict[str, date | int]]]:
    """テスト用。(code, name, deleted, ctx)。delete 表から as_of / year を生成。"""
    cases: list[tuple[str, str, bool, dict[str, date | int]]] = []
    for code, rules in sorted(ISSUER_ELEMENT_ALIASES.items()):
        for rule in rules:
            before = rule.until - timedelta(days=1)
            after = rule.until
            for name in sorted(rule.delete):
                cases.append((code, name, True, {"as_of": before}))
                cases.append((code, name, False, {"as_of": after}))
                cases.append((code, name, True, {"year": rule.until.year}))
                cases.append((code, name, False, {"year": rule.until.year + 1}))
    return cases


def same_yoy_element(
    stored_name: str,
    stored_unit: str | None,
    incoming_name: str,
    incoming_unit: str | None,
) -> bool:
    """incoming は canonicalize 済み。yoy 既存行を同じ規則で正規化して同一行か見る。

    number の `売上高` は yoy の `売上高(前年比)` とは別行。
    """
    if stored_name == incoming_name:
        return True
    stored_u = stored_unit or "number"
    if stored_u not in _YOY_UNITS:
        return False
    if canonicalize_element_label(stored_name, stored_u) == incoming_name:
        return True
    if canonicalize_element_label(stored_name, incoming_unit) == incoming_name:
        return True
    return False


def plan_yoy_duplicate_merges(
    rows: Sequence[tuple[str, int, str, str | None]],
) -> list[tuple[str, int, str, list[str]]]:
    """yoy の短い保存名を正規名へ。同一年の重複が無くても返す。

    number は見ない（売上高 と 売上高(前年比) は別行のまま）。
    戻りは (code, year, canonical, short_names)。
    """
    groups: dict[tuple[str, int, str], set[str]] = {}
    for code, year, element, unit in rows:
        if (unit or "number") not in _YOY_UNITS:
            continue
        canonical = canonicalize_element_label(element, unit)
        if not canonical:
            continue
        groups.setdefault((code, year, canonical), set()).add(element)
    out: list[tuple[str, int, str, list[str]]] = []
    for (code, year, canonical), names in groups.items():
        shorts = sorted(name for name in names if name != canonical)
        if not shorts:
            continue
        out.append((code, year, canonical, shorts))
    out.sort()
    return out


def monthly_element_key(name: str) -> str:
    s = unicodedata.normalize("NFKC", name)
    return re.sub(r"\s+", "", s)


_PRIOR_PERIOD_RE = re.compile(r"(前期|前年同月|前年同期)(?!比)")


def reject_prior_period_element(element: str) -> None:
    """前期・前年同月の実数行は入れない。比（前期比など）は今期の比率なので通す。"""
    label = element.strip()
    if not _PRIOR_PERIOD_RE.search(monthly_element_key(label)):
        return
    raise MonthlyIngestError(
        400,
        "PRIOR_PERIOD_ELEMENT",
        f"「{label}」は前期の実数です。前年の同じ項目に既にあるので、この行は投入しないでください。",
    )


def monthly_series_kind(element: str, unit: str | None) -> str | None:
    """TOP/チャート用。all_store / existing / stores。どれでもなければ None。"""
    key = monthly_element_key(element)
    u = unit or "number"
    if "店舗数" in key and u == "number":
        return "stores"
    if u not in _YOY_UNITS:
        return None
    if "既存" in key:
        return "existing"
    if "全店" in key:
        return "all_store"
    return None


def monthly_row_segment(element: str) -> str:
    """全店 / 既存 / 店舗数 より前。`カラオケ全店 売上高(前年比)` → カラオケ。無ければ空。"""
    key = monthly_element_key(element)
    cut: int | None = None
    for kw in ("全店", "既存", "店舗数"):
        i = key.find(kw)
        if i >= 0 and (cut is None or i < cut):
            cut = i
    if cut is None:
        return ""
    return key[:cut]


def monthly_row_slot(element: str, unit: str | None) -> int:
    """セグメント内の行枠。1全店売上 2既存店売上 3客単価 4店舗数 5その他。店舗数は売上に入れない。"""
    key = monthly_element_key(element)
    u = unit or "number"
    is_yoy = u in _YOY_UNITS
    if (
        is_yoy
        and "売上" in key
        and "全店" in key
        and "既存" not in key
        and "店舗数" not in key
    ):
        return 1
    if is_yoy and "売上" in key and "既存" in key and "店舗数" not in key:
        return 2
    if "客単価" in key or "単価" in key:
        return 3
    if "店舗数" in key:
        return 4
    return 5


def union_monthly_elements(tables: Sequence[Any]) -> list[dict[str, str]]:
    """発行体の年をまたいだ行順。キーは表示名。新しい年から初出のセグメント、各ブロックは 5 枠。"""
    newest_first = sorted(
        (t for t in tables if t),
        key=lambda t: int(t.get("year") or 0),
        reverse=True,
    )
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    seg_index: dict[str, int] = {}
    for table in newest_first:
        for raw in table.get("elements") or []:
            if not isinstance(raw, dict):
                continue
            name = raw.get("element")
            if not isinstance(name, str) or not name:
                continue
            unit = raw.get("unit") or "number"
            if not isinstance(unit, str):
                unit = "number"
            display = canonicalize_element_label(name, unit)
            if display in seen:
                continue
            seen.add(display)
            segment = monthly_row_segment(display)
            if segment not in seg_index:
                seg_index[segment] = len(seg_index)
            items.append({
                "element": display,
                "unit": unit,
                "segment": segment,
                "slot": monthly_row_slot(display, unit),
                "idx": len(items),
            })
    items.sort(key=lambda it: (seg_index[it["segment"]], it["slot"], it["idx"]))
    return [{"element": it["element"], "unit": it["unit"]} for it in items]


def yoy_display_number(value: float, unit: str | None) -> float:
    n = float(value)
    if (unit or "number") == "yoy_pct":
        n = 100.0 + n
    return n


def format_monthly_cell(value: float | None, unit: str | None) -> str:
    """空セルは空。表は指数 100=フラット。保存値は変えない。"""
    if value is None:
        return ""
    n = float(value)
    if n != n or n in (float("inf"), float("-inf")):
        return ""
    u = unit or "number"
    if u == "yoy_pct":
        n = 100.0 + n
    text = f"{n:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def parse_as_of(raw: Any, *, default: date | None = None) -> date:
    if raw is None:
        return default if default is not None else date.today()
    if not isinstance(raw, str) or not raw:
        raise MonthlyIngestError(400, "INVALID_PARAM", "as_of は YYYY-MM-DD です。")
    try:
        parsed = date.fromisoformat(raw)
    except ValueError:
        raise MonthlyIngestError(400, "INVALID_PARAM", "as_of は YYYY-MM-DD です。") from None
    if parsed.isoformat() != raw:
        raise MonthlyIngestError(400, "INVALID_PARAM", "as_of は YYYY-MM-DD です。")
    return parsed


def resolve_fy_end_month(
    rows: Sequence[tuple[date | None, date | None]],
    as_of: date,
) -> int:
    """as_of 以前の最新開示の事業年度末月。同日は期末の新しい方。過去の期変更は見ない。"""
    eligible: list[tuple[date, date]] = []
    for disclosed, fy_end in rows:
        if disclosed is None or fy_end is None:
            continue
        if disclosed <= as_of:
            eligible.append((disclosed, fy_end))
    if not eligible:
        raise MonthlyIngestError(
            400, "NEED_FY_END",
            "この銘柄の決算に事業年度末 (fiscal_year_end) がありません。",
        )
    _disclosed, fy_end = max(eligible, key=lambda item: (item[0], item[1]))
    return fy_end.month


def _is_calendar_layout(months: Sequence[int]) -> bool:
    # 先頭が1で暦月昇順。欠月は可。年をまたぐ並び (12→1) は暦年レイアウトではない。
    if not months or months[0] != 1:
        return False
    for i in range(1, len(months)):
        if months[i] <= months[i - 1]:
            return False
    return True


def _fiscal_start_month(fy_end_month: int) -> int:
    return (fy_end_month % 12) + 1


def _anchor_year(last_month: int, as_of: date) -> int:
    year = as_of.year
    if date(year, last_month, 1) <= as_of:
        return year
    return year - 1


def _assign_years(months: Sequence[int], as_of: date) -> dict[int, int]:
    year = _anchor_year(months[-1], as_of)
    assigned: dict[int, int] = {months[-1]: year}
    for i in range(len(months) - 2, -1, -1):
        prev_month = months[i]
        curr_month = months[i + 1]
        if prev_month > curr_month:
            year -= 1
        assigned[prev_month] = year
    return {month: assigned[month] for month in months}


def plan_column_years(
    months: Sequence[int],
    fy_end_month: int,
    as_of: date,
) -> tuple[str, dict[int, int]]:
    """列順 → 暦年。layout は calendar | fiscal | partial。並びでは拒否しない。"""
    if not months:
        raise MonthlyIngestError(400, "INVALID_PARAM", "months は1〜12の重複しない整数の配列です。")
    start = months[0]
    if _is_calendar_layout(months):
        layout = "calendar"
    elif start == _fiscal_start_month(fy_end_month):
        layout = "fiscal"
    else:
        layout = "partial"
    return layout, _assign_years(months, as_of)


def map_pdf_columns(
    months: Sequence[int],
    fy_end_rows: Sequence[tuple[date | None, date | None]],
    as_of: date,
) -> tuple[int, str, dict[int, int]]:
    fy_end_month = resolve_fy_end_month(fy_end_rows, as_of)
    layout, mapping = plan_column_years(months, fy_end_month, as_of)
    return fy_end_month, layout, mapping
