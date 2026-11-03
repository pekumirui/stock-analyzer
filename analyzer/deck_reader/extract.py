"""決算説明資料（PowerPoint 由来の PDF）→ ページ情報 + 数値ファクト。LLM を呼ばない部分。

- 数字はテキスト層（PDF の文字データ）が正。LLM には「どの数字がどの項目・期間か」の対応付けだけをさせる
- LLM が出した数字はテキスト層に実在するかを機械的に照合する（verified）。数字自体の捏造は見つかるが、
  実在する数字を別の項目に付けた誤りは見つからない（README の「照合の限界」）
- PDF は pypdfium2 で読む（本体の requirements.txt にある。PyMuPDF は AGPL なので使わない）
"""

from __future__ import annotations

import io
import json
import re
import unicodedata

PROMPT_VERSIONS = ("v1", "v2")  # run.py --prompt で選ぶ。結果 JSON の prompt_version は deck-<選んだ方>

# ---------- 数値 ----------
_NUM = re.compile(r"[△▲▼\-−+＋]?\s*\d[\d,]*(?:\.\d+)?")


def norm_text(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


def parse_number(s) -> float | None:
    """'△1,026' -> -1026.0, '+3,450' -> 3450.0, '99.3%' -> 99.3。数値でなければ None。"""
    s = norm_text(str(s)).strip().replace(" ", "")
    neg = s.startswith(("△", "▲", "▼", "-", "−"))
    m = re.search(r"\d[\d,]*(?:\.\d+)?", s)
    if not m:
        return None
    v = float(m.group(0).replace(",", ""))
    return -v if neg else v


def numbers_in(text: str) -> set[float]:
    """テキスト層に現れる数値（絶対値）の集合。符号は表示位置で離れることがあるので見ない。"""
    out = set()
    for m in _NUM.finditer(norm_text(text)):
        v = parse_number(m.group(0))
        if v is not None:
            out.add(abs(v))
    return out


# ---------- ページ分類 ----------
_SKIP_WORDS = (
    "将来予測", "将来に関する記述", "将来の見通し", "見通しに関する注意", "免責", "インサイダー取引",
    "Disclaimer", "forward-looking",
)


def classify(text: str, n_paths: int) -> str:
    """LLM に回す必要があるか。divider（表紙・中扉）/ disclaimer（注意事項）は回さない。"""
    t = norm_text(text)
    body = re.sub(r"\s+", "", t)
    # 年・月日・ページ番号のような小さい数字や年号を除いた「中身の数字」
    real = {v for v in numbers_in(t) if v > 31 and not (1990 <= v <= 2100 and v == int(v))}
    if len(body) < 120 and not real:
        return "divider"
    if any(w in t for w in _SKIP_WORDS) and len(numbers_in(t)) < 15:
        return "disclaimer"
    if n_paths >= 15 and len(numbers_in(t)) >= 4:
        return "chart_or_table"
    if len(numbers_in(t)) >= 4:
        return "table_or_text"
    return "text"


def needs_llm(kind: str) -> bool:
    return kind not in ("divider", "disclaimer")


# ---------- PDF ----------
def load_pages(pdf_bytes: bytes, dpi: int = 110, render: bool = True) -> tuple[list[dict], dict]:
    """[{page, kind, text, png(bytes|None)}], meta。png は LLM に回すページだけ。"""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    doc = pdfium.PdfDocument(pdf_bytes)
    pages = []
    for i in range(len(doc)):
        p = doc[i]
        text = p.get_textpage().get_text_range()
        n_paths = sum(1 for o in p.get_objects(max_depth=15) if o.type == pdfium_c.FPDF_PAGEOBJ_PATH)
        kind = classify(text, n_paths)
        png = None
        if render and needs_llm(kind):
            buf = io.BytesIO()
            p.render(scale=dpi / 72).to_pil().save(buf, "PNG")
            png = buf.getvalue()
        pages.append({"page": i + 1, "kind": kind, "text": text, "png": png})
    meta = {"pages": len(doc), "creator": doc.get_metadata_value("Creator"), "producer": doc.get_metadata_value("Producer")}
    return pages, meta


# ---------- プロンプト ----------
SYSTEM = """あなたは日本の上場企業の決算説明資料を読み取り、データベース用の構造化データを作る専門家です。
スライド画像と、そのスライドから機械抽出した文字データ (数字は正確だが並び順が崩れている) を渡します。
画像でレイアウト (どの数字がどの項目・期間・セグメントに属するか) を読み取り、数字は必ず文字データに現れる表記をそのまま使ってください。

出力は次の JSON オブジェクトだけ (前後に文章やコードブロックを付けない):
{
  "title": "スライドのタイトル",
  "summary": "このスライドが伝えていることを日本語 1〜3 文で。数字の羅列ではなく要点 (増減の理由、見通しの変更、施策) を書く",
  "facts": [
    {
      "metric": "指標名。階層は / で区切る (例: 営業利益, 連結販売台数/北米, 営業利益増減要因/為替変動の影響, 営業利益増減要因/為替変動の影響/米ドル)",
      "segment": "セグメント・地域・製品などの区分。全社なら null",
      "period": "資料に書かれた期間表記そのまま (例: 2026.4-6, 2027年3月期, 前回見通し 2026.4-2027.3)",
      "period_kind": "actual | forecast | previous_forecast | plan | change のいずれか (change は前年差・前回差などの増減額)",
      "value": "資料の表記そのまま (例: △1,026, +3,450, 99.3%, 160円)",
      "unit": "億円, 百万円, 千台, %, 円 など。表の (単位:…) を反映",
      "note": "補足があれば短く。なければ空文字"
    }
  ]
}
ルール:
- スライド上の数値はすべて facts に入れる (グラフの棒・表のセル・注記の内訳も)。ページ番号・年号・軸目盛りは入れない
- 前年同期比の % や構成比も、対応する metric に「/前年同期比」などを付けて入れる
- 為替レートの前提 (例: 米ドル 160円) は metric を「為替レート/米ドル」として入れる
- 文字データに無い数字を作らない。読み取れない場合は facts に入れない"""


SYSTEM_V2 = """あなたは日本の上場企業の決算説明資料を読み取り、データベース用の構造化データを作る専門家です。
スライド画像と、そのスライドから機械抽出した文字データ (数字は正確だが並び順が崩れている) を渡します。
画像でレイアウト (どの数字がどの項目・期間・区分に属するか) を読み取り、数字は必ず文字データに現れる表記をそのまま使ってください。

数値は「表」の形で返します。グラフも表として書きます (横軸の期間が列、系列が行)。出力は次の JSON オブジェクトだけ (前後に文章やコードブロックを付けない):
{
  "title": "スライドのタイトル",
  "summary": "このスライドが伝えていることを日本語 1〜3 文で。数字の羅列ではなく要点 (増減の理由、見通しの変更、施策) を書く",
  "tables": [
    {
      "cols": [{"period": "資料の期間表記そのまま (例: FY25 Q1, 2027年3月期)", "kind": "actual | forecast | previous_forecast | plan | change"}],
      "rows": [
        {"metric": "指標名。階層は / で区切る", "segment": "区分 (セグメント・地域・製品)。無ければ null",
         "unit": "億円, 10億円, 千台, %, pts, 円 など", "of": "この行がほかの行の内訳なら、その行の metric と segment を「metric|segment」で。無ければ null",
         "values": ["cols と同じ順・同じ数。値が無いセルは null"], "note": "行名だけで意味が分からないときだけ短く。普段は省く"}
      ]
    }
  ]
}
ルール:
- スライド上の数値はすべて入れる (グラフの棒・表のセル・注記の内訳も)。ページ番号・年号・軸目盛りは入れない
- values には数字だけを書く。単位・「円安」などの語は付けない (例: "55.9" と unit "%"、"△1,026"、"+3,450")。円安・円高などの向きは metric に入れる (例: 為替レート/米ドル/円安幅)
- 種類 (kind) の違う列は 1 つの表に混ぜてよい。前年差・前四半期比などの増減は kind を change にする
- unit は行で 1 つ。列によって単位が変わる行 (例: 利益率は %、その増減は pts) は行を分ける
- of は「足すとその行になる内訳」だけに付ける (例: 地域別の売上 → 売上の合計、SoC・メモリ・その他 → テストシステム)。比率や増減の行には付けない
- 決議・決定済みの値は actual。将来の目標・計画は plan、会社の業績予想は forecast、前回の予想は previous_forecast
- 注記の金額 (減損損失・一時的な利益など) や期間の無い目標 (生産能力の目標など) も入れる。period には資料の言葉 (例: 注記 FY24 Q4、目標) を書く
- 入れないのは、指数名・制度名・製品名の一部になっている数字だけ (例: 「JPX日経インデックス人的資本100」の 100)
- 文字データに無い数字を作らない。読み取れない場合は入れない"""


def system_prompt(version: str) -> str:
    return {"v1": SYSTEM, "v2": SYSTEM_V2}[version]


def page_text(page: dict) -> str:
    """LLM に渡す文字データ（ページ番号の見出し付き）。"""
    text = re.sub(r"[ \t　]+", " ", norm_text(page["text"]))
    return f"ページ {page['page']} の文字データ:\n{text[:6000]}"


# ---------- 出力の解析と照合 ----------
def parse_json(s: str) -> dict:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip())
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        raise ValueError("no json object")
    return json.loads(s[i : j + 1])


def expand_tables(out: dict) -> dict:
    """v2 の tables を v1 と同じ facts の並びに展開する。行の of は part_of（"metric|segment"）として残す。"""
    if "tables" not in out:
        return out
    facts = []
    for t in out.get("tables") or []:
        cols = t.get("cols") or []
        for r in t.get("rows") or []:
            vals = r.get("values") or []
            for c, v in zip(cols, vals):
                if v in (None, "", "-", "－"):
                    continue
                facts.append({
                    "metric": r.get("metric"), "segment": r.get("segment"),
                    "period": c.get("period"), "period_kind": c.get("kind"),
                    "value": str(v), "unit": r.get("unit"), "note": r.get("note") or "",
                    "part_of": r.get("of"),
                })
    return dict({k: v for k, v in out.items() if k != "tables"}, facts=facts)


def strip_unit(value, unit) -> str:
    """value の末尾に unit が重なっていれば外す（'55.9%' + '%' -> '55.9'、'+1.6pts' + 'pts' -> '+1.6'）。"""
    v, u = norm_text(str(value)).strip(), norm_text(str(unit or "")).strip()
    if u and u not in ("null", "None") and v.endswith(u) and parse_number(v[: -len(u)]) is not None:
        return v[: -len(u)].strip()
    return str(value)


def fact_key(f: dict) -> str:
    seg = f.get("segment")
    return f"{f.get('metric')}|{seg if seg not in (None, 'null') else 'null'}"


def _decimals(s: str) -> int:
    m = re.search(r"\.(\d+)", norm_text(s))
    return len(m.group(1)) if m else 0


def check_sums(facts: list[dict]) -> list[dict]:
    """part_of の付いた facts を親ごと・期間ごとに足し、親の値と比べる。親の fact に sum_ok / sum_of を付ける。
    丸めの許容: 表示桁の半分 × (内訳の数 + 1)。"""
    by_key = {}
    for f in facts:
        by_key.setdefault((fact_key(f), f.get("period"), f.get("period_kind")), f)
    groups: dict[tuple, list[dict]] = {}
    for f in facts:
        parent = f.get("part_of")
        if parent and f.get("value_num") is not None:
            p = norm_text(parent).strip()
            p = p if "|" in p else f"{p}|null"
            groups.setdefault((p, f.get("period"), f.get("period_kind")), []).append(f)
    checks = []
    for (pkey, period, kind), parts in groups.items():
        parent = by_key.get((pkey, period, kind))
        if parent is None or parent.get("value_num") is None or len(parts) < 2:
            continue
        total = sum(x["value_num"] for x in parts)
        tol = 0.5 * 10 ** -max(_decimals(x["value"]) for x in [parent, *parts]) * (len(parts) + 1)
        ok = abs(total - parent["value_num"]) <= tol + 1e-9
        parent["sum_ok"], parent["sum_of"] = ok, round(total, 6)
        checks.append({"key": pkey, "period": period, "kind": kind, "value": parent["value_num"],
                       "sum": round(total, 6), "parts": len(parts), "ok": ok})
    return checks


def verify(page: dict, out: dict) -> dict:
    """facts に value_num と verified（テキスト層に実在するか）を付ける。v2 は表を展開し、内訳の合計を検算する。"""
    out = expand_tables(out)
    nums = numbers_in(page["text"])
    facts = []
    for f in out.get("facts") or []:
        if not isinstance(f, dict) or "value" not in f:
            continue
        f["value"] = strip_unit(f.get("value"), f.get("unit"))
        v = parse_number(f.get("value"))
        f["value_num"] = v
        f["verified"] = v is not None and abs(v) in nums
        facts.append(f)
    out["facts"] = facts
    out["sum_checks"] = check_sums(facts)
    return out


def stats(pages: list[dict]) -> dict:
    fs = [f for p in pages for f in p.get("facts", [])]
    ok = sum(1 for f in fs if f["verified"])
    return {
        "pages": len(pages),
        "llm_pages": sum(1 for p in pages if needs_llm(p["kind"])),
        "facts": len(fs),
        "verified": ok,
        "verified_ratio": round(ok / len(fs), 3) if fs else None,
        "errors": sum(1 for p in pages if p.get("error")),
        "sum_checks": sum(len(p.get("sum_checks") or []) for p in pages),
        "sum_mismatch": sum(1 for p in pages for c in p.get("sum_checks") or [] if not c["ok"]),
    }
