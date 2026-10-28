"""KAB-244: 短信 PDF の正解データ（tests/golden/tdnet_pdf）で読み取りの回帰を見る。IR BANK も本番 DB も使わない。

expected.json は J-Quants と一致を確かめた値だけ。html/ の小さな組（原因の種類ごと）を毎回読み、
正解の値が 1 つも消えたり変わったりしていないことを確かめる。全件は tools/tdnet_pdf/golden_check.py check。
"""
from __future__ import annotations

import gzip
import hashlib
import json

from tools.tdnet_pdf.golden_check import (
    EXPECTED,
    MINI_HTML,
    KEY_FIELDS,
    candidates,
    compare,
    dump_in_process,
    jquants_checks,
    load_expected,
    row_diff,
    write_expected,
)

_SETS = {"r2_prev5", "r2_new5", "r3"}


def _fail(msg: object) -> None:
    raise AssertionError(msg)


def _mini_nos() -> list[str]:
    return sorted(path.name.split(".")[0] for path in MINI_HTML.glob("*.html.gz"))


def test_expected_holds_only_verified_values() -> None:
    data = load_expected()
    if len(data["disclosures"]) < 300:
        _fail(f"disclosures {len(data['disclosures'])}")
    for no, doc in data["disclosures"].items():
        if doc["set"] not in _SETS:
            _fail(f"{no} set {doc['set']}")
        for value in doc["values"]:
            if not value["evidence"] or not all(e.startswith(("jquants:", "edinet:")) for e in value["evidence"]):
                _fail(f"{no} evidence {value}")
            if set(KEY_FIELDS) - set(value):
                _fail(f"{no} key {value}")


def test_mini_html_is_the_verified_html() -> None:
    data = load_expected()
    nos = _mini_nos()
    if len(nos) < 25:
        _fail(f"mini set {len(nos)}")
    titles = json.loads((MINI_HTML / "titles.json").read_text(encoding="utf-8"))
    for no in nos:
        raw = gzip.decompress((MINI_HTML / f"{no}.html.gz").read_bytes())
        if hashlib.sha256(raw).hexdigest() != data["disclosures"][no]["html_sha256"]:
            _fail(f"sha256 {no}")
        if no not in titles:
            _fail(f"title {no}")


def test_mini_set_keeps_every_verified_value() -> None:
    """原因の種類ごとの小さな組を読み、J-Quants と一致した値が消えていない・変わっていない。"""
    data = load_expected()
    nos = _mini_nos()
    result = dump_in_process(data, [MINI_HTML], nos)
    if sorted(result["outcomes"]) != nos:
        _fail(f"outcomes {result['outcomes']}")
    report = compare({"disclosures": {no: data["disclosures"][no] for no in nos}}, result)
    if report.verified < 300:
        _fail(f"verified {report.verified}")
    if not report.ok:
        _fail(f"lost {report.lost[:5]} changed {report.changed[:5]}")


def _row(no: str, std: str, value: str, **kw) -> dict:
    base = {
        "no": no, "std": std, "is_consolidated": True, "period_type": "FY", "period_start": "2024-04-01",
        "period_end": "2025-03-31", "value_kind": "actual", "range_side": "point", "target_period": "FY",
        "value": value, "unit": "JPY",
    }
    return base | kw


def _expected(values: list[dict], gaps: list[dict] | None = None) -> dict:
    return {"disclosures": {"20250512500001": {
        "values": [{k: v for k, v in value.items() if k != "no"} | {"evidence": ["jquants:Sales"]} for value in values],
        "known_gaps": gaps or [],
    }}}


def test_compare_reports_lost_changed_and_fixed() -> None:
    no = "20250512500001"
    sales = _row(no, "net_sales", "1000000000")
    op = _row(no, "operating_income", "100000000")
    eps = _row(no, "eps_basic", "12.34", unit="JPYPerShares")
    gap = {
        "item": "net_income", "kind": "actual", "period_end": "2025-03-31", "tps": ["FY"], "cons": 1,
        "jq": 5e7, "jq_key": "NP", "res": "missing", "bot": "missing",
    }
    expected = _expected([sales, op, eps], [gap])
    result = {"outcomes": {no: "parsed"}, "rows": [
        sales, _row(no, "operating_income", "90000000"), _row(no, "net_income", "50000000"),
    ]}
    report = compare(expected, result)
    if [row["std"] for row in report.lost] != ["eps_basic"]:
        _fail(report.lost)
    if [(e["std"], a["value"]) for e, a in report.changed] != [("operating_income", "90000000")]:
        _fail(report.changed)
    if [g["jq_key"] for g in report.fixed] != ["NP"] or report.ok:
        _fail(report)
    # 値の書き方（1E+9 / 1000000000.0000）は比べない
    same = compare(_expected([sales]), {"outcomes": {no: "parsed"}, "rows": [_row(no, "net_sales", "1E+9")]})
    if not same.ok:
        _fail(same)
    # HTML が無く読まなかった開示は消えた扱いにしない
    skipped = compare(_expected([sales]), {"outcomes": {}, "rows": []})
    if not skipped.ok or skipped.skipped_docs != [no]:
        _fail(skipped)


def test_row_diff_lists_every_row() -> None:
    no = "20250512500001"
    base = {"outcomes": {no: "parsed"}, "rows": [_row(no, "net_sales", "1"), _row(no, "sga", "2")]}
    head = {"outcomes": {no: "failed"}, "rows": [_row(no, "net_sales", "3"), _row(no, "capex", "4")]}
    diff = row_diff(base, head)
    if [r["std"] for r in diff["lost"]] != ["sga"] or [r["std"] for r in diff["added"]] != ["capex"]:
        _fail(diff)
    if [(b["value"], h["value"]) for b, h in diff["changed"]] != [("1", "3")]:
        _fail(diff)
    if diff["outcomes"] != [(no, "parsed", "failed")]:
        _fail(diff)


def test_jquants_checks_follow_cmp_jq() -> None:
    """検証 bot の cmp_jq.py と同じ項目を作る。FY 短信は次期予想（NxF）、修正は単体予想（FNC）も見る。"""
    fy = {
        "DocType": "FYFinancialStatements_Consolidated_JP", "CurPerType": "FY", "CurPerEn": "2025-03-31",
        "CurFYEn": "2025-03-31", "NxtFYEn": "2026-03-31", "Sales": "1000", "EPS": "", "DivAnn": "50",
        "NxFNp": "70", "NxFSales2Q": "400", "FSales": "999",
    }
    got = {(c["jq_key"], c["kind"], c["item"], c["period_end"], tuple(c["tps"])) for c in jquants_checks(fy)}
    want = {
        ("Sales", "actual", "net_sales", "2025-03-31", ("FY",)),
        ("DivAnn", "actual", "dps", "2025-03-31", ("annual",)),
        ("NxFNp", "forecast", "net_income", "2026-03-31", ("FY",)),
        ("NxFSales2Q", "forecast", "net_sales", "2026-03-31", ("H1",)),
    }
    if got != want:
        _fail(got)
    rev = {"DocType": "EarnForecastRevision", "CurPerType": "FY", "CurFYEn": "2025-03-31", "FNCSales": "300"}
    checks = jquants_checks(rev)
    if [(c["jq_key"], c["cons"]) for c in checks] != [("FNCSales#nc", -1)]:
        _fail(checks)


def test_candidates_h1_window_and_consolidation() -> None:
    no = "20250512500001"
    rows = [
        _row(no, "net_sales", "1", value_kind="forecast", period_end="2024-09-30", target_period="H1"),
        _row(no, "net_sales", "2", value_kind="forecast", target_period="FY"),
        _row(no, "net_sales", "3", value_kind="forecast", target_period="H1", is_consolidated=False),
    ]
    spec = {"item": "net_sales", "kind": "forecast", "period_end": "2025-03-31", "tps": ["H1"], "cons": 1}
    if [r["value"] for r in candidates(rows, spec)] != ["1"]:
        _fail(candidates(rows, spec))
    if [r["value"] for r in candidates(rows, spec | {"cons": -1})] != ["1", "3"]:
        _fail("cons -1")


def test_write_expected_round_trips(tmp_path) -> None:
    data = {"version": 1, "note": "x", "disclosures": {
        "20250512500001": {
            "set": "r3", "title": "t", "html_sha256": "0" * 64, "statement": {"code": "72030"},
            "values": [{"std": "net_sales", "value": "1", "evidence": ["jquants:Sales"]}], "known_gaps": [],
        },
    }}
    path = tmp_path / "expected.json"
    write_expected(data, path)
    if json.loads(path.read_text(encoding="utf-8")) != data:
        _fail(path.read_text(encoding="utf-8"))
    if EXPECTED.name != "expected.json":
        _fail(EXPECTED)


# KAB-246: J-Quants の業績予想の修正は、CurPerType=1Q / 3Q のとき第1・第3四半期累計を
# 末尾なしの欄（FSales / FNCSales）に入れて返す。通期として突き合わせない。
_QUARTER_REVISIONS = {
    # 7865 の J-Quants の生データ（必要な欄だけ）
    "20220530563624": {
        "DocType": "EarnForecastRevision", "CurPerType": "1Q", "CurPerSt": "2022-01-21",
        "CurPerEn": "2022-04-20", "CurFYSt": "2022-01-21", "CurFYEn": "2023-01-20",
        "FNCSales": "1420000000", "FNCEPS": "18.55",
    },
    "20231120592700": {
        "DocType": "EarnForecastRevision", "CurPerType": "3Q", "CurPerSt": "2023-01-21",
        "CurPerEn": "2023-10-20", "CurFYSt": "2023-01-21", "CurFYEn": "2024-01-20",
        "FNCSales": "4392000000", "FNCOP": "393000000", "FNCOdP": "411000000",
        "FNCNP": "287000000", "FNCEPS": "65.53",
    },
}


def _checks(raw: dict) -> set[tuple]:
    return {(c["jq_key"], c["period_end"], tuple(c["tps"]), c["cons"], c["jq"]) for c in jquants_checks(raw)}


def test_kab246_quarter_revisions_are_cumulative() -> None:
    """1Q / 3Q の修正の末尾なしの欄は、期末 CurPerEn の Q1 / Q3 累計（FNC* は単体、F* は連結）。"""
    q1 = _checks(_QUARTER_REVISIONS["20220530563624"])
    if q1 != {("FNCSales#nc", "2022-04-20", ("Q1",), 0, 1.42e9), ("FNCEPS#nc", "2022-04-20", ("Q1",), 0, 18.55)}:
        _fail(q1)
    q3 = _checks(_QUARTER_REVISIONS["20231120592700"])
    if {c[1:4] for c in q3} != {("2023-10-20", ("Q3",), 0)} or ("FNCSales#nc", "2023-10-20", ("Q3",), 0, 4.392e9) not in q3:
        _fail(q3)
    consolidated = _QUARTER_REVISIONS["20220530563624"] | {"FSales": "1500000000"}
    consolidated.pop("FNCSales")
    if ("FSales", "2022-04-20", ("Q1",), 1, 1.5e9) not in _checks(consolidated):
        _fail(_checks(consolidated))


def test_kab246_half_year_and_full_year_revisions_unchanged() -> None:
    """2Q の修正は末尾なしが通期・2Q の欄が上期（7513）。通期の修正は通期。期が分からなければ末尾なしは見ない。"""
    h1 = {
        "DocType": "EarnForecastRevision", "CurPerType": "2Q", "CurPerEn": "2022-02-28", "CurFYEn": "2022-08-31",
        "FNCSales": "280700000000", "FNCSales2Q": "138500000000",
    }
    if _checks(h1) != {("FNCSales#nc", "2022-08-31", ("FY",), -1, 2.807e11),
                       ("FNCSales2Q#nc", "2022-08-31", ("H1",), -1, 1.385e11)}:
        _fail(_checks(h1))
    fy = h1 | {"CurPerType": "FY", "CurPerEn": "2022-08-31", "FSales": "280000000000"}
    fy.pop("FNCSales2Q")
    if _checks(fy) != {("FSales", "2022-08-31", ("FY",), 1, 2.8e11)}:
        _fail(_checks(fy))
    unknown = h1 | {"CurPerType": ""}
    if _checks(unknown) != {("FNCSales2Q#nc", "2022-08-31", ("H1",), -1, 1.385e11)}:
        _fail(_checks(unknown))


def test_kab246_expected_has_no_full_year_keys_for_quarter_revisions() -> None:
    """7865 の 1Q / 3Q 修正の累計の値を、通期の正解にも既知の欠けにも入れない。累計の突き合わせは残す。"""

    def full_year(key: str, tps: list[str]) -> bool:
        base = key.split("#")[0]
        return tps == ["FY"] and base.startswith("F") and not base.endswith("2Q")

    data = load_expected()
    for no in ("20220530563624", "20230519577395", "20231120592700", "20240522503651", "20241126529281",
               "20250314594495"):
        doc = data["disclosures"][no]
        gaps = [g for g in doc["known_gaps"] if full_year(g["jq_key"], g["tps"])]
        values = [
            v for v in doc["values"]
            if v["target_period"] == "FY" and any(full_year(e.removeprefix("jquants:"), ["FY"]) for e in v["evidence"])
        ]
        if gaps or values:
            _fail((no, gaps, values))
        end = doc["statement"]["period_end"]
        if not any(g["period_end"] == end for g in doc["known_gaps"]) and not any(
            v["period_end"] == end for v in doc["values"]
        ):
            _fail(f"{no} has no cumulative check")


def test_kab246_cumulative_relabelled_as_full_year_is_caught() -> None:
    """後退の検知: 7865 の Q3 累計（20231120592700）を通期に付け直すと、消えた値として出る。"""
    data = load_expected()
    no = "20231120592700"
    doc = data["disclosures"][no]
    q3 = [v for v in doc["values"] if v["target_period"] == "Q3" and v["value_kind"] == "forecast"]
    if len(q3) != 5:
        _fail(q3)
    good = [{"no": no, **{k: v[k] for k in (*KEY_FIELDS, "value", "unit")}} for v in doc["values"]]
    sub = {"disclosures": {no: doc}}
    if not compare(sub, {"outcomes": {no: "parsed"}, "rows": good}).ok:
        _fail("good rows")
    fy_end = doc["statement"]["fiscal_year_end"]
    relabelled = [
        row | {"period_end": fy_end, "target_period": "FY"} if row["target_period"] == "Q3" else row for row in good
    ]
    report = compare(sub, {"outcomes": {no: "parsed"}, "rows": relabelled})
    if len(report.lost) != 5 or report.ok:
        _fail(report)
