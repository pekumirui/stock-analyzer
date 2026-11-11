"""Read-only earnings tools for agents: the same numbers as the 決算速報 screen, compacted to text.

Rows come from the FastAPI on 127.0.0.1 (same calculation as the screen). Close / market cap /
post-earnings returns come from stock.db read-only. No ranking or scoring here.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
import urllib.error
import urllib.request
from datetime import date
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlparse

DAY_TIMEOUT = 60
APP_TIMEOUT = 20
DEFAULT_DAY_LIMIT = 150
MAX_DAY_LIMIT = 1000
DEFAULT_DOC_LENGTH = 20000
MAX_DOC_LENGTH = 100000
SORTS = ("time", "pts_up", "pts_down", "pts_abs")
QUARTERS = ("1Q", "2Q", "3Q", "4Q")
PERIOD_Q = {"1Q": "1Q", "2Q": "2Q", "3Q": "3Q", "4Q": "4Q", "FY": "4Q", "5Q": "4Q"}
METRICS = (("net_sales", "売上"), ("operating_profit", "営利"), ("ordinary_profit", "経利"), ("profit", "純利"))
DETAIL_METRICS = (("net_sales", "売上"), ("gross_profit", "粗利"), ("operating_profit", "営利"),
                  ("ordinary_profit", "経利"), ("profit", "純利"), ("eps", "EPS"))
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DISCLOSURE_RE = re.compile(r"^[0-9A-Za-z]{1,32}$")
DECK_KEY_RE = re.compile(r"^[0-9A-Za-z_-]{1,80}$")
PATH_RE = re.compile(r"^/api/[A-Za-z0-9_\-/]+$")
FS_STATEMENTS = ("BS", "PL", "CF")
FS_DEFAULT_PERIODS = 8
FS_MAX_PERIODS = 20

GetJson = Callable[[str, dict[str, Any] | None, int], Any]
GetText = Callable[[str, int], str]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ARG002
        return None


def app_get(base: str, path: str, params: dict[str, Any] | None, timeout: int) -> tuple[int, str]:
    """GET base+path on loopback. base is checked by the caller (app_http_base)."""
    if not PATH_RE.fullmatch(path) or ".." in path:
        raise ValueError("invalid app API path")
    url = base + path
    if params:
        url += "?" + urlencode({k: v for k, v in params.items() if v is not None})
    if urlparse(url).path != path:
        raise ValueError("invalid app API path")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with opener.open(req, timeout=timeout) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read() if exc.fp is not None else b""
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, TimeoutError) or "timed out" in str(exc.reason).lower():
            raise ValueError(f"FastAPI timeout after {timeout}s at {base}") from None
        raise ValueError(f"FastAPI unreachable at {base}: {exc.reason}") from None
    except TimeoutError:
        raise ValueError(f"FastAPI timeout after {timeout}s at {base}") from None
    except OSError as exc:
        raise ValueError(f"FastAPI unreachable at {base}: {exc}") from None
    return status, raw.decode("utf-8", errors="replace") if raw else ""


def make_getters(base_fn: Callable[[], str]) -> tuple[GetJson, GetText]:
    def fetch(path: str, params: dict[str, Any] | None, timeout: int) -> str:
        status, text = app_get(base_fn(), path, params, timeout)
        if status >= 400:
            raise ValueError(f"HTTP {status} {path}\n{text[:500]}")
        return text

    def get_json(path: str, params: dict[str, Any] | None, timeout: int) -> Any:
        return json.loads(fetch(path, params, timeout))

    def get_text(path: str, timeout: int) -> str:
        return fetch(path, None, timeout)

    return get_json, get_text


def parse_date(raw: Any, label: str = "date", default: date | None = None) -> date:
    if raw is None or raw == "":
        if default is None:
            raise ValueError(f"{label} is required (YYYY-MM-DD)")
        return default
    s = str(raw).strip()
    if not DATE_RE.fullmatch(s):
        raise ValueError(f"{label} must be YYYY-MM-DD")
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{label} must be a valid date") from None


def _clamp(raw: Any, default: int, lo: int, hi: int, label: str) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool):
        raise ValueError(f"{label} must be an integer {lo}-{hi}")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be an integer {lo}-{hi}") from None
    if n < lo:
        raise ValueError(f"{label} must be an integer {lo}-{hi}")
    return min(n, hi)


def _str_list(raw: Any, label: str) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [p for p in raw.split(",")]
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise ValueError(f"{label} must be an array of strings")
    return [x.strip() for x in raw if x.strip()]


def code4(code5: str) -> str:
    return code5[:4] if len(code5) == 5 and code5.endswith("0") else code5


def _mn(v: Any) -> str:
    """円 → 百万円。"""
    if v is None:
        return ""
    return f"{v / 1e6:,.0f}"


def _pct(v: Any) -> str:
    return "" if v is None else f"{v:+.1f}"


def _num(v: Any, digits: int = 1) -> str:
    return "" if v is None else f"{v:.{digits}f}"


def _cell(s: Any) -> str:
    return str(s or "").replace("\t", " ").replace("\n", " ")


def _tsv(rows: list[list[Any]]) -> str:
    return "\n".join("\t".join(_cell(c) for c in r) for r in rows)


def _growth(g: Any, key: str) -> Any:
    if not isinstance(g, dict):
        return None
    v = g.get(key)
    return v.get("pct") if isinstance(v, dict) else None


def _fye(iso: str | None) -> str:
    if not iso:
        return ""
    y, m, _ = iso.split("-")
    return f"{y[2:]}.{int(m)}"


def next_year_eps(conn: sqlite3.Connection, disclosure_nos: list[str]) -> dict[str, float]:
    """本決算の来期予想EPS。列に無く raw_json の NxFEPS にだけある（app/market/analytics.next_year_forecast）。"""
    out: dict[str, float] = {}
    for i in range(0, len(disclosure_nos), 500):
        chunk = disclosure_nos[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for no, raw in conn.execute(
            f"SELECT disclosure_no, json_extract(raw_json, '$.NxFEPS') FROM statements "  # nosec B608
            f"WHERE disclosure_no IN ({marks}) AND json_valid(raw_json)",
            tuple(chunk),
        ):
            try:
                out[no] = float(raw)
            except (TypeError, ValueError):
                continue
    return out


def market_rows(conn: sqlite3.Connection, codes: list[str], day: date) -> dict[str, dict[str, Any]]:
    """その日以前の直近の終値と時価総額（百万円）、その日の決算後騰落（earnings_events）。"""
    out: dict[str, dict[str, Any]] = {c: {} for c in codes}
    if not codes:
        return out
    iso = day.isoformat()
    for i in range(0, len(codes), 500):
        chunk = codes[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(
            f"SELECT d.code, d.date, d.close, d.market_cap FROM daily_prices d "  # nosec B608
            f"WHERE d.code IN ({marks}) AND d.date = (SELECT MAX(date) FROM daily_prices "
            f"WHERE code = d.code AND date <= ? AND close IS NOT NULL)",
            (*chunk, iso),
        ):
            out[r[0]].update(close_date=r[1], close=r[2], market_cap=r[3])
        for r in conn.execute(
            f"SELECT code, return_1d, alpha_1d, return_5d, alpha_5d FROM earnings_events "  # nosec B608
            f"WHERE code IN ({marks}) AND disclosed_date = ?",
            (*chunk, iso),
        ):
            out[r[0]].update(r1d=r[1], a1d=r[2], r5d=r[3], a5d=r[4])
    return out


def _quarter(period: str | None) -> str:
    return PERIOD_Q.get(period or "", period or "")


def _pts_chg(row: dict[str, Any]) -> float | None:
    p = row.get("pts")
    return p.get("chg_pct") if isinstance(p, dict) else None


def _per(close: Any, eps: Any) -> float | None:
    if close is None or not eps or eps <= 0:
        return None
    return close / eps


def _fc_eps(r: dict[str, Any], nx_eps: dict[str, float]) -> Any:
    if _quarter(r.get("period")) == "4Q":
        return nx_eps.get(r.get("disclosure_no") or "")
    fc = r.get("forecast_fy")
    return fc.get("eps") if isinstance(fc, dict) else None


def _actual_line(r: dict[str, Any], mk: dict[str, Any], eps: Any) -> list[Any]:
    rev = r.get("forecast_revision")
    rev_s = ""
    if isinstance(rev, dict) and rev.get("direction"):
        rev_s = ("上方" if rev["direction"] == "up" else "下方") + (_pct(rev.get("pct")) if rev.get("pct") is not None else "")
    cum, single = r.get("cumulative") or {}, r.get("single") or {}
    yoy, qoq = r.get("yoy") or {}, r.get("qoq") or {}
    prog = r.get("progress") or {}
    cur, prev = prog.get("cur") or {}, prog.get("prev") or {}
    mc = mk.get("market_cap")
    line = [
        code4(r["code"]), r.get("name"), r.get("sector33"), (r.get("disclosed_time") or "")[:5],
        _fye(r.get("fiscal_year_end")), _quarter(r.get("period")),
        rev_s, "訂正" if r.get("restated") else "",
        _pct(_pts_chg(r)), _num(mk.get("close"), 0), "" if mc is None else f"{mc / 100:,.0f}",
        _num(_per(mk.get("close"), eps)),
    ]
    for key, _ in METRICS:
        line += [_mn(cum.get(key)), _pct(_growth(yoy, key)), _mn(single.get(key)), _pct(_growth(qoq, key))]
    line += [_num(cur.get("operating_profit")), _num(prev.get("operating_profit")),
             _pct(mk.get("r1d")), _pct(mk.get("a1d")), r.get("disclosure_no")]
    return line


ACTUAL_HEAD = ["code", "name", "sector", "time", "fye", "Q", "通期営利予想修正", "訂正",
               "PTS%", "終値", "時価総額億", "予想PER"]
for _key, _label in METRICS:
    ACTUAL_HEAD += [f"{_label}累計", f"{_label}YoY%", f"{_label}3か月", f"{_label}QoQ%"]
ACTUAL_HEAD += ["営利進捗%", "前年同期進捗%", "翌日騰落%", "翌日対TOPIX%", "disclosure_no"]
SCHED_HEAD = ["code", "name", "sector", "予定の期", "PTS%", "終値", "時価総額億", "直近実績", "直近営利YoY%", "直近営利QoQ%"]


def _sort_rows(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    if sort == "time":
        return rows
    def key(r: dict[str, Any]) -> tuple[int, float]:
        v = _pts_chg(r)
        if v is None:
            return (1, 0.0)
        return (0, -v if sort == "pts_up" else v if sort == "pts_down" else -abs(v))
    return sorted(rows, key=key)


def tool_earnings_day(args: dict[str, Any], get_json: GetJson, open_db: Callable[[], sqlite3.Connection]) -> str:
    day = parse_date(args.get("date"), default=date.today())
    today = parse_date(args.get("today"), "today", default=date.today())
    sort = str(args.get("sort") or "time")
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {', '.join(SORTS)}")
    sectors = set(_str_list(args.get("sectors"), "sectors"))
    quarters = set(_str_list(args.get("quarters"), "quarters"))
    if quarters - set(QUARTERS):
        raise ValueError("quarters must be from 1Q, 2Q, 3Q, 4Q (FY and 5Q count as 4Q)")
    codes_only = {c.upper() for c in _str_list(args.get("codes"), "codes")}
    offset = _clamp(args.get("offset"), 0, 0, 100000, "offset")
    limit = _clamp(args.get("limit"), DEFAULT_DAY_LIMIT, 1, MAX_DAY_LIMIT, "limit")

    payload = get_json("/api/earnings", {"date": day.isoformat(), "today": today.isoformat()}, DAY_TIMEOUT)
    actuals = payload.get("actuals") or []
    scheduled = payload.get("scheduled") or []

    def keep(r: dict[str, Any], qs: list[str]) -> bool:
        if sectors and (r.get("sector33") or "未分類") not in sectors:
            return False
        if quarters and not any(_quarter(q) in quarters for q in qs):
            return False
        if codes_only and code4(r["code"]) not in codes_only and r["code"] not in codes_only:
            return False
        return True

    acts = [r for r in actuals if keep(r, [r.get("period")])]
    scheds = [r for r in scheduled if keep(r, [p.get("fq") for p in r.get("periods") or []])]
    acts = _sort_rows(acts, sort)
    scheds = _sort_rows(scheds, sort)
    page_a = acts[offset:offset + limit]
    rest = max(0, limit - len(page_a))
    s_off = max(0, offset - len(acts))
    page_s = scheds[s_off:s_off + rest]

    conn = open_db()
    try:
        mk = market_rows(conn, sorted({r["code"] for r in page_a + page_s}), day)
        nx_eps = next_year_eps(conn, [r["disclosure_no"] for r in page_a
                                      if _quarter(r.get("period")) == "4Q" and r.get("disclosure_no")])
    finally:
        conn.close()

    with_pts = sum(1 for r in actuals + scheduled if _pts_chg(r) is not None)
    as_of = next((r["pts"].get("as_of") for r in actuals + scheduled if isinstance(r.get("pts"), dict)), None)
    shown = len(page_a) + len(page_s)
    total = len(acts) + len(scheds)
    lines = [
        (f"date={day.isoformat()} kind={payload.get('kind')} 開示={len(actuals)}件 未発表={len(scheduled)}社 "
        f"PTSあり={with_pts}（株探 PTS ランキング各75位以内だけ。PTS% が空は載らなかった銘柄） PTS時刻={as_of or '-'}"),
        f"絞り込み後 {total} 行（開示 {len(acts)} / 未発表 {len(scheds)}）。この応答は {offset + 1}〜{offset + shown} 行目。sort={sort}",
        ("金額は百万円。YoY は累計の前年同期比、QoQ は 3か月（Q単独）の前四半期比。進捗% は 1Q〜3Q の累計 ÷ 通期予想（前年同期は前期実績が分母）。"
        "予想PER は終値 ÷ 通期予想EPS（本決算と 5Q は来期予想）。終値・時価総額はその日以前の直近。翌日騰落は決算後に買えた値から（場中は翌営業日始値）。"),
    ]
    if offset + shown < total:
        lines.append(f"続きは offset={offset + shown}")
    if page_a:
        lines += ["", "## 開示", _tsv([ACTUAL_HEAD] + [_actual_line(r, mk.get(r["code"], {}), _fc_eps(r, nx_eps)) for r in page_a])]
    if page_s:
        body = [SCHED_HEAD]
        for r in page_s:
            m = mk.get(r["code"], {})
            lt = r.get("latest") or {}
            mc = m.get("market_cap")
            body.append([
                code4(r["code"]), r.get("name"), r.get("sector33"),
                " ".join(f"{str(p.get('label') or '').removeprefix('FY')}{p.get('fq') or ''}" for p in r.get("periods") or []),
                _pct(_pts_chg(r)), _num(m.get("close"), 0), "" if mc is None else f"{mc / 100:,.0f}",
                f"{_fye(lt.get('fiscal_year_end'))} {_quarter(lt.get('period'))}".strip() if lt else "",
                _pct(_growth(lt.get("yoy"), "operating_profit")), _pct(_growth(lt.get("qoq"), "operating_profit")),
            ])
        lines += ["", "## 未発表", _tsv(body)]
    if not page_a and not page_s:
        lines += ["", "行がありません。" if total == 0 else "この offset より後に行はありません。"]
    return "\n".join(lines)


def _detail_rows(rows: list[dict[str, Any]], metrics: tuple[tuple[str, str], ...], progress: bool = False) -> str:
    head = ["期", "Q", "種類", "発表日"]
    for _, label in metrics:
        head += [f"{label}進捗%", f"{label}累計", f"{label}分母"] if progress else [label, f"{label}増減%"]
    out = [head]
    for r in rows:
        v, g = r.get("values") or {}, r.get("growth")
        base, prog = r.get("base") or {}, r.get("progress") or {}
        line = [r.get("range"), r.get("period") or "", r.get("kind"), r.get("disclosed_date")]
        for key, _ in metrics:
            fmt = (lambda x: _num(x, 2)) if key == "eps" else _mn
            if progress:
                line += [_num(prog.get(key)), fmt(v.get(key)), fmt(base.get(key))]
            else:
                line += [fmt(v.get(key)), _pct(_growth(g, key))]
        out.append(line)
    return _tsv(out)


HALF_FORECAST_NOTE = {
    "fy_only": "中間予想は会社が出していない（通期予想だけ）",
    "none": "中間予想は会社が出していない（通期予想も無い）",
    "unknown": "中間予想は元データに項目が無く、出しているかは不明",
}


def tool_earnings_detail(args: dict[str, Any], get_json: GetJson, code5: str) -> str:
    params: dict[str, Any] = {}
    no = args.get("disclosure_no")
    if no not in (None, ""):
        if not DISCLOSURE_RE.fullmatch(str(no)):
            raise ValueError("disclosure_no must be alphanumeric")
        params["disclosure_no"] = str(no)
    d = get_json(f"/api/earnings/detail/{quote(code5, safe='')}", params or None, APP_TIMEOUT)
    notes = d.get("notes") or {}
    note = []
    if notes.get("no_ordinary"):
        note.append("経常利益なし（IFRS / 米国基準）")
    if notes.get("single_unavailable"):
        note.append("3か月は算出不能（決算期変更など）")
    half = HALF_FORECAST_NOTE.get(notes.get("half_forecast") or "")
    if half:
        whose = "" if "_" in str(d.get("label") or "_") else "来期の"
        note.append(whose + half)
    prog_metrics = tuple(m for m in DETAIL_METRICS if m[0] != "gross_profit")
    parts = [
        (f"{code4(d.get('code') or code5)} {d.get('name')} {d.get('label')} 発表 {d.get('disclosed_date')} "
        f"{(d.get('disclosed_time') or '')[:5]} disclosure_no={d.get('disclosure_no')} 予想の出所={d.get('revision_source')}"),
        ("金額は百万円、EPS は円。増減% は累計が前年同期比、3か月が前年同期の 3か月比、修正履歴が前の行からの修正率。"
        "種類: actual=過去の同じ四半期, actual_current=この開示, forecast_h=中間予想, forecast_fy=通期予想, "
        "actual_fy_prev=前期通期, single=Q単独, forecast_initial=期初予想, forecast=予想（期初が取れない年度の最初）, "
        "forecast_revised=修正, actual_fy=通期実績。"),
    ]
    if note:
        parts.append("注: " + " / ".join(note))
    parts += ["", "## 累計", _detail_rows(d.get("cumulative") or [], DETAIL_METRICS)]
    parts += ["", "## 3か月", _detail_rows(d.get("quarterly") or [], DETAIL_METRICS)]
    if d.get("progress"):
        parts += ["", "## 進捗率（累計 ÷ 分母。分母は過去の年度が通期実績、actual_current が当期の通期予想）",
                  _detail_rows(d.get("progress") or [], prog_metrics, progress=True)]
    parts += ["", "## 通期予想の修正履歴", _detail_rows(d.get("revisions") or [], prog_metrics)]
    return "\n".join(parts)


def tool_earnings_docs(get_json: GetJson, code5: str) -> str:
    c = quote(code5, safe="")
    f = get_json(f"/api/stock/{c}/filings", None, APP_TIMEOUT)
    k = get_json(f"/api/stock/{c}/decks", None, APP_TIMEOUT)
    rows = [["日時", "種類", "訂正", "題名", "kind", "id", "seq/部分"]]
    for x in f.get("filings") or []:
        for doc in x.get("documents") or []:
            if x.get("source") == "pdf":
                rows.append([x.get("submitted_at"), x.get("doc_type"), "訂正" if x.get("is_amendment") else "",
                             x.get("title"), "pdf", x.get("disclosure_no"), f"{doc.get('pages') or ''}ページ"])
            else:
                rows.append([x.get("submitted_at"), x.get("doc_type"), "訂正" if x.get("is_amendment") else "",
                             x.get("title"), "filing", x.get("id"), f"{doc.get('seq')} {doc.get('part') or ''}".strip()])
    decks = [["公開日", "題名", "kind", "key", "読み取り", "ページ数"]]
    for x in k.get("decks") or []:
        decks.append([x.get("pubdate"), x.get("title"), "deck", x.get("key"), x.get("status") or "未", x.get("pages") or ""])
    out = [f"{code4(code5)} {f.get('name')} {f.get('sector33') or ''} {f.get('market') or ''}",
           "本文は earnings_doc_read に kind と id（filing は seq も、deck は key）を渡す。"]
    out += ["", "## 短信・開示", _tsv(rows) if len(rows) > 1 else "なし"]
    out += ["", "## 決算説明資料（読み取りが done のものだけ中身が読める）", _tsv(decks) if len(decks) > 1 else "なし"]
    return "\n".join(out)


class _Text(HTMLParser):
    BLOCK = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "text"})
    SKIP = frozenset({"script", "style", "head"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:  # noqa: ARG002
        if tag in self.SKIP:
            self._skip += 1
        elif tag in ("td", "th"):
            self.parts.append("\t")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(raw: str) -> str:
    p = _Text()
    p.feed(raw)
    text = html.unescape("".join(p.parts)).replace("　", " ").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", lambda m: "\t" if "\t" in m.group(0) else " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    return "\n".join(out).strip()


def _deck_text(d: dict[str, Any]) -> str:
    head = d.get("document") or {}
    out = [f"{head.get('pubdate')} {head.get('title')} 読み取り={head.get('status') or '未'}"]
    if not d.get("pages"):
        out.append("中身はまだ読み取られていません。")
        return "\n".join(out)
    for p in d["pages"]:
        out += ["", f"### p{p.get('page')} [{p.get('kind')}] {p.get('title') or ''}".rstrip()]
        if p.get("summary"):
            out.append(p["summary"])
        if p.get("unverified") or p.get("mismatch"):
            out.append(f"（未検証 {p.get('unverified') or 0} / 検算不一致 {p.get('mismatch') or 0}）")
        for t in p.get("tables") or []:
            cols = t.get("cols") or []
            rows = [["行", "区分", "単位"] + [f"{c.get('period')}({c.get('kind')})" for c in cols]]
            for r in t.get("rows") or []:
                name = r.get("metric") or ""
                if r.get("of"):
                    name = f"{name}（{r['of']} の内訳）"
                rows.append([name, r.get("segment") or "", r.get("unit") or ""] + [v if v is not None else "" for v in r.get("values") or []])
            out.append(_tsv(rows))
    return "\n".join(out)


def tool_earnings_doc_read(args: dict[str, Any], get_json: GetJson, get_text: GetText, code5: str) -> str:
    kind = str(args.get("kind") or "")
    ident = str(args.get("id") or "").strip()
    offset = _clamp(args.get("offset"), 0, 0, 10_000_000, "offset")
    length = _clamp(args.get("length"), DEFAULT_DOC_LENGTH, 1000, MAX_DOC_LENGTH, "length")
    if kind == "filing":
        if not ident.isdigit():
            raise ValueError("id must be the filing id (integer) from earnings_docs")
        seq = _clamp(args.get("seq"), 1, 0, 10000, "seq")
        text = html_to_text(get_text(f"/api/stock/filings/{int(ident)}/documents/{seq}", APP_TIMEOUT))
    elif kind == "pdf":
        if not DISCLOSURE_RE.fullmatch(ident):
            raise ValueError("id must be the disclosure_no from earnings_docs")
        text = html_to_text(get_text(f"/api/stock/pdf/{ident}", APP_TIMEOUT))
    elif kind == "deck":
        if not DECK_KEY_RE.fullmatch(ident):
            raise ValueError("id must be the deck key from earnings_docs")
        text = _deck_text(get_json(f"/api/stock/{quote(code5, safe='')}/decks/{ident}", None, APP_TIMEOUT))
    else:
        raise ValueError("kind must be filing, pdf, or deck")
    part = text[offset:offset + length]
    end = offset + len(part)
    tail = f"\n\n[{offset}-{end} / {len(text)} 文字。続きは offset={end}]" if end < len(text) else f"\n\n[{offset}-{end} / {len(text)} 文字。終わり]"
    return part + tail


def _ret(c: list[Any], i: int, back: int) -> float | None:
    j = i - back
    if j < 0 or not c[j] or c[i] is None:
        return None
    return (c[i] / c[j] - 1) * 100


def _at_or_before(t: list[str], day: date) -> int:
    iso = day.isoformat()
    i = len(t) - 1
    while i >= 0 and t[i] > iso:
        i -= 1
    return i


def tool_stock_overview(args: dict[str, Any], get_json: GetJson, code5: str) -> str:
    c = quote(code5, safe="")
    today = parse_date(args.get("today"), "today", default=date.today())
    ov = get_json(f"/api/stock/{c}/overview", {"today": today.isoformat()}, APP_TIMEOUT)
    m = ov.get("metrics") or {}
    q = ov.get("quote") or {}
    mc = q.get("market_cap")
    out = [
        f"{code4(ov.get('code') or code5)} {ov.get('name')} {ov.get('market') or ''} {ov.get('sector33') or ''}",
        (f"終値 {q.get('close')}（{q.get('date')}、前日比 {_pct((q.get('change_pct') or 0) * 100) if q.get('change_pct') is not None else ''}%） "
        f"時価総額 {'' if mc is None else f'{mc / 100:,.0f}億円'}"),
        (f"予想PER {_num(m.get('per'))} / PBR {_num(m.get('pbr'), 2)} / 予想配当利回り {_num((m.get('dividend_yield') or 0) * 100, 2) if m.get('dividend_yield') is not None else ''}% / "
        f"ROE {_num((m.get('roe') or 0) * 100) if m.get('roe') is not None else ''}% / 自己資本比率 {_num((m.get('equity_ratio') or 0) * 100) if m.get('equity_ratio') is not None else ''}%"),
    ]
    ne = ov.get("next_earnings")
    if ne:
        out.append(f"次の決算予定 {ne.get('date') or '未定'} {ne.get('fq') or ''}".rstrip())
    try:
        ch = get_json(f"/api/chart/{c}", None, APP_TIMEOUT)
    except ValueError:
        ch = None
    if ch and ch.get("t"):
        t, cl, v = ch["t"], ch["c"], ch.get("v") or []
        i = _at_or_before(t, today)
        if i >= 0:
            lo = max(0, i - 244)
            hi_v, lo_v = max(cl[lo:i + 1]), min(cl[lo:i + 1])
            v20 = v[max(0, i - 19):i + 1]
            v60 = v[max(0, i - 59):i + 1]
            out += ["", f"## 株価（調整後。{t[i]} 時点）",
                    (f"騰落% 5日 {_pct(_ret(cl, i, 5))} / 20日 {_pct(_ret(cl, i, 20))} / 60日 {_pct(_ret(cl, i, 60))} / "
                    f"120日 {_pct(_ret(cl, i, 120))} / 240日 {_pct(_ret(cl, i, 240))}"),
                    f"1年高値 {hi_v}（現値は {_pct((cl[i] / hi_v - 1) * 100)}%） / 1年安値 {lo_v}（{_pct((cl[i] / lo_v - 1) * 100)}%）",
                    f"出来高 20日平均 {sum(v20) / len(v20):,.0f} / 60日平均 {sum(v60) / len(v60):,.0f}"]
    perf = get_json(f"/api/earnings-performance/{c}", None, APP_TIMEOUT)
    if isinstance(perf, list) and perf:
        rows = [["決算日", "翌日%", "翌日対TOPIX%", "5日%", "5日対TOPIX%", "20日%", "20日対TOPIX%", "60日%", "60日対TOPIX%"]]
        for p in perf[:12]:
            rows.append([p.get("disclosed_date")] + [_pct(p.get(k)) for k in (
                "return_1d", "alpha_1d", "return_5d", "alpha_5d", "return_20d", "alpha_20d", "return_60d", "alpha_60d")])
        out += ["", "## 過去の決算後の株価（決算後に買えた値から。場中は翌営業日始値）", _tsv(rows)]
    return "\n".join(out)


def tool_market_context(args: dict[str, Any], get_json: GetJson) -> str:
    day = parse_date(args.get("date"), default=date.today())
    days = _clamp(args.get("days"), 20, 1, 250, "days")
    tp = get_json("/api/topix", None, APP_TIMEOUT)
    t, c = tp.get("t") or [], tp.get("c") or []
    i = _at_or_before(t, day)
    if i < 0:
        return f"TOPIX の値が {day.isoformat()} 以前にありません。"
    lo = max(0, i - 244)
    hi_v = max(c[lo:i + 1])
    out = [
        f"TOPIX {t[i]} 終値 {c[i]}",
        (f"騰落% 1日 {_pct(_ret(c, i, 1))} / 5日 {_pct(_ret(c, i, 5))} / 20日 {_pct(_ret(c, i, 20))} / "
        f"60日 {_pct(_ret(c, i, 60))} / 240日 {_pct(_ret(c, i, 240))}"),
        f"1年高値 {hi_v}（現値は {_pct((c[i] / hi_v - 1) * 100)}%）",
        "", "## 日足（終値）", _tsv([["日付", "終値", "前日比%"]] + [
            [t[j], c[j], _pct(_ret(c, j, 1))] for j in range(max(0, i - days + 1), i + 1)]),
    ]
    if t[i] != day.isoformat():
        out.insert(1, f"（{day.isoformat()} は休場か未取込。直近の {t[i]} を使った）")
    return "\n".join(out)


def _fs_cell(cell: Any, per_share: bool) -> str:
    if not cell or cell.get("value") is None:
        return ""
    value = float(cell["value"])
    if per_share:
        return f"{value:,.2f}"
    return f"{value / 1e6:,.0f}"


def tool_financial_statements(args: dict[str, Any], get_json: GetJson, code5: str) -> str:
    statement = str(args.get("statement") or "BS").strip().upper()
    if statement not in FS_STATEMENTS:
        raise ValueError(f"statement must be one of {', '.join(FS_STATEMENTS)}")
    periods = _clamp(args.get("periods"), FS_DEFAULT_PERIODS, 1, FS_MAX_PERIODS, "periods")
    consolidated = args.get("consolidated", True)
    if not isinstance(consolidated, bool):
        raise ValueError("consolidated must be a boolean")
    d = get_json(f"/api/stock/{quote(code5, safe='')}/financials", {
        "statement": statement, "periods": periods, "consolidated": "true" if consolidated else "false",
    }, APP_TIMEOUT)
    code = d.get("code") or code5
    basis = d.get("basis") or ("連結" if consolidated else "個別")
    empty = d.get("empty")
    if empty:
        hint = "。個別は consolidated=false" if d.get("consolidated") and empty.get("other_basis_available") else ""
        return f"{code}: {statement}（{basis}）の材料がない（{empty.get('message')}）{hint}"
    cols = d.get("periods") or []
    head = (
        f"{code} {d.get('name') or ''} {statement} {basis} 単位:百万円（1株当たりは円） "
        f"会計基準:{'/'.join(d.get('accounting_standards') or []) or '-'}"
    ).replace("  ", " ")
    if d.get("cumulative"):
        head += " 期首からの累計"
    rows: list[list[Any]] = [["科目", "要素", *(c.get("label") for c in cols)]]
    for r in d.get("rows") or []:
        per_share = r.get("unit") == "per_share"
        rows.append([r.get("label"), r.get("key"), *(_fs_cell(c, per_share) for c in r.get("cells") or [])])
    rows.append(["出どころ", "", *(c.get("source_label") for c in cols)])
    return head + "\n" + _tsv(rows)
