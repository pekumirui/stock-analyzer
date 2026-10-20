from __future__ import annotations

import csv
import io
import zipfile
from datetime import date

import httpx

from .config import (
    CODE_AGGREGATION_CSV,
    EDINET_CODE_LIST_EN,
    EDINET_CODE_LIST_JA,
    FUND_CODE_LIST_EN,
    FUND_CODE_LIST_JA,
)
from .files import decode_csv_bytes
from .models import CodeAggregationEntry, EdinetCodeEntry, FundCodeEntry

_EDINET_CODE_HEADERS = ("ＥＤＩＮＥＴコード", "EDINETコード", "EDINET Code", "EDINETコード")
_SEC_HEADERS = ("証券コード", "Securities Code", "保安コード")
_JCN_HEADERS = ("提出者法人番号", "法人番号", "JCN")
_NAME_HEADERS = ("提出者名", "Filer Name", "提出者名（漢字）")
_FUND_HEADERS = ("ファンドコード", "Fund Code")
_FUND_NAME_HEADERS = ("ファンド名", "Fund Name", "提出者名")


def download_edinet_code_list(
    lang: str = "ja",
    *,
    client: httpx.Client | None = None,
) -> list[EdinetCodeEntry]:
    url = EDINET_CODE_LIST_JA if lang == "ja" else EDINET_CODE_LIST_EN
    payload = _get_bytes(url, client)
    return parse_edinet_code_list(payload)


def download_fund_code_list(
    lang: str = "ja",
    *,
    client: httpx.Client | None = None,
) -> list[FundCodeEntry]:
    url = FUND_CODE_LIST_JA if lang == "ja" else FUND_CODE_LIST_EN
    payload = _get_bytes(url, client)
    return parse_fund_code_list(payload)


def download_code_aggregation_list(
    *,
    client: httpx.Client | None = None,
) -> list[CodeAggregationEntry]:
    payload = _get_bytes(CODE_AGGREGATION_CSV, client)
    return parse_code_aggregation_list(payload)


def parse_edinet_code_list(payload: bytes) -> list[EdinetCodeEntry]:
    text = _zip_csv_text(payload)
    rows = _rows_from_named_header(text, _EDINET_CODE_HEADERS)
    out: list[EdinetCodeEntry] = []
    for row in rows:
        code = _pick(row, _EDINET_CODE_HEADERS)
        if not code:
            continue
        extra = dict(row)
        out.append(
            EdinetCodeEntry(
                edinet_code=code,
                sec_code=_pick(row, _SEC_HEADERS) or None,
                jcn=_pick(row, _JCN_HEADERS) or None,
                filer_name=_pick(row, _NAME_HEADERS) or None,
                extra=extra,
            )
        )
    return out


def parse_fund_code_list(payload: bytes) -> list[FundCodeEntry]:
    text = _zip_csv_text(payload)
    rows = _rows_from_named_header(text, _FUND_HEADERS)
    out: list[FundCodeEntry] = []
    for row in rows:
        code = _pick(row, _FUND_HEADERS)
        if not code:
            continue
        extra = dict(row)
        out.append(
            FundCodeEntry(
                fund_code=code,
                filer_name=_pick(row, _FUND_NAME_HEADERS) or None,
                extra=extra,
            )
        )
    return out


def parse_code_aggregation_list(payload: bytes) -> list[CodeAggregationEntry]:
    text = decode_csv_bytes(payload)
    lines = text.splitlines()
    if len(lines) < 2:
        return []
    header_index = next(
        (index for index, line in enumerate(lines) if "廃止" in line and "継続" in line),
        0,
    )
    reader = csv.reader(io.StringIO("\n".join(lines[header_index:])))
    rows = list(reader)
    if not rows:
        return []
    header = rows[0]
    abolished_idx = _column_index(header, ("廃止",))
    surviving_idx = _column_index(header, ("継続",))
    date_idx = _column_index(header, ("集約", "処理日", "日付"))
    entries: list[CodeAggregationEntry] = []
    for row in rows[1:]:
        if len(row) <= max(abolished_idx, surviving_idx):
            continue
        abolished = row[abolished_idx].strip()
        surviving = row[surviving_idx].strip()
        if not abolished or not surviving:
            continue
        process_date: date | None = None
        if date_idx is not None and date_idx < len(row) and row[date_idx].strip():
            try:
                process_date = date.fromisoformat(row[date_idx].strip().replace("/", "-"))
            except ValueError:
                process_date = None
        entries.append(
            CodeAggregationEntry(
                process_date=process_date,
                abolished_code=abolished,
                surviving_code=surviving,
            )
        )
    return entries


def resolve_edinet_code(
    code: str,
    aggregation: list[CodeAggregationEntry] | None = None,
    *,
    client: httpx.Client | None = None,
) -> str:
    mapping = aggregation if aggregation is not None else download_code_aggregation_list(client=client)
    table = {item.abolished_code: item.surviving_code for item in mapping}
    current = code
    seen: set[str] = set()
    while current in table and current not in seen:
        seen.add(current)
        current = table[current]
    return current


def _get_bytes(url: str, client: httpx.Client | None) -> bytes:
    if client is not None:
        response = client.get(url)
        response.raise_for_status()
        return response.content
    with httpx.Client(follow_redirects=True, timeout=60.0) as owned:
        response = owned.get(url)
        response.raise_for_status()
        return response.content


def _zip_csv_text(payload: bytes) -> str:
    if payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            names = [name for name in zf.namelist() if name.lower().endswith(".csv")]
            if not names:
                raise ValueError("ZIP did not contain a CSV file")
            return decode_csv_bytes(zf.read(names[0]))
    return decode_csv_bytes(payload)


def _rows_from_named_header(text: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    lines = text.splitlines()
    header_index = _find_header_index(lines, required)
    sample = lines[header_index] if lines else ""
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_index:])), delimiter=delimiter)
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def _find_header_index(lines: list[str], tokens: tuple[str, ...]) -> int:
    for index, line in enumerate(lines):
        if any(token in line for token in tokens):
            return index
    return 0


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        if row.get(key):
            return row[key]
    lowered = {name.lower(): value for name, value in row.items()}
    for key in keys:
        value = lowered.get(key.lower())
        if value:
            return value
    return ""


def _column_index(header: list[str], tokens: tuple[str, ...]) -> int:
    for index, name in enumerate(header):
        if any(token in name for token in tokens):
            return index
    return 0
