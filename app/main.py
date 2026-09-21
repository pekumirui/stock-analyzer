from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .api import (
    ApiError, _parse_code, list_tables_payload, router, router_v1, table_year_payload,
)
from .config import MONTHLY_ROW_ORDER_PATH
from .db import get_session, init_db
from .public_monthly import (
    code4, detail_html, display_years, not_found_html, public_origin, robots_txt,
    sitemap_xml, top_html,
)

app = FastAPI(title="株式分析ツール")
# 古いスキーマのDBのまま起動すると個々のAPIが実行時エラーになるため、起動時に検査する
init_db(strict=True)
app.include_router(router)
app.include_router(router_v1)

STATIC = Path(__file__).resolve().parent.parent / "static"
FAVICON = STATIC / "favicon.svg"


def _parse_public_code(raw: str) -> str | None:
    try:
        return _parse_code(raw)
    except ApiError:
        return None


@app.get("/static/monthly_row_order.json")
def monthly_row_order():
    return FileResponse(MONTHLY_ROW_ORDER_PATH)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(ApiError)
def api_error_handler(_request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content=exc.payload)


@app.get("/")
def root():
    return RedirectResponse("/monthly", status_code=301)


@app.get("/app")
@app.get("/app/")
def app_spa():
    return FileResponse(STATIC / "index.html")


@app.get("/robots.txt")
def robots(request: Request):
    return Response(robots_txt(public_origin(request)), media_type="text/plain; charset=utf-8")


@app.get("/sitemap.xml")
def sitemap(request: Request, db: Session = Depends(get_session)):
    return Response(
        sitemap_xml(public_origin(request), list_tables_payload(db)),
        media_type="application/xml; charset=utf-8",
    )


@app.get("/favicon.ico")
def favicon():
    return FileResponse(FAVICON, media_type="image/svg+xml")


@app.get("/monthly")
def public_monthly_top(request: Request, db: Session = Depends(get_session)):
    return HTMLResponse(top_html(request, list_tables_payload(db)))


@app.get("/monthly/{code}")
def public_monthly_detail(code: str, request: Request, db: Session = Depends(get_session)):
    raw = code.strip().upper()
    parsed = _parse_public_code(raw)
    if parsed is None:
        return HTMLResponse(not_found_html(request), status_code=404)
    if len(raw) == 5:
        return RedirectResponse(f"/monthly/{code4(parsed)}", status_code=301)
    issuers = list_tables_payload(db)
    issuer = next((row for row in issuers if row["code"] == parsed), None)
    if issuer is None:
        return HTMLResponse(not_found_html(request), status_code=404)
    tables = []
    for year in display_years(issuer.get("years") or []):
        try:
            tables.append(table_year_payload(db, parsed, year))
        except ApiError:
            continue
    return HTMLResponse(detail_html(request, issuer, tables))
