from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import MONTHLY_ROW_ORDER_PATH
from .db import init_db
from .api import ApiError, router, router_v1

app = FastAPI(title="株式分析ツール")
# 古いスキーマのDBのまま起動すると個々のAPIが実行時エラーになるため、起動時に検査する
init_db(strict=True)
app.include_router(router)
app.include_router(router_v1)

STATIC = Path(__file__).resolve().parent.parent / "static"


@app.get("/static/monthly_row_order.json")
def monthly_row_order():
    return FileResponse(MONTHLY_ROW_ORDER_PATH)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(ApiError)
def api_error_handler(_request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content=exc.payload)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
