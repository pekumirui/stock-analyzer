"""条件ごとの作業フォルダ（<root>/cond_<X>/）を作る。中身はコピー（シンボリックリンクは外を指すので使わない）。

  .venv/bin/python analyzer/agent_forecast/build_conditions.py --target targets/6036_FY2026Q4.json

<root>/raw/ の financials.json・monthly_pdf/・briefing_pdf/・monthly_api/ を先に作っておく。
月次 API のスナップショットは <root>/mcp/ に置き、cond_<X>/ の外から MCP サーバーが読む。
"""

from __future__ import annotations

import argparse
import json
import shutil

from common import HERE, jp_date, load_target, root_of, target_args

# 条件 -> 渡すもの。financials は全条件に入れる
CONDITIONS: dict[str, dict[str, bool]] = {
    "A": {"monthly_pdf": False, "monthly_api": False, "briefing": False},
    "B": {"monthly_pdf": True, "monthly_api": False, "briefing": False},
    "C": {"monthly_pdf": False, "monthly_api": True, "briefing": False},
    "D": {"monthly_pdf": False, "monthly_api": False, "briefing": True},
    "E": {"monthly_pdf": True, "monthly_api": False, "briefing": True},
    "F": {"monthly_pdf": False, "monthly_api": True, "briefing": True},
}


def readme(t: dict, spec: dict[str, bool], financials_source: str) -> str:
    code4 = t["code"][:4] if t["code"].endswith("0") else t["code"]
    text = "# 作業フォルダの中身\n\n" f"すべて {jp_date(t['cutoff'])} までに公表された情報です。\n"
    text += (
        "\n## data/financials.json\n\n"
        f"{financials_source}単位は百万円。\n\n"
        "- `cumulative`: 期首からの累計の PL・CF とセグメント（開示どおり）\n"
        "- `quarterly_standalone`: 四半期単独（累計の差）\n"
        "- `balance_sheet`: 各四半期末の BS\n"
        "- `company_forecasts`: 各短信時点の会社の通期予想\n"
        "- `notes`: 注意点\n"
    )
    r = t.get("readme", {})
    if spec["monthly_pdf"]:
        text += f"\n## data/monthly_pdf/\n\n{r.get('monthly_pdf', '月次の PDF。')}ファイル名の先頭 8 桁は公表日。\n"
    if spec["monthly_api"]:
        text += (
            "\n## 月次データのツール（monthly_list / monthly_get）\n\n"
            f"{r.get('monthly_api', '月次の表（JSON）を暦年ごとに返す。')}銘柄コードは {code4}。"
            "`monthly_list` で年の一覧、`monthly_get` で年ごとの表を取る。\n"
        )
    if spec["briefing"]:
        text += f"\n## data/briefing_pdf/\n\n{r.get('briefing', '決算説明資料。')}ファイル名の先頭 8 桁は公表日。\n"
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    args = ap.parse_args()
    t = load_target(args.target)
    root = root_of(t, args.root)
    raw = root / "raw"
    fin_source = json.loads((raw / "financials.json").read_text())["source"].split("。")[0] + "。"

    has_api = (raw / "monthly_api" / "index.json").exists()
    mcp_dir = root / "mcp"
    if mcp_dir.exists():
        shutil.rmtree(mcp_dir)
    if has_api:
        shutil.copytree(raw / "monthly_api", mcp_dir / "snapshot")
        shutil.copy(HERE / "monthly_mcp.py", mcp_dir / "monthly_mcp.py")

    for name, spec in CONDITIONS.items():
        cond = root / f"cond_{name}"
        if cond.exists():
            shutil.rmtree(cond)
        need = [k for k, on in spec.items() if on]
        if ("monthly_pdf" in need and not (raw / "monthly_pdf").exists()) or ("briefing" in need and not (raw / "briefing_pdf").exists()) or ("monthly_api" in need and not has_api):
            print(f"cond_{name}: 材料が無いので作らない ({need})")
            continue
        data = cond / "data"
        data.mkdir(parents=True)
        shutil.copy(raw / "financials.json", data / "financials.json")
        if spec["monthly_pdf"]:
            shutil.copytree(raw / "monthly_pdf", data / "monthly_pdf")
        if spec["briefing"]:
            shutil.copytree(raw / "briefing_pdf", data / "briefing_pdf")
        (cond / "README.md").write_text(readme(t, spec, fin_source))

        # MCP の設定は作業フォルダの外に置く（エージェントに読ませる必要がない）
        servers = {}
        if spec["monthly_api"]:
            servers["monthly"] = {"command": "python3", "args": [str(mcp_dir / "monthly_mcp.py"), str(mcp_dir / "snapshot")]}
        (root / f"mcp_{name}.json").write_text(json.dumps({"mcpServers": servers}, indent=1))
        n = sum(1 for p in cond.rglob("*") if p.is_file())
        print(f"cond_{name}: {n} files, mcp={'yes' if servers else 'no'}")


if __name__ == "__main__":
    main()
