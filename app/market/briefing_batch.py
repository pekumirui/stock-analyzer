"""決算説明資料を Claude の Message Batches API で読む（KAB-349）。入口は jobs/market/briefing_extract.py。

1. submit: まだ読んでいない資料（briefing_documents）の PDF をページに分け、Claude に回すページを 1 つの依頼にして
   Batch に出す。briefing_extracts に submitted で 1 行
2. collect: 終わった Batch の結果を受け取り、照合・検算（briefing_extract.verify）をして briefing_pages /
   briefing_facts に書き、briefing_extracts を done / failed にする

依頼は 1 ページ 1 件（入力が 10 万トークンを超えると単価が上がるので、PDF を丸ごと 1 件にしない）。
モデルは Haiku 5.5、プロンプトは deck-v3、effort は low（analyzer/deck_reader の試験で決めた）。
"""
from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from app.db import run_sqlite_write, upsert
from app.market import briefing_extract as bx
from app.models import BriefingDocument, BriefingExtract, BriefingFact, BriefingPage

JST = ZoneInfo("Asia/Tokyo")
MODEL = "claude-haiku-5-5"
EFFORT = "low"
MAX_TOKENS = 16000
STATUS_SUBMITTED = "submitted"
STATUS_DONE = "done"
STATUS_FAILED = "failed"
# 1 つの Batch の上限（API は 10 万件・256MB）より小さく切る
BATCH_MAX_REQUESTS = 5000
BATCH_MAX_BYTES = 150 * 1024 * 1024
# 同じ開示番号が複数の出所にあるとき、どの PDF を読むか（前ほど優先）
SOURCE_ORDER = ("tdnet", "irbank", "homepage")
TASK = "上の画像（スライド）と文字データを読み、指定の形式の JSON オブジェクトだけを返してください。"


def _now() -> datetime:
    return datetime.now(JST).replace(tzinfo=None)


def request_key(document_id: str) -> str:
    """Batch の custom_id の頭（英数字だけ。document_id はファイル名で . などを含みうる）。"""
    return "d" + hashlib.sha1(document_id.encode(), usedforsecurity=False).hexdigest()[:20]


def custom_id(key: str, page: int) -> str:
    return f"{key}-p{page}"


def parse_custom_id(cid: str) -> tuple[str, int]:
    key, _, page = cid.rpartition("-p")
    return key, int(page)


def build_params(page: dict, thinking: bool = True) -> dict:
    """1 ページの依頼（Messages API の引数）。システムプロンプトは全依頼で同じなのでキャッシュに置く。"""
    params = {
        "model": MODEL,
        "max_tokens": MAX_TOKENS,
        "system": [{"type": "text", "text": bx.SYSTEM, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.standard_b64encode(page["png"]).decode()}},
            {"type": "text", "text": bx.page_text(page) + "\n\n" + TASK},
        ]}],
        "output_config": {"effort": EFFORT},
    }
    if not thinking:
        params["thinking"] = {"type": "disabled"}
    return params


@dataclass
class Pending:
    document_id: str
    source: str
    code: str
    rel_path: str
    pubdate: str


def pending_documents(db: Session, codes: Iterable[str] | None = None, limit: int | None = None,
                      redo: bool = False) -> list[Pending]:
    """読む資料。新しい開示から。redo なら読み取り済み（done / failed）も読み直す（submitted は除く）。"""
    q = select(BriefingDocument.document_id, BriefingDocument.source, BriefingDocument.code,
               BriefingDocument.rel_path, BriefingDocument.pubdate).where(BriefingDocument.status == "saved")
    if codes:
        q = q.where(BriefingDocument.code.in_(list(codes)))
    rows = db.execute(q.order_by(BriefingDocument.pubdate.desc())).all()
    states = dict(db.execute(select(BriefingExtract.document_id, BriefingExtract.status)).all())
    best: dict[str, Pending] = {}
    for r in rows:
        st = states.get(r.document_id)
        if st == STATUS_SUBMITTED or (st and not redo):
            continue
        cur = best.get(r.document_id)
        rank = SOURCE_ORDER.index(r.source) if r.source in SOURCE_ORDER else len(SOURCE_ORDER)
        if cur is None or rank < (SOURCE_ORDER.index(cur.source) if cur.source in SOURCE_ORDER else len(SOURCE_ORDER)):
            best[r.document_id] = Pending(r.document_id, r.source, r.code, r.rel_path, r.pubdate)
    out = sorted(best.values(), key=lambda p: p.pubdate, reverse=True)
    return out[:limit] if limit else out


@dataclass
class SubmitResult:
    batches: list[str] = field(default_factory=list)
    documents: int = 0
    requests: int = 0
    skipped: list[str] = field(default_factory=list)   # PDF が無い・読めない

    def summary(self, dry_run: bool) -> str:
        head = "dry-run" if dry_run else "submitted"
        return (f"{head}: documents={self.documents} requests={self.requests} batches={len(self.batches)} "
                f"skipped={len(self.skipped)}")


def submit(db: Session, client, briefing_root: Path, docs: list[Pending], *, thinking: bool = True,
           dry_run: bool = False, log: Callable[[str], None] = print) -> SubmitResult:
    """docs の Claude に回すページを Batch に出し、briefing_extracts に submitted で書く。"""
    out = SubmitResult()
    chunk: list[dict] = []
    chunk_docs: list[tuple[Pending, int, int]] = []   # (資料, ページ数, 回すページ数)
    size = 0

    def flush():
        nonlocal chunk, chunk_docs, size
        if not chunk:
            return
        batch_id = None
        if not dry_run:
            batch_id = client.messages.batches.create(requests=chunk).id
            out.batches.append(batch_id)
            now = _now()
            rows = [{
                "document_id": p.document_id, "source": p.source, "code": p.code, "status": STATUS_SUBMITTED,
                "batch_id": batch_id, "request_key": request_key(p.document_id), "model": MODEL,
                "prompt_version": bx.PROMPT_VERSION, "pages": n, "llm_pages": m, "error_pages": None, "facts": None,
                "verified": None, "sum_checks": None, "sum_mismatch": None, "input_tokens": None,
                "output_tokens": None, "error": None, "submitted_at": now, "extracted_at": None,
            } for p, n, m in chunk_docs]
            run_sqlite_write(db, lambda: upsert(db, BriefingExtract, rows, commit=False))
        log(f"batch {batch_id or '(dry-run)'}: documents={len(chunk_docs)} requests={len(chunk)} "
            f"bytes={size / 1e6:.1f}MB")
        chunk, chunk_docs, size = [], [], 0

    for p in docs:
        path = briefing_root / p.rel_path
        try:
            pages, _meta = bx.load_pages(path.read_bytes())
        except Exception as e:  # noqa: BLE001 - 壊れた PDF は飛ばして次へ
            out.skipped.append(p.document_id)
            log(f"skip {p.document_id}: {type(e).__name__}: {e}")
            continue
        key = request_key(p.document_id)
        reqs = [{"custom_id": custom_id(key, pg["page"]), "params": build_params(pg, thinking)}
                for pg in pages if bx.needs_llm(pg["kind"])]
        if not reqs:
            out.skipped.append(p.document_id)
            log(f"skip {p.document_id}: Claude に回すページが無い")
            continue
        req_bytes = sum(len(json.dumps(r, ensure_ascii=False)) for r in reqs)
        if chunk and (len(chunk) + len(reqs) > BATCH_MAX_REQUESTS or size + req_bytes > BATCH_MAX_BYTES):
            flush()
        chunk.extend(reqs)
        chunk_docs.append((p, len(pages), len(reqs)))
        size += req_bytes
        out.documents += 1
        out.requests += len(reqs)
    flush()
    return out


@dataclass
class CollectResult:
    ended: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    done: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (f"collected: batches_ended={len(self.ended)} batches_waiting={len(self.waiting)} "
                f"done={self.done} failed={self.failed}")


def _message_text(message) -> str:
    return "".join(getattr(b, "text", "") for b in message.content if getattr(b, "type", "") == "text")


def _tokens(usage) -> tuple[int, int]:
    inp = (usage.input_tokens or 0) + (getattr(usage, "cache_read_input_tokens", 0) or 0) \
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return inp, usage.output_tokens or 0


def read_results(client, batch_id: str) -> dict[str, dict[int, dict]]:
    """Batch の結果 → {request_key: {page: {"text" | "error", "input_tokens", "output_tokens"}}}。"""
    out: dict[str, dict[int, dict]] = defaultdict(dict)
    for r in client.messages.batches.results(batch_id):
        key, page = parse_custom_id(r.custom_id)
        res = r.result
        if res.type == "succeeded":
            msg = res.message
            inp, outp = _tokens(msg.usage)
            item = {"text": _message_text(msg), "input_tokens": inp, "output_tokens": outp}
            if msg.stop_reason == "max_tokens":
                item["truncated"] = True
            elif msg.stop_reason == "refusal":
                item = {"error": "refusal", "input_tokens": inp, "output_tokens": outp}
        else:
            err = getattr(getattr(res, "error", None), "type", None) or res.type
            item = {"error": f"{res.type}: {err}", "input_tokens": 0, "output_tokens": 0}
        out[key][page] = item
    return out


def _page_result(page: dict, item: dict | None) -> dict:
    """1 ページの結果 → {title, summary, tables, facts, sum_checks} か {error}。"""
    if item is None:
        return {"error": "結果が無い"}
    if "error" in item:
        return {"error": item["error"]}
    try:
        parsed = bx.parse_json(item["text"])
    except (ValueError, json.JSONDecodeError) as e:
        why = "出力が max_tokens で切れた" if item.get("truncated") else f"JSON が読めない: {e}"
        return {"error": why}
    return bx.verify(page, parsed)


def store_document(db: Session, ext: BriefingExtract, pages: list[dict], results: dict[int, dict]) -> str:
    """1 冊分を briefing_pages / briefing_facts に書き（前の読み取りは消す）、briefing_extracts を更新する。"""
    page_rows, fact_rows, done_pages = [], [], []
    inp = outp = 0
    for pg in pages:
        n = pg["page"]
        if not bx.needs_llm(pg["kind"]):
            page_rows.append({"document_id": ext.document_id, "page": n, "kind": pg["kind"], "title": None,
                              "summary": None, "tables_json": None, "error": None})
            continue
        item = results.get(n)
        if item:
            inp += item.get("input_tokens", 0)
            outp += item.get("output_tokens", 0)
        r = _page_result(pg, item)
        r["page"], r["kind"] = n, pg["kind"]
        done_pages.append(r)
        page_rows.append({
            "document_id": ext.document_id, "page": n, "kind": pg["kind"], "title": r.get("title"),
            "summary": r.get("summary"), "tables_json": json.dumps(r["tables"], ensure_ascii=False) if r.get("tables") else None,
            "error": r.get("error"),
        })
        for f in r.get("facts") or []:
            fact_rows.append({
                "document_id": ext.document_id, "page": n, "metric": str(f.get("metric") or ""),
                "segment": None if f.get("segment") in (None, "null", "") else str(f["segment"]),
                "period": None if f.get("period") is None else str(f["period"]), "period_kind": f.get("period_kind"),
                "value": str(f.get("value")), "value_num": f.get("value_num"), "unit": f.get("unit"),
                "part_of": f.get("part_of"), "verified": bool(f.get("verified")), "sum_status": f.get("sum_status"),
            })
    s = bx.stats([dict(p, facts=p.get("facts", [])) for p in done_pages])
    errors = sum(1 for p in done_pages if p.get("error"))
    status = STATUS_DONE if errors < len(done_pages) else STATUS_FAILED

    def write():
        db.execute(delete(BriefingFact).where(BriefingFact.document_id == ext.document_id))
        db.execute(delete(BriefingPage).where(BriefingPage.document_id == ext.document_id))
        if page_rows:
            upsert(db, BriefingPage, page_rows, commit=False)
        if fact_rows:
            db.execute(insert(BriefingFact.__table__), fact_rows)
        ext.status = status
        ext.pages, ext.llm_pages, ext.error_pages = len(pages), len(done_pages), errors
        ext.facts, ext.verified = s["facts"], s["verified"]
        ext.sum_checks, ext.sum_mismatch = s["sum_checks"], s["sum_mismatch"]
        ext.input_tokens, ext.output_tokens = inp, outp
        ext.error = None if status == STATUS_DONE else "すべてのページが読めなかった"
        ext.extracted_at = _now()

    run_sqlite_write(db, write)
    return status


def collect(db: Session, client, briefing_root: Path, *, log: Callable[[str], None] = print) -> CollectResult:
    """submitted の Batch を見て、終わったものを書く。終わっていない Batch は次の実行で。"""
    out = CollectResult()
    batch_ids = [b for (b,) in db.execute(
        select(BriefingExtract.batch_id).where(BriefingExtract.status == STATUS_SUBMITTED).distinct()).all() if b]
    for batch_id in batch_ids:
        batch = client.messages.batches.retrieve(batch_id)
        if batch.processing_status != "ended":
            out.waiting.append(batch_id)
            log(f"batch {batch_id}: {batch.processing_status}")
            continue
        out.ended.append(batch_id)
        results = read_results(client, batch_id)
        exts = db.execute(select(BriefingExtract).where(BriefingExtract.batch_id == batch_id,
                                                        BriefingExtract.status == STATUS_SUBMITTED)).scalars().all()
        for ext in exts:
            doc = db.get(BriefingDocument, (ext.source, ext.document_id))
            try:
                if doc is None:
                    raise FileNotFoundError("briefing_documents に無い")
                pages, _meta = bx.load_pages((briefing_root / doc.rel_path).read_bytes(), render=False)
            except Exception as e:  # noqa: BLE001 - PDF が消えた・壊れた資料は failed にして次へ
                def fail(ext=ext, e=e):
                    ext.status, ext.error, ext.extracted_at = STATUS_FAILED, f"PDF が読めない: {e}", _now()
                run_sqlite_write(db, fail)
                out.failed += 1
                continue
            status = store_document(db, ext, pages, results.get(ext.request_key, {}))
            out.done += status == STATUS_DONE
            out.failed += status == STATUS_FAILED
            log(f"{ext.document_id} ({ext.code}): {status} facts={ext.facts} verified={ext.verified} "
                f"sum_mismatch={ext.sum_mismatch}/{ext.sum_checks} errors={ext.error_pages}/{ext.llm_pages}")
    return out
