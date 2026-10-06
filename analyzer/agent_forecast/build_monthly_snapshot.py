"""月次 API（/api/v1/tables）の応答を、設定の monthly_api_range の月だけに切って保存する。範囲外の月は null。

  .venv/bin/python analyzer/agent_forecast/build_monthly_snapshot.py --target targets/6036_FY2026Q4.json
"""

from __future__ import annotations

import argparse
import json
import urllib.request

from common import load_target, root_of, target_args


def get(base: str, path: str) -> dict:
    with urllib.request.urlopen(f"{base}{path}", timeout=30) as r:  # nosec B310
        return json.load(r)


def main() -> None:
    ap = argparse.ArgumentParser()
    target_args(ap)
    ap.add_argument("--base", default="https://kabukpi.com", help="本番の公開 GET。手元の 8000 は DB が古いことがある")
    args = ap.parse_args()
    t = load_target(args.target)
    out = root_of(t, args.root) / "raw" / "monthly_api"
    out.mkdir(parents=True, exist_ok=True)
    first, last = (tuple(map(int, s.split("-"))) for s in t["monthly_api_range"])

    years = [y for y in get(args.base, f"/api/v1/tables/{t['code']}")["years"] if first[0] <= y <= last[0]]
    for year in years:
        table = get(args.base, f"/api/v1/tables/{t['code']}/{year}")
        for el in table["elements"]:
            for m in list(el["values"]):
                if not (first <= (year, int(m)) <= last):
                    el["values"][m] = None
        (out / f"{year}.json").write_text(json.dumps(table, ensure_ascii=False))
        print(year, len(table["elements"]))
    (out / "index.json").write_text(json.dumps({"code": t["code"], "years": years}))


if __name__ == "__main__":
    main()
