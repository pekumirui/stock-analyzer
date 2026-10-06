"""短信 XBRL（IR Bank の get_disclosure の xbrl_facts）から financials.json を作る。

入力: <root>/raw/xbrl/<stem>.json（設定の tanshin。stem は FY<YYYY-MM>_Q<1|2|3|FY>、YYYY-MM は決算期末）
出力: 累計 PL・CF（開示どおり）、四半期単独（累計の差）、セグメント、期末 BS、会社予想。金額は百万円。

  .venv/bin/python analyzer/agent_forecast/build_financials.py --target targets/6036_FY2026Q4.json

同じ期の数字が複数の短信にあれば新しい短信（翌年の前年同期比較）を採る。
数字が組み替えられた期は警告を出す。原価と販管費の区分変更なら設定の cost_reclass.from に境目の開示日を書く。
"""

from __future__ import annotations

import argparse
import json
import re

from common import (
    PERIOD_Q,
    fy_label,
    fy_start_date,
    load_target,
    next_fye,
    prev_fye,
    quarter_end,
    root_of,
    target_args,
)

PERIOD_ORDER = ["Q1", "Q2", "Q3", "FY"]
COST_SPLIT = {"CostOfSales", "GrossProfit", "SellingGeneralAndAdministrativeExpenses"}
FORECAST_KEYS = {
    "NetSales": "売上高",
    "OperatingIncome": "営業利益",
    "OrdinaryIncome": "経常利益",
    "NetIncome": "当期純利益",
    "ProfitAttributableToOwnersOfParent": "親会社株主に帰属する当期純利益",
    "NetIncomePerShare": "1株当たり当期純利益(円)",
}


def _num(v: object) -> float | None:
    try:
        return float(str(v))
    except ValueError:
        return None


def _mil(v: float) -> float:
    return round(v / 1_000_000, 1)


class Facts:
    """1 本の短信の facts。財務諸表は非連結なら _NonConsolidatedMember、連結なら member なし。"""

    def __init__(self, facts: list[dict]) -> None:
        self.facts = facts
        ctxs = {f["context"] for f in facts}
        self.nonconsolidated = any(c.split("_", 1)[-1] == "NonConsolidatedMember" for c in ctxs)
        self.fs = "_NonConsolidatedMember" if self.nonconsolidated else ""
        self.summary = "_NonConsolidatedMember" if self.nonconsolidated else "_ConsolidatedMember"

    def pick(self, context: str, *, money: bool = True) -> dict[str, float]:
        out: dict[str, float] = {}
        for f in self.facts:
            if f["context"] != context or not f["unit"]:
                continue
            v = _num(f["value"])
            if v is not None:
                out[f["element_name"]] = _mil(v) if money else v
        return out

    def statement(self, ctx: str) -> dict[str, float]:
        return self.pick(f"{ctx}{self.fs}")

    def segments(self, ctx: str, code: str, labels: dict[str, str]) -> dict[str, dict[str, float]]:
        head = f"{ctx}{self.fs}_"
        out: dict[str, dict[str, float]] = {}
        for f in self.facts:
            if not f["context"].startswith(head) or not f["unit"]:
                continue
            name = _segment_of(f["context"][len(head) :], code, labels)
            v = _num(f["value"])
            if name is not None and v is not None:
                out.setdefault(name, {})[f["element_name"]] = _mil(v)
        return out


def _segment_of(suffix: str, code: str, labels: dict[str, str]) -> str | None:
    m = re.fullmatch(rf"tse-[a-z]+-{code}(\w+?)(?:ReportableSegments?)?Member", suffix)
    if m:
        return labels.get(m.group(1), m.group(1))
    if suffix in ("ReportableSegmentsMember", "TotalOfReportableSegmentsAndOthersMember"):
        return "報告セグメント計"
    if suffix == "ReconcilingItemsMember":
        return "調整額"
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    args = ap.parse_args()
    t = load_target(args.target)
    root = root_of(t, args.root)
    reclass = (t.get("cost_reclass") or {}).get("from")
    labels = t.get("segment_labels", {})

    def basis(disclosed: str) -> str:
        if not reclass:
            return "同一区分"
        return "新区分" if disclosed >= reclass else "旧区分"

    cum: dict[tuple[str, str], dict] = {}
    seen: dict[tuple[str, str], list[tuple[str, float | None]]] = {}
    bs: dict[str, dict] = {}
    forecasts: list[dict] = []

    for item in sorted(t["tanshin"], key=lambda x: x["date"]):
        disclosed, stem = item["date"], item["stem"]
        m = re.fullmatch(r"FY(\d{4}-\d{2})_Q(\w+)", stem)
        assert m, stem
        fye, period = m.group(1), ("FY" if m.group(2) == "FY" else f"Q{m.group(2)}")
        facts = Facts(json.loads((root / "raw" / "xbrl" / f"{stem}.json").read_text())["xbrl_facts"])
        is_fy = period == "FY"
        if is_fy:
            cur_ctxs, prior_ctxs, inst_ctxs = ["CurrentYearDuration"], ["Prior1YearDuration"], ["CurrentYearInstant"]
        else:
            # 2024 年 4 月以降の中間期は半期報告の context 名（Interim…）になる
            cur_ctxs = ["CurrentYTDDuration", "InterimDuration"]
            prior_ctxs = ["Prior1YTDDuration", "Prior1InterimDuration"]
            inst_ctxs = ["CurrentQuarterInstant", "InterimInstant"]

        found = False
        for target_fye, ctxs, kind in ((fye, cur_ctxs, "当期"), (prev_fye(fye), prior_ctxs, "前年同期の比較情報")):
            ctx = next((c for c in ctxs if facts.statement(c)), None)
            if ctx is None:
                continue
            found |= kind == "当期"
            pl = facts.statement(ctx)
            key = (target_fye, period)
            seen.setdefault(key, []).append((disclosed, pl.get("CostOfSales")))
            cum[key] = {
                "fiscal_year": fy_label(target_fye),
                "period": "通期" if is_fy else f"{period}累計",
                "period_start": fy_start_date(target_fye),
                "period_end": quarter_end(target_fye, PERIOD_Q[period]),
                "source": f"{disclosed} 短信（{kind}）",
                "cost_classification": basis(disclosed),
                "pl_cf": pl,
                "segments": facts.segments(ctx, t["code"], labels),
            }
        if not found:
            raise SystemExit(f"{stem}: 当期の PL が見つからない（context 名を確認）")

        inst = next((c for c in inst_ctxs if facts.statement(c)), inst_ctxs[0])
        end = quarter_end(fye, PERIOD_Q[period])
        bs[end] = {"as_of": end, "source": f"{disclosed} 短信", "bs": facts.statement(inst)}

        fc_ctx, fc_fye = ("NextYearDuration", next_fye(fye)) if is_fy else ("CurrentYearDuration", fye)
        fc = facts.pick(f"{fc_ctx}{facts.summary}_ForecastMember", money=False)
        if fc:
            forecasts.append(
                {
                    "disclosed": disclosed,
                    "fiscal_year": fy_label(fc_fye),
                    "target": "通期予想",
                    **{
                        label: (v if k == "NetIncomePerShare" else _mil(v))
                        for k, label in FORECAST_KEYS.items()
                        if (v := fc.get(k)) is not None
                    },
                }
            )

    for key, versions in seen.items():
        if len({v for _, v in versions}) > 1:
            print(f"注意: {fy_label(key[0])} {key[1]} の売上原価が組み替えられている {versions}")

    # 四半期単独 = 累計の差。原価区分が違う累計どうしの差では原価・粗利・販管費を出さない
    standalone: list[dict] = []
    for fye in sorted({k[0] for k in cum}):
        prev: dict | None = None
        for period in PERIOD_ORDER:
            cur = cum.get((fye, period))
            if cur is None:
                prev = None
                continue
            q = f"{PERIOD_Q[period]}Q"
            if period == "Q1":
                pl, seg, cls = dict(cur["pl_cf"]), cur["segments"], cur["cost_classification"]
            elif prev is None:
                prev = cur
                continue
            else:
                same = cur["cost_classification"] == prev["cost_classification"]
                cls = cur["cost_classification"] if same else "前後の累計で区分が異なるため原価・粗利・販管費は算出しない"
                pl = {
                    k: round(v - prev["pl_cf"][k], 1)
                    for k, v in cur["pl_cf"].items()
                    if k in prev["pl_cf"] and (same or k not in COST_SPLIT)
                }
                seg = {
                    s: {k: round(v - prev["segments"][s][k], 1) for k, v in items.items() if k in prev["segments"].get(s, {})}
                    for s, items in cur["segments"].items()
                    if s in prev["segments"]
                }
            standalone.append(
                {"fiscal_year": cur["fiscal_year"], "quarter": f"{q}単独", "cost_classification": cls, "pl_cf": pl, "segments": seg}
            )
            prev = cur

    order = sorted(cum, key=lambda k: (k[0], PERIOD_ORDER.index(k[1])))
    first, last = cum[order[0]], cum[order[-1]]
    notes = [
        "同じ期の数字が複数の短信にあるときは、新しい短信（翌年の前年同期比較を含む）の数字を採っている",
        "balance_sheet は各短信の期末（四半期末）時点。1株当たりの値と比率は含まない",
    ]
    if reclass:
        notes.insert(
            1,
            f"{reclass} の短信から売上原価と販管費の区分が変わった。cost_classification が 新区分 の数字は組替後、旧区分 は組替前",
        )
    out = {
        "company": f"{t['name']}（{t['profile']}）",
        "unit": "百万円（1株当たりは円）",
        "source": (
            f"決算短信 {len(t['tanshin'])} 本の XBRL（{first['fiscal_year']} {first['period']} 〜 {last['fiscal_year']} {last['period']}）。"
            "四半期単独は累計の差。項目名は XBRL（jppfs / tse-ed-t）の要素名"
        ),
        "notes": notes,
        "cumulative": [cum[k] for k in order],
        "quarterly_standalone": standalone,
        "balance_sheet": [bs[k] for k in sorted(bs)],
        "company_forecasts": forecasts,
    }
    (root / "raw" / "financials.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"cumulative={len(cum)} standalone={len(standalone)} bs={len(bs)} forecasts={len(forecasts)}")


if __name__ == "__main__":
    main()
