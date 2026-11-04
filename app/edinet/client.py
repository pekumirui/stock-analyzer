"""EDINET ジョブ共通。vendor ラッパー（app/edinet_wrapper）は改変しない。

HTML 応答とメンテ案内への 302 はラッパーが EdinetMaintenanceError にする。
ジョブはそれを 1 件でも見たらその回を打ち切り、WARN 1 行で exit 0（KAB-149）。
このモジュールはラッパーを import しない（アプリ本体は 3.10 でも起動する）。
"""
from __future__ import annotations

__all__ = [
    "EdinetMaintenanceError",
    "is_maintenance",
    "is_not_found",
    "reraise",
    "skip_on_maintenance",
    "warn_maintenance",
]


class EdinetMaintenanceError(Exception):
    """EDINET が HTML またはメンテ案内へのリダイレクトを返した。その回の残りは打ち切る。"""


def is_maintenance(exc: BaseException) -> bool:
    """vendor / アプリのどちらの EdinetMaintenanceError でも True。"""
    return any(c.__name__ == "EdinetMaintenanceError" for c in type(exc).__mro__)


def is_not_found(exc: BaseException) -> bool:
    """書類取得が 404。取り下げ・非公開・取得可能期間外。次の回に取り直しても同じ。"""
    if getattr(exc, "status", None) == 404:
        return True
    resp = getattr(exc, "response", None)
    if getattr(resp, "status_code", None) == 404:
        return True
    return type(exc).__name__ == "EdinetNotFoundError"


def reraise(exc: BaseException) -> None:
    """メンテならアプリ側の EdinetMaintenanceError にする。違う例外は触らない。

    書類ループの `except Exception` から呼ぶ。メンテでなければ return し、404 などの判定を続ける。
    """
    if not is_maintenance(exc):
        return
    if isinstance(exc, EdinetMaintenanceError):
        raise exc
    raise EdinetMaintenanceError(str(exc)) from exc


def warn_maintenance(job: str, exc: BaseException) -> int:
    """cron は exit 0（#alerts-batch に出さない）。戻り値は終了コード。"""
    print(f"WARN edinet maintenance skip job={job} err={exc}", flush=True)
    return 0


def skip_on_maintenance(job: str, exc: BaseException) -> int | None:
    """メンテなら WARN して 0。それ以外は None（呼び側が raise）。"""
    if is_maintenance(exc):
        return warn_maintenance(job, exc)
    return None
