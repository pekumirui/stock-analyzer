"""公開月次 HTML / robots / sitemap / 書き込み 403。"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

_tmp = tempfile.mkdtemp(prefix="stock-analyzer-public-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/stock.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


def _fail(msg: str) -> None:
    raise SystemExit(msg)


def _expect(resp, status: int, label: str) -> None:
    if resp.status_code != status:
        _fail(f"{label}: {resp.status_code} {resp.text[:400]}")


def _seed() -> None:
    from app.db import SessionLocal, init_db
    from app.models import MonthlyElement, MonthlyTable, MonthlyValue, Stock

    init_db()
    db = SessionLocal()
    try:
        db.add(Stock(code="72030", name="トヨタ自動車", market="プライム", sector33="輸送用機器"))
        db.add(Stock(code="11110", name="テスト</script>社", market="プライム", sector33="その他"))
        db.add(MonthlyTable(code="72030", year=2025))
        db.add(MonthlyTable(code="11110", year=2025))
        db.add(MonthlyElement(
            code="72030", year=2025, element="全店 売上高(前年比)",
            sort_order=1, unit="yoy_index",
        ))
        db.add(MonthlyElement(
            code="11110", year=2025, element="全店 売上高(前年比)",
            sort_order=1, unit="yoy_index",
        ))
        db.add(MonthlyValue(
            code="72030", year=2025, element="全店 売上高(前年比)",
            month=4, value=105.2,
        ))
        db.add(MonthlyValue(
            code="11110", year=2025, element="全店 売上高(前年比)",
            month=4, value=100.0,
        ))
        db.commit()
    finally:
        db.close()


def test_public_monthly() -> None:
    _seed()
    c = TestClient(app)

    r = c.get("/", follow_redirects=False)
    _expect(r, 301, "GET /")
    loc = r.headers.get("location") or ""
    if not loc.endswith("/monthly"):
        _fail(f"GET / Location: {loc!r}")

    r = c.get("/app")
    _expect(r, 200, "GET /app")
    if "週足" not in r.text or "<title>株式分析ツール</title>" not in r.text:
        _fail("GET /app is not the private SPA")
    if 'id="monthly-layout-debug"' in r.text or 'id="monthly-view-debug"' in r.text:
        _fail("private SPA must not keep the years-view / gate debug selects (timeline only)")
    # iOS Safari はメモリの古い CSS を確かめずに使うので、/static は中身のハッシュ付き URL で出す
    m = re.search(r'href="/static/css/app\.css\?v=([0-9a-f]{12})"', r.text)
    if not m or not re.search(r'src="/static/js/monthly\.js\?v=[0-9a-f]{12}"', r.text):
        _fail("GET /app must version /static URLs with ?v=<hash>")
    if not r.headers.get("etag"):
        _fail("GET /app must send an ETag (boot.js compares it)")
    if c.get(f"/static/css/app.css?v={m.group(1)}").status_code != 200:
        _fail("versioned /static URL must still be served")

    r = c.get("/monthly")
    _expect(r, 200, "GET /monthly")
    if "株KPI" not in r.text:
        _fail("public TOP missing site name")
    if 'rel="canonical"' not in r.text or "/monthly" not in r.text:
        _fail("public TOP missing canonical")
    if "トヨタ" not in r.text:
        _fail("public TOP missing issuer name")
    if "/monthly/7203" not in r.text:
        _fail("public TOP missing code4 link")
    if "東証P" in r.text or "輸送用機器" in r.text:
        _fail("public TOP must not show J-Quants market/sector33")
    if "lightweight-charts" in r.text:
        _fail("public HTML must not load chart CDN")
    if "ゲート（一時）" in r.text or "IR（一時）" in r.text or 'id="monthly-layout-debug"' in r.text:
        _fail("public TOP must not show TEMP debug UI")
    if "data-public-monthly" not in r.text:
        _fail("public body flag missing")
    if 'id="monthly-value-tabs"' in r.text or "setMonthlyValueMode" in r.text:
        _fail("public HTML must not carry value tabs")
    if not re.search(r'href="/static/css/app\.css\?v=[0-9a-f]{12}"', r.text):
        _fail("public /monthly must version /static URLs with ?v=<hash>")

    r = c.get("/monthly/72030", follow_redirects=False)
    _expect(r, 301, "GET /monthly/72030")
    if not (r.headers.get("location") or "").endswith("/monthly/7203"):
        _fail(f"5-digit redirect: {r.headers.get('location')!r}")

    r = c.get("/monthly/7203")
    _expect(r, 200, "GET /monthly/7203")
    if "トヨタ自動車" not in r.text:
        _fail("detail missing name")
    if "105.2" not in r.text:
        _fail("detail missing SSR value")
    if 'rel="canonical"' not in r.text:
        _fail("detail missing canonical")

    from app.monthly.public import _ld
    payload = {"name": "テスト</script>社 & 他"}
    encoded = _ld(payload)
    if "<" in encoded or ">" in encoded or "&" in encoded:
        _fail(f"ld markup: {encoded}")
    if json.loads(encoded) != payload:
        _fail(f"ld roundtrip: {json.loads(encoded)!r}")

    r = c.get("/monthly/1111")
    _expect(r, 200, "GET /monthly/1111")
    m_ld = re.search(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.S)
    if not m_ld:
        _fail("script-name detail missing jsonld")
    body = m_ld.group(1)
    if "<" in body or ">" in body:
        _fail(f"jsonld still has angle brackets: {body}")
    obj = json.loads(body)
    if "テスト</script>社" not in str(obj.get("name")):
        _fail(f"jsonld name roundtrip: {obj!r}")
    if '<h1 id="monthly-title">テスト&lt;/script&gt;社 1111</h1>' not in r.text:
        _fail("escaped h1 missing on script-name detail")

    r = c.get("/monthly/9999")
    _expect(r, 404, "unknown issuer")
    if "見つかりません" not in r.text:
        _fail("404 page text")

    r = c.get("/monthly/nope")
    _expect(r, 404, "invalid code")

    r = c.get("/robots.txt")
    _expect(r, 200, "robots")
    if "Disallow: /app" not in r.text or "Sitemap:" not in r.text:
        _fail(f"robots.txt: {r.text!r}")
    if "Allow: /monthly" not in r.text:
        _fail("robots must allow /monthly")

    r = c.get("/sitemap.xml")
    _expect(r, 200, "sitemap")
    if "/monthly/7203" not in r.text or "/monthly<" not in r.text.replace(" ", ""):
        if "/monthly</loc>" not in r.text:
            _fail(f"sitemap missing TOP or code4: {r.text[:400]}")

    r = c.get("/api/v1/tables")
    _expect(r, 200, "GET tables")
    if r.headers.get("cache-control") != "public, max-age=60":
        _fail(f"cache-control: {r.headers.get('cache-control')!r}")

    r = c.post(
        "/api/v1/tables/72030/2025/elements",
        json={"element": "店舗数", "unit": "number"},
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    _expect(r, 403, "proxied POST")
    if r.json().get("error", {}).get("code") != "FORBIDDEN":
        _fail(f"proxied POST body: {r.json()!r}")

    r = c.post("/api/v1/tables/72030/2025/elements", json={"element": "店舗数", "unit": "number"})
    if r.status_code not in (200, 201):
        _fail(f"loopback POST should work: {r.status_code} {r.text[:200]}")

    print("ok: public monthly")


if __name__ == "__main__":
    test_public_monthly()
