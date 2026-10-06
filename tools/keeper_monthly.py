#!/usr/bin/env python3
"""ＫｅｅＰｅｒ技研 (6036) の月次速報 PDF → MCP `monthly_ingest` の引数（読み取り専用）。

6036 は表の形が複雑で `tdnet_monthly_pdf_sidecar.py` が行を再現できず、毎月 manual になる。
行名を人や bot に考えさせないため、PDF の表から保存名どおりの rows を作る。
行名と範囲の決め方は `docs/DESIGN_monthly_labels.md` の ＫｅｅＰｅｒ技研。DB にも R2 にも書かない。

  python tools/keeper_monthly.py /mnt/r2/monthly/6036/20261005_6036_…(9月).pdf
  python tools/keeper_monthly.py 20261005_6036_….txt   # pdftotext -layout 済みのテキストでもよい

出力の JSON をそのまま MCP `monthly_ingest` に渡す（先に dry_run=true で暦年の割当を確かめる）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

CODE = "60360"
LABO = "キーパーLABO運営事業"
PRODUCT = "キーパー製品等関連事業"
MARKETS = ("アフターマーケット", "新車マーケット", "海外", "その他コーティング")

_NUM = re.compile(r"-?[\d,]+(?:\.\d+)?%?")
_FILE_DATE = re.compile(r"^(\d{4})(\d{2})(\d{2})_")
_FILE_MONTH = re.compile(r"[（(](\d{1,2})月")
# 月次動向推移（2026年6月の書式から 直営 / FC 新店 と FC 込みの店舗合計）
_TREND_ROW = r"{y}年{m}月\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s+([\d.]+)%\s+(\S+)\s+(\S+)\s+(\d+)"


def pdf_text(path: Path) -> str:
    if path.suffix.lower() == ".txt":
        return path.read_text(encoding="utf-8")
    exe = shutil.which("pdftotext")
    if not exe:
        raise SystemExit("pdftotext が要ります（poppler-utils）")
    out = subprocess.run([exe, "-layout", str(path), "-"], capture_output=True, check=True)  # nosec B603
    return out.stdout.decode("utf-8", "replace")


def report_month(name: str) -> tuple[str, int, int]:
    """ファイル名 `YYYYMMDD_6036_…(N月).pdf` → (開示日, 暦年, 月)。月次は翌月開示。"""
    d = _FILE_DATE.match(name)
    m = _FILE_MONTH.findall(name)
    if not d or not m:
        raise SystemExit(f"ファイル名から開示日と月が読めません: {name}")
    fy, fm, month = int(d.group(1)), int(d.group(2)), int(m[-1])
    return f"{d.group(1)}-{d.group(2)}-{d.group(3)}", (fy if month < fm else fy - 1), month


def _amt(tok: str) -> float:
    return float(tok.replace(",", ""))


def _pct(tok: str) -> float:
    return float(tok.rstrip("%"))


def _nums(s: str) -> list[str]:
    return _NUM.findall(s)


def _yoy_line(lines: list[str], i: int) -> list[str]:
    """表の次の行が 前年比 なら、その数字（見出しより後ろ）。"""
    for j in range(i + 1, min(i + 4, len(lines))):
        if "前年比" in lines[j]:
            return _nums(lines[j].split("前年比", 1)[1])
        if lines[j].strip():
            return []
    return []


def parse_text(text: str, year: int, month: int) -> dict[str, float]:
    """PDF のテキスト → {保存名: 値}。値の無い行は返さない。"""
    # 「108 .8%」「98 .7 %」のように割れた数字を繋ぐ
    text = re.sub(r"(\d) +\.(\d)", r"\1.\2", text)
    text = re.sub(r"(\d) +%", r"\1%", text)
    lines = text.split("\n")
    out: dict[str, float] = {}

    def store_row(rx: str, pre: str) -> None:
        # 直営の 全店合計 / 既存店合計。前年比の行の後ろ 3 つは 来店台数 / 平均単価 / 人時生産
        for i, line in enumerate(lines):
            m = re.match(rx, line)
            if not m:
                continue
            toks = _nums(line[m.end():])
            out[f"{pre} 売上高"] = _amt(toks[0])
            out[f"{pre} 店舗数"] = float(next(g for g in m.groups() if g))
            yt = _yoy_line(lines, i)
            if len(toks) > 1 and toks[1].endswith("%"):
                out[f"{pre} 売上高(前年比)"] = _pct(toks[1])
            elif yt:
                out[f"{pre} 売上高(前年比)"] = _pct(yt[0])
            if len(yt) >= 3:
                out[f"{pre} 客数(前年比)"] = _pct(yt[-3])
                out[f"{pre} 客単価(前年比)"] = _pct(yt[-2])
            return

    store_row(r"^\s*(?:ラボ\s*全(\d+)店合計|全店合計[（(](\d+)店[）)])", f"{LABO} 直営 全店")
    store_row(r"^\s*(?:ラボ\s*既存店[（(](\d+)店[）)]計|既存店合計[（(](\d+)店[）)])", f"{LABO} 直営 既存")

    def amount_row(rx: str, el: str) -> None:
        for i, line in enumerate(lines):
            m = re.match(rx, line)
            if not m:
                continue
            toks = _nums(line[m.end():])
            if not toks or toks[0].endswith("%"):
                continue
            out[el] = _amt(toks[0])
            y = toks[1] if len(toks) > 1 and toks[1].endswith("%") else None
            if y is None:
                yt = _yoy_line(lines, i)
                y = yt[0] if yt else None
            # 「-」は前年ゼロ。0 円で 0.0% も前年ゼロかもしれず入れない
            if y is not None and not (out[el] == 0 and _pct(y) == 0):
                out[f"{el}(前年比)"] = _pct(y)
            return

    amount_row(r"^\s*FC関連(?:売上|・その他合計)(?=\s)", f"{LABO} FC 売上高")
    amount_row(r"^\s*キーパー\s*(?:LABO|ラボ)運営事業(?=\s)", f"{LABO} 売上高")
    amount_row(r"^\s*キーパー製品等関連事業(?:合計)?(?=\s)", f"{PRODUCT} 売上高")
    for seg in MARKETS:
        amount_row(rf"^\s*{seg}(?=\s)", f"{seg} 売上高")
    amount_row(r"^\s*全社合計(?=\s)", "全社 売上高")

    # 店舗数は月次動向推移の 店舗合計（FC 込み。2026年6月の書式から）だけを正とする。
    # 注１の FC 店舗数は更新が遅れた月があるので、店舗合計と合うときだけ。旧書式の PDF では出さない
    if "直営FC店舗" in re.sub(r"\s", "", text):
        trend = re.search(_TREND_ROW.format(y=year, m=month), text)
        if trend:
            total = int(trend.group(7))
            out[f"{LABO} 直営+FC 店舗数"] = float(total)
            note = re.search(r"全(\d+)店舗の内、\s*FC(\d+)店舗", text)
            if note and int(note.group(1)) == total:
                out[f"{LABO} FC 店舗数"] = float(note.group(2))
    return out


def ingest_args(path: Path) -> dict:
    as_of, year, month = report_month(path.name)
    values = parse_text(pdf_text(path), year, month)
    if "全社 売上高" not in values:
        raise SystemExit(f"全社合計が読めません（書式が変わった?）: {path.name}")
    rows = [
        {"element": el, "unit": "yoy_index" if el.endswith("(前年比)") else "number",
         "values": {str(month): (int(v) if not el.endswith("(前年比)") and v == int(v) else v)}}
        for el, v in values.items()
    ]
    return {"code": CODE, "as_of": as_of, "months": [month], "rows": rows}


def check_sums(values: dict[str, float]) -> list[str]:
    """表の足し算（円）。食い違えば読み違い。"""
    bad = []
    tot = values.get("全社 売上高")
    labo = values.get(f"{LABO} 売上高")
    prod = values.get(f"{PRODUCT} 売上高")
    direct = values.get(f"{LABO} 直営 全店 売上高")
    fc = values.get(f"{LABO} FC 売上高")
    if tot is not None and labo is not None and prod is not None and abs(labo + prod - tot) > 2:
        bad.append(f"全社 {tot:,.0f} ≠ LABO {labo:,.0f} + 製品 {prod:,.0f}")
    if labo is not None and direct is not None and fc is not None and abs(direct + fc - labo) > 2:
        bad.append(f"LABO {labo:,.0f} ≠ 直営 {direct:,.0f} + FC {fc:,.0f}")
    parts = [values.get(f"{s} 売上高") for s in MARKETS]
    if prod is not None and all(p is not None for p in parts) and abs(sum(parts) - prod) > 2:  # type: ignore[arg-type]
        bad.append(f"製品 {prod:,.0f} ≠ 4 マーケットの和 {sum(parts):,.0f}")  # type: ignore[arg-type]
    return bad


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pdf", type=Path)
    a = ap.parse_args()
    args = ingest_args(a.pdf)
    bad = check_sums({r["element"]: float(next(iter(r["values"].values()))) for r in args["rows"]})
    if bad:
        raise SystemExit("足し算が合いません: " + " / ".join(bad))
    json.dump(args, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
