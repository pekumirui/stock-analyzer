"""月次の後始末。デプロイごとに jobs/migrate.py が run_deploy_maintenance を呼ぶ。

保存名の付け替え（apply_issuer_element_aliases / merge_yoy_duplicate_elements）は
jobs/monthly/monthly_names.py だけが呼ぶ。
"""
from sqlalchemy import inspect, text

from ..db import SessionLocal, engine
from ..models import MonthlyElement, MonthlyElementSource
from .ingest import apply_issuer_aliases, canonicalize_element_label, plan_yoy_duplicate_merges
from .store import (
    _norm_element_name, merge_monthly_element_into, record_element_source, rename_monthly_element,
)


def apply_issuer_element_aliases(verbose: bool = True, *, dry_run: bool = False) -> list[dict]:
    """銘柄エイリアスで保存名が変わる行をリネーム。先に正規名があれば値を寄せる。冪等。

    起動時には呼ばない（保存名は凍結）。jobs/monthly/monthly_names.py だけが呼ぶ。dry_run は書かずに報告だけ。
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
                    r.code, canonicalize_element_label(r.element, r.unit, r.code),
                    year=r.year,
                ),
                r.unit,
                r.code,
            ) != r.element
        ]
        rows.sort(key=lambda r: (r.code, r.year, r.sort_order, r.element))
        for row in rows:
            old = row.element
            new = canonicalize_element_label(
                apply_issuer_aliases(
                    row.code, canonicalize_element_label(old, row.unit, row.code),
                    year=row.year,
                ),
                row.unit,
                row.code,
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

    起動時には呼ばない（保存名は凍結）。jobs/monthly/monthly_names.py だけが呼ぶ。dry_run は書かずに報告だけ。
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
            apply_issuer_aliases(
                code, canonicalize_element_label(element, unit, code), year=year,
            ),
            unit,
            code,
        )
        if want and want != element:
            out.append((code, year, element, want))
    out.sort()
    return out


def report_monthly_name_drift(verbose: bool = True) -> int:
    """保存名は起動時に書き換えない。ずれた行の数だけログに出す。付け替えは jobs/monthly/monthly_names.py。"""
    drift = monthly_name_drift()
    if drift and verbose:
        codes = sorted({code for code, *_ in drift})
        head = ", ".join(codes[:10]) + (" …" if len(codes) > 10 else "")
        print(
            f"月次の保存名ドリフト: {len(drift)} 行 / {len(codes)} 銘柄 ({head})。"
            "jobs/monthly/monthly_names.py plan で確認、apply で付け替え"
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
    tables = (
        "monthly_values", "monthly_element_sources", "monthly_elements", "monthly_tables",
        "monthly_issuers",
    )
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


def backfill_legacy_element_sources(verbose: bool = True) -> int:
    """元の行名が残っていない行に、保存名そのものを via=legacy で入れる。冪等。

    元の行名を記録する前（KAB-17）に入った行。スナップショットの入力を欠かさないため。
    """
    db = SessionLocal()
    try:
        have = {
            (c, y, e) for c, y, e in
            db.query(MonthlyElementSource.code, MonthlyElementSource.year,
                     MonthlyElementSource.element)
        }
        added = 0
        for row in db.query(MonthlyElement).all():
            if (row.code, row.year, row.element) in have:
                continue
            if db.get(MonthlyElementSource, (row.code, row.year, row.element, row.unit)):
                continue
            record_element_source(
                db, row.code, row.year, row.element, row.unit, row.element, "legacy",
            )
            added += 1
        db.commit()
    finally:
        db.close()
    if verbose and added:
        print(f"元の行名の無い行を legacy で記録: {added} 行")
    return added


def run_deploy_maintenance(verbose: bool = True) -> None:
    """unit の補正、保存名ドリフトのログ、マスタに無い銘柄の行の削除、legacy の記録。冪等。"""
    apply_known_monthly_element_units()
    report_monthly_name_drift(verbose)
    delete_orphan_monthly_rows(verbose)
    backfill_legacy_element_sources(verbose)
