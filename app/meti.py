"""経済産業省の統計 Excel。素材の月次量（app/material_stats.py）のうち e-Stat API に無い最新月を取る。

生産動態統計の「統計表一覧」ページから 3 種類の Excel を拾う（シート「実数表」は縦持ちで形が同じ）:
- 速報   sokuho/h2daaYYYYMMs.xlsx              revision 1。品目と指標が確報より少ない
- 確報   kakuho/YYYY/h2daaYYYYMM_jikei.xlsx     revision 2。その年の 1 月から
- 年間補正号 kakuho/YYYY/h2daaYYYY_hosei_jikei.xlsx revision 3（e-Stat の年報と同じ値）
METI は既定の User-Agent を 403 にするので、名乗った User-Agent を付ける。
"""
from __future__ import annotations

import io
import re
import time
import unicodedata
from typing import NamedTuple
from urllib.parse import urljoin

import httpx

from .config import METI_SEIDOU_URL, METI_USER_AGENT
from .material_stats import (
    REVISION_KAKUHO, REVISION_NENPO, REVISION_SOKUHO, SeidouRecord, product_name,
)

_LINKS = (
    (re.compile(r'href="([^"]*/sokuho/h2daa\d{6}s\.xlsx)"'), REVISION_SOKUHO),
    (re.compile(r'href="([^"]*/kakuho/\d{4}/h2daa\d{6}_jikei\.xlsx)"'), REVISION_KAKUHO),
    (re.compile(r'href="([^"]*/kakuho/\d{4}/h2daa\d{4}_hosei_jikei\.xlsx)"'), REVISION_NENPO),
)
_HEADERS = {"report": "調査票番号", "section": "項目名", "product": "品目名", "metric": "アイテム名", "unit": "単位名"}


class MetiError(RuntimeError):
    pass


class MetiFile(NamedTuple):
    url: str
    revision: int

    @property
    def name(self) -> str:
        return self.url.rsplit("/", 1)[-1]


def seidou_files(html: str, base: str = METI_SEIDOU_URL) -> list[MetiFile]:
    """統計表一覧の HTML → 取り込む Excel。revision の低い順（後から書くほうが勝つ）"""
    out: list[MetiFile] = []
    for pattern, revision in _LINKS:
        for href in dict.fromkeys(pattern.findall(html)):
            out.append(MetiFile(urljoin(base, href), revision))
    return out


class MetiClient:
    def __init__(self, http: httpx.Client | None = None, pause: float = 2.0) -> None:
        self.http = http or httpx.Client(timeout=120, follow_redirects=True,
                                         headers={"User-Agent": METI_USER_AGENT})
        self.pause = pause  # 続けて取ると空の 200 が返ることがある

    def get(self, url: str) -> bytes:
        for attempt in range(4):
            time.sleep(self.pause)
            try:
                r = self.http.get(url)
                r.raise_for_status()
                # 混んでいると 200 で空や HTML が返ることがある
                if not r.content or (url.endswith(".xlsx") and not r.content.startswith(b"PK")):
                    raise httpx.HTTPError(f"empty or not an xlsx: {url}")
                return r.content
            except httpx.HTTPError:
                if attempt == 3:
                    raise
                time.sleep(10 * (attempt + 1))
        raise MetiError(url)  # 到達しない


def seidou_records(data: bytes, reports: set[str]) -> list[SeidouRecord]:
    """Excel のシート「実数表」→ セル。2 行目が見出し（調査票番号 … 単位名、月は YYYYMM）。製品の行だけ"""
    from openpyxl import load_workbook  # 重いので使うときだけ

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    if "実数表" not in wb.sheetnames:
        raise MetiError(f"no sheet 実数表: {wb.sheetnames}")
    rows = wb["実数表"].iter_rows(values_only=True)
    header: tuple | None = None
    for i, row in enumerate(rows):
        if _HEADERS["report"] in row:
            header = row
            break
        if i > 10:
            break
    if header is None:
        raise MetiError("header row not found")
    col = {k: header.index(v) for k, v in _HEADERS.items() if v in header}
    if len(col) != len(_HEADERS):
        raise MetiError(f"missing columns: {sorted(set(_HEADERS.values()) - set(header))}")
    months = [(i, f"{h[:4]}-{h[4:]}") for i, h in enumerate(header)
              if isinstance(h, str) and re.fullmatch(r"\d{6}", h)]
    if not months:
        raise MetiError("no month columns")
    out: list[SeidouRecord] = []
    for row in rows:
        report = str(row[col["report"]] or "")
        if report not in reports or row[col["section"]] != "製品":
            continue
        product, metric, unit = (product_name(row[col["product"]]), str(row[col["metric"]] or ""),
                                 str(row[col["unit"]] or ""))
        for i, month in months:
            if i < len(row) and row[i] not in (None, ""):
                out.append(SeidouRecord(report, product, metric, unit, month, row[i]))
    return out


# ---------- 石油統計（資源・エネルギー統計） ----------
# 確報 sekiyuka/xls/seYYYYMMkakji.xlsx: 時系列表。品目ごとのブロック（（４）ガソリン …）に直近 15 か月ほどの月の行
# 速報 sekiyuso/result/xls/h2j5*j.xlsx: 当月だけ。シート「需給概要」（品目 × 指標）と「原油受払」
# 年報 sekiyuka/xls/h2dhhpeYYYYk.xlsx: その年の 12 か月。横持ちの表（①原油処理 / ②原油在庫 / 石油製品月別国内向販売 …）

_SEKIYU_KAKUHO = re.compile(r'href="([^"]*xls/se\d{6}kakji\.xlsx)"')
_SEKIYU_SOKUHO = re.compile(r'href="([^"]*result/xls/h2j\w+j\.xlsx)"')
_SEKIYU_NENPO = re.compile(r'href="([^"]*xls/h2dhhpe\d{4}k\.xlsx)"')
_TITLE = re.compile(r"^\s*(?:[（(][0-9０-９]+[）)]|[①-⑳])\s*")


class SekiyuCell(NamedTuple):
    block: str   # 品目（ガソリン / 原油 …）。空白を除いた NFKC
    column: str  # 指標（国内向販売 / 消費 / 在庫 …）
    unit: str
    month: str   # YYYY-MM
    raw: object


def _norm(x: object) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(x if x is not None else "")))


def _block_name(raw: str) -> str:
    """「　（４）ガソリン　Gasoline」→ ガソリン。「　　①Ａ重油　Fuel Oil A」→ A重油"""
    first = _TITLE.sub("", raw).split()[0] if _TITLE.sub("", raw).split() else ""
    return _norm(first)


def _reiwa(year: str) -> int:
    return 2018 + int(year)


def sekiyu_files(kakuho_html: str, sokuho_html: str, kakuho_base: str, sokuho_base: str) -> list[MetiFile]:
    """年報（各年の 12 か月、revision 3）、確報は一覧の最新 1 本（時系列なので過去月も入っている）、速報は当月の 1 本"""
    out: list[MetiFile] = [MetiFile(urljoin(kakuho_base, h), REVISION_NENPO)
                           for h in sorted(set(_SEKIYU_NENPO.findall(kakuho_html)))]
    kakuho = sorted(set(_SEKIYU_KAKUHO.findall(kakuho_html)))
    if kakuho:
        out.append(MetiFile(urljoin(kakuho_base, kakuho[-1]), REVISION_KAKUHO))
    for href in dict.fromkeys(_SEKIYU_SOKUHO.findall(sokuho_html)):
        out.append(MetiFile(urljoin(sokuho_base, href), REVISION_SOKUHO))
    return out


def sekiyu_kakuho_cells(data: bytes) -> list[SekiyuCell]:
    """確報の時系列表。ブロックは見出し行「年・期・月」ごとに左右 2 つ。見出しの無い左ブロックは上の続き（原油の在庫など）"""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[SekiyuCell] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        last_name: dict[int, str] = {}
        for h, row in enumerate(rows):
            if len(row) < 3 or _norm(row[1]) != "年・期・月":
                continue
            above = range(max(0, h - 3), h)
            titles = {c: _block_name(str(v)) for r in above for c, v in enumerate(rows[r])
                      if isinstance(v, str) and _TITLE.match(v)}
            starts = sorted({1, *titles})
            width = max(len(r) for r in rows)
            first = next((r for r in range(h + 1, len(rows)) if rows[r][1] not in (None, "")), len(rows))
            for i, s in enumerate(starts):
                end = starts[i + 1] if i + 1 < len(starts) else width
                name = titles.get(s) or last_name.get(s, "")
                last_name[s] = name
                cols = range(max(s, 2), end)
                unit = next((m.group(1) for r in above for c in cols if c < len(rows[r])
                             and (m := re.search(r"単位[:：]\s*([^）)]+)", _norm(rows[r][c])))), "")
                heads = {}
                for c in cols:
                    for r in range(h - 1, first):
                        v = _norm(rows[r][c]) if c < len(rows[r]) else ""
                        if v and "単位" not in v and not _TITLE.match(str(rows[r][c])):
                            heads[c] = v
                            break
                year, monthly = None, False
                for r in range(first, len(rows)):
                    label = _norm(rows[r][1])
                    if "前年同月比" in label:
                        break
                    m = re.match(r"令和(\d+)年(\d+)(月?)$", label)
                    if m and "~" not in label:
                        year, month, monthly = _reiwa(m.group(1)), int(m.group(2)), True
                    elif monthly and re.fullmatch(r"\d{1,2}", label):
                        month = int(label)
                    else:
                        continue
                    for c, head in heads.items():
                        if c < len(rows[r]) and rows[r][c] not in (None, ""):
                            out.append(SekiyuCell(name, head, unit, f"{year}-{month:02d}", rows[r][c]))
    return out


def sekiyu_sokuho_cells(data: bytes) -> list[SekiyuCell]:
    """速報の「需給概要」（列が品目、行が指標）と「原油受払」（行が指標、当月の数量列）"""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[SekiyuCell] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        text = " ".join(_norm(v) for r in rows[:3] for v in r if v is not None)
        if "需給概要" in ws.title:
            m = re.search(r"\((\d+)年(\d+)月\)", text)
            h = next((i for i, r in enumerate(rows) if "区分" in _norm(r[0])), None)
            if not m or h is None:
                raise MetiError(f"{ws.title}: month or header not found")
            month = f"{_reiwa(m.group(1))}-{int(m.group(2)):02d}"
            names = {c: _norm(rows[h][c]) + _norm(rows[h + 1][c]) for c in range(2, len(rows[h]))}
            units = {c: _norm(rows[h + 2][c]) for c in names}
            for r in rows[h + 3:]:
                label = _norm(r[0])
                if not label:
                    continue
                for c, name in names.items():
                    if name and c < len(r) and r[c] not in (None, ""):
                        out.append(SekiyuCell(name, label, units[c], month, r[c]))
        elif rows and "原油受払" in _norm(rows[0][0]):  # 4 頁目も題名に「原油受払」を含むので 1 行目で見る
            h = next((i for i, r in enumerate(rows) if any(re.fullmatch(r"\d+年\d+月", _norm(v)) for v in r)), None)
            if h is None:
                raise MetiError(f"{ws.title}: month header not found")
            c, m = next((c, mm) for c, v in enumerate(rows[h]) if (mm := re.fullmatch(r"(\d+)年(\d+)月", _norm(v))))
            month = f"{_reiwa(m.group(1))}-{int(m.group(2)):02d}"
            unit_col = c - 1
            for r in rows[h + 1:]:
                label = _norm(r[0])
                if label.startswith(("2.", "3.")) or "原油出荷内訳" in label:
                    break
                if label and c < len(r) and r[c] not in (None, ""):
                    out.append(SekiyuCell("原油", label, _norm(r[unit_col]), month, r[c]))
    return out


def _cjk(x: object) -> str:
    """見出しのセルから日本語の行だけ（英訳の行や「Total」を落とす）。改行は _x000D_ のことがある"""
    lines = re.split(r"[\r\n]+|_x000D_", str(x if x is not None else ""))
    # 「・」(U+30FB) は英訳の行にも出る（Fuel Oil B・C）ので仮名に数えない
    return "".join(_norm(v) for v in lines if re.search(r"[\u3040-\u30fa\u30fc-\u30ff\u4e00-\u9fff]", v))


def sekiyu_nenpo_cells(data: bytes) -> list[SekiyuCell]:
    """年報の横持ちの表。block は列の見出し（ガソリン / 合計 …）、column は表の題（原油処理 / 石油製品月別国内向販売 …）"""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out: list[SekiyuCell] = []
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        for h, row in enumerate(rows):
            if not any(_norm(v) == "年・期・月" for v in row[:4]):
                continue
            title_row = next((r for r in range(h - 1, max(-1, h - 4), -1)
                              if any(isinstance(v, str) and _TITLE.match(v) for v in rows[r][:4])), None)
            if title_row is None:
                continue
            title_cell = next(v for v in rows[title_row][:4] if isinstance(v, str) and _TITLE.match(v))
            table = _block_name(title_cell.split("/")[0])
            unit = next((m.group(1) for v in rows[title_row]
                         if (m := re.search(r"単位[:：]\s*([^/）)]+)", _norm(v)))), "")
            first = next((r for r in range(h + 1, len(rows))
                          if re.match(r"令和", "".join(_norm(v) for v in rows[r][:4]))), len(rows))
            heads = {}
            for c in range(4, max(len(r) for r in rows[h:first] or [row])):
                name = "".join(_cjk(rows[r][c]) for r in range(h, first) if c < len(rows[r]))
                if name and not name.startswith(("年・期・月", "Year")):
                    heads[c] = name
            # 「kl、アスファルト以降:t」: その列から右は t
            units = {c: unit for c in heads}
            if (m := re.fullmatch(r"(\w+)、(.+)以降[:：](\w+)", unit)):
                switch = next((c for c, name in sorted(heads.items()) if name == m.group(2)), None)
                units = {c: m.group(3) if switch is not None and c >= switch else m.group(1) for c in heads}
            year, monthly = None, False
            for r in range(first, len(rows)):
                label = "".join(_norm(v) for v in rows[r][:4])
                if not label:
                    if monthly:
                        break
                    continue
                m = re.match(r"令和(\d+)年(\d{1,2})(月?)$", label)
                if m and 1 <= int(m.group(2)) <= 12:
                    year, month, monthly = _reiwa(m.group(1)), int(m.group(2)), True
                elif monthly and re.fullmatch(r"\d{1,2}", label) and 1 <= int(label) <= 12:
                    month = int(label)
                elif monthly:
                    break
                else:
                    continue
                for c, head in heads.items():
                    if c < len(rows[r]) and rows[r][c] not in (None, ""):
                        out.append(SekiyuCell(head, table, units[c], f"{year}-{month:02d}", rows[r][c]))
    return out
