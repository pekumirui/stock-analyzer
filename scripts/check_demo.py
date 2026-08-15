"""本番バッチ実行前のガード。

  1. DBスキーマがモデル定義と一致しているか
  2. デモデータが残っていないか

exit 0 = 実行OK / exit 1 = 問題あり
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect

from app.db import SessionLocal, engine, missing_columns
from app.models import Stock, Statement


def main() -> int:
    if not inspect(engine).has_table("stocks"):
        return 0  # DB未作成 = クリーン

    # --- 1. スキーマ検査 ---
    lacks = missing_columns()
    if lacks:
        print("=" * 64)
        print("DBのスキーマが古く、以下の列がありません:")
        for t, cols in lacks.items():
            print(f"  {t}: {', '.join(cols)}")
        print()
        print("対処法 (どちらか):")
        print("  A) .\\run.ps1 reset     DBを削除して作り直す (推奨)")
        print("  B) .\\run.ps1 migrate   列だけ追加して既存データを残す")
        print("=" * 64)
        return 1

    # --- 2. デモデータ検査 ---
    db = SessionLocal()
    try:
        n_demo_stock = db.query(Stock).filter(Stock.code.like("0%")).count()
        n_demo_stmt = db.query(Statement).filter(Statement.raw_json.like('%"demo"%')).count()
    finally:
        db.close()

    if n_demo_stock or n_demo_stmt:
        print("=" * 64)
        print(f"デモデータが残っています (銘柄 {n_demo_stock}件 / 決算 {n_demo_stmt}件)")
        print("実データと混ざるとRSランクが全銘柄で狂います。")
        print("先に  .\\run.ps1 reset  でDBを削除してください。")
        print("=" * 64)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
