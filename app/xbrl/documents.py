"""短信 ZIP の HTML → xbrl_documents（KAB-161。docs/jobs/xbrl.md「短信の原文 HTML」）。

PDF と同じ中身を画面で読むための原文。サマリーの iXBRL と `Attachment/qualitative.htm`
（添付資料まるごと）を入れる。qualitative.htm が無い書類（REIT・一部の訂正）は本表の iXBRL を
ファイル名順に入れる。どれも無いフォルダなしの ZIP（業績予想・配当予想の修正 tse-rvfc など）は
ZIP の中の iXBRL をサマリーとして入れる。元の HTML のまま gzip で持ち、script などは表示のときに除く。
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
from app.xbrl.zip_scope import kept_names


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
    if not out:
        # フォルダなしの ZIP（tse-rvfc など）。Summary/ も Attachment/ も無い
        out = [("summary", n) for n in sorted(n for n in htm if n.lower().endswith("-ixbrl.htm"))]
    return out


def read_documents(zip_path: Path, doc_id: str | None = None) -> list[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        picked = pick_documents(kept_names(doc_id, zf.namelist(), zf.read))
        return [
            {"seq": i, "part": part, "file_name": name.rsplit("/", 1)[-1], "html": zf.read(name)}
            for i, (part, name) in enumerate(picked)
        ]


def store_documents(db: Session, filing: XbrlFiling, zip_path: Path) -> int:
    """filing の xbrl_documents を作り直す。commit しない。戻り値は入れた件数。

    0 件なら filing.documents_status='empty' にして、毎時の埋め直しで読み直さない。
    """
    docs = read_documents(zip_path, filing.doc_id)
    db.execute(delete(XbrlDocument).where(XbrlDocument.filing_id == filing.id))
    filing.documents_status = "stored" if docs else "empty"
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
    """TDnet の filing のうち xbrl_documents が無いもの。新しい順。iXBRL の無い ZIP と、読んで 0 件だった ZIP は除く。"""
    q = (
        db.query(XbrlFiling)
        .filter(XbrlFiling.source == "tdnet")
        .filter(~exists().where(XbrlDocument.filing_id == XbrlFiling.id))
        .filter((XbrlFiling.parse_error.is_(None)) | (XbrlFiling.parse_error != "no-ixbrl"))
        .filter((XbrlFiling.documents_status.is_(None)) | (XbrlFiling.documents_status != "empty"))
        .order_by(XbrlFiling.submitted_at.desc())
    )
    if limit:
        q = q.limit(limit)
    return q.all()


def fill_missing_documents(db: Session, dest: Path, *, limit: int | None = None) -> dict[str, int]:
    """既にパースした filing に原文を足す（デプロイ後の流し込みと取りこぼしの拾い直し）。"""
    stored = empty = missing = errors = 0
    for filing in filings_missing_documents(db, limit=limit):
        zip_path = Path(dest) / filing.raw_path
        if not zip_path.is_file():
            missing += 1
            continue
        try:
            if store_documents(db, filing, zip_path):
                stored += 1
            else:
                empty += 1
                print(f"documents empty doc_id={filing.doc_id} code={filing.code}", flush=True)
            db.commit()
        except Exception as exc:
            db.rollback()
            errors += 1
            print(f"documents failed doc_id={filing.doc_id} code={filing.code} err={exc}", flush=True)
    return {"stored": stored, "empty": empty, "missing_zip": missing, "errors": errors}


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
_HEAD_OPEN = re.compile(r"<head\b[^>]*>", re.I)
# iOS Safari の文字の自動拡大を止める（KAB-168）。viewport の無い原文で、横に長い段落（目次の …… の行）だけ
# 文字を大きくし、リーダーが次の行に落ちて両端そろえで 1 字ずつ離れる
TEXT_SIZE_ADJUST = "<style>html{-webkit-text-size-adjust:100%;text-size-adjust:100%}</style>"
# sanitize_html の出力の版。出力を変えたら上げる。原文の ETag に混ぜ、ブラウザに古い HTML を使わせない
# （ETag が原文の sha256 だけだと、出力を変えても 304 が返り続ける。KAB-168）
SANITIZE_VERSION = "2"


def sanitize_html(html: str) -> str:
    """会社が作った HTML から、動くもの・外へ出るものを除く。本当の防御は API の CSP と sandbox。"""
    s = _XML_DECL.sub("", html)
    s = _DROP_BLOCKS.sub("", s)
    s = _DROP_TAGS.sub("", s)
    s = _DROP_META_REFRESH.sub("", s)
    s = _ON_ATTR.sub("", s)
    s = _JS_URL.sub(r'\1=\2#\2', s)
    s = _EXT_IMG.sub("", s)
    m = _HEAD_OPEN.search(s)
    return s[:m.end()] + TEXT_SIZE_ADJUST + s[m.end():] if m else TEXT_SIZE_ADJUST + s
