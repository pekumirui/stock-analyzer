"""PDF 月次列順と事業年度末月から暦年を割り当てる。year は呼び出し側が渡さない。"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal, Sequence, TypedDict

MONTHLY_UNITS = ("number", "yoy_index", "yoy_pct")
_YOY_UNITS = frozenset({"yoy_index", "yoy_pct"})
# 長い語を先に。前年同月比 を 前年同月 より先、括弧付きを 前年比 より先。
_YOY_FRAGMENTS = ("前年同月比", "前年同月", "(前年比)", "（前年比）", "前年比")
_YOY_SUFFIX = "(前年比)"
_UNIT_MSG = "unit は number / yoy_index / yoy_pct です。"
# 表示だけ。保存 unit は number|yoy_index。長い token を先に（受注高 ⊂ 新規受注高）。
MONTHLY_DISPLAY_UNITS: tuple[tuple[str, str, int], ...] = (
    ("1人当たり受注高", "万円", 0),
    ("新規受注高", "百万円", 0),
    ("キャンセル額", "百万円", 0),
    ("家賃ベース入居率", "%", 2),
    ("入居者斡旋件数", "件", 0),
    ("営業人員数", "人", 0),
    ("空室在庫", "カ月", 2),
    ("空室数", "戸", 0),
    ("受注高", "百万円", 0),
)


class MonthlyIngestError(Exception):
    """API の error.code に載せる。DB には触らない。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        extra: dict | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra or {}
        super().__init__(message)


def parse_monthly_unit(raw: Any, *, omitted_ok: bool = False) -> str | None:
    """許可文字列だけ。省略時は omitted_ok なら None。大きさでは決めない。"""
    if raw is None and omitted_ok:
        return None
    if raw is None:
        raise MonthlyIngestError(
            400, "UNIT_REQUIRED",
            "unit は number / yoy_index / yoy_pct です。省略できません。",
        )
    if not isinstance(raw, str) or raw not in MONTHLY_UNITS:
        raise MonthlyIngestError(400, "INVALID_PARAM", _UNIT_MSG)
    return raw


def coerce_monthly_storage(unit: str, value: float | None) -> tuple[str, float | None]:
    if unit != "yoy_pct":
        return unit, value
    if value is None:
        return "yoy_index", None
    return "yoy_index", value + 100.0


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
    """保存名・表示名。トークン順を揃え、yoy は (前年比) を1回。"""
    trimmed = element.strip()
    is_yoy = (unit or "number") in _YOY_UNITS
    if unit is None and ("(前年比)" in trimmed or "（前年比）" in trimmed):
        is_yoy = True
    base = strip_yoy_fragments(trimmed) if is_yoy else trimmed
    base = normalize_element_tokens(base, fill_default_metric=is_yoy)
    if not is_yoy:
        return base
    if base.endswith(_YOY_SUFFIX):
        return base
    return f"{base}{_YOY_SUFFIX}"


_UNGLUE: tuple[tuple[str, str], ...] = (
    ("単月Monthly", "単月 Monthly"),
    ("既存直営店", "直営 既存"),
    ("全店舗数", "店舗数"),
    ("総店舗数", "店舗数"),
    ("実店舗数", "実店舗 店舗数"),
    ("店舗数", "店舗数"),
    ("店数", "店舗数"),
    ("来客数", "来客数"),
    ("客単価", "客単価"),
    ("売上高", "売上高"),
    ("既存店舗", "既存"),
    ("既存店", "既存"),
    ("全店舗", "全店"),
    ("全店", "全店"),
    ("全社計", "全社"),
    ("フランチャイズ", "FC"),
    ("FC店舗", "FC"),
    ("FC店", "FC"),
    ("ＦＣ店舗", "FC"),
    ("ＦＣ店", "FC"),
    ("直営店", "直営"),
    ("直営", "直営"),
    ("既存", "既存"),
    ("月末", "月末"),
    ("期末", "期末"),
    ("合計", "合計"),
    ("総計", "総計"),
    ("小計", "小計"),
    ("部門", "部門"),
    ("うち", "うち"),
)
_CHAN_BOTH_MARK = "\x00CHANBOTH\x00"
_OPEN_CNT_MARK = "\x00OPENCNT\x00"
_OPEN_MARK = "\x00OPEN\x00"
_CLOSE_CNT_MARK = "\x00CLOSECNT\x00"
_CLOSE_MARK = "\x00CLOSE\x00"
_LEAVE_CNT_MARK = "\x00LEAVECNT\x00"
_LEAVE_MARK = "\x00LEAVE\x00"
_SHUT_CNT_MARK = "\x00SHUTCNT\x00"
_SHUT_MARK = "\x00SHUT\x00"
_LAUNCH_CNT_MARK = "\x00LAUNCHCNT\x00"
_LAUNCH_MARK = "\x00LAUNCH\x00"
_TENKAN_CNT_MARK = "\x00TENKANCNT\x00"
_TENKAN_MARK = "\x00TENKAN\x00"
_KAISETSU_CNT_MARK = "\x00KAISETSUCNT\x00"
_KAISETSU_MARK = "\x00KAISETSU\x00"
_TETTAI_CNT_MARK = "\x00TETTAICNT\x00"
_TETTAI_MARK = "\x00TETTAI\x00"
_FLOW_PROTECT: tuple[tuple[str, str], ...] = (
    ("転換出店数", _TENKAN_CNT_MARK),
    ("転換出店", _TENKAN_MARK),
    ("開店数", _LAUNCH_CNT_MARK),
    ("出店数", _OPEN_CNT_MARK),
    ("退店数", _LEAVE_CNT_MARK),
    ("閉店数", _SHUT_CNT_MARK),
    ("閉鎖数", _CLOSE_CNT_MARK),
    ("開設数", _KAISETSU_CNT_MARK),
    ("撤退数", _TETTAI_CNT_MARK),
    ("開店", _LAUNCH_MARK),
    ("出店", _OPEN_MARK),
    ("退店", _LEAVE_MARK),
    ("閉店", _SHUT_MARK),
    ("閉鎖", _CLOSE_MARK),
    ("開設", _KAISETSU_MARK),
    ("撤退", _TETTAI_MARK),
)
_FLOW_RESTORE: tuple[tuple[str, str], ...] = (
    (_TENKAN_CNT_MARK, "転換出店数"),
    (_TENKAN_MARK, "転換出店"),
    (_LAUNCH_CNT_MARK, "開店数"),
    (_OPEN_CNT_MARK, "出店数"),
    (_LEAVE_CNT_MARK, "退店数"),
    (_SHUT_CNT_MARK, "閉店数"),
    (_CLOSE_CNT_MARK, "閉鎖数"),
    (_KAISETSU_CNT_MARK, "開設数"),
    (_TETTAI_CNT_MARK, "撤退数"),
    (_LAUNCH_MARK, "開店"),
    (_OPEN_MARK, "出店"),
    (_LEAVE_MARK, "退店"),
    (_SHUT_MARK, "閉店"),
    (_CLOSE_MARK, "閉鎖"),
    (_KAISETSU_MARK, "開設"),
    (_TETTAI_MARK, "撤退"),
)
_FC_EMBED_RE = re.compile(r"(?<![A-Za-z])FC(?![A-Za-z])")
_FCW_EMBED_RE = re.compile(r"ＦＣ")
_DROP_TOKS = frozenset({
    "合計", "総計", "小計", "計", "月末", "期末", "うち", "部門", "店",
    "前期比", "同期比", "増減率", "前年比", "前年同月比", "前年同月", "全",
    "累計", "店舗状況", "拠点数", "（％）", "（%）", "%", "※",
    "（", "）", "(", ")", "(前年比)", "（前年比）",
})
# 表の注番号（SRS の 売上高(1) / 売上高① / (2) ＊M&A除く）。(前年比) は数字だけではないので残る。
_FOOTNOTE_NUM_RE = re.compile(r"[（(]\d+[）)]")
_FOOTNOTE_CIRCLED_RE = re.compile(r"[①-⑳]")
# 大黒天物産などの `2026年（令和8年）度` / `2026年度` 年度帯
_FY_NENDO_RE = re.compile(r"\d{4}\s*年(?:\s*[（(][^）)]*[）)])?\s*度")
_TIME_AXIS_TOKS = frozenset({"単月", "Monthly", "monthly"})
_COUNTRY_CANON = {"日本": "日本", "国内": "日本", "海外": "海外"}
_CHANNEL_CANON = {
    "直営+FC": "直営+FC", "直営＋FC": "直営+FC", "直営": "直営",
    "FC": "FC", "ＦＣ": "FC", "フランチャイズ": "FC",
}
_SCOPE_CANON = {
    "既存": "既存",
    "全店": "全店",
    "全社": "全社",
}
_METRIC_FILL_ALL = frozenset({"売上高", "客数", "客単価"})
_BREAKDOWN_CANON = {
    "ドラッグ": "ドラッグ",
    "ドラッグ店舗": "ドラッグ",
    "調剤": "調剤",
    "調剤店舗": "調剤",
    "物販": "物販",
}
_BREAKDOWN_MARKS = frozenset({"内)", "（内", "うち"})
_BREAKDOWN_RANK = {"": 0, "ドラッグ": 1, "調剤": 2, "物販": 3}
_STORE_FLOW_CANON = {
    "出店": "出店", "出店数": "出店",
    "開店": "出店", "開店数": "出店",
    "新規": "出店",
    "開設": "出店", "開設数": "出店",
    "転換出店": "転換出店", "転換出店数": "転換出店",
    "退店": "退店", "退店数": "退店",
    "閉店": "退店", "閉店数": "退店",
    "閉鎖": "退店", "閉鎖数": "退店",
    "撤退": "退店", "撤退数": "退店",
}
_STORE_FLOW_METRICS = frozenset({"出店", "退店", "転換出店"})
_OPEN_SHARDS = frozenset({"開"})
_CLOSE_SHARDS = frozenset({"退", "閉"})
_DUMMY_STORE_TOKS = frozenset({"店舗"})
_OPEN_QUALIFIERS = frozenset({"新規"})
_STORE_ADJ_MARKS = ("純増", "新設", "改装", "業態転換")
_METRIC_PREFIX: tuple[tuple[str, str], ...] = (
    ("店舗数", "店舗数"),
    ("来客数", "客数"),
    ("客単価", "客単価"),
    ("売上高", "売上高"),
    ("客数", "客数"),
    ("来客", "客数"),
    ("単価", "客単価"),
    ("売上", "売上高"),
)
_METRIC_STARTSWITH = frozenset({"店舗数", "来客数", "客単価", "売上高", "客数"})
_COUNTRY_RANK = {"": 0, "日本": 1, "海外": 2}
_CHANNEL_RANK = {"直営+FC": 0, "直営": 1, "FC": 2, "": 3}
_FC_TOKEN_RE = re.compile(r"^(?:FC|ＦＣ)$")
_METRIC_SHARD_PAIRS: tuple[tuple[str, str], ...] = (
    ("客", "数"),
    ("売", "上"),
)


def _stitch_metric_shards(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        if i + 1 < len(tokens) and (tokens[i], tokens[i + 1]) in _METRIC_SHARD_PAIRS:
            out.append(tokens[i] + tokens[i + 1])
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out


def _unglue_element(text: str) -> str:
    s = _FOOTNOTE_NUM_RE.sub(" ", text)
    s = _FOOTNOTE_CIRCLED_RE.sub(" ", s)
    s = _FY_NENDO_RE.sub(" ", s)
    s = re.sub(r"[（(]※[）)]", "", s)
    s = s.replace("※", "")
    for both in ("直営・ＦＣ", "直営･ＦＣ", "直営・FC", "直営･FC"):
        s = s.replace(both, "直営+FC")
    s = s.replace("直営+FC", f" {_CHAN_BOTH_MARK} ").replace(
        "直営＋FC", f" {_CHAN_BOTH_MARK} ",
    )
    for src, mark in _FLOW_PROTECT:
        s = s.replace(src, f" {mark} ")
    for src, dst in _UNGLUE:
        s = s.replace(src, f" {dst} ")
    s = _FC_EMBED_RE.sub(" FC ", s)
    s = _FCW_EMBED_RE.sub(" FC ", s)
    for mark, dst in _FLOW_RESTORE:
        s = s.replace(mark, dst)
    s = s.replace(_CHAN_BOTH_MARK, "直営+FC")
    return re.sub(r"\s+", " ", s).strip()


def _peel_metric(tok: str) -> tuple[str | None, str | None]:
    """指標語をトークンの前後から剥がす。残りはセグメント候補。

    短い接頭辞（売上/来客/単価）は前方一致しない（売上変化を売上高にしない）。
    """
    for src, canon in _METRIC_PREFIX:
        if tok == src:
            return canon, None
        if len(tok) > len(src) and tok.endswith(src):
            return canon, tok[:-len(src)]
        if src in _METRIC_STARTSWITH and len(tok) > len(src) and tok.startswith(src):
            return canon, tok[len(src):]
    return None, tok


def _is_flow_metric(metric: str) -> bool:
    return metric.endswith("出店") or metric.endswith("退店")


def _stitch_store_flow(leftover: list[str], metric: str) -> tuple[list[str], str]:
    """店数→店舗数 が 退店/開店/閉店 を割った残骸を指標へ戻す。新規は出店。"""
    if metric in ("", "店舗数"):
        if any(tok in _OPEN_SHARDS for tok in leftover):
            leftover = [tok for tok in leftover if tok not in _OPEN_SHARDS]
            metric = "出店"
        elif any(tok in _CLOSE_SHARDS for tok in leftover):
            leftover = [tok for tok in leftover if tok not in _CLOSE_SHARDS]
            metric = "退店"
    if _is_flow_metric(metric):
        leftover = [tok for tok in leftover if tok not in _DUMMY_STORE_TOKS]
    if metric.endswith("出店"):
        leftover = [tok for tok in leftover if tok not in _OPEN_QUALIFIERS]
    if metric == "出店" and "転換" in leftover:
        leftover = [tok for tok in leftover if tok != "転換"]
        metric = "転換出店"
    return leftover, metric


def _stitch_real_store(leftover: list[str], metric: str) -> tuple[list[str], str]:
    """店舗数 unglue が 実店舗 を 実 に割った残骸を戻す。"""
    if metric == "店舗数":
        leftover = ["実店舗" if tok == "実" else tok for tok in leftover]
    return leftover, metric


def _stitch_vs_sales(leftover: list[str], metric: str) -> tuple[list[str], str]:
    """対前年比の残り 対 を落とし、指標が空なら 売上高。"""
    had = False
    kept: list[str] = []
    for tok in leftover:
        if tok == "対":
            had = True
            continue
        if len(tok) > 1 and tok.endswith("対"):
            had = True
            tok = tok[:-1]
            if not tok or tok in _DROP_TOKS:
                continue
        kept.append(tok)
    if not metric and had:
        metric = "売上高"
    return kept, metric


def _peel_adj_metric(leftover: list[str], metric: str) -> tuple[list[str], str]:
    """業態転換などを指標にして 全店 が末尾に残らないようにする。名前は変えない。"""
    if metric:
        return leftover, metric
    kept: list[str] = []
    peeled = ""
    for tok in leftover:
        if not peeled and any(mark in tok for mark in _STORE_ADJ_MARKS):
            peeled = tok
            continue
        kept.append(tok)
    return kept, peeled


def _is_store_adj(element: str, segment: str = "") -> bool:
    blob = f"{segment}{element}"
    return any(mark in blob for mark in _STORE_ADJ_MARKS)


def _token_breakdown(tok: str) -> str:
    """売上高の内訳語。内)/（内/うち と 店舗 を剥がしてから照合する。"""
    t = tok
    for mark in ("内)", "（内", "うち"):
        if t.startswith(mark):
            t = t[len(mark):]
    t = t.replace("）", "").replace(")", "").replace("（", "").replace("(", "").strip()
    if t in _BREAKDOWN_CANON:
        return _BREAKDOWN_CANON[t]
    if t.endswith("店舗") and t[:-2] in _BREAKDOWN_CANON:
        return _BREAKDOWN_CANON[t[:-2]]
    return ""


def _peel_breakdown(leftover: list[str], metric: str) -> tuple[list[str], str]:
    """売上高の leftover 末尾/内) を内訳へ。調剤事業や内アパレルは残す。"""
    if metric != "売上高":
        return leftover, ""
    kept: list[str] = []
    breakdown = ""
    for tok in leftover:
        if tok in _BREAKDOWN_MARKS:
            continue
        bd = _token_breakdown(tok)
        if bd:
            breakdown = bd
            continue
        kept.append(tok)
    while kept:
        bd = _token_breakdown(kept[-1])
        if not bd:
            break
        breakdown = bd
        kept.pop()
    return kept, breakdown


def parse_element_layers(element: str) -> dict[str, str]:
    """国 / セグメント / 時間軸 / チャネル / スコープ / 指標 / 内訳。正規化前の名前でも読む。"""
    country = ""
    channel = ""
    scope = ""
    metric = ""
    time_axis = ""
    leftover: list[str] = []
    for tok in _stitch_metric_shards(_unglue_element(element).split()):
        if tok in _DROP_TOKS:
            continue
        if tok in _TIME_AXIS_TOKS:
            time_axis = "単月"
            continue
        flow = _STORE_FLOW_CANON.get(tok)
        if flow:
            if metric in ("", "店舗数"):
                metric = flow
            continue
        peeled, rest = _peel_metric(tok)
        if peeled:
            rest_flow = _STORE_FLOW_CANON.get(rest) if rest else None
            if rest_flow:
                if metric in ("", "店舗数"):
                    metric = rest_flow
            elif not (peeled == "店舗数" and _is_flow_metric(metric)):
                metric = peeled
                if rest and rest not in _DROP_TOKS:
                    leftover.append(rest)
            elif rest and rest not in _DROP_TOKS:
                leftover.append(rest)
            continue
        if tok in _COUNTRY_CANON:
            country = _COUNTRY_CANON[tok]
            continue
        mapped_ch = _CHANNEL_CANON.get(tok)
        if mapped_ch or _FC_TOKEN_RE.fullmatch(tok):
            channel = mapped_ch or "FC"
            continue
        mapped_scope = _SCOPE_CANON.get(tok)
        if mapped_scope:
            scope = mapped_scope
            continue
        leftover.append(tok)
    leftover, metric = _stitch_store_flow(leftover, metric)
    leftover, metric = _stitch_real_store(leftover, metric)
    leftover, metric = _stitch_vs_sales(leftover, metric)
    leftover, metric = _peel_adj_metric(leftover, metric)
    axis_left = [tok for tok in leftover if tok in _TIME_AXIS_TOKS]
    leftover = [tok for tok in leftover if tok not in _TIME_AXIS_TOKS]
    if axis_left and not time_axis:
        time_axis = "単月"
    if metric:
        time_axis = ""
    if _is_flow_metric(metric):
        scope = ""
        leftover = [tok for tok in leftover if tok != "（内"]
    if not scope and metric in _METRIC_FILL_ALL:
        scope = "全店"
    leftover, breakdown = _peel_breakdown(leftover, metric)
    return {
        "country": country,
        "segment": " ".join(leftover),
        "time_axis": time_axis,
        "channel": channel,
        "scope": scope,
        "metric": metric,
        "breakdown": breakdown,
    }


def normalize_element_tokens(element: str, *, fill_default_metric: bool = False) -> str:
    """空白区切り [国] [セグメント] [単月?] [チャネル] [スコープ] [指標] [内訳]。"""
    trimmed = re.sub(r"\s+", " ", element.strip())
    if not trimmed:
        return trimmed
    parts = parse_element_layers(trimmed)
    if (
        fill_default_metric
        and not parts["metric"]
        and parts["scope"]
        and not parts["channel"]
    ):
        parts["metric"] = "売上高"
        parts["time_axis"] = ""
        leftover, breakdown = _peel_breakdown(parts["segment"].split(), "売上高")
        parts["segment"] = " ".join(leftover)
        parts["breakdown"] = breakdown
    ordered = [
        parts["country"], parts["segment"], parts["time_axis"], parts["channel"],
        parts["scope"], parts["metric"], parts["breakdown"],
    ]
    built = " ".join(p for p in ordered if p)
    return built or trimmed


def _alias_unit(name: str) -> str:
    if any(frag in name for frag in ("(前年比)", "（前年比）", "前年比")):
        return "yoy_index"
    return "number"


class IssuerPeriodRename:
    """until 未満の as_of、または until.year 以下の year に rename を掛ける。"""

    def __init__(self, until: date, rename: dict[str, str]) -> None:
        self.until = until
        self.rename = rename


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
            rename_raw = item.get("rename")
            if not isinstance(until_raw, str) or not isinstance(rename_raw, dict):
                continue
            rename = {}
            for src, dst in rename_raw.items():
                src_s = str(src).strip()
                dst_s = str(dst).strip()
                if not src_s or not dst_s:
                    continue
                unit = _alias_unit(src_s) if _alias_unit(src_s) == "yoy_index" else _alias_unit(dst_s)
                key = canonicalize_element_label(src_s, unit)
                val = canonicalize_element_label(dst_s, unit)
                if key:
                    rename[key] = val or key
            rules.append(IssuerPeriodRename(date.fromisoformat(until_raw), rename))
        rules.sort(key=lambda rule: rule.until)
        out[str(code)] = tuple(rules)
    return out


ISSUER_ELEMENT_ALIASES = load_issuer_aliases()

_ROW_ORDER_PATH = Path(__file__).with_name("monthly_row_order.json")
_SEGMENT_TAIL_BASE = 10_000


def load_monthly_row_order(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """issuer code → segment_tail の表示順。無い銘柄は空。"""
    raw = json.loads((path or _ROW_ORDER_PATH).read_text(encoding="utf-8"))
    out: dict[str, tuple[str, ...]] = {}
    if not isinstance(raw, dict):
        return out
    for code, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        tail_raw = cfg.get("segment_tail")
        if not isinstance(tail_raw, list):
            continue
        tail: list[str] = []
        for item in tail_raw:
            name = str(item).strip()
            if name and name not in tail:
                tail.append(name)
        out[str(code)] = tuple(tail)
    return out


MONTHLY_ROW_ORDER = load_monthly_row_order()


def issuer_segment_tail(code: str | None) -> tuple[str, ...]:
    if not code:
        return ()
    return MONTHLY_ROW_ORDER.get(str(code), ())


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
        active = False
        if as_of is not None:
            active = as_of < rule.until
        elif year is not None:
            active = year <= rule.until.year
        if active:
            name = rule.rename.get(name, name)
    return name


def plan_issuer_alias_cases() -> list[tuple[str, str, str, dict[str, date | int]]]:
    """テスト用。(code, incoming, stored, ctx)。rename 表から as_of / year を生成。

    複数 until がある銘柄では、ある period の外側でも後続ルールが残る。
    境界の「非適用」側の expected は、残りのアクティブルール適用後の名前にする。
    dest 側の expected もアクティブルール適用後（canonicalize 後に dest が別 src になる場合がある）。
    """
    cases: list[tuple[str, str, str, dict[str, date | int]]] = []

    def _apply_all(
        rules: tuple[IssuerPeriodRename, ...],
        name: str,
        *,
        year: int | None = None,
        as_of: date | None = None,
    ) -> str:
        out = name
        for r in rules:
            active = False
            if as_of is not None:
                active = as_of < r.until
            elif year is not None:
                active = year <= r.until.year
            if active:
                out = r.rename.get(out, out)
        return out

    for code, rules in sorted(ISSUER_ELEMENT_ALIASES.items()):
        for rule in rules:
            before = rule.until - timedelta(days=1)
            after = rule.until
            year_on = rule.until.year
            year_off = rule.until.year + 1
            for src, dst in rule.rename.items():
                cases.append((code, src, dst, {"as_of": before}))
                cases.append((code, src, _apply_all(rules, src, as_of=after), {"as_of": after}))
                cases.append((code, src, dst, {"year": year_on}))
                cases.append((code, src, _apply_all(rules, src, year=year_off), {"year": year_off}))
                cases.append(
                    (code, dst, _apply_all(rules, dst, year=year_on), {"year": year_on}),
                )
    return cases


def same_yoy_element(
    stored_name: str,
    stored_unit: str | None,
    incoming_name: str,
    incoming_unit: str | None,
) -> bool:
    """incoming は canonicalize 済み。正規化後に同一なら同一行。

    number の `売上高` は yoy の `売上高(前年比)` とは別行。
    """
    if stored_name == incoming_name:
        return True
    stored_u = stored_unit or "number"
    incoming_u = incoming_unit or "number"
    stored_yoy = stored_u in _YOY_UNITS
    incoming_yoy = incoming_u in _YOY_UNITS
    if stored_yoy != incoming_yoy:
        return False
    return canonicalize_element_label(stored_name, stored_u) == incoming_name


def plan_yoy_duplicate_merges(
    rows: Sequence[tuple[str, int, str, str | None]],
) -> list[tuple[str, int, str, list[str]]]:
    """保存名を canonicalize へ。同一年の重複がなくても返す。

    number の `売上高` と yoy の `売上高(前年比)` は別行のまま。
    戻りは (code, year, canonical, short_names)。
    """
    groups: dict[tuple[str, int, str], set[str]] = {}
    for code, year, element, unit in rows:
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
    key = monthly_element_key(label)
    if key in ("前年", "前期", "前年同月", "前年同期"):
        raise MonthlyIngestError(
            400,
            "PRIOR_PERIOD_ELEMENT",
            f"「{label}」は前期の実数です。前年の同じ項目に既にあるので、この行は投入しないでください。",
        )
    if not _PRIOR_PERIOD_RE.search(key):
        return
    raise MonthlyIngestError(
        400,
        "PRIOR_PERIOD_ELEMENT",
        f"「{label}」は前期の実数です。前年の同じ項目に既にあるので、この行は投入しないでください。",
    )


def monthly_series_kind(element: str, unit: str | None) -> str | None:
    """TOP/チャート用。all_store / existing / stores。どれでもなければ None。

    全社・全店舗は会社/全店系。既存が付いていれば既存を優先。
    """
    key = monthly_element_key(element)
    u = unit or "number"
    if "店舗数" in key and u == "number":
        if any(mark in key for mark in _STORE_ADJ_MARKS):
            return None
        if _is_flow_metric(parse_element_layers(strip_yoy_fragments(element))["metric"]):
            return None
        return "stores"
    if u not in _YOY_UNITS:
        return None
    if _layers_of(element)["breakdown"]:
        return None
    if "既存" in key:
        return "existing"
    if "全店" in key or "全社" in key:
        return "all_store"
    return None


_SCOPE_RANK = {"all": 0, "company": 1, "existing": 2, "other": 3}
_METRIC_RANK = {
    "sales": 1, "customers": 2, "spend": 3,
    "open": 4, "close": 5, "adj": 6, "stores": 7, "other": 8,
}


def _layers_of(element: str) -> dict[str, str]:
    return parse_element_layers(strip_yoy_fragments(element))


def _row_scope(key: str) -> str:
    """既存 / 全店 / 全社。チャネルの直営・FC はスコープにしない。"""
    scope = _layers_of(key)["scope"]
    if scope == "既存":
        return "existing"
    if scope == "全店":
        return "all"
    if scope == "全社":
        return "company"
    return "other"


def _row_metric(key: str) -> str:
    layers = _layers_of(key)
    metric = layers["metric"]
    if metric.endswith("出店"):
        return "open"
    if metric.endswith("退店"):
        return "close"
    if _is_store_adj(key, layers["segment"]):
        return "adj"
    if metric == "店舗数":
        return "stores"
    if metric == "客単価":
        return "spend"
    if metric == "客数":
        return "customers"
    if metric == "売上高":
        return "sales"
    if any(mark in key for mark in _STORE_ADJ_MARKS):
        return "adj"
    if "店舗数" in key:
        return "stores"
    if "客単価" in key or "単価" in key:
        return "spend"
    if "客数" in key or "来客" in key:
        return "customers"
    if "売上" in key:
        return "sales"
    return "other"


def _row_segment(key: str) -> str:
    """ブランド。国・チャネル・スコープ・指標を除いた残り。純増・新規は塊にしない。"""
    toks = []
    for tok in _layers_of(key)["segment"].split():
        if tok in _OPEN_QUALIFIERS or any(mark in tok for mark in _STORE_ADJ_MARKS):
            continue
        toks.append(tok)
    return " ".join(toks)


def monthly_row_segment(element: str) -> str:
    """ブランド。`カラオケ 全店 売上高(前年比)` → カラオケ。全社/全店のみなら空。"""
    return _row_segment(element)


def monthly_row_slot(element: str, unit: str | None) -> int:
    """指標枠。1売上高 2客数 3客単価 4出店 5退店 6純増/新設/改装/業態転換 7店舗数 8その他。"""
    _ = unit
    return _METRIC_RANK[_row_metric(element)]


def monthly_row_breakdown_rank(element: str) -> int:
    """売上高の親が先。内訳は ドラッグ → 調剤 → 物販。"""
    bd = _layers_of(element)["breakdown"]
    return _BREAKDOWN_RANK.get(bd, 4)


def monthly_row_scope_rank(element: str) -> int:
    """0全店 1全社 2既存 3その他。スコープ無しの店舗数は 3（既存の後）。"""
    layers = _layers_of(element)
    if layers["scope"] == "既存":
        return _SCOPE_RANK["existing"]
    if layers["scope"] == "全社":
        return _SCOPE_RANK["company"]
    if layers["scope"] == "全店":
        return _SCOPE_RANK["all"]
    if _is_flow_metric(layers["metric"]) or layers["metric"] == "店舗数":
        return _SCOPE_RANK["other"]
    if _row_metric(element) in {"stores", "open", "close", "adj"}:
        return _SCOPE_RANK["other"]
    if not layers["segment"]:
        return _SCOPE_RANK["all"]
    return _SCOPE_RANK["other"]


def _row_country_rank(element: str) -> int:
    country = _layers_of(element)["country"]
    return _COUNTRY_RANK.get(country, 3)


def _row_channel_rank(element: str) -> int:
    channel = _layers_of(element)["channel"]
    return _CHANNEL_RANK.get(channel, 3)


def union_monthly_elements(
    tables: Sequence[Any],
    *,
    code: str | None = None,
) -> list[dict[str, str]]:
    """発行体の年をまたいだ行順。キーは表示名。

    国なし → 日本 → 海外。セグメント無しが先、名前付きは新しい年の初出順。
    segment_tail（monthly_row_order.json）は非 tail の後。
    各塊は 全店 → 既存 → チャネル（直営+FC → 直営 → FC）→
    売上高 → 客数 → 客単価 → 出店 → 退店 → 純増/新設/改装/業態転換 → 店舗数 → その他。
    スコープ無しの店舗数は既存の後。保存名は変えない。
    """
    resolved = code
    if resolved is None:
        for t in tables:
            if not t:
                continue
            c = t.get("code") if isinstance(t, dict) else getattr(t, "code", None)
            if c:
                resolved = str(c)
                break
    tail = issuer_segment_tail(resolved)
    tail_rank = {name: i for i, name in enumerate(tail)}

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
            if segment and segment not in tail_rank and segment not in seg_index:
                seg_index[segment] = len(seg_index)
            items.append({
                "element": display,
                "unit": unit,
                "segment": segment,
                "country": _row_country_rank(display),
                "scope": monthly_row_scope_rank(display),
                "channel": _row_channel_rank(display),
                "slot": monthly_row_slot(display, unit),
                "bd": monthly_row_breakdown_rank(display),
                "idx": len(items),
            })

    def _seg_key(segment: str) -> int:
        if not segment:
            return 0
        if segment in tail_rank:
            return _SEGMENT_TAIL_BASE + tail_rank[segment]
        return seg_index.get(segment, _SEGMENT_TAIL_BASE - 1)

    items.sort(key=lambda it: (
        0 if not it["segment"] else 1,
        it["country"],
        _seg_key(it["segment"]),
        it["scope"],
        it["channel"],
        it["slot"],
        it["bd"],
        it["idx"],
    ))
    return [{"element": it["element"], "unit": it["unit"]} for it in items]


def monthly_display_unit(element: str | None, unit: str | None = None) -> tuple[str, int] | None:
    if (unit or "number") != "number":
        return None
    name = element or ""
    for token, suffix, digits in MONTHLY_DISPLAY_UNITS:
        if token in name:
            return suffix, digits
    return None


def strip_monthly_display_unit(element: str | None, unit: str | None = None) -> str:
    name = element or ""
    if (unit or "number") != "number":
        return name
    for _token, suffix, _digits in MONTHLY_DISPLAY_UNITS:
        name = name.replace(f"（{suffix}）", "")
    return " ".join(name.split())


def monthly_display_label(element: str | None, unit: str | None = None) -> str:
    """画面の項目名だけ。保存名は単位なし。"""
    name = strip_monthly_display_unit(element, unit)
    spec = monthly_display_unit(name, unit)
    if spec is None:
        return name
    suffix, _digits = spec
    return name + f"（{suffix}）"


MetricKind = Literal["sales", "customers", "spend", "open", "close", "adj", "stores", "other"]
SeriesKind = Literal["all_store", "existing", "stores"]


class MonthlyElementLabel(TypedDict):
    display: str
    group: str
    metric_kind: MetricKind
    series_kind: SeriesKind | None
    country: str
    segment: str
    time_axis: str
    channel: str
    scope: str
    metric: str
    breakdown: str


def monthly_element_label(element: str, unit: str | None) -> MonthlyElementLabel:
    u = unit or "number"
    canon = canonicalize_element_label(strip_monthly_display_unit(element, u), u)
    display = monthly_display_label(canon, u)
    layers = _layers_of(canon)
    return {
        "display": display,
        "group": monthly_row_segment(canon),
        "metric_kind": _row_metric(canon),
        "series_kind": monthly_series_kind(canon, u),
        **layers,
    }


def _round_half_up(value: float, digits: int) -> Decimal:
    quant = Decimal(1).scaleb(-digits)
    return Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP)


def format_monthly_cell(
    value: float | None,
    unit: str | None = None,
    element: str | None = None,
) -> str:
    """空セルは -。前年比は保存値。number で表示単位がある行は四捨五入。単位は項目名。"""
    if value is None:
        return "-"
    n = float(value)
    if n != n or n in (float("inf"), float("-inf")):
        return "-"
    spec = monthly_display_unit(element, unit)
    if spec is None:
        text = f"{n:.4f}".rstrip("0").rstrip(".")
        return text if text else "0"
    _suffix, digits = spec
    text = format(_round_half_up(n, digits), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if not text:
        text = "0"
    return text


def parse_as_of(raw: Any, *, default: date | None = None) -> date:
    if raw is None:
        if default is not None:
            return default
        raise MonthlyIngestError(
            400, "AS_OF_REQUIRED",
            "as_of は必須です。YYYY-MM-DD で指定してください。",
        )
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
    """暦年レイアウト用。右端月が as_of 年に既に始まっていればその年、そうでなければ as_of.year-1。"""
    year = as_of.year
    if date(year, last_month, 1) <= as_of:
        return year
    return year - 1


def _current_fy_end_year(fy_end_month: int, as_of: date) -> int:
    """as_of が属する会計年度の期末年。

    新FYの先頭月（期末月の翌月）は、完了直後の期末速報とみなし直前FYの期末年を返す。
    それ以外は期末月より後なら翌年期末、そうでなければ当年期末。
    """
    if as_of.month == _fiscal_start_month(fy_end_month):
        if fy_end_month < as_of.month:
            return as_of.year
        return as_of.year - 1
    if as_of.month > fy_end_month:
        return as_of.year + 1
    return as_of.year


def _anchor_year_for_fy(last_month: int, fy_end_month: int, as_of: date) -> int:
    """fiscal: 現行 FY の期末年から右端列の暦年を決める。"""
    fy_end_year = _current_fy_end_year(fy_end_month, as_of)
    if last_month <= fy_end_month:
        return fy_end_year
    return fy_end_year - 1


def _assign_years(
    months: Sequence[int],
    as_of: date,
    *,
    fy_end_month: int | None = None,
) -> list[tuple[int, int]]:
    """列ごとに暦年。右端列から左へ。同じ月番号でも列が違えば年が違ってよい。
    fy_end_month あり → 会計年度アンカー。なし → 暦年アンカー。"""
    if fy_end_month is None:
        year = _anchor_year(months[-1], as_of)
    else:
        year = _anchor_year_for_fy(months[-1], fy_end_month, as_of)
    years: list[int] = [0] * len(months)
    years[-1] = year
    for i in range(len(months) - 2, -1, -1):
        if months[i] > months[i + 1]:
            year -= 1
        years[i] = year
    return list(zip(months, years))


def plan_column_years(
    months: Sequence[int],
    fy_end_month: int,
    as_of: date,
) -> tuple[str, list[tuple[int, int]]]:
    """列順 → 暦年。layout は calendar | fiscal | partial。並びでは拒否しない。
    戻り mapping は列順の (month, year)。Genky 型の月重複（13列）も列インデックス単位。
    calendar/partial は右端月+as_of。fiscal は fy_end+as_of で現行 FY を決める。"""
    if not months:
        raise MonthlyIngestError(400, "INVALID_PARAM", "months は1〜12の整数の配列です。")
    start = months[0]
    if _is_calendar_layout(months):
        layout = "calendar"
        mapping = _assign_years(months, as_of)
    elif start == _fiscal_start_month(fy_end_month):
        layout = "fiscal"
        # 期末月+as_of で現行 FY を決める（as_of が期末月より後なら期末は翌年）
        mapping = _assign_years(months, as_of, fy_end_month=fy_end_month)
    else:
        layout = "partial"
        # 部分列は右端月+as_of（暦年アンカー）。FY 全体を仮定しない。
        mapping = _assign_years(months, as_of)
    return layout, mapping


def map_pdf_columns(
    months: Sequence[int],
    fy_end_rows: Sequence[tuple[date | None, date | None]],
    as_of: date,
) -> tuple[int, str, list[tuple[int, int]]]:
    fy_end_month = resolve_fy_end_month(fy_end_rows, as_of)
    layout, mapping = plan_column_years(months, fy_end_month, as_of)
    return fy_end_month, layout, mapping


def serialize_column_mapping(
    mapping: Sequence[tuple[int, int]],
) -> dict[str, int] | list[dict[str, int]]:
    """一意な月は {month: year}、重複があるときは列順 [{month, year}, ...]。"""
    months = [m for m, _ in mapping]
    if len(months) == len(set(months)):
        return {str(month): year for month, year in mapping}
    return [{"month": month, "year": year} for month, year in mapping]
