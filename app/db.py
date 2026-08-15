from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from .config import DATABASE_URL
from .models import Base

engine = create_engine(DATABASE_URL, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)


class SchemaMismatch(RuntimeError):
    """既存DBのテーブル定義がモデル定義と食い違っている。"""


def missing_columns() -> dict[str, list[str]]:
    """既存テーブルに不足している列を {テーブル名: [列名...]} で返す。

    SQLAlchemy の create_all は既存テーブルを ALTER しないため、
    モデルに列を追加した後に古いDBを使うと実行時エラーになる。事前に検出する。
    """
    insp = inspect(engine)
    existing = set(insp.get_table_names())
    out: dict[str, list[str]] = {}
    for table in Base.metadata.sorted_tables:
        if table.name not in existing:
            continue  # 未作成なら create_all が正しく作る
        have = {c["name"] for c in insp.get_columns(table.name)}
        lack = [c.name for c in table.columns if c.name not in have]
        if lack:
            out[table.name] = lack
    return out


def migrate_add_columns(verbose: bool = True) -> int:
    """不足列を ALTER TABLE ADD COLUMN で追加する (データは保持)。

    追加された列の値は NULL になるため、株価・財務は再取得が必要。
    戻り値は追加した列数。
    """
    lacks = missing_columns()
    if not lacks:
        return 0
    n = 0
    tables = {t.name: t for t in Base.metadata.sorted_tables}
    with engine.begin() as conn:
        for tname, cols in lacks.items():
            for cname in cols:
                col = tables[tname].columns[cname]
                ddl = col.type.compile(engine.dialect)
                conn.execute(text(f'ALTER TABLE {tname} ADD COLUMN "{cname}" {ddl}'))
                if verbose:
                    print(f"  列を追加: {tname}.{cname} ({ddl})")
                n += 1
    return n


def init_db(strict: bool = False):
    """テーブル作成。strict=True なら既存スキーマの不整合で例外を投げる。"""
    Base.metadata.create_all(engine)
    if strict:
        lacks = missing_columns()
        if lacks:
            detail = "\n".join(f"  {t}: {', '.join(c)}" for t, c in lacks.items())
            raise SchemaMismatch(
                "既存DBのスキーマが古く、以下の列がありません:\n" + detail +
                "\n\n対処法 (どちらか):\n"
                "  A) .\\run.ps1 reset      DBを削除して作り直す (推奨・データは再取得)\n"
                "  B) .\\run.ps1 migrate    列だけ追加して既存データを残す (追加列は空のまま)\n"
            )


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
