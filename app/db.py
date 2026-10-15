from itertools import groupby
import re
import time

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from .config import DATABASE_URL
from .models import Base


def sqlite_connect_args(url: str) -> dict[str, float]:
    """sqlite3 の busy timeout（秒）。未設定だと 5s で database is locked になる。"""
    if not str(url).startswith("sqlite"):
        return {}
    return {"timeout": 30.0}


def apply_sqlite_pragmas(dbapi_connection, connection_record=None) -> None:  # noqa: ARG001
    """本番は app + cron が同じ stock.db を触る。WAL が無いと読取も locked になる。"""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


engine = create_engine(
    DATABASE_URL, future=True, connect_args=sqlite_connect_args(DATABASE_URL),
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, future=True)

if str(DATABASE_URL).startswith("sqlite"):
    event.listens_for(engine, "connect")(apply_sqlite_pragmas)


LOCK_MARKERS = ("database is locked", "database is busy")
WRITE_LOCK_ATTEMPTS = 6
WRITE_LOCK_SLEEP_S = 0.05
WRITE_LOCK_SLEEP_CAP_S = 2.0


def is_sqlite_lock(exc: BaseException) -> bool:
    msg = str(exc).lower()
    orig = getattr(exc, "orig", None)
    if orig is not None:
        msg = f"{msg} {orig}".lower()
    return any(marker in msg for marker in LOCK_MARKERS)


def run_sqlite_write(db, fn, *, attempts: int = WRITE_LOCK_ATTEMPTS):
    """短いライターロックの隙間を待つ。fn のあと commit。失敗時は rollback して再実行。

    busy_timeout は接続あたり最大 30s。それを超えるロック（旧 compute_indicators の
    全置換 1 トランザクションなど）はチャンク commit 側で短くするのが本筋。
    ここはチャンクとチャンクの間に入れなかったとき用。
    """
    delay = WRITE_LOCK_SLEEP_S
    for attempt in range(1, attempts + 1):
        try:
            result = fn()
            db.commit()
            return result
        except OperationalError as exc:
            db.rollback()
            if not is_sqlite_lock(exc) or attempt >= attempts:
                raise
            time.sleep(delay)
            delay = min(delay * 2, WRITE_LOCK_SLEEP_CAP_S)
        except Exception:
            db.rollback()
            raise


class SchemaMismatch(RuntimeError):
    """既存DBのテーブル定義がモデル定義と食い違っている。"""


WRITE_CHUNK_SIZE = 50_000
_TABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def upsert(db: Session, model, rows: list[dict], *, commit: bool = True) -> None:
    if not rows:
        return
    table = model.__table__
    keys = [c.name for c in table.primary_key.columns]
    # 行ごとに VALUES を埋めた文はコンパイルが重い (indicators 146万行で約7分)。
    # 列の組が同じ連続区間ごとに 1 文を executemany する。順序は入力どおり。
    for cols, grp in groupby(rows, key=lambda r: tuple(sorted(r))):
        ins = sqlite_insert(table)
        upd = {c: ins.excluded[c] for c in cols if c not in keys}
        stmt = (ins.on_conflict_do_update(index_elements=keys, set_=upd) if upd
                else ins.on_conflict_do_nothing(index_elements=keys))
        db.execute(stmt, list(grp))
    if commit:
        db.commit()


def delete_all_chunked(
    db: Session, model, *, chunk_size: int = WRITE_CHUNK_SIZE,
) -> int:
    """全行を短いトランザクションで消す。SQLite は DB 全体でライター 1 本。

    indicators の 1 トランザクション全削除は busy_timeout(30s) を超えて
    月次 PUT が database is locked になる (#522)。
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    name = model.__table__.name
    if not _TABLE_NAME_RE.fullmatch(name):
        raise ValueError(f"unsafe table name: {name!r}")
    sql = text(
        f"DELETE FROM {name} WHERE rowid IN (SELECT rowid FROM {name} LIMIT :n)"
    )
    deleted = 0
    while True:
        n = db.execute(sql, {"n": chunk_size}).rowcount or 0
        db.commit()
        db.expire_all()
        if n <= 0:
            break
        deleted += n
    return deleted


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
                default_sql = _server_default_sql(col)
                if default_sql:
                    ddl = f"{ddl} DEFAULT {default_sql}"
                conn.execute(text(f'ALTER TABLE {tname} ADD COLUMN "{cname}" {ddl}'))
                if verbose:
                    print(f"  列を追加: {tname}.{cname} ({ddl})")
                n += 1
    return n


def _server_default_sql(col) -> str | None:
    default = col.server_default
    if default is None or default.arg is None:
        return None
    arg = default.arg
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return str(arg)


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
