from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .api import router, router_v1
from .api.monthly import ApiError, _parse_code, list_tables_payload, table_year_payload
from .assets import html_etag, version_static_urls
from .config import MONTHLY_ROW_ORDER_PATH
from .db import get_session, init_db
from .monthly.ir_codes import load_ir_monthly_codes
from .monthly.public import (
    code4, detail_html, display_years, not_found_html, public_origin, robots_txt,
    sitemap_xml, top_html,
)

app = FastAPI(title="株式分析ツール")
init_db(strict=True)
# nginx は JSON を圧縮しない。決算速報のピーク日は一覧が数百 KB になる
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.middleware("http")
async def revalidate_spa_assets(request: Request, call_next):
    # Cache-Control が無いと Safari は Last-Modified から推測して古い JS を使い続け、
    # API の形が変わったときに画面が壊れる。毎回 ETag で確かめさせる (変わらなければ 304)
    response = await call_next(request)
    path = request.url.path
    if (path.startswith("/static/") or path.rstrip("/") == "/app") \
            and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-cache"
    return response
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


@app.get("/static/ir_monthly_codes.json")
def ir_monthly_codes():
    return {"codes": load_ir_monthly_codes()}


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
    html = version_static_urls((STATIC / "index.html").read_text(encoding="utf-8"))
    return HTMLResponse(html, headers={"ETag": html_etag(html)})


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
    return HTMLResponse(version_static_urls(top_html(request, list_tables_payload(db))))


@app.get("/monthly/{code}")
def public_monthly_detail(code: str, request: Request, db: Session = Depends(get_session)):
    raw = code.strip().upper()
    parsed = _parse_public_code(raw)
    if parsed is None:
        return HTMLResponse(version_static_urls(not_found_html(request)), status_code=404)
    if len(raw) == 5:
        return RedirectResponse(f"/monthly/{code4(parsed)}", status_code=301)
    issuers = list_tables_payload(db)
    issuer = next((row for row in issuers if row["code"] == parsed), None)
    if issuer is None:
        return HTMLResponse(version_static_urls(not_found_html(request)), status_code=404)
    tables = []
    for year in display_years(issuer.get("years") or []):
        try:
            tables.append(table_year_payload(db, parsed, year))
        except ApiError:
            continue
    return HTMLResponse(version_static_urls(detail_html(request, issuer, tables)))
