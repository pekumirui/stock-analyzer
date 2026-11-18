"""e-Stat の表を探して中身の分類を見る。config/supply/material_stats.json に素材を足すときの下調べ用。

使い方（ESTAT_APP_ID が要る）:
  python tools/supply/estat_fetch.py names 生産動態                 # 統計名 → 統計コード
  python tools/supply/estat_fetch.py tables 00550200 2025 石油化学   # 統計コードと調査年で表を探す（表題で絞る）
  python tools/supply/estat_fetch.py meta 0004061869 エチレン        # 表の分類。名前で絞る

検索語だけの getStatsList は古い表から 1000 件で打ち切られるので、統計コードと年で探す。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.supply.estat import EstatClient, table_title


def _text(x: object) -> str:
    return x.get("$", "") if isinstance(x, dict) else str(x or "")


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    cmd, args = sys.argv[1], sys.argv[2:]
    client = EstatClient(pause=0)
    if cmd == "names":
        body = client.call("getStatsList", "GET_STATS_LIST", statsNameList="Y", searchWord=args[0])
        infos = (body.get("DATALIST_INF") or {}).get("LIST_INF") or []
        for t in infos if isinstance(infos, list) else [infos]:
            name = t.get("STAT_NAME") or {}
            print(name.get("@code"), _text(name), _text(t.get("GOV_ORG")))
    elif cmd == "tables":
        word = args[2] if len(args) > 2 else ""
        for t in client.tables(args[0], args[1]):
            title = table_title(t)
            if word in title:
                print(t["@id"], t.get("SURVEY_DATE"), t.get("UPDATED_DATE"), title)
    elif cmd == "meta":
        word = args[1] if len(args) > 1 else ""
        for dim, classes in client.meta(args[0]).items():
            for code, name in classes.items():
                if word in name:
                    print(dim, code, name)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
