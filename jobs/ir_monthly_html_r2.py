"""IR サイトの月次 HTML を Cloudflare R2 mount へ保存し、ingest JSON を隣に書く。

使い方:
  python jobs/ir_monthly_html_r2.py
  python jobs/ir_monthly_html_r2.py --dest /tmp/monthly --only 2664
  python jobs/ir_monthly_html_r2.py --dry-run
  python jobs/ir_monthly_html_r2.py --reparse

対象は config/ir_monthly.json の html。セレクタは json。
9989 / 7522 / 7561 / 8179 は jobs/html_issuers。それ以外は html_to_ingest。
原本は .html、派生が .ingest.json。自動 POST しない。パーサーを直したら --reparse。
ネットワークと app/ はテストから分離する。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from ir_monthly_r2 import (  # noqa: E402
    DEFAULT_CONFIG,
    DEFAULT_DEST,
    SLEEP_SEC,
    atomic_write,
    dest_path,
    http_get,
    jst_today,
    sha256_bytes,
    _AParser,
    _SKIP_HREF_RE,
)
from tdnet_monthly_r2 import (  # noqa: E402
    display_code,
    download_skip_reason,
    load_stock_product_map,
)
from html_issuers import PARSERS  # noqa: E402
from html_monthly import (  # noqa: E402
    _VALID_UNITS,
    _apply_force_unit,
    _drop_ytd_when_monthly_pair,
    _is_count_header,
    _parsed_score,
    _section_pick_rank,
    _sticky_section_fy,
    decode_html_bytes,
    drop_trailing_empty_months,
    dumps_ingest,
    fy_end_from_text,
    page_context,
    parse_grid,
    parse_tables,
    resolve_as_of,
    section_fy_end_from_text,
    table_section_texts,
    unit_mark_context,
)

EIR_URL = "https://ssl4.eir-parts.net/V4Public/eir/{code}/ja/announcement/announcement_{n}.js"
_CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")


@dataclass(frozen=True)
class HtmlIssuer:
    code: str
    name: str
    index_url: str
    title: str
    source: str
    table: int | None
    eir_code: str
    eir_n: int
    eir_title_re: str
    follow_re: str = ""
    parse: bool = True
    force_unit: str | None = None


def ingest_path(html_path: Path) -> Path:
    return html_path.with_name(html_path.stem + ".ingest.json")


def parse_eir_date(raw: str) -> str | None:
    s = (raw or "").strip()
    m = re.match(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", s)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
    except ValueError:
        return None


def _is_store_event_parsed(parsed: dict) -> bool:
    """開店/閉店/改装の店名×店舗面積リスト。月次速報ではない。"""
    rows = parsed.get("rows") or []
    if not rows:
        return False
    n_area = sum(1 for r in rows if "店舗面積" in str(r.get("element") or ""))
    return n_area >= max(1, (len(rows) + 1) // 2)


def _is_count_only_parsed(parsed: dict) -> bool:
    rows = parsed.get("rows") or []
    if not rows:
        return False
    return all(
        r.get("unit") == "number" and _is_count_header(str(r.get("element") or ""))
        for r in rows
    )


_SCOPED_STORE_UNIT_HEAD_RE = re.compile(
    r"(既存店|全店)(?=(?:・卸計)?(?:\(単位|（単位|単位|$))"
)
# Royal form: （既存店前年比）. Scope is inside the parens.
# 既存店売上（前年同月比） (ROYAL_TRAILING_UNIT_HTML) must not match.
_SCOPED_STORE_YOY_HEAD_RE = re.compile(
    r"[（(](既存店|全店)[^）)]*前年(?:同月)?比[）)]"
)
_SCOPE_METRICS = frozenset({"売上高", "来客数", "客数", "客単価"})


def _section_tail(section_text: str, n: int = 160) -> str:
    s = unicodedata.normalize("NFKC", section_text or "")
    s = re.sub(r"\s+", "", s)
    return s[-n:] if len(s) > n else s


@dataclass(frozen=True)
class Section:
    """表直前の見出し帯。スコープ（既存店/全店）だけ。事業接頭辞は html_issuers。"""

    segment: str | None
    scope: str | None


def _store_scope_from_section(section_text: str) -> str | None:
    """表直前の見出し帯だけ（やまやの「既存店 (単位:…)」、Royal の「（既存店前年比）」）。卸計は対象外。"""
    tail = _section_tail(section_text, 80)
    if "卸計" in tail:
        return None
    m = _SCOPED_STORE_UNIT_HEAD_RE.search(tail) or _SCOPED_STORE_YOY_HEAD_RE.search(tail)
    if not m:
        return None
    return "既存店" if m.group(1) == "既存店" else "全店"


def _rows_need_store_scope(rows: list[dict]) -> bool:
    """裸のスコープ層指標（売上高/来客数/客数/客単価）だけスコープを付ける。"""
    if not rows:
        return False
    for row in rows:
        el = str(row.get("element") or "").strip()
        compact = re.sub(r"\s+", "", unicodedata.normalize("NFKC", el))
        if compact not in _SCOPE_METRICS:
            return False
        if "既存" in el or "全店" in el:
            return False
    return True


def _apply_store_scope(rows: list[dict], scope: str) -> None:
    """見出しの既存/全店を行名に付ける。"""
    for row in rows:
        el = str(row.get("element") or "").strip()
        if not el:
            continue
        if scope in el or ("既存" in scope and "既存" in el) or (scope == "全店" and "全店" in el):
            continue
        row["element"] = f"{scope} {el}".strip()


def _prefer_section_candidates(
    candidates: list[tuple[int, list[list[str]], dict, tuple[int, int] | None]],
    sections: list[str],
) -> list[tuple[int, list[list[str]], dict, tuple[int, int] | None]]:
    if not candidates:
        return candidates
    ranks = [
        _section_pick_rank(sections[i] if i < len(sections) else "")
        for i, _g, _p, _fy in candidates
    ]
    best = max(ranks)
    return [c for c, r in zip(candidates, ranks) if r == best]


def _drop_all_store_sales_breakdowns(rows: list[dict], section_text: str) -> list[dict]:
    """全店売上セクションで売上高合計があるときだけ、内訳行（CVS/FF）を落とす。"""
    if "全店売上" not in _section_tail(section_text):
        return rows
    totals = [r for r in rows if "売上高合計" in str(r.get("element") or "")]
    return totals or rows


def _merge_companion_total_stores(
    primary: dict,
    candidates: list[tuple[int, list[list[str]], dict, tuple[int, int] | None]],
    *,
    primary_index: int,
) -> None:
    """営業速報表に、同月軸の店舗推移から総店舗数だけ足す。"""
    primary_months = list(primary.get("months") or [])
    have = {str(r.get("element") or "") for r in primary.get("rows") or []}
    for idx, _grid, other, _fy in candidates:
        if idx == primary_index or _is_store_event_parsed(other):
            continue
        if list(other.get("months") or []) != primary_months:
            continue
        for row in other.get("rows") or []:
            el = str(row.get("element") or "")
            if "総店舗数" not in el:
                continue
            if el in have:
                continue
            primary.setdefault("rows", []).append(row)
            have.add(el)


def _merge_companion_store_scopes(
    primary: dict,
    candidates: list[tuple[int, list[list[str]], dict, tuple[int, int] | None]],
    sections: list[Section],
    *,
    primary_index: int,
) -> None:
    """同月軸の 既存店/全店 表を足す（やまや。卸計は入れない）。"""
    primary_months = list(primary.get("months") or [])
    have = {
        (str(r.get("element") or ""), r.get("unit"))
        for r in primary.get("rows") or []
    }
    for idx, _grid, other, _fy in candidates:
        if idx == primary_index or _is_store_event_parsed(other):
            continue
        if list(other.get("months") or []) != primary_months:
            continue
        scope = sections[idx].scope if idx < len(sections) else None
        if scope is None or not _rows_need_store_scope(other.get("rows") or []):
            continue
        for row in other.get("rows") or []:
            merged = dict(row)
            _apply_store_scope([merged], scope)
            el = str(merged.get("element") or "")
            key = (el, merged.get("unit"))
            if not el or key in have:
                continue
            primary.setdefault("rows", []).append(merged)
            have.add(key)


def _sticky_sections(section_texts: list[str]) -> list[Section]:
    """各表の見出し帯から既存店/全店だけ取る。事業接頭辞は html_issuers。"""
    return [Section(None, _store_scope_from_section(text)) for text in section_texts]



def html_to_ingest(
    html: str,
    code: str,
    *,
    as_of: str | None = None,
    table: int | None = 0,
    context: str | None = None,
    force_unit: str | None = None,
) -> dict:
    tables = parse_tables(html)
    if not tables:
        raise ValueError("no html table")
    section_texts = table_section_texts(html)
    base_ctx = context if context is not None else page_context(html)
    unit_marks = unit_mark_context(html)
    indices: list[int]
    if table is None:
        indices = list(range(len(tables)))
    else:
        if table < 0 or table >= len(tables):
            raise ValueError(f"table index {table} out of range ({len(tables)})")
        indices = [table]
    last_err: str | None = None
    sticky_fy = _sticky_section_fy(section_texts)
    candidates: list[tuple[int, list[list[str]], dict, tuple[int, int] | None]] = []
    for i in indices:
        section_text = section_texts[i] if i < len(section_texts) else ""
        ctx = " ".join(p for p in (base_ctx, section_text, unit_marks) if p)
        try:
            parsed = parse_grid(tables[i], ctx, html)
        except Exception as e:
            last_err = str(e)
            parsed = None
        if parsed and parsed.get("rows"):
            section_fy = sticky_fy[i] if i < len(sticky_fy) else None
            if section_fy is None:
                section_fy = section_fy_end_from_text(
                    section_text, parsed.get("months")
                )
            candidates.append((i, tables[i], parsed, section_fy))
            if table is not None:
                break
    if not candidates:
        raise ValueError(last_err or "no monthly rows")
    monthly_cands = [c for c in candidates if not _is_store_event_parsed(c[2])]
    sales_cands = [c for c in monthly_cands if not _is_count_only_parsed(c[2])]
    pick_pool = sales_cands or monthly_cands or candidates
    sections = _sticky_sections(section_texts)
    pick_pool = _prefer_section_candidates(pick_pool, section_texts)
    table_index, grid_used, parsed, section_fy = max(
        pick_pool,
        key=lambda it: _parsed_score(
            html, it[1], it[2], table_index=it[0], section_fy=it[3]
        ),
    )
    section_text = section_texts[table_index] if table_index < len(section_texts) else ""
    scope = sections[table_index].scope if table_index < len(sections) else None
    if scope and _rows_need_store_scope(parsed["rows"]):
        _apply_store_scope(parsed["rows"], scope)
    _merge_companion_total_stores(parsed, candidates, primary_index=table_index)
    _merge_companion_store_scopes(
        parsed, candidates, sections, primary_index=table_index
    )
    as_of_s = resolve_as_of(
        html,
        grid_used,
        parsed["rows"],
        as_of,
        parsed.get("months"),
        section_fy,
    )
    _apply_force_unit(parsed["rows"], force_unit)
    parsed["rows"] = _drop_ytd_when_monthly_pair(parsed["rows"])
    parsed["rows"] = _drop_all_store_sales_breakdowns(parsed["rows"], section_text)
    months = drop_trailing_empty_months(list(parsed.get("months") or []), parsed["rows"])
    return {
        "code": display_code(code),
        "months": months,
        "rows": parsed["rows"],
        "as_of": as_of_s,
    }


def parse_eir_jsonp(body: str) -> dict:
    s = (body or "").lstrip("\ufeff").strip()
    lparen = s.find("(")
    rparen = s.rfind(")")
    if lparen >= 0 and rparen > lparen:
        payload = s[lparen + 1 : rparen]
    else:
        payload = s
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("eir jsonp is not an object")
    return data


def pick_eir_monthly(payload: dict, title_re: str) -> tuple[str, str | None]:
    try:
        rx = re.compile(title_re)
    except re.error as e:
        raise ValueError(f"invalid eir_title_re: {title_re!r} ({e})") from e
    items = payload.get("item")
    if not isinstance(items, list):
        raise ValueError("eir jsonp missing item")
    matched: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or "")
        if rx.search(title):
            matched.append(it)
    if not matched:
        raise ValueError("no eir monthly item")
    with_text = [it for it in matched if str(it.get("text") or "").strip()]
    pool = with_text or matched
    latest = max(
        pool,
        key=lambda it: (
            str(it.get("date") or ""),
            fy_end_from_text(str(it.get("title") or "")) or (0, 0),
        ),
    )
    html = str(latest.get("text") or "").strip()
    if not html:
        raise ValueError("eir monthly text empty")
    return html, parse_eir_date(str(latest.get("date") or ""))


def eir_url(code: str, n: int) -> str:
    return EIR_URL.format(code=display_code(code), n=int(n))


def existing_html_hashes(code_dir: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    if not code_dir.is_dir():
        return out
    for p in code_dir.iterdir():
        if not p.is_file() or p.suffix.lower() != ".html":
            continue
        try:
            if p.stat().st_size <= 0:
                continue
            out[sha256_bytes(p.read_bytes())] = p
        except OSError:
            continue
    return out


def _parse_html_issuer(raw: object) -> HtmlIssuer:
    if not isinstance(raw, dict):
        raise ValueError("html issuer must be an object")
    raw_code = str(raw.get("code") or "").strip().upper()
    if not _CODE_RE.fullmatch(raw_code):
        raise ValueError(f"invalid issuer code: {raw.get('code')!r}")
    code = display_code(raw_code)
    name = str(raw.get("name") or "").strip()
    index_url = str(raw.get("index_url") or "").strip()
    title = str(raw.get("title") or "").strip() or "月次"
    source = str(raw.get("source") or "html").strip().lower() or "html"
    if source not in ("html", "eir"):
        raise ValueError(f"issuer {code} invalid source: {source!r}")
    if not name:
        raise ValueError(f"issuer {code} missing name")
    parsed = urlparse(index_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"invalid index_url for {code}: {index_url!r}")
    table_raw = raw.get("table", 0)
    table: int | None
    if table_raw is None:
        table = None
    elif isinstance(table_raw, bool) or not isinstance(table_raw, int):
        raise ValueError(f"issuer {code} invalid table")
    else:
        table = table_raw
    eir_code = display_code(str(raw.get("eir_code") or code).strip().upper())
    eir_n_raw = raw.get("eir_n", 0)
    if isinstance(eir_n_raw, bool) or not isinstance(eir_n_raw, int) or eir_n_raw < 0:
        raise ValueError(f"issuer {code} invalid eir_n")
    eir_title_re = str(raw.get("eir_title_re") or "月次").strip() or "月次"
    try:
        re.compile(eir_title_re)
    except re.error as e:
        raise ValueError(f"issuer {code} invalid eir_title_re: {e}") from e
    follow_re = str(raw.get("follow_re") or "").strip()
    if follow_re:
        try:
            re.compile(follow_re)
        except re.error as e:
            raise ValueError(f"issuer {code} invalid follow_re: {e}") from e
    parse_raw = raw.get("parse", True)
    if not isinstance(parse_raw, bool):
        raise ValueError(f"issuer {code} invalid parse")
    force_raw = raw.get("force_unit")
    force_unit: str | None
    if force_raw is None:
        force_unit = None
    elif isinstance(force_raw, str) and force_raw in _VALID_UNITS:
        force_unit = force_raw
    else:
        raise ValueError(f"issuer {code} invalid force_unit: {force_raw!r}")
    return HtmlIssuer(
        code=code,
        name=name,
        index_url=index_url,
        title=title,
        source=source,
        table=table,
        eir_code=eir_code,
        eir_n=eir_n_raw,
        eir_title_re=eir_title_re,
        follow_re=follow_re,
        parse=parse_raw,
        force_unit=force_unit,
    )


def load_html_registry(path: Path) -> list[HtmlIssuer]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        items = raw.get("html")
    else:
        items = raw
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path} has no html issuers")
    out: list[HtmlIssuer] = []
    seen: set[str] = set()
    for item in items:
        iss = _parse_html_issuer(item)
        if iss.code in seen:
            raise ValueError(f"duplicate html issuer code: {iss.code}")
        seen.add(iss.code)
        out.append(iss)
    return out


_FOLLOW_YM_RE = re.compile(r"(20\d{2})_(1[0-2]|0?[1-9])(?:/|$)")


def _follow_key(url: str) -> tuple[int, int, int, str]:
    """年 → 月 → パス深さ。`orders_2026/2026_8` は年ページより後。`2026_10` は `2026_9` より後。"""
    path = urlparse(url).path.rstrip("/")
    ms = list(_FOLLOW_YM_RE.finditer(path))
    if ms:
        return (int(ms[-1].group(1)), int(ms[-1].group(2)), path.count("/"), url)
    years = [int(y) for y in re.findall(r"20\d{2}", path)]
    y = years[-1] if years else 0
    return (y, 0, path.count("/"), url)


def pick_follow_url(
    html: str,
    base_url: str,
    follow_re: str,
    *,
    under: str | None = None,
) -> str | None:
    """index から follow_re に合うリンクを集め、年・月が最大のものを返す。

    under を付けると、その URL の子パスだけ（年一覧 → 最新月）。無ければ None。
    """
    try:
        rx = re.compile(follow_re)
    except re.error as e:
        raise ValueError(f"invalid follow_re: {follow_re!r} ({e})") from e
    parser = _AParser()
    parser.feed(html or "")
    parser.close()
    found: list[str] = []
    seen: set[str] = set()
    for href, _text in parser.links:
        raw = (href or "").strip()
        if not raw or _SKIP_HREF_RE.match(raw):
            continue
        abs_url = urljoin(base_url, raw).split("#", 1)[0]
        if abs_url in seen:
            continue
        if not rx.search(abs_url) and not rx.search(raw):
            continue
        seen.add(abs_url)
        found.append(abs_url)
    if under:
        prefix = under.rstrip("/") + "/"
        under_n = under.rstrip("/")
        found = [u for u in found if u.rstrip("/") != under_n and u.startswith(prefix)]
    if not found:
        if under is not None:
            return None
        raise ValueError(f"no follow url matching {follow_re!r}")
    found.sort(key=_follow_key)
    return found[-1]


def fetch_html_bytes(iss: HtmlIssuer) -> tuple[bytes, str | None, str]:
    """returns (html_bytes, as_of, source_url)."""
    if iss.source == "eir":
        url = eir_url(iss.eir_code, iss.eir_n)
        body = http_get(url)
        time.sleep(SLEEP_SEC)
        payload = parse_eir_jsonp(decode_html_bytes(body))
        html, as_of = pick_eir_monthly(payload, iss.eir_title_re)
        return html.encode("utf-8"), as_of, url
    url = iss.index_url
    data = http_get(url)
    time.sleep(SLEEP_SEC)
    if iss.follow_re:
        followed = pick_follow_url(decode_html_bytes(data), url, iss.follow_re)
        assert followed is not None
        if followed.rstrip("/") != url.rstrip("/"):
            data = http_get(followed)
            time.sleep(SLEEP_SEC)
            url = followed
        if iss.parse:
            html = decode_html_bytes(data)
            try:
                ingest_payload(html, iss)
            except ValueError:
                child = pick_follow_url(html, url, iss.follow_re, under=url)
                if child and child.rstrip("/") != url.rstrip("/"):
                    data = http_get(child)
                    time.sleep(SLEEP_SEC)
                    url = child
    return data, None, url


def ingest_payload(html: str, iss: HtmlIssuer, as_of: str | None = None) -> dict:
    """PARSERS の code は銘柄パーサ。それ以外は html_to_ingest。"""
    parser = PARSERS.get(iss.code)
    if parser is not None:
        return parser(html, as_of=as_of)
    return html_to_ingest(
        html, iss.code, as_of=as_of, table=iss.table, force_unit=iss.force_unit
    )


def write_ingest(html_path: Path, html: str, iss: HtmlIssuer, as_of: str | None = None) -> None:
    payload = ingest_payload(html, iss, as_of)
    atomic_write(ingest_path(html_path), dumps_ingest(payload).encode("utf-8"))


def _try_write_ingest(
    html_path: Path,
    html: str,
    iss: HtmlIssuer,
    as_of: str | None,
    rel: object,
) -> int:
    """0 ok, 1 failed. parse=false は JSON を書かず成功。"""
    if not iss.parse:
        print(f"skip parse {rel}", flush=True)
        return 0
    try:
        write_ingest(html_path, html, iss, as_of)
    except Exception as e:
        print(f"fail ingest {rel}: {e}", flush=True)
        return 1
    print(f"saved {ingest_path(html_path).name}", flush=True)
    return 0


def fetch_issuer(
    iss: HtmlIssuer,
    dest: Path,
    stock_map: Mapping[str, tuple[str | None, str | None]],
    today: date,
    *,
    dry_run: bool,
) -> tuple[int, int, int, int]:
    """returns listed, saved, skipped, failed."""
    reason = download_skip_reason(iss.code, iss.name, stock_map)
    if reason:
        print(f"skip {reason}", flush=True)
        return 0, 0, 1, 0
    if dry_run:
        if iss.source == "eir":
            print(f"dry-run {iss.code} eir {eir_url(iss.eir_code, iss.eir_n)}", flush=True)
        elif iss.follow_re:
            print(f"dry-run {iss.code} html {iss.index_url} follow={iss.follow_re}", flush=True)
        else:
            print(f"dry-run {iss.code} html {iss.index_url}", flush=True)
        return 1, 0, 1, 0
    try:
        data, eir_as_of, url = fetch_html_bytes(iss)
    except Exception as e:
        print(f"fail issuer {iss.code} fetch: {e}", flush=True)
        return 1, 0, 0, 1
    digest = sha256_bytes(data)
    code_dir = dest / display_code(iss.code)
    hashes = existing_html_hashes(code_dir)
    pubdate = today.isoformat()
    path = dest_path(dest, pubdate, iss.code, iss.name, iss.title, "html")
    rel = path.relative_to(dest)
    prev = hashes.get(digest)
    html = decode_html_bytes(data)
    as_of = eir_as_of
    if prev is not None:
        sidecar = ingest_path(prev)
        try:
            missing = (not sidecar.is_file()) or sidecar.stat().st_size <= 0
        except OSError:
            missing = True
        if missing and iss.parse:
            if _try_write_ingest(prev, html, iss, as_of, sidecar.relative_to(dest)):
                return 1, 0, 0, 1
            return 1, 1, 0, 0
        print(f"skip hash {rel} == {prev.relative_to(dest)}", flush=True)
        return 1, 0, 1, 0
    try:
        if path.is_file() and path.stat().st_size > 0:
            hashes.pop(sha256_bytes(path.read_bytes()), None)
        atomic_write(path, data)
    except Exception as e:
        print(f"fail {rel} url={url}: {e}", flush=True)
        return 1, 0, 0, 1
    print(f"saved {rel} ({len(data)} bytes)", flush=True)
    if _try_write_ingest(path, html, iss, as_of, rel):
        return 1, 1, 0, 1
    return 1, 1, 0, 0


def reparse_issuer(iss: HtmlIssuer, dest: Path) -> tuple[int, int, int]:
    """returns parsed, skipped, failed."""
    code_dir = dest / display_code(iss.code)
    if not code_dir.is_dir():
        return 0, 0, 0
    if not iss.parse:
        print(f"skip parse {iss.code}", flush=True)
        return 0, 0, 0
    parsed = skipped = failed = 0
    files = sorted(p for p in code_dir.iterdir() if p.is_file() and p.suffix.lower() == ".html")
    for path in files:
        rel = path.relative_to(dest)
        try:
            data = path.read_bytes()
            if not data:
                skipped += 1
                continue
            html = decode_html_bytes(data)
            write_ingest(path, html, iss)
            parsed += 1
            print(f"reparse {rel} -> {ingest_path(path).name}", flush=True)
        except Exception as e:
            failed += 1
            print(f"fail reparse {rel}: {e}", flush=True)
    return parsed, skipped, failed


def run(
    dest: Path,
    config_path: Path,
    *,
    only: list[str] | None = None,
    dry_run: bool = False,
    reparse: bool = False,
    today: date | None = None,
) -> int:
    issuers = load_html_registry(config_path)
    if only:
        want = {display_code(c.strip().upper()) for c in only if c.strip()}
        issuers = [i for i in issuers if i.code in want]
        missing = want - {i.code for i in issuers}
        if missing:
            print(f"warn unknown --only {sorted(missing)}", flush=True)
    if not issuers:
        print("ir_monthly_html_r2 no issuers", flush=True)
        return 2
    day = today or jst_today()
    dest.mkdir(parents=True, exist_ok=True)
    print(
        f"ir_monthly_html_r2 start dest={dest} issuers={len(issuers)} "
        f"day={day.isoformat()} dry_run={int(dry_run)} reparse={int(reparse)}",
        flush=True,
    )
    if reparse:
        parsed = skipped = failed = 0
        for iss in issuers:
            print(f"issuer {iss.code} {iss.name}", flush=True)
            p, k, f = reparse_issuer(iss, dest)
            parsed += p
            skipped += k
            failed += f
        print(
            f"ir_monthly_html_r2 reparse parsed={parsed} skipped={skipped} failed={failed}",
            flush=True,
        )
        return 1 if failed else 0
    stock_map = load_stock_product_map()
    if not stock_map:
        print("warn stocks map empty; unknown-stock skip disabled", flush=True)
    listed = saved = skipped = failed = 0
    for iss in issuers:
        print(f"issuer {iss.code} {iss.name}", flush=True)
        try:
            n, s, k, f = fetch_issuer(iss, dest, stock_map, day, dry_run=dry_run)
        except Exception as e:
            failed += 1
            print(f"fail issuer {iss.code} {iss.index_url}: {e}", flush=True)
            continue
        listed += n
        saved += s
        skipped += k
        failed += f
    print(
        f"ir_monthly_html_r2 done listed={listed} saved={saved} skipped={skipped} failed={failed}",
        flush=True,
    )
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch IR monthly HTML into R2 mount and write ingest JSON")
    ap.add_argument("--dest", default=DEFAULT_DEST, help="destination directory")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG), help="issuer registry JSON")
    ap.add_argument(
        "--only",
        action="append",
        default=[],
        help="limit to these codes (repeatable)",
    )
    ap.add_argument("--dry-run", action="store_true", help="list URLs, do not download")
    ap.add_argument("--reparse", action="store_true", help="rebuild ingest JSON from saved HTML")
    args = ap.parse_args(argv)
    return run(
        Path(args.dest),
        Path(args.config),
        only=args.only or None,
        dry_run=args.dry_run,
        reparse=args.reparse,
    )


if __name__ == "__main__":
    raise SystemExit(main())
