import re
import time
import unicodedata

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from .config import DATABASE_URL
from .models import Base, MonthlyElement, MonthlyValue
from .monthly.ingest import (
    MonthlyIngestError,
    apply_issuer_aliases,
    canonicalize_element_label,
    plan_yoy_duplicate_merges,
)


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
        report_monthly_name_drift(verbose=verbose)
        delete_orphan_monthly_rows(verbose=verbose)
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
    apply_known_monthly_element_units()
    report_monthly_name_drift(verbose=verbose)
    delete_orphan_monthly_rows(verbose=verbose)
    return n


def _server_default_sql(col) -> str | None:
    default = col.server_default
    if default is None or default.arg is None:
        return None
    arg = default.arg
    if isinstance(arg, str):
        return "'" + arg.replace("'", "''") + "'"
    return str(arg)


def _norm_element_name(element: str) -> str:
    s = unicodedata.normalize("NFKC", element)
    return re.sub(r"\s+", "", s)


def rename_monthly_element(
    db, code: str, year: int, old: str, new: str, *, strict: bool = False,
) -> None:
    """複合PKの element 名を変える。先に values、無い正規名はコピーしてから短い行を消す。"""
    if old == new:
        return
    src = db.get(MonthlyElement, (code, year, old))
    dst = db.get(MonthlyElement, (code, year, new))
    if src is None:
        return
    if dst is not None:
        merge_monthly_element_into(db, src, dst, strict=strict)
        return
    db.add(MonthlyElement(
        code=code, year=year, element=new,
        sort_order=src.sort_order, unit=src.unit,
    ))
    db.flush()
    for v in list(db.query(MonthlyValue).filter_by(code=code, year=year, element=old)):
        db.add(MonthlyValue(
            code=code, year=year, element=new, month=v.month, value=v.value,
        ))
        db.delete(v)
    db.delete(src)
    db.flush()


def delete_monthly_element(db, code: str, year: int, element: str) -> None:
    """行と値を消す。表自体は残す。"""
    src = db.get(MonthlyElement, (code, year, element))
    if src is None:
        return
    for v in list(db.query(MonthlyValue).filter_by(code=code, year=year, element=element)):
        db.delete(v)
    db.delete(src)
    db.flush()


def merge_monthly_element_into(
    db, src: MonthlyElement, dst: MonthlyElement, *, strict: bool = False,
) -> list[dict]:
    """短い行の値を正規名へ。欠損は埋める。両方非NULLで不一致なら正規名を残して報告。

    strict=True は書き込み経路。衝突があれば src を消さず 409。
    strict=False は init_db の既存重複掃除。衝突を返して dst を残す。
    """
    if src is dst or (
        src.code == dst.code and src.year == dst.year and src.element == dst.element
    ):
        return []
    src_vals = list(db.query(MonthlyValue).filter_by(
        code=src.code, year=src.year, element=src.element,
    ))
    conflicts: list[dict] = []
    for v in src_vals:
        existing = db.get(MonthlyValue, (dst.code, dst.year, dst.element, v.month))
        if existing is not None and float(existing.value) != float(v.value):
            conflicts.append({
                "code": src.code,
                "year": src.year,
                "month": v.month,
                "canonical": dst.element,
                "dropped_from": src.element,
                "kept": float(existing.value),
                "dropped": float(v.value),
            })
    if strict and conflicts:
        raise MonthlyIngestError(
            409, "FOLD_CONFLICT",
            "折りたたみ先と値が食い違っています。セルを直してから再試行してください。",
            extra={"conflicts": conflicts},
        )
    for v in src_vals:
        existing = db.get(MonthlyValue, (dst.code, dst.year, dst.element, v.month))
        if existing is None:
            db.add(MonthlyValue(
                code=dst.code, year=dst.year, element=dst.element,
                month=v.month, value=v.value,
            ))
        db.delete(v)
    db.delete(src)
    db.flush()
    return conflicts


def apply_issuer_element_aliases(verbose: bool = True, *, dry_run: bool = False) -> list[dict]:
    """銘柄エイリアスで保存名が変わる行をリネーム。先に正規名があれば値を寄せる。冪等。

    起動時には呼ばない（保存名は凍結）。jobs/monthly_names.py だけが呼ぶ。dry_run は書かずに報告だけ。
    """
    insp = inspect(engine)
    if "monthly_elements" not in insp.get_table_names():
        return []
    db = SessionLocal()
    reports: list[dict] = []
    try:
        rows = [
            r for r in db.query(MonthlyElement).all()
            if canonicalize_element_label(
                apply_issuer_aliases(
                    r.code, canonicalize_element_label(r.element, r.unit), year=r.year,
                ),
                r.unit,
            ) != r.element
        ]
        rows.sort(key=lambda r: (r.code, r.year, r.sort_order, r.element))
        for row in rows:
            old = row.element
            new = canonicalize_element_label(
                apply_issuer_aliases(
                    row.code, canonicalize_element_label(old, row.unit), year=row.year,
                ),
                row.unit,
            )
            if db.get(MonthlyElement, (row.code, row.year, old)) is None:
                continue
            dst_existed = db.get(MonthlyElement, (row.code, row.year, new)) is not None
            rename_monthly_element(db, row.code, row.year, old, new)
            report = {
                "code": row.code,
                "year": row.year,
                "canonical": new,
                "renamed": [] if dst_existed else [old],
                "deleted": [old] if dst_existed else [],
            }
            reports.append(report)
            if verbose:
                print(f"月次エイリアス: {row.code} {row.year} {old!r} → {new!r}")
        if reports and not dry_run:
            db.commit()
        else:
            db.rollback()
        return reports
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def merge_yoy_duplicate_elements(verbose: bool = True, *, dry_run: bool = False) -> list[dict]:
    """保存名を canonicalize へ。同一年に両方あれば正規名へ寄せて短い行を消す。冪等。

    起動時には呼ばない（保存名は凍結）。jobs/monthly_names.py だけが呼ぶ。dry_run は書かずに報告だけ。
    """
    insp = inspect(engine)
    if "monthly_elements" not in insp.get_table_names():
        return []
    db = SessionLocal()
    reports: list[dict] = []
    try:
        rows = [
            (r.code, r.year, r.element, r.unit)
            for r in db.query(MonthlyElement).all()
        ]
        plans = plan_yoy_duplicate_merges(rows)
        for code, year, canonical, shorts in plans:
            dst = db.get(MonthlyElement, (code, year, canonical))
            renamed: list[str] = []
            conflicts: list[dict] = []
            deleted: list[str] = []
            if dst is None:
                first = None
                for name in shorts:
                    first = db.get(MonthlyElement, (code, year, name))
                    if first is not None:
                        break
                if first is None:
                    continue
                old = first.element
                rename_monthly_element(db, code, year, old, canonical)
                dst = db.get(MonthlyElement, (code, year, canonical))
                renamed.append(old)
                shorts = [name for name in shorts if name != old]
            if dst is None:
                continue
            for name in shorts:
                src = db.get(MonthlyElement, (code, year, name))
                if src is None:
                    continue
                conflicts.extend(merge_monthly_element_into(db, src, dst))
                deleted.append(name)
                dst = db.get(MonthlyElement, (code, year, canonical))
                if dst is None:
                    break
            if not renamed and not deleted and not conflicts:
                continue
            report = {
                "code": code,
                "year": year,
                "canonical": canonical,
                "renamed": renamed,
                "deleted": deleted,
                "conflicts": conflicts,
            }
            reports.append(report)
            if verbose:
                moved = renamed + deleted
                print(
                    f"月次行を正規化: {code} {year} {moved!r} → {canonical!r}"
                )
                for item in conflicts:
                    print(
                        f"  月{item['month']} 値の衝突: "
                        f"正規名={item['kept']} 短い名={item['dropped']} "
                        f"({item['dropped_from']!r}、正規名を残す)"
                    )
        if reports and not dry_run:
            db.commit()
        else:
            db.rollback()
        return reports
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def monthly_name_drift() -> list[tuple[str, int, str, str]]:
    """今の正規化と alias で作り直すと名前が変わる保存行。(code, year, 保存名, 作り直した名前)。読むだけ。"""
    insp = inspect(engine)
    if "monthly_elements" not in insp.get_table_names():
        return []
    db = SessionLocal()
    try:
        rows = db.query(
            MonthlyElement.code, MonthlyElement.year, MonthlyElement.element, MonthlyElement.unit,
        ).all()
    finally:
        db.close()
    out: list[tuple[str, int, str, str]] = []
    for code, year, element, unit in rows:
        want = canonicalize_element_label(
            apply_issuer_aliases(code, canonicalize_element_label(element, unit), year=year), unit,
        )
        if want and want != element:
            out.append((code, year, element, want))
    out.sort()
    return out


def report_monthly_name_drift(verbose: bool = True) -> int:
    """保存名は起動時に書き換えない。ずれた行の数だけログに出す。付け替えは jobs/monthly_names.py。"""
    drift = monthly_name_drift()
    if drift and verbose:
        codes = sorted({code for code, *_ in drift})
        head = ", ".join(codes[:10]) + (" …" if len(codes) > 10 else "")
        print(
            f"月次の保存名ドリフト: {len(drift)} 行 / {len(codes)} 銘柄 ({head})。"
            "jobs/monthly_names.py plan で確認、apply で付け替え"
        )
    return len(drift)


def apply_known_monthly_element_units() -> int:
    """Gift 92790 の既存行だけ unit を付ける。値は変換しない。該当が無ければ 0。"""
    insp = inspect(engine)
    if "monthly_elements" not in insp.get_table_names():
        return 0
    have = {c["name"] for c in insp.get_columns("monthly_elements")}
    if "unit" not in have:
        return 0
    db = SessionLocal()
    try:
        n = 0
        gift = db.query(MonthlyElement).filter(MonthlyElement.code == "92790").all()
        for row in gift:
            want = "number" if "店舗数" in _norm_element_name(row.element) else "yoy_index"
            if row.unit != want:
                row.unit = want
                n += 1
        if n:
            db.commit()
        return n
    finally:
        db.close()


def delete_orphan_monthly_rows(verbose: bool = True) -> dict[str, int]:
    """stocks に無い code の月次行を子から消す。FK は付けない。何度呼んでも冪等。

    順は monthly_values → monthly_elements → monthly_tables → monthly_issuers。
    本番の名証単独 (30320 / 30660) など、マスタに無い表を落とす。
    """
    insp = inspect(engine)
    names = set(insp.get_table_names())
    deleted: dict[str, int] = {}
    if "stocks" not in names:
        return deleted
    tables = ("monthly_values", "monthly_elements", "monthly_tables", "monthly_issuers")
    with engine.begin() as conn:
        for tname in tables:
            if tname not in names:
                continue
            result = conn.execute(text(
                f"DELETE FROM {tname} WHERE code NOT IN (SELECT code FROM stocks)"
            ))
            deleted[tname] = result.rowcount or 0
    total = sum(deleted.values())
    if verbose and total:
        parts = ", ".join(f"{k}={v}" for k, v in deleted.items())
        print(f"孤立月次を削除: {parts}")
    return deleted


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
    apply_known_monthly_element_units()
    report_monthly_name_drift()
    delete_orphan_monthly_rows()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
