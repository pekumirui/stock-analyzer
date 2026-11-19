#!/usr/bin/env python3
"""短信 PDF の読み取りの回帰チェック（KAB-244。docs/DESIGN_xbrl.md「短信 PDF の回帰チェック」）。

検証済みの開示（XBRL 検証 bot の 1〜3 回目、19 銘柄）を正解データにして、読み取りを直すたびに
J-Quants と一致を確かめた値が消えたり変わったりしていないかを見る。本番 DB にも VPS にも触れない。
スクラッチ SQLite（一時ファイル）に statements と HTML を入れ、sync_pdf_filing で読む。

  # HTML を用意する（IR BANK の PDF を変換。PDF は保存しない）
  python tools/tdnet_pdf/golden_check.py fetch --html-dir data/tdnet_pdf_golden/html
  # 今のチェックアウトを正解データと比べる。消えた値・変わった値があれば exit 1
  python tools/tdnet_pdf/golden_check.py check --html-dir data/tdnet_pdf_golden/html
  # main の worktree と比べ、正解データに無い行も含めて差を出す
  python tools/tdnet_pdf/golden_check.py check --html-dir ... --base-repo ../sa-main --md out.md

正解データ（期待値）は tests/golden/tdnet_pdf/expected.json。HTML の全件は repo に入れず
data/tdnet_pdf_golden/html/{開示番号}.html.gz に置く。tests/golden/tdnet_pdf/html/ の小さな組は pytest が回す。
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = ROOT / "tests" / "golden" / "tdnet_pdf" / "expected.json"
MINI_HTML = ROOT / "tests" / "golden" / "tdnet_pdf" / "html"
DATA_HTML = ROOT / "data" / "tdnet_pdf_golden" / "html"
USER_AGENT = "stock-analyzer/1.0 (personal; tools/tdnet_pdf/golden_check)"
_DATE_FIELDS = ("disclosed_date", "period_start", "period_end", "fiscal_year_start", "fiscal_year_end")
STATEMENT_FIELDS = (
    "code", "disclosed_date", "disclosed_time", "type_of_period", "type_of_document", *_DATE_FIELDS[1:],
)
# 行の鍵。値と単位は比べる側。
KEY_FIELDS = (
    "std", "is_consolidated", "period_type", "period_start", "period_end",
    "value_kind", "range_side", "target_period",
)

# J-Quants との突き合わせ（XBRL 検証 bot の cmp_jq.py と同じ規則）。
_ACTUAL = {
    "net_sales": "Sales", "operating_income": "OP", "ordinary_income": "OdP", "net_income": "NP",
    "eps_basic": "EPS", "eps_diluted": "DEPS", "total_assets": "TA", "net_assets": "Eq",
    "equity_ratio": "EqAR", "bps": "BPS", "operating_cf": "CFO", "investing_cf": "CFI",
    "financing_cf": "CFF", "cash_end": "CashEq",
}
_FORECAST = {"net_sales": "Sales", "operating_income": "OP", "ordinary_income": "OdP", "net_income": "NP", "eps_basic": "EPS"}
_DIVIDEND = (("Q2", "Div2Q"), ("year_end", "DivFY"), ("annual", "DivAnn"))
_PER_SHARE = {"eps_basic", "eps_diluted", "bps", "dps"}
_MONEY_TOL = 1e6 + 1



def load_expected(path: Path = EXPECTED) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_expected(data: dict, path: Path = EXPECTED) -> None:
    """1 値 1 行で書く（差分を PR で読めるように）。"""
    lines = ["{", f' "version": {json.dumps(data["version"])},', f' "note": {json.dumps(data["note"], ensure_ascii=False)},']
    lines.append(' "disclosures": {')
    nos = sorted(data["disclosures"])
    for n, no in enumerate(nos):
        doc = data["disclosures"][no]
        lines.append(f"  {json.dumps(no)}: {{")
        for field in ("set", "title", "html_sha256", "statement"):
            lines.append(f"   {json.dumps(field)}: {json.dumps(doc[field], ensure_ascii=False, sort_keys=True)},")
        for field in ("values", "known_gaps"):
            items = doc[field]
            lines.append(f"   {json.dumps(field)}: [")
            lines.extend(
                f"    {json.dumps(item, ensure_ascii=False, sort_keys=True)}{',' if i < len(items) - 1 else ''}"
                for i, item in enumerate(items)
            )
            lines.append("   ]" + ("," if field == "values" else ""))
        lines.append("  }" + ("," if n < len(nos) - 1 else ""))
    lines += [" }", "}"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def html_file(no: str, dirs: list[Path]) -> Path | None:
    for base in dirs:
        path = base / f"{no}.html.gz"
        if path.exists():
            return path
    return None


def row_key(row: dict) -> tuple:
    return (row["no"], *(row[field] for field in KEY_FIELDS))



def _seed_and_sync(db, expected: dict, html_dirs: list[Path], nos: list[str]) -> dict[str, str]:
    from app.models import Statement, Stock, TdnetPdfDocument
    from app.xbrl.mappings import apply_mappings
    from app.xbrl.pdf import sync_pdf_filing

    apply_mappings(db)
    ready = []
    stocks = {code for (code,) in db.query(Stock.code)}
    for no in nos:
        doc = expected["disclosures"][no]
        path = html_file(no, html_dirs)
        if path is None:
            continue
        raw = gzip.decompress(path.read_bytes())
        st = {k: (date.fromisoformat(v) if k in _DATE_FIELDS and v else v) for k, v in doc["statement"].items()}
        if st["code"] not in stocks:
            stocks.add(st["code"])
            db.add(Stock(
                code=st["code"], name=st["code"], sector17="x", sector33="x", market="プライム",
                scale_category="", margin_type="", product_category="011",
            ))
        db.merge(Statement(disclosure_no=no, **st))
        db.merge(TdnetPdfDocument(
            disclosure_no=no, code=st["code"], disclosed_date=st["disclosed_date"],
            disclosed_time=st["disclosed_time"], title=doc["title"], status="ok",
            html_gz=gzip.compress(raw, mtime=0), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
            pages=None, converter="svg2", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc).replace(tzinfo=None),
        ))
        ready.append(no)
    db.commit()
    outcomes: dict[str, str] = {}
    for no in ready:
        try:
            outcomes[no] = sync_pdf_filing(db, no).value
        except Exception as exc:
            db.rollback()
            outcomes[no] = f"error:{type(exc).__name__}"
        db.commit()
    return outcomes


def _rows(db) -> list[dict]:
    from app.models import XbrlFiling, XbrlFinancial, XbrlStdItem

    query = (
        db.query(XbrlFinancial, XbrlStdItem.code, XbrlFiling.doc_id)
        .join(XbrlStdItem, XbrlFinancial.std_item_id == XbrlStdItem.id)
        .join(XbrlFiling, XbrlFinancial.filing_id == XbrlFiling.id)
        .filter(XbrlFiling.source == "tdnet_pdf")
    )
    out = []
    for row, std, no in query:
        out.append({
            "no": no, "std": std, "is_consolidated": bool(row.is_consolidated),
            "period_type": row.period_type,
            "period_start": row.period_start.isoformat() if row.period_start else None,
            "period_end": row.period_end.isoformat(),
            "value_kind": row.value_kind, "range_side": row.range_side, "target_period": row.target_period,
            "value": format(Decimal(str(row.value)).normalize(), "f"), "unit": row.unit,
        })
    out.sort(key=row_key)
    return out


def dump_in_process(expected: dict, html_dirs: list[Path], nos: list[str] | None = None) -> dict:
    """DATABASE_URL の DB（空であること）に入れて読む。pytest は conftest の一時 DB、CLI は dump が用意する。"""
    from app.db import SessionLocal

    nos = sorted(expected["disclosures"]) if nos is None else sorted(nos)
    db = SessionLocal()
    try:
        outcomes = _seed_and_sync(db, expected, html_dirs, nos)
        return {"outcomes": outcomes, "rows": _rows(db)}
    finally:
        db.close()


def dump(expected_path: Path, html_dirs: list[Path], repo: Path) -> dict:
    """repo のチェックアウトの読み取りで、新しい一時 DB に流した結果。repo は main の worktree など。"""
    with tempfile.TemporaryDirectory(prefix="golden-check-") as tmp:
        out = Path(tmp) / "rows.json"
        env = dict(os.environ, DATABASE_URL=f"sqlite:///{Path(tmp) / 'stock.db'}")
        cmd = [
            sys.executable, str(Path(__file__).resolve()), "_dump", "--repo", str(repo.resolve()),
            "--expected", str(expected_path.resolve()), "--out", str(out),
        ]
        for base in html_dirs:
            cmd += ["--html-dir", str(base.resolve())]
        subprocess.run(cmd, cwd=repo, env=env, check=True)
        return json.loads(out.read_text(encoding="utf-8"))


def _dump_main(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    sys.path.insert(0, str(repo))
    from app.db import init_db

    init_db()
    result = dump_in_process(load_expected(Path(args.expected)), [Path(p) for p in args.html_dir])
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0



@dataclass
class Report:
    lost: list[dict]          # 正解の値の行が無い
    changed: list[tuple]      # 正解の値と違う (expected, actual)
    verified: int             # 正解の値の数（HTML がある開示だけ）
    fixed: list[dict]         # known_gaps のうち、今は J-Quants と一致するもの
    gaps_wrong: list[dict]    # known_gaps のうち、今は違う値が入っているもの
    skipped_docs: list[str]   # HTML が無くて読まなかった開示

    @property
    def ok(self) -> bool:
        return not self.lost and not self.changed


def _num(value) -> Decimal:
    return Decimal(str(value))


def _close(item: str, a: float, b: float) -> bool:
    if item in _PER_SHARE:
        return abs(a - b) <= 0.011
    if item == "equity_ratio":
        return abs(a - b) <= 0.0011
    return abs(a - b) < _MONEY_TOL


def candidates(rows: list[dict], spec: dict) -> list[dict]:
    """J-Quants の 1 項目に当たる行（cmp_jq.py の cands と同じ）。"""
    pe = spec["period_end"]
    tps = tuple(spec["tps"])
    out = []
    for row in rows:
        if row["std"] != spec["item"] or row["value_kind"] != spec["kind"] or row["range_side"] != "point":
            continue
        p = row["period_end"]
        in_h1 = tps == ("H1",) and str(int(pe[:4]) - 1) + pe[4:] < p < pe
        if p != pe and not in_h1:
            continue
        cons = spec["cons"]
        if not (int(row["is_consolidated"]) == cons or spec["item"] == "dps" or cons == -1):
            continue
        if row["target_period"] in tps:
            out.append(row)
    return out


def compare(expected: dict, result: dict) -> Report:
    by_no: dict[str, list[dict]] = defaultdict(list)
    for row in result["rows"]:
        by_no[row["no"]].append(row)
    actual = {row_key(row): row for row in result["rows"]}
    lost: list[dict] = []
    changed: list[tuple] = []
    fixed: list[dict] = []
    wrong: list[dict] = []
    verified = 0
    skipped = []
    for no, doc in sorted(expected["disclosures"].items()):
        if no not in result["outcomes"]:
            skipped.append(no)
            continue
        for value in doc["values"]:
            verified += 1
            got = actual.get(row_key({"no": no, **value}))
            if got is None:
                lost.append({"no": no, **value})
            elif _num(got["value"]) != _num(value["value"]):
                changed.append(({"no": no, **value}, got))
        for gap in doc["known_gaps"]:
            cands = candidates(by_no[no], gap)
            if any(_close(gap["item"], float(row["value"]), gap["jq"]) for row in cands):
                fixed.append({"no": no, **gap})
            elif cands and gap["res"] == "missing":
                wrong.append({"no": no, **gap, "pdf": [row["value"] for row in cands]})
    return Report(lost, changed, verified, fixed, wrong, skipped)


def row_diff(base: dict, head: dict) -> dict:
    """正解データに無い行も含めた全行の差（base → head）。"""
    b = {row_key(row): row for row in base["rows"]}
    h = {row_key(row): row for row in head["rows"]}
    return {
        "lost": [b[k] for k in sorted(b.keys() - h.keys())],
        "added": [h[k] for k in sorted(h.keys() - b.keys())],
        "changed": [(b[k], h[k]) for k in sorted(b.keys() & h.keys()) if _num(b[k]["value"]) != _num(h[k]["value"])],
        "outcomes": sorted(
            (no, base["outcomes"].get(no), head["outcomes"].get(no))
            for no in base["outcomes"].keys() | head["outcomes"].keys()
            if base["outcomes"].get(no) != head["outcomes"].get(no)
        ),
    }


def _fmt_row(row: dict) -> str:
    cons = "連結" if row["is_consolidated"] else "単体"
    return (f"{row['no']} {row['std']} {cons} {row['period_end']} {row['value_kind']}"
            f"/{row['range_side']}/{row['target_period']} = {row['value']}")


def markdown(report: Report, result: dict, diff: dict | None, *, label: str) -> str:
    status = Counter(result["outcomes"].values())
    out = [
        f"### golden_check: {label}",
        "",
        (f"- 開示 {len(result['outcomes'])}（HTML が無く読まなかった {len(report.skipped_docs)}）"
        f" / filing {dict(sorted(status.items()))}"),
        f"- 正解の値 {report.verified}: **消えた {len(report.lost)} / 変わった {len(report.changed)}**",
        (f"- 既知の欠け（J-Quants と不一致）のうち一致するようになった {len(report.fixed)}"
        f" / 違う値が入った {len(report.gaps_wrong)}"),
    ]
    for title, rows in (("消えた値", report.lost), ("変わった値", [e for e, _ in report.changed])):
        if rows:
            out += ["", f"<details><summary>{title} {len(rows)}</summary>", "", "```"]
            if title == "変わった値":
                out += [f"{_fmt_row(e)} -> {a['value']}" for e, a in report.changed]
            else:
                out += [_fmt_row(row) for row in rows]
            out += ["```", "</details>"]
    if report.fixed:
        out += ["", f"<details><summary>一致するようになった {len(report.fixed)}</summary>", "", "```"]
        out += [f"{g['no']} {g['jq_key']} {g['item']} {g['kind']} {g['period_end']} = {g['jq']}" for g in report.fixed]
        out += ["```", "</details>"]
    if diff is not None:
        out += [
            "",
            (f"- base との全行の差: 消えた {len(diff['lost'])} / 変わった {len(diff['changed'])}"
            f" / 増えた {len(diff['added'])} / filing の状態の変化 {len(diff['outcomes'])}"),
        ]
        for title, rows in (("base から消えた行", diff["lost"]), ("base から増えた行", diff["added"])):
            if rows:
                out += ["", f"<details><summary>{title} {len(rows)}</summary>", "", "```"]
                out += [_fmt_row(row) for row in rows]
                out += ["```", "</details>"]
        if diff["changed"]:
            out += ["", f"<details><summary>base から変わった行 {len(diff['changed'])}</summary>", "", "```"]
            out += [f"{_fmt_row(b)} -> {h['value']}" for b, h in diff["changed"]]
            out += ["```", "</details>"]
        if diff["outcomes"]:
            out += ["", "```"] + [f"{no} {b} -> {h}" for no, b, h in diff["outcomes"]] + ["```"]
    return "\n".join(out) + "\n"



def jquants_checks(raw: dict) -> list[dict]:
    """J-Quants の 1 開示から突き合わせる項目を作る（cmp_jq.py と同じ。修正の 1Q / 3Q だけ違う。KAB-246）。"""

    def num(v):
        if v in (None, "", "－", "-"):
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    dt = raw.get("DocType") or ""
    cons = 0 if "NonConsolidated" in dt else 1
    isfy = raw.get("CurPerType") == "FY"
    isrep = "FinancialStatements" in dt
    checks = []

    def add(kind, item, pe, tps, jv, key, cn=None):
        checks.append({"kind": kind, "item": item, "period_end": pe, "tps": list(tps), "jq": jv,
                       "jq_key": key, "cons": cons if cn is None else cn})

    if isrep:
        tp = {"1Q": "Q1", "2Q": "Q2", "3Q": "Q3", "FY": "FY"}[raw["CurPerType"]]
        for item, key in _ACTUAL.items():
            jv = num(raw.get(key))
            if jv is not None:
                add("actual", item, raw["CurPerEn"], (tp,), jv, key)
        for t, key in _DIVIDEND:
            jv = num(raw.get(key))
            if jv is not None:
                add("actual", "dps", raw["CurFYEn"], (t,), jv, key)
    pre = "NxF" if isfy and isrep else "F"
    fyend = raw.get("NxtFYEn") if pre == "NxF" else raw.get("CurFYEn")
    # 業績予想の修正の末尾なしの欄（FSales / FNCSales）は CurPerType の期の値（KAB-246）。
    # 1Q / 3Q の修正は第1・第3四半期累計を入れて返す（7865 20220530563624 の FNCSales は Q1 累計 1,420）。
    # 2Q の修正は末尾なしが通期、2Q の欄が上期（7513）。CurPerType が分からなければ末尾なしは突き合わせない。
    plain: tuple[str | None, tuple[str, ...], int | None, int | None] = (fyend, ("FY",), None, -1)
    if not isrep:
        quarter = {"1Q": "Q1", "3Q": "Q3"}.get(raw.get("CurPerType") or "")
        if quarter:
            plain = (raw.get("CurPerEn"), (quarter,), 1, 0)
        elif raw.get("CurPerType") not in ("2Q", "FY"):
            plain = (None, ("FY",), None, -1)
    for item, key in _FORECAST.items():
        k1 = pre + ("Np" if (pre == "NxF" and key == "NP") else key)
        for suf in ("", "2Q"):
            # (期末, 期, F* の連結, FNC* の連結)。2Q の欄は上期。
            pe, tps, cn_f, cn_nc = plain if suf == "" else (fyend, ("H1",), None, -1)
            jv = num(raw.get(k1 + suf))
            if jv is not None and pe:
                add("forecast", item, pe, tps, jv, k1 + suf, cn=cn_f)
            elif not isrep:
                k2 = pre + "NC" + k1[len(pre):] + suf
                jv = num(raw.get(k2))
                if jv is not None and pe:
                    add("forecast", item, pe, tps, jv, k2 + "#nc", cn=cn_nc)
    for t, key in _DIVIDEND:
        jv = num(raw.get(pre + key))
        if jv is not None and fyend:
            add("forecast", "dps", fyend, (t,), jv, pre + key)
    return checks


def build(
    result: dict, statements: dict[str, dict], titles: dict[str, str], evidence: dict[tuple, str],
    sets: dict[str, str], html_dirs: list[Path],
) -> dict:
    """読み取りの結果のうち、J-Quants と一致し、検証 bot も match とした値だけを正解にする。"""
    by_no: dict[str, list[dict]] = defaultdict(list)
    for row in result["rows"]:
        by_no[row["no"]].append(row)
    disclosures = {}
    for no in sorted(result["outcomes"]):
        raw = statements[no]
        values: dict[tuple, dict] = {}
        gaps = []
        for spec in jquants_checks(raw):
            bot = evidence.get((no, spec["jq_key"]))
            if bot is None:
                continue  # bot が見ていない項目（ZIP のある回など）
            cands = candidates(by_no[no], spec)
            hits = [row for row in cands if _close(spec["item"], float(row["value"]), spec["jq"])]
            if hits and bot == "match":
                for row in hits:
                    key = row_key(row)
                    entry = values.setdefault(key, {f: row[f] for f in (*KEY_FIELDS, "value", "unit")} | {"evidence": []})
                    entry["evidence"].append(f"jquants:{spec['jq_key']}")
            else:
                gaps.append(spec | {"res": "diff" if cands else "missing", "bot": bot})
        path = html_file(no, html_dirs)
        html = gzip.decompress(path.read_bytes()) if path else b""
        disclosures[no] = {
            "set": sets[no],
            "title": titles.get(no) or raw.get("DocType"),
            "html_sha256": hashlib.sha256(html).hexdigest(),
            "statement": {f: _statement_value(raw, f) for f in STATEMENT_FIELDS},
            "values": [values[k] for k in sorted(values)],
            "known_gaps": sorted(gaps, key=lambda g: (g["jq_key"], g["item"], g["kind"])),
        }
    return {
        "version": 1,
        "note": "KAB-244。values は J-Quants と一致を確かめた値（XBRL 検証 bot の cmp_jq も match）。"
                "known_gaps は J-Quants と合わない項目（直ると golden_check が「一致するようになった」と出す）。",
        "disclosures": disclosures,
    }


_JQ_FIELD = {
    "code": "Code", "disclosed_date": "DiscDate", "disclosed_time": "DiscTime", "type_of_period": "CurPerType",
    "type_of_document": "DocType", "period_start": "CurPerSt", "period_end": "CurPerEn",
    "fiscal_year_start": "CurFYSt", "fiscal_year_end": "CurFYEn",
}


def _statement_value(raw: dict, field: str):
    value = raw.get(_JQ_FIELD[field]) or None
    if field in _DATE_FIELDS and value:
        return str(value)[:10]
    return value


def _build_main(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    evidence: dict[tuple, str] = {}
    sets: dict[str, str] = {}
    for spec in args.cmp_jq:
        name, _, path = spec.partition("=")
        for item in json.loads(Path(path).read_text(encoding="utf-8")):
            evidence[(item["no"], item["jq_key"])] = item["res"]
            sets[item["no"]] = name
    raw_rows = json.loads(Path(args.statements).read_text(encoding="utf-8"))
    statements = {str(r["DiscNo"]): r for r in raw_rows}
    html_dirs = [Path(p) for p in args.html_dir]
    titles = _titles(html_dirs)
    stub = {"disclosures": {
        no: {"title": titles.get(no) or statements[no].get("DocType"),
             "statement": {f: _statement_value(statements[no], f) for f in STATEMENT_FIELDS}}
        for no in sorted(sets) if no in statements
    }}
    missing_meta = sorted(set(sets) - set(statements))
    with tempfile.TemporaryDirectory(prefix="golden-build-") as tmp:
        stub_path = Path(tmp) / "stub.json"
        stub_path.write_text(json.dumps(stub, ensure_ascii=False), encoding="utf-8")
        result = dump(stub_path, html_dirs, Path(args.repo))
    data = build(result, statements, titles, evidence, sets, html_dirs)
    write_expected(data, Path(args.out))
    values = sum(len(d["values"]) for d in data["disclosures"].values())
    gaps = sum(len(d["known_gaps"]) for d in data["disclosures"].values())
    bot_match = sum(1 for k, v in evidence.items() if v == "match" and k[0] in data["disclosures"])
    print(f"disclosures {len(data['disclosures'])} values {values} known_gaps {gaps} "
          f"bot_match {bot_match} no_statement {missing_meta}")
    return 0



def _titles(html_dirs: list[Path]) -> dict[str, str]:
    out: dict[str, str] = {}
    for base in reversed(html_dirs):
        path = base / "titles.json"
        if path.exists():
            out.update(json.loads(path.read_text(encoding="utf-8")))
    return out


def _fetch_main(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    import httpx

    from tools.tdnet_pdf.convert import convert_pdf
    from tools.tdnet_pdf.ingest import fetch_pdf

    out_dir = Path(args.html_dir[0])
    out_dir.mkdir(parents=True, exist_ok=True)
    nos: dict[str, str | None] = {}
    if Path(args.expected).exists():
        for no, doc in load_expected(Path(args.expected))["disclosures"].items():
            nos[no] = doc["html_sha256"]
    for spec in args.cmp_jq:
        for item in json.loads(Path(spec.partition("=")[2]).read_text(encoding="utf-8")):
            nos.setdefault(item["no"], None)
    titles_path = out_dir / "titles.json"
    titles = json.loads(titles_path.read_text(encoding="utf-8")) if titles_path.exists() else {}
    done = fetched = failed = mismatched = 0
    with httpx.Client(timeout=60.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        for no, sha in sorted(nos.items()):
            path = out_dir / f"{no}.html.gz"
            if path.exists() and no in titles:
                done += 1
                continue
            try:
                conv = convert_pdf(fetch_pdf(client, no))
            except Exception as exc:
                print(f"fail {no} {type(exc).__name__}: {exc}", flush=True)
                failed += 1
                continue
            finally:
                time.sleep(args.sleep)
            raw = conv.html.encode("utf-8")
            if sha and hashlib.sha256(raw).hexdigest() != sha:
                print(f"sha256 mismatch {no}", flush=True)
                mismatched += 1
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(raw, mtime=0))
            tmp.replace(path)
            titles[no] = conv.title
            titles_path.write_text(json.dumps(titles, ensure_ascii=False, sort_keys=True, indent=0), encoding="utf-8")
            fetched += 1
    print(f"html_dir {out_dir} kept {done} fetched {fetched} failed {failed} sha256_mismatch {mismatched}")
    return 1 if failed or mismatched else 0



def _check_main(args: argparse.Namespace) -> int:
    expected_path = Path(args.expected)
    expected = load_expected(expected_path)
    html_dirs = [Path(p) for p in args.html_dir]
    head = dump(expected_path, html_dirs, Path(args.repo))
    report = compare(expected, head)
    diff = None
    if args.base_repo:
        base = dump(expected_path, html_dirs, Path(args.base_repo))
        diff = row_diff(base, head)
    text = markdown(report, head, diff, label=args.label or str(Path(args.repo).resolve()))
    if args.md:
        Path(args.md).write_text(text, encoding="utf-8")
    if args.save_rows:
        Path(args.save_rows).write_text(json.dumps(head, ensure_ascii=False), encoding="utf-8")
    print(text)
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--expected", default=str(EXPECTED))
        p.add_argument("--html-dir", action="append", default=[],
                       help="HTML（{開示番号}.html.gz と titles.json）の場所。繰り返し可。"
                            "既定は data/tdnet_pdf_golden/html と tests/golden/tdnet_pdf/html（fetch は前者だけ）")

    p = sub.add_parser("check", help="正解データと比べる（--base-repo で base との全行の差も）")
    common(p)
    p.add_argument("--repo", default=str(ROOT), help="読み取りに使うチェックアウト（既定はこの repo）")
    p.add_argument("--base-repo", help="比べる base（main の worktree など）")
    p.add_argument("--md", help="結果の Markdown を書くファイル")
    p.add_argument("--save-rows", help="head の行を JSON で保存")
    p.add_argument("--label")
    p = sub.add_parser("fetch", help="IR BANK の PDF を HTML にして --html-dir に置く（PDF は残さない）")
    common(p)
    p.add_argument("--cmp-jq", action="append", default=[], help="NAME=cmp_jq.json。正解データに無い開示も取る")
    p.add_argument("--sleep", type=float, default=1.0)
    p = sub.add_parser("build", help="（保守用）検証 bot の cmp_jq と J-Quants から正解データを作り直す")
    common(p)
    p.add_argument("--cmp-jq", action="append", required=True, help="NAME=cmp_jq.json（繰り返し）")
    p.add_argument("--statements", required=True, help="J-Quants /fins/summary の行（JSON の配列）")
    p.add_argument("--repo", default=str(ROOT))
    p.add_argument("--out", default=str(EXPECTED))
    p = sub.add_parser("_dump")
    p.add_argument("--repo", required=True)
    p.add_argument("--expected", required=True)
    p.add_argument("--html-dir", action="append", default=[])
    p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.cmd != "_dump" and not args.html_dir:
        args.html_dir = [str(DATA_HTML)] if args.cmd == "fetch" else [str(DATA_HTML), str(MINI_HTML)]
    return {"check": _check_main, "fetch": _fetch_main, "build": _build_main, "_dump": _dump_main}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
