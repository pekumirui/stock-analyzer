#!/usr/bin/env python3
"""
fmtdiff.py — 月次資料PDFの「フォーマット変更」を機械的に検出する

数値は毎月変わって当然なので、数値をマスクした「構造の指紋」だけを比較する。

  python fmtdiff.py new.pdf                 # 指紋(structure_hash)を出す
  python fmtdiff.py new.pdf --base old.pdf  # 前月と比較して差分を出す
  python fmtdiff.py new.pdf --base old.pdf --json
  python fmtdiff.py --dir DIR               # 同一発行体の連続月だけ隣接比較
  python fmtdiff.py --dir DIR --json
  # DIR/*.pdf と DIR/<code>/*.pdf。空で DIR/monthly があればそこを1段だけ見る

終了コード: 0=変更なし / 1=フォーマット変更あり / 2=エラー
"""
import argparse
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pdfplumber

from tools.monthly.text_mask import mask

RE_ISSUER_DIR = re.compile(r"^[0-9][0-9A-Z]{3}$")
RE_ISSUER_IN_NAME = re.compile(r"_([0-9][0-9A-Z]{3})_")


def norm(s: str) -> str:
    """数値・日付をマスクして構造だけ残す。'2026年7月度 売上高 1,234' -> '<D>度売上高<N>'"""
    return mask(s)


def is_label(s: str) -> bool:
    """マスク後に <N>/<D> と記号しか残らないものはラベルではない（＝データセル）"""
    if not s:
        return False
    stripped = s.replace("<N>", "").replace("<D>", "").replace("<Q>", "")
    return bool(re.search(r"[^\W\d_]", stripped, re.UNICODE))


def table_fingerprint(raw_table, page_no, idx):
    rows = [[norm(c) for c in (r or [])] for r in raw_table if r]
    if not rows:
        return None
    header = rows[0]
    labels = []
    for r in rows[1:]:
        for c in r:
            if is_label(c):
                labels.append(c)
                break
    return {
        "page": page_no,
        "index": idx,
        "n_cols": max(len(r) for r in rows),
        "n_rows": len(rows),
        "header": header,
        "labels": labels,
    }


def fingerprint(path):
    pages, tables, headings = 0, [], []
    with pdfplumber.open(path) as pdf:
        pages = len(pdf.pages)
        for pi, page in enumerate(pdf.pages, 1):
            for ti, t in enumerate(page.extract_tables()):
                fp = table_fingerprint(t, pi, ti)
                if fp:
                    tables.append(fp)
            text = page.extract_text() or ""
            for line in text.split("\n"):
                n = norm(line)
                if is_label(n) and 2 <= len(n) <= 40:
                    headings.append(n)
    # 表の中身が見出しとして二重計上されるのを除去
    in_table = set()
    for t in tables:
        in_table.add("".join(c for c in t["header"] if c))
        in_table.update(t["labels"])
    headings = [
        h
        for h in headings
        if "<N>" not in h
        and h not in in_table
        and not any(h.startswith(lb) for lb in in_table if lb)
    ]

    fp = {"pages": pages, "tables": tables, "headings": sorted(set(headings))}
    fp["structure_hash"] = hashlib.sha256(
        json.dumps(
            {
                "pages": pages,
                "tables": [
                    {k: t[k] for k in ("n_cols", "header", "labels")} for t in tables
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    return fp


def similarity(a, b):
    """表どうしの近さ。ヘッダーと行ラベルの一致度"""
    if (a["header"], a["labels"], a["n_cols"]) == (b["header"], b["labels"], b["n_cols"]):
        return 1.0
    sa, sb = set(a["labels"]), set(b["labels"])
    hdr = SequenceMatcher(None, "|".join(a["header"]), "|".join(b["header"])).ratio()
    if not sa and not sb:
        # 行ラベルが全て数値の表（データ専用ブロック）はヘッダーと形状だけで判定
        shape = 1.0 if a["n_cols"] == b["n_cols"] else 0.0
        return 0.6 * hdr + 0.4 * shape
    jac = len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0
    return 0.7 * jac + 0.3 * hdr


def match_tables(old, new, threshold=0.35):
    """ページ移動しても追えるように、index順ではなく類似度で対応付ける"""
    pairs, used = [], set()
    cands = sorted(
        ((similarity(o, n), oi, ni) for oi, o in enumerate(old) for ni, n in enumerate(new)),
        reverse=True,
    )
    taken_o = set()
    for score, oi, ni in cands:
        if score < threshold or oi in taken_o or ni in used:
            continue
        taken_o.add(oi)
        used.add(ni)
        pairs.append((oi, ni, score))
    removed = [i for i in range(len(old)) if i not in taken_o]
    added = [i for i in range(len(new)) if i not in used]
    return pairs, removed, added


def compare(old, new):
    """差分の『説明』を作る。判定はしない（判定は structure_hash が担当）"""
    changes, notes = [], []
    if old["pages"] != new["pages"]:
        changes.append(
            {"type": "page_count", "detail": f"ページ数 {old['pages']} → {new['pages']}"}
        )
    if len(old["tables"]) != len(new["tables"]):
        changes.append(
            {
                "type": "table_count",
                "detail": f"表の数 {len(old['tables'])} → {len(new['tables'])}",
            }
        )

    pairs, removed, added = match_tables(old["tables"], new["tables"])

    for i in added:
        t = new["tables"][i]
        changes.append(
            {
                "type": "table_added",
                "detail": f"表が追加 (p.{t['page']}, {t['n_cols']}列)",
                "sample": t["labels"][:5],
            }
        )
    for i in removed:
        t = old["tables"][i]
        changes.append(
            {
                "type": "table_removed",
                "detail": f"表が消滅 (旧 p.{t['page']}, {t['n_cols']}列)",
                "sample": t["labels"][:5],
            }
        )

    for oi, ni, score in sorted(pairs, key=lambda x: x[2], reverse=True):
        o, n = old["tables"][oi], new["tables"][ni]
        where = f"表(旧p.{o['page']}→新p.{n['page']})"
        if o["n_cols"] != n["n_cols"]:
            changes.append(
                {"type": "col_count", "detail": f"{where} 列数 {o['n_cols']} → {n['n_cols']}"}
            )
        if o["header"] != n["header"]:
            changes.append(
                {
                    "type": "header",
                    "detail": f"{where} ヘッダー変更",
                    "before": [c for c in o["header"] if c],
                    "after": [c for c in n["header"] if c],
                }
            )
        so, sn = set(o["labels"]), set(n["labels"])
        if sn - so:
            changes.append(
                {
                    "type": "row_added",
                    "detail": f"{where} 行ラベル追加（新セグメントの可能性）",
                    "items": sorted(sn - so),
                }
            )
        if so - sn:
            changes.append(
                {"type": "row_removed", "detail": f"{where} 行ラベル消滅", "items": sorted(so - sn)}
            )

    ho, hn = set(old["headings"]), set(new["headings"])
    if hn - ho:
        notes.append({"type": "heading_added", "detail": "新出の見出し/項目", "items": sorted(hn - ho)[:30]})
    if ho - hn:
        notes.append({"type": "heading_removed", "detail": "消えた見出し/項目", "items": sorted(ho - hn)[:30]})
    return changes, notes


def render(changed, changes, notes, old, new):
    out = []
    out.append(f"旧: {old['pages']}p / 表{len(old['tables'])} / hash={old['structure_hash']}")
    out.append(f"新: {new['pages']}p / 表{len(new['tables'])} / hash={new['structure_hash']}")
    out.append("")

    if not changed:
        out.append("判定: フォーマット変更なし（数値更新のみ）")
    elif changes:
        out.append(f"判定: フォーマット変更あり — 内訳 {len(changes)}件")
        for c in changes:
            out.append(f"  - {c['detail']}")
            for k in ("before", "after", "items", "sample"):
                if c.get(k):
                    out.append(f"      {k}: {c[k]}")
    else:
        out.append("判定: フォーマット変更あり — ただし内訳を特定できず（要目視）")

    if notes:
        out.append(f"\n■ 文言レベルの差分 {len(notes)}件（判定には影響しない / LLM判定向き）")
        for c in notes:
            out.append(f"  - {c['detail']}: {len(c.get('items', []))}件")
            for it in c.get("items", [])[:10]:
                out.append(f"      {it}")
    return "\n".join(out)


def discover_pdfs(root):
    """DIR/*.pdf と DIR/<subdir>/*.pdf のみ。2段以上は見ない。"""
    root = Path(root)
    found = []
    for child in root.iterdir():
        if child.is_file() and child.suffix.lower() == ".pdf":
            found.append(child)
        elif child.is_dir():
            for gchild in child.iterdir():
                if gchild.is_file() and gchild.suffix.lower() == ".pdf":
                    found.append(gchild)
    return found


def issuer_code(pdf_path, root):
    """1段サブフォルダ名が銘柄コードならそれを優先。なければ basename の _CODE_。"""
    pdf_path = Path(pdf_path)
    root = Path(root)
    try:
        rel = pdf_path.resolve().relative_to(root.resolve())
    except ValueError:
        rel = Path(pdf_path.name)
    parts = rel.parts
    if len(parts) >= 2 and RE_ISSUER_DIR.match(parts[0]):
        return parts[0]
    m = RE_ISSUER_IN_NAME.search(pdf_path.name)
    if m:
        return m.group(1)
    return "?"


def scan_directory(root):
    """発行体ごとに basename 順で隣接ペアだけ比較。全対比較はしない。

    DIR 直下に PDF が無く DIR/monthly がディレクトリなら、そこをルートにする
    （data/monthly/{code}/*.pdf。任意再帰はしない）。
    """
    root = Path(root)
    if not discover_pdfs(root):
        monthly = root / "monthly"
        if monthly.is_dir():
            root = monthly
    groups = {}
    for pdf in discover_pdfs(root):
        groups.setdefault(issuer_code(pdf, root), []).append(pdf)

    issuers = []
    for code in sorted(groups):
        files = sorted(groups[code], key=lambda p: p.name)
        fps = [fingerprint(str(p)) for p in files]
        changed_pairs = []
        unchanged = 0
        for i in range(len(files) - 1):
            old, new = fps[i], fps[i + 1]
            if old["structure_hash"] == new["structure_hash"]:
                unchanged += 1
                continue
            changes, _notes = compare(old, new)
            changed_pairs.append(
                {
                    "files": [files[i].name, files[i + 1].name],
                    "hashes": [old["structure_hash"], new["structure_hash"]],
                    "changes_count": len(changes),
                }
            )
        issuers.append(
            {
                "code": code,
                "pdf_count": len(files),
                "pair_count": max(0, len(files) - 1),
                "unchanged": unchanged,
                "changed": len(changed_pairs),
                "changed_pairs": changed_pairs,
            }
        )
    totals = {
        "issuers": len(issuers),
        "pdfs": sum(x["pdf_count"] for x in issuers),
        "pairs": sum(x["pair_count"] for x in issuers),
        "unchanged": sum(x["unchanged"] for x in issuers),
        "changed": sum(x["changed"] for x in issuers),
    }
    return {"issuers": issuers, "totals": totals}


def render_dir(result):
    lines = []
    for iss in result["issuers"]:
        lines.append(
            f"issuer {iss['code']}  pdfs={iss['pdf_count']}  "
            f"pairs={iss['pair_count']}  unchanged={iss['unchanged']}  "
            f"changed={iss['changed']}"
        )
        for pair in iss["changed_pairs"]:
            f0, f1 = pair["files"]
            h0, h1 = pair["hashes"]
            lines.append(f"  CHANGED  {f0}  hash={h0}")
            lines.append(f"           {f1}  hash={h1}")
            lines.append(f"           changes={pair['changes_count']}")
    t = result["totals"]
    lines.append(
        f"TOTAL  issuers={t['issuers']}  pdfs={t['pdfs']}  "
        f"pairs={t['pairs']}  unchanged={t['unchanged']}  changed={t['changed']}"
    )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", nargs="?", help="指紋を取るPDF")
    ap.add_argument("--base", help="比較元（前月）のPDF")
    ap.add_argument("--dir", help="issuer ごとに隣接PDFを比較")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.dir and a.target:
        print("ERROR: --dir and target are mutually exclusive", file=sys.stderr)
        return 2
    if a.dir and a.base:
        print("ERROR: --dir and --base are mutually exclusive", file=sys.stderr)
        return 2
    if not a.dir and not a.target:
        print("ERROR: specify a PDF or --dir DIR", file=sys.stderr)
        return 2

    if a.dir:
        root = Path(a.dir)
        if not root.exists():
            print(f"ERROR: directory not found: {a.dir}", file=sys.stderr)
            return 2
        if not root.is_dir():
            print(f"ERROR: not a directory: {a.dir}", file=sys.stderr)
            return 2
        result = scan_directory(root)
        if a.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(render_dir(result))
        return 1 if result["totals"]["changed"] else 0

    new = fingerprint(a.target)
    if not a.base:
        print(json.dumps(new, ensure_ascii=False, indent=2) if a.json else new["structure_hash"])
        return 0
    old = fingerprint(a.base)
    changed = old["structure_hash"] != new["structure_hash"]
    changes, notes = compare(old, new)
    if a.json:
        print(json.dumps(
            {
                "changed": changed,
                "old_hash": old["structure_hash"],
                "new_hash": new["structure_hash"],
                "changes": changes if changed else [],
                "notes": notes,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        print(render(changed, changes, notes, old, new))
    return 1 if changed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
