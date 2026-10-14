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
    def __init__(self, http: httpx.Client | None = None) -> None:
        self.http = http or httpx.Client(timeout=120, follow_redirects=True,
                                         headers={"User-Agent": METI_USER_AGENT})

    def get(self, url: str) -> bytes:
        for attempt in range(3):
            try:
                r = self.http.get(url)
                r.raise_for_status()
                return r.content
            except httpx.HTTPError:
                if attempt == 2:
                    raise
                time.sleep(3 * (attempt + 1))
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
