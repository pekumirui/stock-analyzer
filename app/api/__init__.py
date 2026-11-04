"""REST API。機能（画面のタブ）ごとのモジュールを /api と /api/v1 にまとめる。"""
from fastapi import APIRouter

from . import chart, earnings, holders, pts, ranking, screener, stock, stock_decks, stocks, supply
from .monthly import router_v1

router = APIRouter(prefix="/api")
for _mod in (earnings, chart, screener, pts, ranking, stocks, stock, stock_decks, holders, supply):
    router.include_router(_mod.router)

__all__ = ["router", "router_v1"]
