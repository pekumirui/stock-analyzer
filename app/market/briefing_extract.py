"""決算説明資料（PowerPoint 由来の PDF）を 1 ページずつ Claude に読ませる部分のうち、LLM を呼ばないところ（KAB-349）。

- PDF の文字データ（pypdfium2）とページの分類、プロンプト（deck-v3）、返ってきた JSON の解析
- 照合（verified）: LLM の数字が文字データにあるか。検算: 内訳（of）を足して合計と比べる
- PoC は analyzer/deck_reader/extract.py（OCR の照合もそこ。本番にはまだ入れない）。同じ規則を両方に置いている
"""
from __future__ import annotations

import io
import json
import re
import unicodedata

PROMPT_VERSION = "deck-v3"

# ---------- 数値 ----------
# カンマは 3 桁区切りのときだけ数字の一部とみなす。ラベルが重なって「34,30734,155」となった文字データを 34,307 と 34,155 に分ける
_DIGITS = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_NUM = re.compile(r"[△▲▼\-−+＋]?\s*" + _DIGITS)


def norm_text(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


def parse_number(s) -> float | None:
    """'△1,026' -> -1026.0, '+3,450' -> 3450.0, '99.3%' -> 99.3。数値でなければ None。"""
    s = norm_text(str(s)).strip().replace(" ", "")
    neg = s.startswith(("△", "▲", "▼", "-", "−"))
    m = re.search(_DIGITS, s)
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
画像でレイアウト (どの数字がどの項目・期間・区分に属するか) を読み取ってください。数字の桁は文字データの表記を使い、符号だけ下のルールで揃えます。

数値は「表」の形で返します。グラフも表として書きます (横軸の期間が列、系列が行)。出力は次の JSON オブジェクトだけ (前後に文章やコードブロックを付けない):
{
  "title": "スライドのタイトル",
  "summary": "このスライドが伝えていることを日本語 1〜3 文で。数字の羅列ではなく要点 (増減の理由、見通しの変更、施策) を書く",
  "tables": [
    {
      "cols": [{"period": "資料の期間表記そのまま (例: FY25 Q1, 2027年3月期)", "kind": "actual | forecast | previous_forecast | plan | change"}],
      "rows": [
        {"metric": "指標名。階層は / で区切る", "segment": "区分 (セグメント・地域・製品)。無ければ null",
         "unit": "億円, 10億円, 千台, %, pts, 円 など", "of": "内訳の親 (下のルール)。その行の metric と segment を「metric|segment」で。無ければ null",
         "values": ["cols と同じ順・同じ数。値が無いセルは null"], "note": "行名だけで意味が分からないときだけ短く。普段は省く"}
      ]
    }
  ]
}
数値のルール:
- スライド上の数値はすべて入れる (グラフの棒・表のセル・注記の内訳も)。ページ番号・年号・軸目盛りは入れない
- values には数字だけを書く。単位・「円安」などの語は付けない (例: "55.9" と unit "%"、"+3,450")。円安・円高などの向きは metric に入れる (例: 為替レート/米ドル/円安幅)
- マイナスは必ず - で書く。資料が △1,026・▲1,026・(731) のようにマイナスを表していたら "-1,026"、"-731" と書く
- 表の「-」「―」「—」や空欄は「値が無い」という意味。values は null にする (0 やマイナスにしない)
- マイナスでない括弧 (前回予想・構成比・利益率などの補足) は括弧を外して書き、意味は kind や行で表す (例: 前回予想 (1,575.0) → kind previous_forecast の列に "1,575.0")
- unit は行で 1 つ。金額の行に率 (%) を入れない。増減率・利益率・その増減 (pts) は落とさずに、それぞれ別の行にする
- 種類 (kind) の違う列は 1 つの表に混ぜてよい。前年差・前四半期比などの増減は kind を change にする
- 決議・決定済みの値は actual。将来の目標・計画は plan、会社の業績予想は forecast、前回の予想は previous_forecast
- 注記の金額 (減損損失・一時的な利益など) や期間の無い目標 (生産能力の目標など) も入れる。period には資料の言葉 (例: 注記 FY24 Q4、目標) を書く
- 入れないのは、指数名・制度名・製品名の一部になっている数字だけ (例: 「JPX日経インデックス人的資本100」の 100)
- 文字データに無くても、画像にはっきり書かれている数字は入れる (グラフが画像で貼られている資料がある)。画像にも無い数字は作らない
内訳 (of) のルール:
- of は「その親の内訳を全部足すと親の値になる」ときだけ付ける。比率・増減率の行には付けない
- 一部だけを並べた内訳 (「うち」「主な」「主要製品」など、足しても親にならないもの) には付けない
- 階層は 1 段ずつ。中身は小計に、小計は合計に付ける。小計とその中身の両方を合計に付けない (二重に数えることになる)
- 親と内訳の unit が違うとき (兆円と億円など) は付けない
- 同じ親に別の切り口の内訳 (部門別と地域別など) があるときは、両方に付けてよい"""


def page_text(page: dict) -> str:
    """LLM に渡す文字データ（ページ番号の見出し付き）。"""
    text = re.sub(r"[ \t　]+", " ", norm_text(page["text"]))
    return f"ページ {page['page']} の文字データ:\n{text[:6000]}"


# ---------- 出力の解析と照合 ----------
def parse_json(s: str) -> dict:
    """応答の JSON を 1 ページ分の dict にする。1 枚を複数に分けた [{...}, {...}] や {...}, {...} は表をつないで 1 つにする。"""
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s.strip())
    dec = json.JSONDecoder()
    if s.startswith("["):
        got, end = dec.raw_decode(s)
        objs = [o for o in got if isinstance(o, dict)] if isinstance(got, list) else []
    else:
        i = s.find("{")
        if i < 0:
            raise ValueError("no json object")
        objs, end = [], i
        while end < len(s) and s[end] == "{":
            o, end = dec.raw_decode(s, end)
            objs.append(o)
            end = len(s) - len(s[end:].lstrip(" \t\r\n,"))
    if not objs:
        raise ValueError("no json object")
    if s[end:].strip() and not any("tables" in o or "facts" in o for o in objs):
        raise ValueError(f"表が無いまま JSON が閉じた (後ろに余分: char {end})")
    if len(objs) == 1:
        return objs[0]
    out = {"title": " / ".join(str(o["title"]) for o in objs if o.get("title")),
           "summary": " ".join(str(o["summary"]) for o in objs if o.get("summary"))}
    for key in ("tables", "facts"):
        if any(key in o for o in objs):
            out[key] = [x for o in objs for x in o.get(key) or []]
    return out


def expand_tables(out: dict) -> dict:
    """v2 以降の tables を v1 と同じ facts の並びに展開する。行の of は part_of（"metric|segment"）として残す。
    tables もそのまま残す（view.py が表の並び・列の順のままグラフと表に組み直す）。"""
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
    return dict(out, facts=facts)


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


_RATE_COL = re.compile(r"[%％]|率")
_RATE_UNITS = ("%", "pts", "pt", "ポイント")


def _paren(s) -> bool:
    return bool(re.fullmatch(r"\(.*\)", norm_text(str(s)).strip()))


def _two_breakdowns(values: list[float], total: float, tol: float) -> bool:
    """内訳を 2 組に分けて、両方とも total に一致するか（部門別と地域別のように、同じ親に 2 通りの内訳があるとき）。
    内訳が多いと組み合わせが増えるので 18 個まで。"""
    if len(values) < 3 or len(values) > 18 or abs(sum(values) - 2 * total) > 2 * tol:
        return False
    first, rest = values[0], values[1:]
    for mask in range(1 << len(rest)):  # values[0] を含む組を数える（組の入れ替えを重複して数えない）
        s = first + sum(v for i, v in enumerate(rest) if mask >> i & 1)
        if abs(s - total) <= tol and mask.bit_count() + 1 < len(values):
            return True
    return False


def check_sums(facts: list[dict]) -> list[dict]:
    """part_of の付いた facts を親ごと・期間ごとに足し、親の値と比べる。丸めの許容: 一番粗い表示桁の半分 × (内訳の数 + 1)。

    status: ok / split（別の切り口の内訳が 2 組あり、それぞれが一致）/ paren（括弧をマイナスとみなすと一致）/ partial（内訳がすべてプラスで合計に届かない＝一部だけの内訳）/ mismatch。
    親の fact に sum_status / sum_of を付ける。括弧の意味（マイナス・前回予想・率）は資料ごとに違うので値は書き換えない。
    同じ行が 2 つの表に出るときは 1 つにして足す。率の列（期間に % や「率」）と率の行は検算しない。"""
    by_key = {}
    for f in facts:
        by_key.setdefault((fact_key(f), f.get("period"), f.get("period_kind")), f)
    groups: dict[tuple, dict[str, dict]] = {}
    for f in facts:
        parent = f.get("part_of")
        if parent and f.get("value_num") is not None:
            p = norm_text(parent).strip()
            p = p if "|" in p else f"{p}|null"
            groups.setdefault((p, f.get("period"), f.get("period_kind")), {}).setdefault(fact_key(f), f)
    checks = []
    for (pkey, period, kind), uniq in groups.items():
        parts = list(uniq.values())
        parent = by_key.get((pkey, period, kind))
        if parent is None or parent.get("value_num") is None or len(parts) < 2:
            continue
        if _RATE_COL.search(norm_text(str(period))) or norm_text(str(parent.get("unit") or "")).strip() in _RATE_UNITS:
            continue
        value = parent["value_num"]
        total = sum(x["value_num"] for x in parts)
        # 丸めの許容は一番粗い桁に合わせる（親が整数・内訳が小数 1 桁なら 0.5 単位）
        tol = 0.5 * 10 ** -min(_decimals(x["value"]) for x in [parent, *parts]) * (len(parts) + 1) + 1e-9
        if abs(total - value) <= tol:
            status = "ok"
        elif _two_breakdowns([x["value_num"] for x in parts], value, tol):
            status = "split"
        else:
            signed = lambda x: -abs(x["value_num"]) if _paren(x["value"]) else x["value_num"]  # noqa: E731
            alt = sum(signed(x) for x in parts)
            if abs(alt - signed(parent)) <= tol and any(_paren(x["value"]) for x in [parent, *parts]):
                status, total = "paren", alt
            elif all(x["value_num"] >= 0 for x in parts) and 0 <= total < value:
                status = "partial"
            else:
                status = "mismatch"
        parent["sum_status"], parent["sum_of"] = status, round(total, 6)
        checks.append({"key": pkey, "period": period, "kind": kind, "value": value,
                       "sum": round(total, 6), "parts": len(parts), "status": status, "ok": status in ("ok", "split", "paren")})
    return checks


def recheck(page: dict) -> dict:
    """保存済みの結果のページを、LLM を呼ばずに照合・検算し直す（スクリプトを直したあと用）。"""
    if "facts" not in page:
        return page
    facts = [{k: v for k, v in f.items() if k not in ("value_num", "verified", "verified_by", "sum_status", "sum_of", "sum_ok")}
             for f in page["facts"]]
    out = verify(page, {"facts": facts})
    return dict(page, facts=out["facts"], sum_checks=out["sum_checks"])


def verify(page: dict, out: dict) -> dict:
    """facts に value_num・verified・verified_by（text / ocr）を付ける。表を展開し、内訳の合計を検算する。
    page に ocr_numbers があれば、文字データに無い数字をそれで照合する（本番は OCR しない。analyzer の PoC だけ）。"""
    out = expand_tables(out)
    nums = numbers_in(page["text"])
    ocr = set(page.get("ocr_numbers") or [])
    facts = []
    for f in out.get("facts") or []:
        if not isinstance(f, dict) or "value" not in f:
            continue
        f["value"] = strip_unit(f.get("value"), f.get("unit"))
        v = parse_number(f.get("value"))
        f["value_num"] = v
        f["verified_by"] = None if v is None else "text" if abs(v) in nums else "ocr" if abs(v) in ocr else None
        f["verified"] = f["verified_by"] is not None
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
        "verified_ocr": sum(1 for f in fs if f.get("verified_by") == "ocr"),
        "verified_ratio": round(ok / len(fs), 3) if fs else None,
        "errors": sum(1 for p in pages if p.get("error")),
        "sum_checks": sum(len(p.get("sum_checks") or []) for p in pages),
        "sum_mismatch": sum(1 for p in pages for c in p.get("sum_checks") or [] if c.get("status", "ok" if c["ok"] else "mismatch") == "mismatch"),
        "sum_partial": sum(1 for p in pages for c in p.get("sum_checks") or [] if c.get("status") == "partial"),
    }
