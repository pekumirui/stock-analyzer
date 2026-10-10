"""IR サイトの月次 HTML を Cloudflare R2 mount へ保存し、ingest JSON を隣に書く。

使い方:
  python jobs/ir_monthly_html_r2.py
  python jobs/ir_monthly_html_r2.py --dest /tmp/monthly --only 2664
  python jobs/ir_monthly_html_r2.py --dry-run
  python jobs/ir_monthly_html_r2.py --reparse
  python jobs/ir_monthly_html_r2.py --force --only 3082

--only は 4桁 (3082) でも 5桁の J-Quants コード (30820) でもよい。
display_code が先頭 4 桁に切る。--force は --only が必須。

対象は config/ir_monthly.json の html。セレクタは json。
parse: true は jobs/html_issuers。parse: false は HTML だけ残す。
原本は .html、派生が .ingest.json。自動 POST しない。パーサーを直したら --reparse（過去 HTML）。
同じ HTML の再取得でも、現行パーサの JSON が保存済みと違えば sidecar を書き直す。
ネットワークと app/ はテストから分離する。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from _r2common import (  # noqa: E402
    abort_if_disconnected,
    fail_if_mount_down,
    is_mount_disconnect_error,
    jst_today,
    load_stock_product_map,
)
from ir_monthly_r2 import (  # noqa: E402
    DEFAULT_CONFIG,
    DEFAULT_DEST,
    SLEEP_SEC,
    _AParser,
    _SKIP_HREF_RE,
    atomic_write,
    dest_path,
    http_get,
    sha256_bytes,
)
from tdnet_monthly_r2 import display_code, download_skip_reason  # noqa: E402
from html_issuers import PARSERS  # noqa: E402
from html_monthly import (  # noqa: E402
    decode_html_bytes,
    dumps_ingest,
    fy_end_from_text,
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
    eir_code: str
    eir_n: int
    eir_title_re: str
    follow_re: str = ""
    parse: bool = True


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
        except OSError as e:
            if is_mount_disconnect_error(e):
                raise
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
    return HtmlIssuer(
        code=code,
        name=name,
        index_url=index_url,
        title=title,
        source=source,
        eir_code=eir_code,
        eir_n=eir_n_raw,
        eir_title_re=eir_title_re,
        follow_re=follow_re,
        parse=parse_raw,
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
_LOCATION_HREF_RE = re.compile(r"""location\.href\s*=\s*(['"])([^'"]*)\1""")


def _url_host(url: str) -> str:
    return urlparse(url).netloc.lower()


def _issuer_fetch_host(iss: HtmlIssuer) -> str:
    if iss.source == "eir":
        return _url_host(eir_url(iss.eir_code, iss.eir_n))
    return _url_host(iss.index_url)


def _rate_limited_host(exc: BaseException, fallback_url: str) -> str:
    """HTTPError 429 の URL ホスト。判定できなければ fallback。"""
    url = str(getattr(exc, "filename", "") or "")
    return _url_host(url) or _url_host(fallback_url)


def is_rate_limited(exc: BaseException) -> bool:
    return isinstance(exc, HTTPError) and exc.code == 429


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
    hrefs = [href for href, _text in parser.links]
    hrefs.extend(m.group(2) for m in _LOCATION_HREF_RE.finditer(html or ""))
    for href in hrefs:
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
    """parse: true は銘柄モジュール。宣言と違うページは ValueError で sidecar を書かない。"""
    parser = PARSERS.get(iss.code)
    if parser is None:
        raise ValueError(f"{iss.code} page does not match declaration")
    return parser(html, as_of=as_of)


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


def _refresh_ingest_if_changed(
    html_path: Path,
    html: str,
    iss: HtmlIssuer,
    as_of: str | None,
    rel: object,
) -> int:
    """0 unchanged, 1 rewritten, 2 failed. parse=false は触らない。"""
    if not iss.parse:
        return 0
    sidecar = ingest_path(html_path)
    try:
        new_bytes = dumps_ingest(ingest_payload(html, iss, as_of)).encode("utf-8")
    except Exception as e:
        print(f"fail ingest {rel}: {e}", flush=True)
        return 2
    try:
        old = sidecar.read_bytes() if sidecar.is_file() else b""
    except OSError:
        old = b""
    if old and sha256_bytes(old) == sha256_bytes(new_bytes):
        return 0
    try:
        atomic_write(sidecar, new_bytes)
    except Exception as e:
        print(f"fail ingest {rel}: {e}", flush=True)
        return 2
    verb = "refresh ingest" if old else "saved"
    print(f"{verb} {sidecar.name}", flush=True)
    return 1


def fetch_issuer(
    iss: HtmlIssuer,
    dest: Path,
    stock_map: Mapping[str, tuple[str | None, str | None]],
    today: date,
    *,
    dry_run: bool,
    force: bool = False,
    cooled_hosts: set[str] | None = None,
) -> tuple[int, int, int, int]:
    """returns listed, saved, skipped, failed.

    打ち切った 429 は failed にせず skip。cooled_hosts があれば同一ホストを残りスキップする。
    """
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
    host = _issuer_fetch_host(iss)
    if cooled_hosts is not None and host and host in cooled_hosts:
        print(f"skip issuer {iss.code} host-cooled {host}", flush=True)
        return 1, 0, 1, 0
    try:
        data, eir_as_of, url = fetch_html_bytes(iss)
    except Exception as e:
        if is_rate_limited(e):
            cooled = _rate_limited_host(e, iss.index_url) or host
            print(f"skip issuer {iss.code} rate-limited: {e}", flush=True)
            if cooled_hosts is not None and cooled:
                cooled_hosts.add(cooled)
            return 1, 0, 1, 0
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
    if prev is not None and not force:
        sidecar = ingest_path(prev)
        status = _refresh_ingest_if_changed(
            prev, html, iss, as_of, sidecar.relative_to(dest),
        )
        if status == 2:
            return 1, 0, 0, 1
        if status == 1:
            return 1, 1, 0, 0
        print(f"skip hash {rel} == {prev.relative_to(dest)}", flush=True)
        return 1, 0, 1, 0
    # 同じバイトでも今日のファイルを書く。ingest はファイル名日付の新しい sidecar を選ぶ。
    if force and prev is not None:
        print(f"force {rel} (unchanged == {prev.relative_to(dest)})", flush=True)
    try:
        if path.is_file() and path.stat().st_size > 0:
            hashes.pop(sha256_bytes(path.read_bytes()), None)
        atomic_write(path, data)
    except Exception as e:
        if is_mount_disconnect_error(e):
            raise
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
            if is_mount_disconnect_error(e):
                raise
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
    force: bool = False,
) -> int:
    if force and not only:
        print("error: --force requires --only", flush=True)
        return 2
    if force and reparse:
        print("error: --force and --reparse cannot be combined", flush=True)
        return 2
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
    rc = fail_if_mount_down("ir_monthly_html_r2", dest, require_existing=False)
    if rc is not None:
        return rc
    day = today or jst_today()
    dest.mkdir(parents=True, exist_ok=True)
    print(
        f"ir_monthly_html_r2 start dest={dest} issuers={len(issuers)} "
        f"day={day.isoformat()} dry_run={int(dry_run)} reparse={int(reparse)}"
        + (" force=1" if force else ""),
        flush=True,
    )
    if reparse:
        parsed = skipped = failed = 0
        for iss in issuers:
            print(f"issuer {iss.code} {iss.name}", flush=True)
            try:
                p, k, f = reparse_issuer(iss, dest)
            except Exception as e:
                down = abort_if_disconnected("ir_monthly_html_r2", dest, e)
                if down is not None:
                    return down
                raise
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
    cooled_hosts: set[str] = set()
    for iss in issuers:
        print(f"issuer {iss.code} {iss.name}", flush=True)
        try:
            n, s, k, f = fetch_issuer(
                iss,
                dest,
                stock_map,
                day,
                dry_run=dry_run,
                force=force,
                cooled_hosts=cooled_hosts,
            )
        except Exception as e:
            down = abort_if_disconnected("ir_monthly_html_r2", dest, e)
            if down is not None:
                return down
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
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-save HTML and rebuild ingest JSON even if unchanged; requires --only",
    )
    args = ap.parse_args(argv)
    return run(
        Path(args.dest),
        Path(args.config),
        only=args.only or None,
        dry_run=args.dry_run,
        reparse=args.reparse,
        force=args.force,
    )


if __name__ == "__main__":
    raise SystemExit(main())
