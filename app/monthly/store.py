"""月次の行（element）の付け替え・削除・統合。API の書き込みと付け替えジョブが使う。"""
import re
import unicodedata

from ..models import MonthlyElement, MonthlyValue
from .ingest import MonthlyIngestError


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
