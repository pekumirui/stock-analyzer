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
            continue
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


XBRL_FINANCIALS_UNIQUE_COLS = (
    "filing_id",
    "std_item_id",
    "is_consolidated",
    "period_end",
    "value_kind",
    "range_side",
    "target_period",
)


def _xbrl_financials_unique_indexes(conn) -> list[dict]:
    rows = conn.execute(text("PRAGMA index_list('xbrl_financials')")).fetchall()
    out = []
    for row in rows:
        name = row[1]
        unique = row[2]
        origin = row[3] if len(row) > 3 else ""
        if not unique:
            continue
        cols = [
            r[2]
            for r in conn.execute(text(f'PRAGMA index_info("{name}")')).fetchall()
        ]
        out.append({"name": name, "origin": origin, "cols": cols})
    return out


def migrate_xbrl_financials_source_layer() -> int:
    """source_layer が空の既存行を source_element の namespace から埋める。

    tse-ed-t → summary、それ以外で要素がある行 → primary。戻り値は更新行数。
    """
    insp = inspect(engine)
    if "xbrl_financials" not in insp.get_table_names():
        return 0
    cols = {c["name"] for c in insp.get_columns("xbrl_financials")}
    if "source_layer" not in cols:
        return 0
    elem_tables = insp.get_table_names()
    if "xbrl_elements" not in elem_tables:
        return 0
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE xbrl_financials SET source_layer = CASE "
                "WHEN ("
                "  SELECT namespace_key FROM xbrl_elements"
                "  WHERE xbrl_elements.id = xbrl_financials.source_element_id"
                ") = 'tse-ed-t' THEN 'summary' "
                "WHEN source_element_id IS NOT NULL THEN 'primary' "
                "ELSE source_layer END "
                "WHERE source_layer IS NULL"
            )
        )
        return int(result.rowcount or 0)


def migrate_xbrl_financials_unique() -> bool:
    """旧 UNIQUE (filing, std, consol, period_end) を予想・レンジ列込みに付け替える。

    SQLite の表制約 UNIQUE は DROP INDEX できないので、そのときは表を作り直す。
    既存23項目の value はコピーしたまま。新しい列は actual/point/period_type。
    戻り値はユニークキーを付け替えたとき True。
    """
    insp = inspect(engine)
    if "xbrl_financials" not in insp.get_table_names():
        return False
    wanted = list(XBRL_FINANCIALS_UNIQUE_COLS)
    with engine.begin() as conn:
        indexes = _xbrl_financials_unique_indexes(conn)
        if any(idx["cols"] == wanted for idx in indexes):
            conn.execute(
                text(
                    "UPDATE xbrl_financials SET target_period = COALESCE("
                    "NULLIF(target_period, ''), NULLIF(period_type, ''), 'FY') "
                    "WHERE target_period IS NULL OR target_period = ''"
                )
            )
            return False
        stale = [
            idx for idx in indexes
            if idx["cols"] != ["id"]
            and "filing_id" in idx["cols"]
            and "value_kind" not in idx["cols"]
        ]
        if not stale:
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_xbrl_financials_key "
                    "ON xbrl_financials ("
                    "filing_id, std_item_id, is_consolidated, period_end, "
                    "value_kind, range_side, target_period)"
                )
            )
            return True
        table_constraint = any(idx["origin"] == "u" for idx in stale)
        if not table_constraint:
            for idx in stale:
                conn.execute(text(f'DROP INDEX IF EXISTS "{idx["name"]}"'))
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_xbrl_financials_key "
                    "ON xbrl_financials ("
                    "filing_id, std_item_id, is_consolidated, period_end, "
                    "value_kind, range_side, target_period)"
                )
            )
            conn.execute(
                text(
                    "UPDATE xbrl_financials SET target_period = COALESCE("
                    "NULLIF(target_period, ''), NULLIF(period_type, ''), 'FY') "
                    "WHERE target_period IS NULL OR target_period = ''"
                )
            )
            return True
        _recreate_xbrl_financials_table(conn)
        return True


def _recreate_xbrl_financials_table(conn) -> None:
    from app.models import XbrlFinancial

    old_cols = [
        r[1] for r in conn.execute(text("PRAGMA table_info(xbrl_financials)")).fetchall()
    ]
    conn.execute(text("ALTER TABLE xbrl_financials RENAME TO xbrl_financials__old"))
    # RENAME 後も名前付き INDEX は残る。新しい CREATE と衝突するので先に外す。
    for row in conn.execute(text("PRAGMA index_list('xbrl_financials__old')")).fetchall():
        name = row[1]
        if name in ("uq_xbrl_financials_key", "idx_xbrl_financials_lookup"):
            conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
    XbrlFinancial.__table__.create(bind=conn)
    new_cols = [
        r[1] for r in conn.execute(text("PRAGMA table_info(xbrl_financials)")).fetchall()
    ]
    shared = [c for c in new_cols if c in old_cols and c != "id"]
    select_bits = []
    for c in shared:
        ident = '"' + c.replace('"', '""') + '"'
        if c == "target_period":
            select_bits.append(
                f"COALESCE(NULLIF({ident}, ''), NULLIF(\"period_type\", ''), 'FY')"
            )
        elif c == "value_kind":
            select_bits.append(f"COALESCE(NULLIF({ident}, ''), 'actual')")
        elif c == "range_side":
            select_bits.append(f"COALESCE(NULLIF({ident}, ''), 'point')")
        else:
            select_bits.append(ident)
    dest_sql = ", ".join('"' + c.replace('"', '""') + '"' for c in shared)
    src_sql = ", ".join(select_bits)
    conn.execute(
        text(
            f"INSERT INTO xbrl_financials ({dest_sql}) "
            f"SELECT {src_sql} FROM xbrl_financials__old"
        )
    )
    conn.execute(text("DROP TABLE xbrl_financials__old"))


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
