"""実績（<root>/answer/ の短信 zip）と各条件の予想を比べ、誤差とトークンを表にする。

実行ログは <root>/results/<vendor>/<条件>.jsonl（claude の stream-json）。使ったモデルはログの init と modelUsage から取る。

実績の nQ 単独 = n Q 累計（短信 iXBRL）− (n-1)Q 累計（raw/financials.json）。4Q は通期 − 3Q 累計。
EPS は財務諸表に無いのでサマリーの累計 EPS を使い、(n-1)Q 累計は raw/xbrl/ の前の短信から取る（累計の差なので株数の変動ぶんはずれる）。
会社予想からの逆算（4Q のみ）= 締切時点の最新の通期予想 − 3Q 累計。
エージェントが cond_<X>/ の外を読もうとした形跡（permission_denials）も数える。

  .venv/bin/python analyzer/agent_forecast/score.py --target targets/6036_FY2026Q4.json [--vendor claude] [--markdown out.md]
"""

from __future__ import annotations

import argparse
import html
import json
import re
import zipfile
from pathlib import Path

from common import fy_label, load_target, root_of, target_args, target_label

EPS = "NetIncomePerShare"
# 項目 -> (XBRL 要素の候補（先にあるものを採る）, 表の見出し)
ITEMS = {
    "net_sales": (("NetSales",), "売上高"),
    "gross_profit": (("GrossProfit",), "粗利"),
    "sga": (("SellingGeneralAndAdministrativeExpenses",), "販管費"),
    "operating_income": (("OperatingIncome",), "営業利益"),
    "ordinary_income": (("OrdinaryIncome",), "経常利益"),
    # 連結は親会社株主に帰属する分。非連結は ProfitLoss
    "net_income": (("ProfitLossAttributableToOwnersOfParent", "ProfitLoss"), "純利益"),
    "eps": ((EPS,), "EPS(円)"),
}
# 項目 -> financials.json の company_forecasts のラベルの候補
GUIDANCE_KEYS = {
    "net_sales": ("売上高",),
    "operating_income": ("営業利益",),
    "ordinary_income": ("経常利益",),
    "net_income": ("親会社株主に帰属する当期純利益", "当期純利益"),
    "eps": ("1株当たり当期純利益(円)",),
}
# サマリーの累計実績の context（例 CurrentAccumulatedQ1Duration_ConsolidatedMember_ResultMember）
SUMMARY_CTX = re.compile(r"Current(?:AccumulatedQ\dDuration|YearDuration)_(?:Non)?ConsolidatedMember_ResultMember")


def answer_cum(zip_path: Path, quarter: int) -> dict[str, float]:
    """短信 iXBRL から当期累計（4Q は通期）の財務諸表の値を読む（百万円）。"""
    texts: list[str] = []
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if n.endswith("-ixbrl.htm"):
                texts.append(z.read(n).decode("utf-8"))
    joined = "".join(texts)
    ctxs = ["CurrentYearDuration"] if quarter == 4 else ["CurrentYTDDuration", "InterimDuration"]
    # 連結でもサマリー（配当など）に NonConsolidatedMember が出るので、財務諸表の context で判定する
    fs = "_NonConsolidatedMember" if any(f'contextRef="{c}_NonConsolidatedMember"' in joined for c in ctxs) else ""
    out: dict[str, float] = {}
    for tag in re.finditer(r"<ix:nonFraction\b([^>]*)>([^<]*)</ix:nonFraction>", joined):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag.group(1)))
        if attrs.get("contextRef") not in {f"{c}{fs}" for c in ctxs}:
            continue
        raw = html.unescape(tag.group(2)).replace(",", "").strip()
        if not raw:
            continue
        v = float(raw) * 10 ** int(attrs.get("scale", "0"))
        out[attrs.get("name", "").split(":")[-1]] = round((-v if attrs.get("sign") == "-" else v) / 1_000_000, 1)
    for tag in re.finditer(r"<ix:nonFraction\b([^>]*)>([^<]*)</ix:nonFraction>", joined):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', tag.group(1)))
        raw = html.unescape(tag.group(2)).replace(",", "").strip()
        if attrs.get("name", "").endswith(f":{EPS}") and SUMMARY_CTX.fullmatch(attrs.get("contextRef", "")) and raw:
            out[EPS] = -float(raw) if attrs.get("sign") == "-" else float(raw)
    return out


def prev_cum(financials: dict, fy: str, quarter: int) -> dict[str, float]:
    if quarter == 1:
        return {}
    period = f"Q{quarter - 1}累計"
    return next(r for r in financials["cumulative"] if r["fiscal_year"] == fy and r["period"] == period)["pl_cf"]


def prev_eps(root: Path, fye: str, quarter: int) -> float:
    """(n-1)Q 累計の EPS（サマリー）。raw/xbrl/FY<fye>_Q<n-1>.json から読む。"""
    if quarter == 1:
        return 0.0
    facts = json.loads((root / "raw" / "xbrl" / f"FY{fye}_Q{quarter - 1}.json").read_text())["xbrl_facts"]
    return float(next(f["value"] for f in facts if f["element_name"] == EPS and SUMMARY_CTX.fullmatch(f["context"])))


def _element(items: dict, candidates: tuple[str, ...]) -> str | None:
    return next((e for e in candidates if e in items), None)


def parse_run(path: Path) -> dict:
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip().startswith("{")]
    result = next((e for e in events if e.get("type") == "result"), {})
    text = result.get("result") or ""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    forecast: dict = {}
    for raw in reversed(blocks):
        obj = json.loads(raw)
        if isinstance(obj, dict) and isinstance(obj.get("forecast"), dict):
            forecast = obj
            break
    tool_uses = [
        c
        for e in events
        if e.get("type") == "assistant"
        for c in e["message"].get("content", [])
        if c.get("type") == "tool_use"
    ]
    u = result.get("usage") or {}
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    models = sorted(result.get("modelUsage") or {}) or [init.get("model") or "?"]
    return {
        "model": ",".join(models),
        "forecast": forecast,
        "usage": {k: u.get(k) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")},
        "total_tokens": sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")),
        "cost": round(result.get("total_cost_usd") or 0, 3),
        "turns": result.get("num_turns"),
        "duration_s": round((result.get("duration_ms") or 0) / 1000),
        "tool_calls": len(tool_uses),
        "reads": sorted({(c["input"].get("file_path") or "").split("/cond_")[-1].split("/", 1)[-1] for c in tool_uses if c["name"] == "Read"}),
        "mcp_calls": sum(1 for c in tool_uses if c["name"].startswith("mcp__")),
        "denials": result.get("permission_denials") or [],
    }


def _pct(pred: float | None, actual: float) -> float | None:
    return round((pred - actual) / abs(actual) * 100, 1) if isinstance(pred, (int, float)) else None


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    ap.add_argument("--vendor", default=None, help="results/<vendor>/ だけ採点する（省略で全部）")
    ap.add_argument("--markdown", type=Path, default=None, help="結果を Markdown で書き出す")
    ap.add_argument("--no-model", action="store_true", help="Markdown にモデル名を書かない")
    args = ap.parse_args()
    t = load_target(args.target)
    root = root_of(t, args.root)
    fye, q = t["target"]["fiscal_year_end"], t["target"]["quarter"]
    fy = fy_label(fye)

    fin = json.loads((root / "raw" / "financials.json").read_text())
    cur = answer_cum(next((root / "answer").glob("*.zip")), q)
    prev = {**prev_cum(fin, fy, q), EPS: prev_eps(root, fye, q)}
    actual = {
        k: round(cur[e] - prev.get(e, 0.0), 1 if k != "eps" else 2)
        for k, (cands, _) in ITEMS.items()
        if (e := _element(cur, cands)) is not None
    }

    guidance: dict[str, float] = {}
    if q == 4:
        fcs = [f for f in fin["company_forecasts"] if f["fiscal_year"] == fy and f["disclosed"] <= t["cutoff"]]
        if fcs:
            latest = max(fcs, key=lambda f: f["disclosed"])
            for k, labels in GUIDANCE_KEYS.items():
                label = next((x for x in labels if x in latest), None)
                e = _element(prev, ITEMS[k][0])
                if label and e and k in actual:
                    guidance[k] = round(latest[label] - prev[e], 1 if k != "eps" else 2)

    rows = []
    for path in sorted((root / "results").glob(f"{args.vendor or '*'}/*.jsonl")):
        r = parse_run(path)
        fc = r["forecast"].get("forecast", {})
        rows.append({"vendor": path.parent.name, "cond": path.stem, **r, "error_pct": {k: _pct(fc.get(k), actual[k]) for k in actual}})

    def table(with_model: bool) -> list[str]:
        head = ["ベンダー", "条件"] + (["モデル"] if with_model else [])
        tail = ["総トークン", "出力", "費用$", "ターン", "読んだファイル", "MCP", "拒否"]
        blank = " |" * (len(head) - 1)
        out = [
            "| " + " | ".join(head + [ITEMS[k][1] for k in actual] + tail) + " |",
            "|" + "---|" * (len(head) + len(actual) + len(tail)),
            "| 実績 |" + blank + " " + " | ".join(f"{v:,.2f}" if k == "eps" else f"{v:,.1f}" for k, v in actual.items()) + " |" * (len(tail) + 1),
        ]
        if guidance:
            out.append(
                "| 会社予想から逆算 |" + blank + " "
                + " | ".join((f"{guidance[k]:,.1f} ({_pct(guidance[k], actual[k])}%)" if k in guidance else "−") for k in actual)
                + " |" * (len(tail) + 1)
            )
        for r in rows:
            fc = r["forecast"].get("forecast", {})
            cells = [r["vendor"], r["cond"]] + ([r["model"]] if with_model else [])
            cells += [(f"{fc[k]:,} ({r['error_pct'][k]}%)" if k in fc else "−") for k in actual]
            cells += [
                f"{r['total_tokens']:,}", f"{r['usage']['output_tokens'] or 0:,}", str(r["cost"]), str(r["turns"]),
                str(len(r["reads"])), str(r["mcp_calls"]), str(len(r["denials"])),
            ]
            out.append("| " + " | ".join(cells) + " |")
        return out

    head_lines = [
        f"## {t['name']} {target_label(t)}",
        "",
        f"締切 {t['cutoff']} / 発表 {t['announce']}。単位は百万円、括弧内は実績との誤差。",
        "",
    ]
    lines = head_lines + table(with_model=not args.no_model)
    print("\n".join(head_lines + table(with_model=True)))

    if args.markdown:
        detail = ["", "## 各条件の出力", ""]
        for r in rows:
            detail += [
                f"### {r['vendor']} / {r['cond']}" + ("" if args.no_model else f"（{r['model']}）"),
                "",
                f"読んだファイル: {', '.join(r['reads']) or 'なし'}",
                "",
                f"トークン内訳: {json.dumps(r['usage'])}",
                "",
                "```json",
                json.dumps(r["forecast"], ensure_ascii=False, indent=1),
                "```",
                "",
            ]
        args.markdown.write_text("\n".join(lines + detail) + "\n")
    (root / "results" / "score.json").write_text(
        json.dumps({"actual": actual, "guidance": guidance, "rows": rows}, ensure_ascii=False, indent=1)
    )


if __name__ == "__main__":
    main()
