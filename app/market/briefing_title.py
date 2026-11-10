"""決算説明資料の表題判定（KAB-327。質疑応答は KAB-336）。保存ジョブも、あとから参照する処理もここだけを使う。

空白と全角・半角は畳んでから見る。採否は次のとおり。

採る（決算・四半期・中間・通期・業績のどれかと、資料の種類か質疑応答のどちらもある）:
- 説明資料（決算説明資料、決算補足説明資料、決算 説明資料）
- 説明会資料（決算説明会資料。説明会の語だけでは採らない）
- 補足資料（決算補足資料、決算短信の補足資料、説明補足資料）
- プレゼン資料 / プレゼンテーション資料（決算説明会の投影資料）
- 質疑応答（決算説明会質疑応答(要旨)、主な質疑、Q&A。要旨・概要だけでは採らない）
- 英語: earnings / financial results の presentation、または briefing / supplementary materials、または Q&A / questions and answers

落とす（資料の語があってもこちらを先に見る）:
- 訂正・誤記（「（訂正）…決算説明資料の一部訂正」。差し替えの本文ではなく通知）
- お知らせ、開催・中止・延期、掲載について・公開について
  （決算説明会開催のお知らせ、説明資料をホームページへ載せた案内）
- 英語の notice / correction / postponement / cancellation

採らない（種類が違う）:
- 決算短信そのもの（補足資料の語が無い）
- 資料の語も質疑応答の語も無い「決算説明会」（開催そのものの案内）
- 成長可能性に関する説明資料、決算の語が無いファイナンス補足説明資料
- 中期経営計画の説明会資料（決算・四半期などの語が無いもの）
- 決算概要、決算短信の要旨（質疑応答の語が無い）

「修正」だけでは落とさない。補足資料の表題に業績予想の修正が入ることがある。

台本つき版（ノーツ付き・ノート付き・スクリプト付・台本付）は保存するが、読み取りには回さない
（is_script_title）。スライドはスライドだけの版と同じ。書き起こしと質疑応答は別の中身なので含めない。
"""
from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_CORRECTION = ("訂正", "誤記")
_NOTICE = ("お知らせ", "開催", "中止", "延期", "公開について", "掲載について")
_EARNINGS = ("決算", "四半期", "中間", "通期", "業績")
_KINDS = (
    "説明資料",
    "説明会資料",
    "補足資料",
    "プレゼンテーション資料",
    "プレゼン資料",
)
# 「主な質疑」「質疑応答」を含む。要旨・概要だけでは採らない。
_QA = ("質疑",)
_SCRIPT = ("ノーツ付", "ノート付", "スクリプト付", "台本付")
_EN_NOTICE = re.compile(
    r"\b(notice|correction|postponement|cancellation|canceled|cancelled)\b",
    re.I,
)
_EN_KIND = re.compile(
    r"presentation|briefing materials|supplementary materials|supplemental materials",
    re.I,
)
_EN_EARNINGS = re.compile(r"earning|financial results|results briefing", re.I)
_EN_QA = re.compile(
    r"(?<![a-z0-9])(?:q\s*&\s*a|questions and answers)(?![a-z0-9])",
    re.I,
)


def spaced_title(title: str | None) -> str:
    """全角英数を半角にし、空白は1つに畳む。英語の語境界はこちらで見る。"""
    return _WS.sub(" ", unicodedata.normalize("NFKC", title or "")).strip()


def compact_title(title: str | None) -> str:
    """空白を除く。日本語は「決算 説明資料」も「決算説明資料」と同じにする。"""
    return _WS.sub("", spaced_title(title))


def title_decision(title: str | None) -> str:
    """accept / correction / notice / other。"""
    text = compact_title(title)
    if not text:
        return "other"
    if any(word in text for word in _CORRECTION):
        return "correction"
    if any(word in text for word in _NOTICE):
        return "notice"
    folded = spaced_title(title).casefold()
    if _EN_NOTICE.search(folded):
        if "correct" in folded:
            return "correction"
        return "notice"
    earnings = any(hint in text for hint in _EARNINGS) or _EN_EARNINGS.search(folded)
    if any(kind in text for kind in _KINDS) and any(hint in text for hint in _EARNINGS):
        return "accept"
    if _EN_KIND.search(folded) and _EN_EARNINGS.search(folded):
        return "accept"
    if earnings and (any(word in text for word in _QA) or _EN_QA.search(folded)):
        return "accept"
    return "other"


def is_material_title(title: str | None) -> bool:
    """決算説明資料・決算補足説明資料・説明会の資料・質疑応答。通知と訂正は含まない。"""
    return title_decision(title) == "accept"


def is_script_title(title: str | None) -> bool:
    """スライドに台本を付けた版。"""
    text = compact_title(title)
    return any(word in text for word in _SCRIPT)
