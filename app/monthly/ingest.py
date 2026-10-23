"""PDF 月次列順と事業年度末月から暦年を割り当てる。year は呼び出し側が渡さない。"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Literal, Sequence, TypedDict

from app.config import ISSUER_ALIASES_PATH, ISSUER_METRICS_PATH, MONTHLY_ROW_ORDER_PATH, MONTHLY_VOCAB_PATH
from app.monthly.calendar import anchor_year as _anchor_year

MONTHLY_UNITS = ("number", "yoy_index", "yoy_pct")
_YOY_UNITS = frozenset({"yoy_index", "yoy_pct"})
_YOY_SUFFIX = "(前年比)"
_UNIT_MSG = "unit は number / yoy_index / yoy_pct です。"


_ISSUER_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]$")
_STORE_COUNT_SPLIT = frozenset({"店舗数", "店数"})
_CHANNEL_STORE_SPLIT = frozenset({
    "直営店舗", "直営店", "FC店舗", "FC店", "ＦＣ店舗", "ＦＣ店",
})
_SHORT_METRIC_NO_PREFIX = frozenset({"売上", "来客", "単価"})


def _require_longest_first(words: Sequence[str], *, field: str) -> None:
    for i, short in enumerate(words):
        if not short:
            raise ValueError(f"{field} に空の語があります")
        for long in words[i + 1:]:
            if short != long and short in long:
                raise ValueError(f"{field}: {short!r} が {long!r} より先です")


def _require_store_count_before_channel_store(pairs: Sequence[Any], *, field: str) -> None:
    store_at = [i for i, item in enumerate(pairs) if item[0] in _STORE_COUNT_SPLIT]
    if not store_at:
        return
    first_store = min(store_at)
    for i, item in enumerate(pairs):
        src = item[0]
        if src in _CHANNEL_STORE_SPLIT and i < first_store:
            raise ValueError(f"{field}: {src!r} が 店舗数 より先です")


def _require_short_metrics_without_prefix(metrics: Sequence[Any], *, field: str) -> None:
    for item in metrics:
        if not isinstance(item, dict):
            raise ValueError(f"{field} の要素は object です")
        word = str(item.get("word") or "")
        if word in _SHORT_METRIC_NO_PREFIX and item.get("prefix"):
            raise ValueError(f"{field}: {word!r} に prefix を付けられません")


def load_monthly_vocab(path: Path | None = None) -> dict[str, Any]:
    """全銘柄に共通の行名の語彙。語順と発行体例外はここで強制する。"""
    raw = json.loads((path or MONTHLY_VOCAB_PATH).read_text(encoding="utf-8"))
    keys = (
        "yoy_words", "split", "country", "channel", "scope", "time_axis", "drop",
        "store_flow", "breakdown", "metrics", "metric_kind",
    )
    missing = [k for k in keys if k not in raw]
    if missing:
        raise ValueError(f"monthly_vocab.json に {missing} がありません")
    yoy = [str(w) for w in raw["yoy_words"]]
    _require_longest_first(yoy, field="monthly_vocab.json yoy_words")
    raw["yoy_words"] = yoy
    split = [(str(src), str(dst)) for src, dst in raw["split"]]
    _require_longest_first([src for src, _dst in split], field="monthly_vocab.json split")
    _require_store_count_before_channel_store(split, field="monthly_vocab.json split")
    raw["split"] = split
    metrics = list(raw["metrics"])
    _require_short_metrics_without_prefix(metrics, field="monthly_vocab.json metrics")
    _require_longest_first(
        [str(item["word"]) for item in metrics],
        field="monthly_vocab.json metrics",
    )
    return raw


_VOCAB = load_monthly_vocab()
_YOY_FRAGMENTS: tuple[str, ...] = tuple(_VOCAB["yoy_words"])
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
_require_longest_first(
    [token for token, _suffix, _digits in MONTHLY_DISPLAY_UNITS],
    field="MONTHLY_DISPLAY_UNITS",
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


def canonicalize_element_label(
    element: str, unit: str | None, code: str | None = None,
) -> str:
    """保存名・表示名。トークン順を揃え、yoy は (前年比) を1回。

    code があると、その銘柄の issuer_metrics を共通語彙より先に見る。
    """
    trimmed = element.strip()
    is_yoy = (unit or "number") in _YOY_UNITS
    if unit is None and ("(前年比)" in trimmed or "（前年比）" in trimmed):
        is_yoy = True
    base = strip_yoy_fragments(trimmed) if is_yoy else trimmed
    base = normalize_element_tokens(base, fill_default_metric=is_yoy, code=code)
    if not is_yoy:
        return base
    if base.endswith(_YOY_SUFFIX):
        return base
    return f"{base}{_YOY_SUFFIX}"


_UNGLUE: tuple[tuple[str, str], ...] = tuple((str(src), str(dst)) for src, dst in _VOCAB["split"])
_UNGLUE_KEEP = ("ヴィンテージ商品売上高", "百貨店事業合計")
# yoy でスコープ+この leftover だけなら売上高を補わず指標位置へ（31740）。
# 共通語彙の指標にすると `売上総利益 既存 売上高(前年比)` が `既存 売上高` に潰れる。
_YOY_SCOPE_LEFTOVER_METRICS = frozenset({"売上総利益", "売上個数"})
_FLOW_WORDS = (
    "転換出店数", "転換出店", "開店数", "出店数", "退店数", "閉店数", "閉鎖数",
    "開設数", "撤退数", "稼働店舗数", "取引店舗数", "開店", "出店", "退店",
    "閉店", "閉鎖", "開設", "撤退",
)


def _require_compounds_kept(compounds: Sequence[str], split: Sequence[tuple[str, str]]) -> None:
    srcs = [src for src, _dst in split]
    for word in compounds:
        if not any(src != word and src in word for src in srcs):
            raise ValueError(f"{word!r} は split に切れないので protect から外してください")


_require_compounds_kept(_UNGLUE_KEEP, _UNGLUE)
_require_longest_first(_UNGLUE_KEEP + _FLOW_WORDS, field="flow protect")
_CHAN_BOTH_MARK = "\x00CHANBOTH\x00"
_STATUS_STORES_MARK = "\x00STATUSSTORES\x00"


def _flow_marks(words: Sequence[str]) -> tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]:
    protect: list[tuple[str, str]] = []
    restore: list[tuple[str, str]] = []
    for i, word in enumerate(words):
        mark = f"\x00F{i}\x00"
        protect.append((word, mark))
        restore.append((mark, word))
    return tuple(protect), tuple(restore)


_FLOW_PROTECT, _FLOW_RESTORE = _flow_marks(_UNGLUE_KEEP + _FLOW_WORDS)
_STATUS_STORE_AFFIX = "(?:中|予定|準備中)"
_STATUS_STORE_SPACED: tuple[tuple[str, bool | None], ...] = (
    ("一時閉店", True),
    ("開店準備", True),
    ("出店予定", None),
    ("閉店", False),
    ("閉鎖", False),
    ("休業", True),
)
_STATUS_STORE_GLUED = ("閉鎖",)


def _status_store_pattern() -> str:
    parts: list[str] = []
    for stem, optional in _STATUS_STORE_SPACED:
        if optional is None:
            parts.append(stem)
        elif optional:
            parts.append(f"{stem}{_STATUS_STORE_AFFIX}?")
        else:
            parts.append(f"{stem}{_STATUS_STORE_AFFIX}")
    spaced = "(?:" + "|".join(parts) + r")[の 　]*(?:店舗数|店数)"
    glued = "|".join(f"{stem}(?:店舗数|店数)" for stem in _STATUS_STORE_GLUED)
    return f"{spaced}|{glued}"


_STATUS_STORE_RE = re.compile(_status_store_pattern())
_FC_EMBED_RE = re.compile(r"(?<![A-Za-z])FC(?![A-Za-z])")
_FCW_EMBED_RE = re.compile(r"ＦＣ")
_DROP_TOKS = frozenset(_VOCAB["drop"])
# 表の注番号（SRS の 売上高(1) / 売上高① / (2) ＊M&A除く）。(前年比) は数字だけではないので残る。
_FOOTNOTE_NUM_RE = re.compile(r"[（(]\d+[）)]")
_FOOTNOTE_CIRCLED_RE = re.compile(r"[①-⑳]")
# 大黒天物産などの `2026年（令和8年）度` / `2026年度` 年度帯
_FY_NENDO_RE = re.compile(r"\d{4}\s*年(?:\s*[（(][^）)]*[）)])?\s*度")
_TIME_AXIS_TOKS = frozenset(_VOCAB["time_axis"])
_COUNTRY_CANON: dict[str, str] = dict(_VOCAB["country"])
_CHANNEL_CANON: dict[str, str] = dict(_VOCAB["channel"])
_SCOPE_CANON: dict[str, str] = dict(_VOCAB["scope"])
_SCOPE_STICKY = next(
    (v for v in _SCOPE_CANON.values() if v == "既存"),
    next(iter(_SCOPE_CANON.values()), ""),
)
_BREAKDOWN_CANON: dict[str, str] = dict(_VOCAB["breakdown"])
_BREAKDOWN_MARKS = frozenset({"内)", "（内", "うち"})
_BREAKDOWN_RANK = {"": 0, "ドラッグ": 1, "調剤": 2, "物販": 3}
_ACTIVE_STORE_BREAKDOWN_RANK = {
    "管理テナント数": 1,
    "取引店舗数": 2,
    "増加": 3,
    "減少": 4,
}
_BREAKDOWN_RANK.update(_ACTIVE_STORE_BREAKDOWN_RANK)
_AREA_TREE: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "グロース領域": (
        ("プロパティ", ("ホテル", "インカム太陽光")),
        ("専門通販", ("化粧品健康食品", "グルメ", "ワイン", "ナース関連")),
    ),
    "サステナブル領域": (
        ("呉服関連", ()),
        ("アパレル雑貨", ()),
        ("その他", ()),
    ),
}
_AREA_CHILD_ALIAS: dict[str, str] = {
    "インカム、太陽光": "インカム太陽光",
    "アパレル・雑貨": "アパレル雑貨",
}


def _require_area_aliases() -> None:
    known: set[str] = set()
    for nodes in _AREA_TREE.values():
        for mid, twigs in nodes:
            known.add(mid)
            known.update(twigs)
    missing = [dst for dst in _AREA_CHILD_ALIAS.values() if dst not in known]
    if missing:
        raise ValueError(f"area alias の先が木にありません: {missing}")


_require_area_aliases()


def _area_child_maps() -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    children: dict[str, dict[str, int]] = {}
    depth: dict[str, int] = {}
    for area, nodes in _AREA_TREE.items():
        cmap: dict[str, int] = {}
        rank = 1
        for mid, twigs in nodes:
            cmap[mid] = rank
            depth[mid] = 1
            rank += 1
            for twig in twigs:
                cmap[twig] = rank
                depth[twig] = 2
                rank += 1
        children[area] = cmap
    return children, depth


_AREA_CHILDREN, _AREA_BREAKDOWN_DEPTH = _area_child_maps()
for _area_children in _AREA_CHILDREN.values():
    _BREAKDOWN_RANK.update(_area_children)
_STORE_FLOW_CANON: dict[str, str] = dict(_VOCAB["store_flow"])
_STATUS_STORE_STEMS = frozenset({"閉鎖", "休業"})
_STORE_FLOW_METRICS = frozenset({"出店", "退店", "転換出店"})
_OPEN_SHARDS = frozenset({"開"})
_CLOSE_SHARDS = frozenset({"退", "閉"})
_DUMMY_STORE_TOKS = frozenset({"店舗"})
_OPEN_QUALIFIERS = frozenset({"新規"})
_STORE_ADJ_MARKS = ("純増", "新設", "改装", "業態転換")
_GROUP_MODIFIER_TOKS = frozenset({"入店", "総額"})
if _GROUP_MODIFIER_TOKS & _DROP_TOKS:
    raise ValueError(f"group modifier が drop に入っています: {_GROUP_MODIFIER_TOKS & _DROP_TOKS}")
_METRIC_PREFIX: tuple[tuple[str, str], ...] = tuple(
    (str(item["word"]), str(item["metric"])) for item in _VOCAB["metrics"]
)
_METRIC_STARTSWITH = frozenset(
    str(item["word"]) for item in _VOCAB["metrics"] if item.get("prefix")
)
_METRIC_KIND: dict[str, str] = dict(_VOCAB["metric_kind"])
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


def _compact_status_store(raw: str) -> str:
    """状態語＋店舗数を1語にする。の と空白は落とす。店数は店舗数。"""
    s = re.sub(r"[の 　]+", "", raw)
    if s.endswith("店数") and not s.endswith("店舗数"):
        s = f"{s[:-2]}店舗数"
    return s


def _glue_status_store_metric(segment: str, metric: str) -> tuple[str, str]:
    """閉鎖 店舗数 は再パースで退店になるので、フロー語ステムは店舗数に接着する。"""
    if metric != "店舗数" or not segment:
        return segment, metric
    parts = segment.split()
    stem = parts[-1]
    if stem not in _STATUS_STORE_STEMS or stem not in _STORE_FLOW_CANON:
        return segment, metric
    glued = f"{stem}{metric}"
    head = " ".join(parts[:-1])
    return (f"{head} {glued}".strip() if head else glued), ""


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
    held: list[str] = []

    def _hold_status_store(m: re.Match[str]) -> str:
        held.append(_compact_status_store(m.group(0)))
        return f"{_STATUS_STORES_MARK}{len(held) - 1}{_STATUS_STORES_MARK}"

    s = _STATUS_STORE_RE.sub(_hold_status_store, s)
    for src, mark in _FLOW_PROTECT:
        s = s.replace(src, f" {mark} ")
    for src, dst in _UNGLUE:
        s = s.replace(src, f" {dst} ")
    s = _FC_EMBED_RE.sub(" FC ", s)
    s = _FCW_EMBED_RE.sub(" FC ", s)
    for mark, dst in _FLOW_RESTORE:
        s = s.replace(mark, dst)
    s = s.replace(_CHAN_BOTH_MARK, "直営+FC")
    for i in range(len(held) - 1, -1, -1):
        s = s.replace(f"{_STATUS_STORES_MARK}{i}{_STATUS_STORES_MARK}", held[i])
    return re.sub(r"\s+", " ", s).strip()


def load_issuer_metrics(path: Path | None = None) -> dict[str, frozenset[str]]:
    """issuer code → 件数・率指標の語。層の分解と、code 付きの保存名で共通語彙より先に見る。"""
    raw = json.loads((path or ISSUER_METRICS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("issuer_metrics.json は object です")
    out: dict[str, frozenset[str]] = {}
    for code, words in raw.items():
        key = str(code)
        if not _ISSUER_CODE_RE.fullmatch(key):
            raise ValueError(f"issuer_metrics.json のキーは5桁コードです: {code!r}")
        if not isinstance(words, list):
            raise ValueError(f"issuer_metrics.json {key} は配列です")
        toks: list[str] = []
        for w in words:
            tok = str(w).strip()
            if not tok:
                continue
            if tok.split() != [tok]:
                raise ValueError(f"issuer_metrics.json {key} {tok!r}: 1語にしてください")
            toks.append(tok)
        if toks:
            out[key] = frozenset(toks)
    _require_issuer_metrics_not_common(out)
    return out


def issuer_count_metrics(code: str | None) -> frozenset[str]:
    if not code:
        return frozenset()
    return ISSUER_METRICS.get(str(code), frozenset())


def _peel_metric(
    tok: str, *, count_metrics: frozenset[str] = frozenset(),
) -> tuple[str | None, str | None]:
    """指標語をトークンの前後から剥がす。残りはセグメント候補。

    短い接頭辞（売上/来客/単価）は前方一致しない（売上変化を売上高にしない）。
    count_metrics は銘柄別の件数・率指標（`config/monthly/issuer_metrics.json`）。
    完全一致を共通語彙の後ろからの一致より先に見る（一品単価を客単価に割らない）。
    """
    if tok in _ACTIVE_STORE_BREAKDOWN_RANK:
        return None, tok
    if tok in count_metrics:
        return tok, None
    for src, canon in _METRIC_PREFIX:
        if tok == src:
            return canon, None
        if len(tok) > len(src) and tok.endswith(src):
            return canon, tok[:-len(src)]
        if src in _METRIC_STARTSWITH and len(tok) > len(src) and tok.startswith(src):
            return canon, tok[len(src):]
    return None, tok


def _require_issuer_metrics_not_common(metrics: dict[str, frozenset[str]]) -> None:
    """共通指標と完全に一致する語だけ禁止。一品単価のように長い複合は置ける。"""
    for code, words in metrics.items():
        for word in words:
            peeled, rest = _peel_metric(word)
            if peeled and not rest:
                raise ValueError(
                    f"issuer_metrics.json {code} {word!r}: 共通指標と重なります"
                )


ISSUER_METRICS = load_issuer_metrics()


def _is_flow_metric(metric: str) -> bool:
    return metric.endswith("出店") or metric.endswith("退店")


def _stitch_store_flow(leftover: list[str], metric: str) -> tuple[list[str], str]:
    """店数→店舗数 が 退店/開店/閉店 を割った残骸を指標へ戻す。新規は店舗の行だけ出店。"""
    if metric in ("", "店舗数"):
        if any(tok in _OPEN_SHARDS for tok in leftover):
            leftover = [tok for tok in leftover if tok not in _OPEN_SHARDS]
            metric = "出店"
        elif any(tok in _CLOSE_SHARDS for tok in leftover):
            leftover = [tok for tok in leftover if tok not in _CLOSE_SHARDS]
            metric = "退店"
    # 新規→出店は店舗の行だけ（#661）。売上・会員などの新規はセグメントに残す。
    store_open = metric == "店舗数" or (
        not metric and any(tok in _DUMMY_STORE_TOKS for tok in leftover)
    )
    if store_open and any(tok in _OPEN_QUALIFIERS for tok in leftover):
        leftover = [tok for tok in leftover if tok not in _OPEN_QUALIFIERS]
        leftover = [tok for tok in leftover if tok not in _DUMMY_STORE_TOKS]
        metric = "出店"
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


def _stitch_active_stores(leftover: list[str], metric: str) -> tuple[list[str], str]:
    """稼働 + 店舗数 → 稼働店舗数。セグメントに 稼働 を残さない。"""
    if metric == "店舗数" and "稼働" in leftover:
        leftover = [tok for tok in leftover if tok != "稼働"]
        return leftover, "稼働店舗数"
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


def _metric_core(metric: str) -> str:
    if metric.startswith("連結") and len(metric) > 2:
        return metric[2:]
    return metric


def _stitch_consolidated(leftover: list[str], metric: str) -> tuple[list[str], str]:
    kept: list[str] = []
    has_consol = False
    for tok in leftover:
        if tok == "連結":
            has_consol = True
            continue
        kept.append(tok)
    if not metric and len(kept) == 1 and kept[0].startswith("連結") and kept[0] != "連結":
        metric = kept.pop(0)
    if has_consol:
        if metric:
            if not metric.startswith("連結"):
                metric = "連結" + metric
        elif len(kept) == 1:
            rest = kept.pop(0)
            metric = rest if rest.startswith("連結") else "連結" + rest
        else:
            kept.insert(0, "連結")
    return kept, metric


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


def _peel_area_breakdown(
    leftover: list[str], metric: str, breakdown: str,
) -> tuple[list[str], str]:
    if breakdown or _metric_core(metric) != "売上高":
        return leftover, breakdown
    parent_i = next(
        (i for i, tok in enumerate(leftover) if tok in _AREA_CHILDREN),
        None,
    )
    if parent_i is None:
        return leftover, breakdown
    rest = leftover[parent_i + 1:]
    if not rest:
        return leftover, breakdown
    children = _AREA_CHILDREN[leftover[parent_i]]
    joined = " ".join(rest)
    child = _AREA_CHILD_ALIAS.get(joined, joined)
    if child not in children:
        compact = joined.replace(" ", "")
        child = _AREA_CHILD_ALIAS.get(compact, compact)
    if child not in children:
        return leftover, breakdown
    return leftover[: parent_i + 1], child


def _peel_active_store_breakdown(
    leftover: list[str], metric: str, breakdown: str,
) -> tuple[list[str], str]:
    """稼働店舗数の子（管理テナント数 / 取引店舗数 / 増加 / 減少）を内訳へ。"""
    if breakdown or metric != "稼働店舗数" or not leftover:
        return leftover, breakdown
    last = leftover[-1]
    if last in _ACTIVE_STORE_BREAKDOWN_RANK:
        return leftover[:-1], last
    if len(leftover) >= 2:
        joined = leftover[-2] + leftover[-1]
        if joined in _ACTIVE_STORE_BREAKDOWN_RANK:
            return leftover[:-2], joined
    return leftover, breakdown


def parse_element_layers(element: str, *, code: str | None = None) -> dict[str, str]:
    """国 / セグメント / 時間軸 / チャネル / スコープ / 指標 / 内訳。正規化前の名前でも読む。

    code があれば、その銘柄の件数・率指標の語も指標にする。
    """
    count_metrics = issuer_count_metrics(code)
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
        peeled, rest = _peel_metric(tok, count_metrics=count_metrics)
        if peeled:
            rest_flow = _STORE_FLOW_CANON.get(rest) if rest else None
            if rest_flow and rest not in _STATUS_STORE_STEMS:
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
            if mapped_scope == _SCOPE_STICKY or scope != _SCOPE_STICKY:
                scope = mapped_scope
            continue
        leftover.append(tok)
    leftover, metric = _stitch_store_flow(leftover, metric)
    leftover, metric = _stitch_real_store(leftover, metric)
    leftover, metric = _stitch_active_stores(leftover, metric)
    leftover, metric = _stitch_vs_sales(leftover, metric)
    leftover, metric = _peel_adj_metric(leftover, metric)
    leftover, metric = _stitch_consolidated(leftover, metric)
    axis_left = [tok for tok in leftover if tok in _TIME_AXIS_TOKS]
    leftover = [tok for tok in leftover if tok not in _TIME_AXIS_TOKS]
    if axis_left and not time_axis:
        time_axis = "単月"
    if metric:
        time_axis = ""
    if _is_flow_metric(metric):
        leftover = [tok for tok in leftover if tok != "（内"]
    leftover, breakdown = _peel_breakdown(leftover, _metric_core(metric))
    leftover, breakdown = _peel_area_breakdown(leftover, metric, breakdown)
    leftover, breakdown = _peel_active_store_breakdown(leftover, metric, breakdown)
    return {
        "country": country,
        "segment": " ".join(leftover),
        "time_axis": time_axis,
        "channel": channel,
        "scope": scope,
        "metric": metric,
        "breakdown": breakdown,
    }


def _prefer_issuer_metric_layers(
    common: dict[str, str],
    issuer: dict[str, str],
    count_metrics: frozenset[str],
    *,
    fill_default_metric: bool,
) -> dict[str, str]:
    """銘柄別の指標を共通語彙より優先する。#534 の件数語の並べ替えはしない。

    使うのは (1) 共通語彙が銘柄別の語を割る（一品単価→一品+客単価）ときと
    (2) 前年比の既定指標 売上高 を足してしまうとき（全店 買上点数）と
    (3) leftover が銘柄別指標そのものでスコープがあるとき
    （既存 粗利率 が number の語順で 粗利率 既存 に戻る。KAB-127）だけ。
    """
    issuer_metric = issuer["metric"]
    if issuer_metric not in count_metrics or issuer_metric == common["metric"]:
        return common
    peeled, rest = _peel_metric(issuer_metric)
    if peeled and rest:
        return issuer
    if (
        fill_default_metric
        and not common["metric"]
        and common["scope"]
        and not common["channel"]
        and "会員数" not in common["segment"]
    ):
        return issuer
    if (
        not common["metric"]
        and common["scope"]
        and common["segment"] == issuer_metric
    ):
        return issuer
    return common


def normalize_element_tokens(
    element: str, *, fill_default_metric: bool = False, code: str | None = None,
) -> str:
    """空白区切り [国] [セグメント] [単月?] [チャネル] [スコープ] [指標] [内訳]。"""
    trimmed = re.sub(r"\s+", " ", element.strip())
    if not trimmed:
        return trimmed
    # 銘柄別の件数・率指標は層が先。保存名の並べ替えは割れる語・既定売上高・leftover＝指標＋スコープだけ。
    parts = parse_element_layers(trimmed)
    count_metrics = issuer_count_metrics(code)
    if count_metrics:
        parts = _prefer_issuer_metric_layers(
            parts, parse_element_layers(trimmed, code=code), count_metrics,
            fill_default_metric=fill_default_metric,
        )
    # leftover に会員数がある yoy は売上高を補わない（#366）。
    if (
        fill_default_metric
        and not parts["metric"]
        and parts["scope"]
        and not parts["channel"]
        and "会員数" not in parts["segment"]
    ):
        leftover = parts["segment"].split()
        if len(leftover) == 1 and leftover[0] in _YOY_SCOPE_LEFTOVER_METRICS:
            parts["metric"] = leftover[0]
            parts["segment"] = ""
        else:
            parts["metric"] = "売上高"
            parts["time_axis"] = ""
            leftover, breakdown = _peel_breakdown(leftover, "売上高")
            leftover, breakdown = _peel_area_breakdown(leftover, "売上高", breakdown)
            leftover, metric = _stitch_consolidated(leftover, parts["metric"])
            parts["segment"] = " ".join(leftover)
            parts["metric"] = metric
            parts["breakdown"] = breakdown
    parts["segment"], parts["metric"] = _glue_status_store_metric(
        parts["segment"], parts["metric"],
    )
    # 拠点数は件数指標。別指標があるときの表見出しだけ落とす（#534 前の _DROP_TOKS）。
    if parts["metric"]:
        parts["segment"] = " ".join(
            tok for tok in parts["segment"].split() if tok != "拠点数"
        )
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


_ALIAS_PATH = ISSUER_ALIASES_PATH


def load_issuer_aliases(path: Path | None = None) -> dict[str, tuple[IssuerPeriodRename, ...]]:
    raw = json.loads((path or _ALIAS_PATH).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("issuer_aliases.json は object です")
    out: dict[str, tuple[IssuerPeriodRename, ...]] = {}
    for code, periods in raw.items():
        key = str(code)
        if not _ISSUER_CODE_RE.fullmatch(key):
            raise ValueError(f"issuer_aliases.json のキーは5桁コードです: {code!r}")
        if not isinstance(periods, list):
            raise ValueError(f"issuer_aliases.json {key} は配列です")
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
                canon_src = canonicalize_element_label(src_s, unit, key)
                canon_dst = canonicalize_element_label(dst_s, unit, key)
                if canon_src:
                    rename[canon_src] = canon_dst or canon_src
            rules.append(IssuerPeriodRename(date.fromisoformat(until_raw), rename))
        rules.sort(key=lambda rule: rule.until)
        out[key] = tuple(rules)
    return out


ISSUER_ELEMENT_ALIASES = load_issuer_aliases()

_ROW_ORDER_PATH = MONTHLY_ROW_ORDER_PATH
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
    *,
    code: str | None = None,
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
    return canonicalize_element_label(stored_name, stored_u, code) == incoming_name


def uncovered_yoy_number_twins(
    existing: Sequence[tuple[str, str | None]],
    incoming_name: str,
    incoming_unit: str | None,
    posted: Sequence[tuple[str, str | None]],
    *,
    code: str | None = None,
) -> list[str]:
    """strip 後の名前が同じで yoy と number だけ違う既存行のうち、posted に無いもの。

    incoming_name と posted の名前は canonicalize 済み。空に潰れた名前は比べない。
    同じ POST に反対側があるときは空（実数と前年比の併存）。
    """
    incoming_yoy = (incoming_unit or "number") in _YOY_UNITS
    base = strip_yoy_fragments(incoming_name)
    if not base:
        return []
    out: list[str] = []
    for name, unit in existing:
        stored = unit or "number"
        if (stored in _YOY_UNITS) == incoming_yoy:
            continue
        if strip_yoy_fragments(name) != base:
            continue
        if any(
            same_yoy_element(name, stored, posted_name, posted_unit, code=code)
            for posted_name, posted_unit in posted
        ):
            continue
        out.append(name)
    return out


def plan_yoy_duplicate_merges(
    rows: Sequence[tuple[str, int, str, str | None]],
) -> list[tuple[str, int, str, list[str]]]:
    """保存名を canonicalize へ。同一年の重複がなくても返す。

    number の `売上高` と yoy の `売上高(前年比)` は別行のまま。
    戻りは (code, year, canonical, short_names)。
    """
    groups: dict[tuple[str, int, str], set[str]] = {}
    for code, year, element, unit in rows:
        canonical = canonicalize_element_label(element, unit, code)
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


# 長い語を先に。比/対比/差/増減は今期の比率。同月/同期は 前年同月比 が 前年 に食われないため。
_PRIOR_PERIOD_RE = re.compile(
    r"(前期|前年同月|前年同期|前年)(?!(?:比|対比|差|増減|同月|同期))"
)


def is_prior_period_element(element: str) -> bool:
    """前期・前年の実数行。前年比 / 前年差 / 前年増減率は False。"""
    key = monthly_element_key(element.strip())
    if not key:
        return False
    return bool(_PRIOR_PERIOD_RE.search(key))


def reject_prior_period_element(element: str) -> None:
    """前期・前年同月の実数行は入れない。比（前期比など）は今期の比率なので通す。"""
    label = element.strip()
    if not is_prior_period_element(label):
        return
    raise MonthlyIngestError(
        400,
        "PRIOR_PERIOD_ELEMENT",
        f"「{label}」は前期の実数です。前年の同じ項目に既にあるので、この行は投入しないでください。",
    )


def monthly_series_kind(element: str, unit: str | None) -> str | None:
    """TOP/チャート用。all_store / existing / stores。どれでもなければ None。

    全社・全店舗は会社/全店系。既存が付いていれば既存を優先。
    スコープ無しの yoy 売上高/連結売上高/客数/客単価は全店枠。
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
    layers = _layers_of(element)
    if layers["breakdown"]:
        return None
    # 会員数の前年比は既存/全店があっても売上高系列にしない (#366)。
    if "会員数" in key:
        return None
    if "既存" in key:
        return "existing"
    if "全店" in key or "全社" in key:
        return "all_store"
    if _metric_core(layers["metric"]) in {"売上高", "客数", "客単価"}:
        return "all_store"
    return None


_SCOPE_RANK = {"all": 0, "company": 1, "existing": 2, "other": 3}
_METRIC_RANK = {
    "sales": 1, "customers": 2, "spend": 3,
    "open": 4, "close": 5, "adj": 6, "stores": 7, "count": 8, "other": 9,
}


def _layers_of(element: str, code: str | None = None) -> dict[str, str]:
    return parse_element_layers(strip_yoy_fragments(element), code=code)


def _row_scope(key: str, code: str | None = None) -> str:
    """既存 / 全店 / 全社。空スコープの売上高/客数/客単価は全店枠。"""
    scope = _layers_of(key, code)["scope"]
    if scope == "既存":
        return "existing"
    if scope == "全店":
        return "all"
    if scope == "全社":
        return "company"
    if _row_metric(key, code) in {"sales", "customers", "spend"}:
        return "all"
    return "other"


def _row_metric(key: str, code: str | None = None) -> str:
    layers = _layers_of(key, code)
    metric = layers["metric"]
    if metric.endswith("出店"):
        return "open"
    if metric.endswith("退店"):
        return "close"
    if _is_store_adj(key, layers["segment"]):
        return "adj"
    if metric in _METRIC_KIND:
        return _METRIC_KIND[metric]
    if "売上収益" in metric:
        return "sales"
    if any(mark in key for mark in _STORE_ADJ_MARKS):
        return "adj"
    if "店舗数" in key:
        return "stores"
    if "客単価" in key or "単価" in key:
        return "spend"
    if "客数" in key or "来客" in key:
        return "customers"
    if metric in issuer_count_metrics(code):
        return "count"
    if "売上" in key:
        return "sales"
    return "other"


def _row_segment(key: str, code: str | None = None) -> str:
    """ブランド。国・チャネル・スコープ・指標を除いた残り。純増・新規は塊にしない。"""
    toks = []
    for tok in _layers_of(key, code)["segment"].split():
        if tok in _OPEN_QUALIFIERS or any(mark in tok for mark in _STORE_ADJ_MARKS):
            continue
        if tok in _GROUP_MODIFIER_TOKS:
            continue
        toks.append(tok)
    return " ".join(toks)


def monthly_row_segment(element: str, code: str | None = None) -> str:
    """ブランド。`カラオケ 全店 売上高(前年比)` → カラオケ。全社/全店のみなら空。"""
    return _row_segment(element, code)


def monthly_row_slot(element: str, unit: str | None, code: str | None = None) -> int:
    """指標枠。1売上高 2客数 3客単価 4出店 5退店 6純増/新設/改装/業態転換 7店舗数 8件数 9その他。"""
    _ = unit
    return _METRIC_RANK[_row_metric(element, code)]


def monthly_row_unit_rank(unit: str | None) -> int:
    """0=number 1=yoy。同じ指標の実数を先、前年比を後。"""
    return 1 if (unit or "number") in _YOY_UNITS else 0


def monthly_row_breakdown_rank(element: str) -> int:
    """売上高の親が先。内訳は ドラッグ → 調剤 → 物販。領域は木の先行順。"""
    bd = _layers_of(element)["breakdown"]
    return _BREAKDOWN_RANK.get(bd, 4)


def monthly_breakdown_depth(element: str) -> int:
    """0=親。1=内訳。2=領域の小枝（プロパティの下のホテル）。"""
    bd = _layers_of(element)["breakdown"]
    if not bd:
        return 0
    if bd in _ACTIVE_STORE_BREAKDOWN_RANK:
        return 1
    return _AREA_BREAKDOWN_DEPTH.get(bd, 1)


def monthly_row_scope_rank(element: str, code: str | None = None) -> int:
    """0全店 1全社 2既存 3その他。スコープ無しの店舗数は 3（既存の後）。"""
    layers = _layers_of(element, code)
    if layers["scope"] == "既存":
        return _SCOPE_RANK["existing"]
    if layers["scope"] == "全社":
        return _SCOPE_RANK["company"]
    if layers["scope"] == "全店":
        return _SCOPE_RANK["all"]
    if _is_flow_metric(layers["metric"]) or layers["metric"] in ("店舗数", "稼働店舗数"):
        return _SCOPE_RANK["other"]
    if _row_metric(element, code) in {"stores", "open", "close", "adj", "count"}:
        return _SCOPE_RANK["other"]
    if not layers["segment"]:
        return _SCOPE_RANK["all"]
    return _SCOPE_RANK[_row_scope(element, code)]


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
    売上高（実数の親→内訳、そのあと前年比の親→内訳）→ 客数 → 客単価 →
    出店 → 退店 → 純増/新設/改装/業態転換 → 店舗数 → 件数 → その他。
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
            display = canonicalize_element_label(name, unit, resolved)
            if display in seen:
                continue
            seen.add(display)
            segment = monthly_row_segment(display, resolved)
            if segment and segment not in tail_rank and segment not in seg_index:
                seg_index[segment] = len(seg_index)
            items.append({
                "element": display,
                "unit": unit,
                "segment": segment,
                "country": _row_country_rank(display),
                "scope": monthly_row_scope_rank(display, resolved),
                "channel": _row_channel_rank(display),
                "slot": monthly_row_slot(display, unit, resolved),
                "unit_rank": monthly_row_unit_rank(unit),
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
        it["unit_rank"],
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


MetricKind = Literal[
    "sales", "customers", "spend", "open", "close", "adj", "stores", "count", "other",
]
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
    breakdown_depth: int


def monthly_element_label(
    element: str, unit: str | None, code: str | None = None,
) -> MonthlyElementLabel:
    """code は銘柄別の件数・率指標を層と保存名に効かせる。#534 の件数語の並べ替えはしない。"""
    u = unit or "number"
    canon = canonicalize_element_label(strip_monthly_display_unit(element, u), u, code)
    display = monthly_display_label(canon, u)
    layers = _layers_of(canon, code)
    return {
        "display": display,
        "group": monthly_row_segment(canon, code),
        "metric_kind": _row_metric(canon, code),
        "series_kind": monthly_series_kind(canon, u),
        **layers,
        "breakdown_depth": monthly_breakdown_depth(canon),
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
    if not months or months[0] != 1:
        return False
    for i in range(1, len(months)):
        if months[i] <= months[i - 1]:
            return False
    return True


def _fiscal_start_month(fy_end_month: int) -> int:
    return (fy_end_month % 12) + 1


def _current_fy_end_year(fy_end_month: int, as_of: date) -> int:
    """as_of が属する会計年度の期末年。

    期末月の翌月以降（新FY先頭月に限らない）は、完了した FY の期末年を返す。
    12月期末の1月は直前FY。期末月以前は当年期末。
    """
    if as_of.month == _fiscal_start_month(fy_end_month):
        if fy_end_month < as_of.month:
            return as_of.year
        return as_of.year - 1
    return as_of.year


def _anchor_year_for_fy(last_month: int, fy_end_month: int, as_of: date) -> int:
    """fiscal: 現行 / 完了 FY の期末年から右端列の暦年を決める。"""
    fy_end_year = _current_fy_end_year(fy_end_month, as_of)
    if last_month <= fy_end_month:
        return fy_end_year
    # 期末より後の暦月は FY の期首側。as_of が期末後なら完了FY年＝当年。
    if as_of.month > fy_end_month:
        return fy_end_year
    return fy_end_year - 1


def _future_column(
    mapping: Sequence[tuple[int, int]],
    as_of: date,
) -> tuple[int, int] | None:
    """mapping のうち as_of の年月より後の最後の列。(month, year)。"""
    future: tuple[int, int] | None = None
    for month, year in mapping:
        if (year, month) > (as_of.year, as_of.month):
            future = (month, year)
    return future


def _reject_future_columns(
    mapping: Sequence[tuple[int, int]],
    as_of: date,
    *,
    layout: str,
    fy_end_month: int,
) -> None:
    """as_of より未来の年月の列は書かない。当期FYの期末前は残月を許す。"""
    future = _future_column(mapping, as_of)
    if future is None:
        return
    if layout == "fiscal" and as_of.month <= fy_end_month:
        return
    month, year = future
    raise MonthlyIngestError(
        400,
        "FUTURE_COLUMN",
        f"{year}年{month}月の列は as_of {as_of.isoformat()} より未来です。",
        extra={"month": month, "year": year, "as_of": as_of.isoformat()},
    )


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
    calendar/partial は右端月+as_of。fiscal は fy_end+as_of で現行/完了 FY を決める。
    mapping の年月が as_of より後なら FUTURE_COLUMN（当期FYの期末前は除く）。"""
    if not months:
        raise MonthlyIngestError(400, "INVALID_PARAM", "months は1〜12の整数の配列です。")
    start = months[0]
    if _is_calendar_layout(months):
        layout = "calendar"
        mapping = _assign_years(months, as_of)
    elif start == _fiscal_start_month(fy_end_month):
        layout = "fiscal"
        mapping = _assign_years(months, as_of, fy_end_month=fy_end_month)
    else:
        layout = "partial"
        mapping = _assign_years(months, as_of)
    _reject_future_columns(
        mapping, as_of, layout=layout, fy_end_month=fy_end_month,
    )
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
