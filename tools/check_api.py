"""J-Quants V2 の疎通確認と項目マッピング検証 (DBには書き込まない)。

バックフィル前にこれを実行して、APIキー・プラン範囲・項目名を確認する。
  python tools\\check_api.py           # 直近営業日で確認
  python tools\\check_api.py 2026-08-14
"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.market.jquants import JQuantsClient
from app.market.ingest import _price_row, _statement_row


def show(title, obj, keys):
    print(f"\n--- {title} ---")
    if not obj:
        print("  (データなし)")
        return
    for k in keys:
        print(f"  {k:22} = {obj.get(k)!r}")


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else None
    if not day:
        d = date.today()
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        day = d.strftime("%Y-%m-%d")
    print(f"検証日: {day}")

    with JQuantsClient() as jq:
        print("\n[1/4] 上場銘柄一覧 /equities/master")
        master = jq.equities_master()
        print(f"  取得件数: {len(master)}")
        show("サンプル", master[0] if master else None,
             ["Code", "CoName", "S17Nm", "S33Nm", "MktNm", "ScaleCat", "MrgnNm", "ProdCat"])

        print("\n[2/4] 株価四本値 /equities/bars/daily (date指定=全銘柄)")
        bars = jq.bars_daily(date=day)
        print(f"  取得件数: {len(bars)}  ← 1リクエストで全銘柄取れている")
        if bars:
            show("サンプル(生)", bars[0],
                 ["Date", "Code", "O", "H", "L", "C", "Vo", "Va",
                  "AdjFactor", "AdjO", "AdjH", "AdjL", "AdjC", "AdjVo", "MktCap"])
            print("  → DB行:", _price_row(bars[0]))

        print("\n[3/4] TOPIX /indices/bars/daily/topix")
        tp = jq.topix(from_=day, to=day)
        show("サンプル", tp[0] if tp else None, ["Date", "O", "H", "L", "C"])

        print("\n[4/4] 財務情報 /fins/summary (date指定=全銘柄)")
        st = jq.fins_summary(date=day)
        print(f"  開示件数: {len(st)}")
        if st:
            s = st[0]
            show("サンプル(生)", s,
                 ["DiscDate", "DiscTime", "Code", "DiscNo", "DocType", "CurPerType",
                  "CurFYSt", "CurFYEn", "Sales", "OP", "OdP", "NP", "EPS",
                  "FSales2Q", "FOP2Q", "FEPS2Q", "FSales", "FOP", "FOdP", "FNP", "FEPS"])
            row = _statement_row(s)
            print("  → DB行(主要):")
            for k in ("code", "disclosed_date", "type_of_period", "net_sales",
                      "operating_profit", "ordinary_profit", "profit", "eps",
                      "fy_net_sales", "fy_operating_profit", "fy_eps", "is_revision"):
                print(f"      {k:22} = {row[k]!r}")
            # 会計基準の分布 (経常利益がIFRSで空になることの確認)
            n_ifrs = sum(1 for x in st if "IFRS" in (x.get("DocType") or ""))
            n_noord = sum(1 for x in st if not str(x.get("OdP", "")).strip())
            print(f"\n  IFRS/USGAAP開示: {n_ifrs}/{len(st)}件, 経常利益が空: {n_noord}/{len(st)}件")
            periods = {}
            for x in st:
                periods[x.get("CurPerType")] = periods.get(x.get("CurPerType"), 0) + 1
            print(f"  CurPerType分布: {periods}")

    print("\n✅ すべて成功。バックフィルを実行できます: .\\run.ps1 backfill")


if __name__ == "__main__":
    main()
