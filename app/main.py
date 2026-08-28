from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .api import ApiError, router, router_v1

app = FastAPI(title="株式分析ツール")
# 古いスキーマのDBのまま起動すると個々のAPIが実行時エラーになるため、起動時に検査する
init_db(strict=True)
app.include_router(router)
app.include_router(router_v1)

STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(ApiError)
def api_error_handler(_request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content=exc.payload)


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
