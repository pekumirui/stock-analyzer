"""公開月次 HTML / robots / sitemap。数値は IR・TDnet。J-Quants の株価・決算は出さない。"""
from __future__ import annotations

import json
from html import escape

from fastapi import Request

from ..config import PUBLIC_ORIGIN, PUBLIC_SITE_NAME
from .ingest import format_monthly_cell

DISCLAIMER = (
    "個人の学習・分析用です。投資判断は自己責任で行ってください。"
    "数値は各社 IR・TDnet の開示の転記です。"
)
SOURCE = "出典: 各社 IR・TDnet"


def code4(code: str) -> str:
    return str(code or "")[:4]


def public_origin(request: Request) -> str:
    if PUBLIC_ORIGIN:
        return PUBLIC_ORIGIN
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"


def issuer_label(code: str, name: str | None) -> str:
    short = "".join(list(name or "")[:5])
    return f"{code4(code)} {short}".strip()


def fmt_yoy_list(value: float | None) -> str:
    if value is None:
        return "-"
    n = float(value)
    if n != n or n in (float("inf"), float("-inf")):
        return "-"
    text = f"{n:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def display_years(years: list[int]) -> list[int]:
    ys = sorted({int(y) for y in years}, reverse=True)
    if not ys:
        return []
    latest = ys[0]
    return [y for y in ys if y >= latest - 2]


def _ld(obj: dict) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _shell(
    *,
    origin: str,
    canonical: str,
    title: str,
    description: str,
    json_ld: dict,
    main: str,
    top_hidden: bool,
    detail_hidden: bool,
    status_note: str = "",
) -> str:
    can = escape(canonical)
    t = escape(title)
    d = escape(description)
    note = f'<p class="sub">{escape(status_note)}</p>' if status_note else ""
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{t}</title>
<meta name="description" content="{d}">
<link rel="canonical" href="{can}">
<meta property="og:type" content="website">
<meta property="og:locale" content="ja_JP">
<meta property="og:site_name" content="{escape(PUBLIC_SITE_NAME)}">
<meta property="og:title" content="{t}">
<meta property="og:description" content="{d}">
<meta property="og:url" content="{can}">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/css/app.css">
<script type="application/ld+json">{_ld(json_ld)}</script>
</head>
<body class="public-monthly" data-public-monthly="1">
<header>
  <a class="brand" href="/monthly">{escape(PUBLIC_SITE_NAME)}</a>
</header>
<main>
<section id="tab-monthly">
  <div id="monthly-top"{' style="display:none"' if top_hidden else ""}>
    <div class="phead"><h1>月次売上</h1></div>
    {note}
    <div class="row" style="position:relative;margin:0 0 10px">
      <input id="monthly-sym" placeholder="銘柄名 or コード" autocomplete="off" style="width:200px">
      <div id="monthly-suggest" class="suggest" style="display:none"></div>
    </div>
    <div id="monthly-groups"></div>
    <div class="tbl-wrap" id="monthly-list">{main if not top_hidden else ""}</div>
  </div>
  <div id="monthly-detail"{' style="display:none"' if detail_hidden else ""}>
      <div class="phead">
        <a href="/monthly" id="monthly-back">戻る</a>
        <h1 id="monthly-title"></h1>
      </div>
      <select id="monthly-series" onchange="setMonthlySeries(this.value)"></select>
      <div id="monthly-chart"></div>
      <div class="tbl-wrap" id="monthly-table-wrap">{main if not detail_hidden else ""}</div>
  </div>
</section>
</main>
<footer class="site-foot">{escape(DISCLAIMER)} {escape(SOURCE)}</footer>
<script src="/static/js/api.js"></script>
<script src="/static/js/monthly.js"></script>
<script src="/static/js/monthly-public.js"></script>
</body>
</html>
"""


def render_top_list(issuers: list[dict]) -> str:
    if not issuers:
        return '<div class="sub">登録された月次データはありません</div>'
    rows = []
    for r in issuers:
        code = str(r.get("code") or "")
        href = f"/monthly/{escape(code4(code))}"
        label = escape(issuer_label(code, r.get("name")))
        neu = '<span class="badge new">NEW</span>' if r.get("ingested_today") else ""
        ind = escape(str(r.get("industry") or "その他"))
        all_y = escape(fmt_yoy_list(r.get("all_store_yoy")))
        ex_y = escape(fmt_yoy_list(r.get("existing_store_yoy")))
        rows.append(
            f'<tr data-code="{escape(code)}">'
            f'<td class="l name"><a href="{href}">{label}</a>{neu}</td>'
            f'<td class="l">{ind}</td>'
            f'<td class="num">{all_y}</td>'
            f'<td class="num">{ex_y}</td>'
            f"</tr>"
        )
    body = "".join(rows)
    return (
        '<table id="monthly-tbl"><thead><tr>'
        '<th class="l">会社名</th><th class="l">月次業種</th>'
        "<th>全店(前年比)</th><th>既存店(前年比)</th>"
        f"</tr></thead><tbody>{body}</tbody></table>"
    )


def render_year_tables(tables: list[dict]) -> str:
    chunks: list[str] = []
    for d in tables:
        year = d.get("year")
        elems = d.get("elements") or []
        if not elems:
            continue
        head = (
            f'<table class="fin monthly"><thead><tr>'
            f'<th class="l year">{escape(str(year))}</th>'
        )
        for m in range(1, 13):
            head += f"<th>{m}月</th>"
        head += "</tr></thead><tbody>"
        body = []
        for e in elems:
            name = e.get("label", {}).get("display") or e.get("element") or ""
            unit = e.get("unit") or "number"
            vals = e.get("values") or {}
            cells = "".join(
                f'<td class="num">{escape(format_monthly_cell(vals.get(str(m)), unit, name))}</td>'
                for m in range(1, 13)
            )
            body.append(f'<tr><td class="l">{escape(str(name))}</td>{cells}</tr>')
        chunks.append(head + "".join(body) + "</tbody></table>")
    return "".join(chunks) or '<div class="sub">データなし</div>'


def top_html(request: Request, issuers: list[dict]) -> str:
    origin = public_origin(request)
    canonical = f"{origin}/monthly"
    desc = f"上場企業の月次売上（前年比）を業種別に一覧します。{SOURCE}"
    return _shell(
        origin=origin,
        canonical=canonical,
        title=f"月次売上 | {PUBLIC_SITE_NAME}",
        description=desc,
        json_ld={
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": PUBLIC_SITE_NAME,
            "url": canonical,
            "inLanguage": "ja",
        },
        main=render_top_list(issuers),
        top_hidden=False,
        detail_hidden=True,
    )


def detail_html(request: Request, issuer: dict, tables: list[dict]) -> str:
    origin = public_origin(request)
    code = str(issuer.get("code") or "")
    c4 = code4(code)
    name = str(issuer.get("name") or c4)
    canonical = f"{origin}/monthly/{c4}"
    all_y = fmt_yoy_list(issuer.get("all_store_yoy"))
    ex_y = fmt_yoy_list(issuer.get("existing_store_yoy"))
    desc = (
        f"{name}（{c4}）の月次売上。全店(前年比) {all_y}、既存店(前年比) {ex_y}。"
        f"{SOURCE}"
    )
    title_h = f"{name} {c4}"
    summary = (
        f'<p class="sub">全店(前年比) {escape(all_y)}　既存店(前年比) {escape(ex_y)}</p>'
        + render_year_tables(tables)
    )
    page = _shell(
        origin=origin,
        canonical=canonical,
        title=f"{name}（{c4}）の月次売上 | {PUBLIC_SITE_NAME}",
        description=desc,
        json_ld={
            "@context": "https://schema.org",
            "@type": "Dataset",
            "name": f"{name}の月次売上",
            "url": canonical,
            "inLanguage": "ja",
            "isAccessibleForFree": True,
        },
        main=summary,
        top_hidden=True,
        detail_hidden=False,
    )
    return page.replace(
        '<h1 id="monthly-title"></h1>',
        f'<h1 id="monthly-title">{escape(title_h)}</h1>',
    )


def not_found_html(request: Request) -> str:
    origin = public_origin(request)
    canonical = f"{origin}/monthly"
    return _shell(
        origin=origin,
        canonical=canonical,
        title=f"見つかりません | {PUBLIC_SITE_NAME}",
        description="指定した発行体の月次データはありません。",
        json_ld={
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": PUBLIC_SITE_NAME,
            "url": canonical,
        },
        main="",
        top_hidden=False,
        detail_hidden=True,
        status_note="発行体が見つかりません。",
    )


def robots_txt(origin: str) -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "Allow: /monthly\n"
        "Allow: /static/\n"
        "Disallow: /app\n"
        "Disallow: /api/\n"
        "Disallow: /docs\n"
        "Disallow: /redoc\n"
        "Disallow: /openapi.json\n"
        "Disallow: /mcp\n"
        "Disallow: /oauth\n"
        f"Sitemap: {origin}/sitemap.xml\n"
    )


def sitemap_xml(origin: str, issuers: list[dict]) -> str:
    urls = [
        f"  <url><loc>{escape(origin)}/monthly</loc><changefreq>daily</changefreq></url>"
    ]
    for r in issuers:
        code = str(r.get("code") or "")
        if not code:
            continue
        loc = f"{origin}/monthly/{code4(code)}"
        last = r.get("last_ingested_at")
        lastmod = ""
        if isinstance(last, str) and last:
            lastmod = f"<lastmod>{escape(last[:10])}</lastmod>"
        urls.append(
            f"  <url><loc>{escape(loc)}</loc>{lastmod}<changefreq>weekly</changefreq></url>"
        )
    body = "\n".join(urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{body}\n"
        "</urlset>\n"
    )
