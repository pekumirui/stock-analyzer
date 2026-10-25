"""短信 ZIP の HTML → xbrl_documents（KAB-161。docs/DESIGN_xbrl.md §15）。

PDF と同じ中身を画面で読むための原文。サマリーの iXBRL と `Attachment/qualitative.htm`
（添付資料まるごと）を入れる。qualitative.htm が無い書類（REIT・一部の訂正）は本表の iXBRL を
ファイル名順に入れる。元の HTML のまま gzip で持ち、script などは表示のときに除く。
"""
from __future__ import annotations

import gzip
import hashlib
import re
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, exists
from sqlalchemy.orm import Session

from app.models import XbrlDocument, XbrlFiling


def _now() -> datetime:
    return datetime.utcnow()


def pick_documents(names: list[str]) -> list[tuple[str, str]]:
    """ZIP の中のファイル名 → 表示順の (part, name)。"""
    htm = [n for n in names if n.lower().endswith(".htm")]
    summary = sorted(n for n in htm if "/summary/" in f"/{n.lower()}" and n.lower().endswith("-ixbrl.htm"))
    qual = sorted(n for n in htm if n.lower().endswith("/qualitative.htm") or n.lower() == "qualitative.htm")
    out = [("summary", n) for n in summary]
    if qual:
        out += [("qualitative", n) for n in qual[:1]]
    else:
        att = sorted(
            (n for n in htm if "/attachment/" in f"/{n.lower()}" and n.lower().endswith("-ixbrl.htm")),
            key=lambda n: n.rsplit("/", 1)[-1],
        )
        out += [("attachment", n) for n in att]
    return out


def read_documents(zip_path: Path) -> list[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        picked = pick_documents(zf.namelist())
        return [
            {"seq": i, "part": part, "file_name": name.rsplit("/", 1)[-1], "html": zf.read(name)}
            for i, (part, name) in enumerate(picked)
        ]


def store_documents(db: Session, filing: XbrlFiling, zip_path: Path) -> int:
    """filing の xbrl_documents を作り直す。commit しない。戻り値は入れた件数。"""
    docs = read_documents(zip_path)
    db.execute(delete(XbrlDocument).where(XbrlDocument.filing_id == filing.id))
    now = _now()
    for d in docs:
        raw = d["html"]
        db.add(XbrlDocument(
            filing_id=filing.id,
            seq=d["seq"],
            part=d["part"],
            file_name=d["file_name"],
            html_gz=gzip.compress(raw, compresslevel=6, mtime=0),
            bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            created_at=now,
        ))
    db.flush()
    return len(docs)


def filings_missing_documents(db: Session, limit: int | None = None) -> list[XbrlFiling]:
    """TDnet の filing のうち xbrl_documents が無いもの。新しい順。iXBRL の無い ZIP は除く。"""
    q = (
        db.query(XbrlFiling)
        .filter(XbrlFiling.source == "tdnet")
        .filter(~exists().where(XbrlDocument.filing_id == XbrlFiling.id))
        .filter((XbrlFiling.parse_error.is_(None)) | (XbrlFiling.parse_error != "no-ixbrl"))
        .order_by(XbrlFiling.submitted_at.desc())
    )
    if limit:
        q = q.limit(limit)
    return q.all()


def fill_missing_documents(db: Session, dest: Path, *, limit: int | None = None) -> dict[str, int]:
    """既にパースした filing に原文を足す（デプロイ後の流し込みと取りこぼしの拾い直し）。"""
    stored = missing = errors = 0
    for filing in filings_missing_documents(db, limit=limit):
        zip_path = Path(dest) / filing.raw_path
        if not zip_path.is_file():
            missing += 1
            continue
        try:
            if store_documents(db, filing, zip_path):
                stored += 1
            db.commit()
        except Exception as exc:
            db.rollback()
            errors += 1
            print(f"documents failed doc_id={filing.doc_id} code={filing.code} err={exc}", flush=True)
    return {"stored": stored, "missing_zip": missing, "errors": errors}


def load_html(doc: XbrlDocument) -> str:
    raw = gzip.decompress(doc.html_gz)
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp932", errors="replace")


_DROP_BLOCKS = re.compile(r"<(script|iframe|object|embed|frameset|frame|noscript)\b.*?</\1\s*>", re.I | re.S)
_DROP_TAGS = re.compile(r"<(?:script|iframe|object|embed|frame|link|base)\b[^>]*>", re.I)
_DROP_META_REFRESH = re.compile(r"<meta\b[^>]*http-equiv\s*=\s*['\"]?refresh[^>]*>", re.I)
_ON_ATTR = re.compile(r"""\son[a-z]+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_JS_URL = re.compile(r"(href|src|action)\s*=\s*(['\"])\s*javascript:[^'\"]*\2", re.I)
# TDnet の FASF マーク（外の gif）。外へは取りに行かないので枠ごと外す
_EXT_IMG = re.compile(r"<img\b[^>]*\bsrc\s*=\s*['\"]https?://[^>]*>", re.I)
_XML_DECL = re.compile(r"^\s*<\?xml[^>]*\?>", re.I)


def sanitize_html(html: str) -> str:
    """会社が作った HTML から、動くもの・外へ出るものを除く。本当の防御は API の CSP と sandbox。"""
    s = _XML_DECL.sub("", html)
    s = _DROP_BLOCKS.sub("", s)
    s = _DROP_TAGS.sub("", s)
    s = _DROP_META_REFRESH.sub("", s)
    s = _ON_ATTR.sub("", s)
    s = _JS_URL.sub(r'\1=\2#\2', s)
    s = _EXT_IMG.sub("", s)
    return s
