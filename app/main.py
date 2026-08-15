from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .db import init_db
from .api import router

app = FastAPI(title="株式分析ツール")
# 古いスキーマのDBのまま起動すると個々のAPIが実行時エラーになるため、起動時に検査する
init_db(strict=True)
app.include_router(router)

STATIC = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
