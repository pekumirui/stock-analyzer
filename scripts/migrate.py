"""既存DBに不足している列を追加する (データを消さずにスキーマだけ追随)。

追加された列の値はNULLのままなので、株価・財務は再取得が必要。
デモデータ段階なら .\\run.ps1 reset の方が確実。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import init_db, migrate_add_columns, missing_columns


def main():
    init_db()
    lacks = missing_columns()
    if not lacks:
        print("スキーマは最新です。追加する列はありません。")
        return
    print("不足している列:")
    for t, cols in lacks.items():
        print(f"  {t}: {', '.join(cols)}")
    n = migrate_add_columns()
    print(f"\n{n}列を追加しました。")
    print("追加列の値は空のため、株価・財務の再取得を推奨します:")
    print("  .\\run.ps1 backfill 2")


if __name__ == "__main__":
    main()
