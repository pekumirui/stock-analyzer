"""月次表 /api/v1/tables。書き込みは loopback のみ（forbid_proxied_monthly_write）。"""
from fastapi import APIRouter

from . import doc, elements, ingest, tables
from ._common import ApiError, _parse_code
from .tables import list_tables_payload, table_year_payload

router_v1 = APIRouter(prefix="/api/v1")
for _mod in (elements, ingest, tables, doc):
    router_v1.include_router(_mod.router)

__all__ = ["ApiError", "_parse_code", "list_tables_payload", "router_v1", "table_year_payload"]
