"""EDINET の XBRL（書類取得 API type=1）からテキストブロックの表と文を読む（KAB-79）。

自己株券買付状況報告書・公開買付の書類は、数値の多くが表の形のテキストブロックにしか無い
（CSV の type=5 では表がつぶれて 1 行の文になる）。インスタンス（XBRL/PublicDoc/*.xbrl）の
テキストブロックを HTML として読み、行（セルの列）と文に分ける。設計は docs/DESIGN_edinet.md §6。
"""
from __future__ import annotations

import io
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EdinetDocument

_DASHES = frozenset("－―‐-ー—–")
_ERA = {"令和": 2018, "平成": 1988}
_DATE_RE = re.compile(r"(令和|平成)?\s*(\d{1,4}|元)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2}\s*日|末日?)")
_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def nfkc(s: str | None) -> str:
    return unicodedata.normalize("NFKC", s or "")


def squash(s: str | None) -> str:
    """NFKC して空白を全部取る。行の見出しの比較用。"""
    return re.sub(r"\s+", "", nfkc(s))


def is_dash(cell: str | None) -> bool:
    """「－」「―」だけのセル（該当なし）。単位の（株）は無視する。"""
    s = re.sub(r"[（(][^）)]*[）)]", "", squash(cell))
    return bool(s) and all(ch in _DASHES for ch in s)


def number(cell: str | None) -> Decimal | None:
    """セルの最初の数（3,682 / 52.97 / 1,520,000（上限））。ダッシュ・空は NULL。"""
    s = nfkc(cell).replace("，", ",")
    if not s.strip() or is_dash(s):
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        return Decimal(m[0].replace(",", ""))
    except InvalidOperation:
        return None


def integer(cell: str | None) -> int | None:
    v = number(cell)
    return None if v is None else int(v)


def jp_dates(s: str | None) -> list[date]:
    """文の中の日付を順に（2026年８月６日、令和8年8月6日、2026年９月末日）。"""
    out = []
    for era, y, m, d in _DATE_RE.findall(nfkc(s)):
        year = 1 if y == "元" else int(y)
        if era:
            year += _ERA[era]
        try:
            if d.startswith("末"):
                nxt = date(year + int(m) // 12, int(m) % 12 + 1, 1)
                out.append(date.fromordinal(nxt.toordinal() - 1))
            else:
                out.append(date(year, int(m), int(d.rstrip("日 "))))
        except ValueError:
            continue
    return out


class _Blocks(HTMLParser):
    """HTML → [("T", 行のリスト) | ("X", 文)]。表の中の表は外の表のセルの文字にする。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, object]] = []
        self.depth = 0
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None
        self.para: list[str] = []

    def _flush_para(self) -> None:
        text = re.sub(r"\s+", " ", "".join(self.para)).strip()
        if text:
            self.items.append(("X", text))
        self.para = []

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            if self.depth == 1:
                self._flush_para()
                self.rows = []
        elif self.depth == 1 and tag == "tr":
            self.row = []
        elif self.depth == 1 and tag in ("td", "th"):
            self.cell = []
        elif tag == "br":
            self.handle_data(" ")
        elif self.depth == 0 and tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()

    def handle_endtag(self, tag):
        if tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self.items.append(("T", self.rows))
                self.rows = []
        elif self.depth == 1 and tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append(re.sub(r"\s+", " ", "".join(self.cell)).strip())
            self.cell = None
        elif self.depth == 1 and tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif self.depth == 0 and tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush_para()

    def handle_data(self, data):
        if self.depth:
            if self.cell is not None:
                self.cell.append(data)
        else:
            self.para.append(data)

    def close(self):
        super().close()
        self._flush_para()


@dataclass
class Block:
    """1 つのテキストブロック。tables は表ごとの行、texts は表の外の文。"""
    items: list[tuple[str, object]] = field(default_factory=list)

    @property
    def tables(self) -> list[list[list[str]]]:
        return [v for k, v in self.items if k == "T"]

    @property
    def texts(self) -> list[str]:
        return [v for k, v in self.items if k == "X"]

    def rows(self) -> list[list[str]]:
        """全部の表の行をつなげたもの。"""
        return [r for t in self.tables for r in t]

    def text(self) -> str:
        """表も含めた全文（セルは空白で区切る）。"""
        parts = []
        for k, v in self.items:
            parts.append(v if k == "X" else " ".join(" ".join(r) for r in v))
        return " ".join(parts)


def parse_html(html: str) -> Block:
    p = _Blocks()
    p.feed(html)
    p.close()
    return Block(p.items)


@dataclass
class Instance:
    """XBRL インスタンス。facts はローカル名 → 最初の値（テキストブロックは HTML のまま）。"""
    facts: dict[str, str] = field(default_factory=dict)
    name: str = ""                    # インスタンスのファイル名（jpcrp170000-sbr-001_… など）

    def value(self, local: str) -> str | None:
        v = self.facts.get(local)
        if v is None:
            return None
        v = v.strip()
        return None if not v or is_dash(v) else v

    def block(self, *locals_: str) -> Block | None:
        """最初に見つかったテキストブロック。無ければ None。"""
        for local in locals_:
            raw = self.facts.get(local)
            if raw is not None:
                return parse_html(raw)
        return None

    def has(self, local: str) -> bool:
        return local in self.facts


def read_instance(content: bytes) -> Instance:
    """type=1 の ZIP から XBRL/PublicDoc の .xbrl を読む。"""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = [n for n in zf.namelist() if n.endswith(".xbrl") and "PublicDoc/" in n]
        if not names:
            raise ValueError(f"XBRL インスタンスが無い: {zf.namelist()[:10]}")
        first = min(names)
        raw = zf.read(first)
    root = ET.fromstring(raw)
    inst = Instance(name=first.rsplit("/", 1)[-1])
    for el in root:
        tag = el.tag
        if not isinstance(tag, str) or "}" not in tag:
            continue
        local = tag.rsplit("}", 1)[1]
        if local in ("context", "unit", "schemaRef"):
            continue
        inst.facts.setdefault(local, "".join(el.itertext()))
    return inst


@dataclass
class DocMeta:
    """docID ごとにまとめた edinet_documents。修正があれば修正の行（新しいメタデータ）を使う。"""
    doc: EdinetDocument
    gone: bool                         # 取下げ・不開示


def doc_metas(db: Session, doc_types: tuple[str, ...]) -> dict[str, DocMeta]:
    """書類種別の docID → DocMeta。メタデータは ope_datetime が新しい修正の行、無ければ元の行。"""
    T = EdinetDocument
    rows = db.scalars(select(T).where(T.doc_type_code.in_(doc_types))
                      .order_by(T.file_date, T.seq_number)).all()
    floor = datetime.min  # noqa: DTZ901  JST naive で比べる
    best: dict[str, EdinetDocument] = {}
    gone: set[str] = set()
    for r in rows:
        if r.withdrawal_status == "2" or r.disclosure_status == "2":
            gone.add(r.doc_id)
        if r.withdrawal_status == "1" or r.disclosure_status in ("1", "3"):
            continue                    # 取下書・不開示の開始/解除の行はメタデータを持たない
        cur = best.get(r.doc_id)
        if cur is None or (r.doc_info_edit_status == "1"
                           and (cur.doc_info_edit_status != "1"
                                or (r.ope_datetime or r.submit_datetime or floor)
                                >= (cur.ope_datetime or cur.submit_datetime or floor))):
            best[r.doc_id] = r
    return {k: DocMeta(v, k in gone) for k, v in best.items()}


def years_ago(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(month=2, day=28, year=day.year - years)


def chain_root(doc_id: str, parent: dict[str, str | None]) -> str:
    """parent をたどった大元。輪になっていたら止める。"""
    seen = {doc_id}
    cur = doc_id
    while parent.get(cur) and parent[cur] not in seen:
        cur = parent[cur]
        seen.add(cur)
    return cur


def sample_ids(ids: list[str], n: int = 8) -> str:
    if len(ids) <= n:
        return ",".join(ids)
    return ",".join(ids[:n]) + f",...+{len(ids) - n}"
