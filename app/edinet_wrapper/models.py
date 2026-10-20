from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    DisclosureStatus,
    DocInfoEditStatus,
    DocType,
    DocumentFileType,
    LegalStatus,
    WithdrawalStatus,
)

JST = ZoneInfo("Asia/Tokyo")

_OPTIONAL_STR_FIELDS = (
    "edinet_code",
    "sec_code",
    "jcn",
    "filer_name",
    "fund_code",
    "ordinance_code",
    "form_code",
    "doc_type_code",
    "doc_description",
    "issuer_edinet_code",
    "subject_edinet_code",
    "parent_doc_id",
)


class _Base(BaseModel):
    model_config = ConfigDict(populate_by_name=True, frozen=True, extra="allow")


def _empty_to_none(value: Any) -> Any:
    # NOTE(spec-gap): the spec uses JSON null; empty strings appear in some dumps.
    if value == "":
        return None
    return value


def _parse_date(value: Any) -> Any:
    value = _empty_to_none(value)
    if value is None or (isinstance(value, date) and not isinstance(value, datetime)):
        return value
    if isinstance(value, datetime):
        return value.date()
    return date.fromisoformat(str(value))


def _parse_jst_datetime(value: Any) -> Any:
    value = _empty_to_none(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=JST)
    return datetime.strptime(str(value), "%Y-%m-%d %H:%M").replace(tzinfo=JST)


def _parse_flag(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if value == "1":
        return True
    if value == "0":
        return False
    raise ValueError(f"flag must be '0' or '1', got {value!r}")


def _parse_csv_tuple(value: Any) -> Any:
    if value is None or value == "":
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return tuple(part.strip() for part in str(value).split(",") if part.strip())


class DocumentListMetadata(_Base):
    title: str
    file_date: date = Field(alias="date")
    list_type: str = Field(alias="type")
    count: int
    process_datetime: datetime = Field(alias="processDateTime")
    status: str
    message: str

    @model_validator(mode="before")
    @classmethod
    def _flatten(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        parameter = data.get("parameter") or {}
        resultset = data.get("resultset") or {}
        flat = dict(data)
        if "date" not in flat and "date" in parameter:
            flat["date"] = parameter["date"]
        if "type" not in flat and "type" in parameter:
            flat["type"] = parameter["type"]
        if "count" not in flat and "count" in resultset:
            flat["count"] = resultset["count"]
        return flat

    @field_validator("file_date", mode="before")
    @classmethod
    def _date(cls, value: Any) -> Any:
        return _parse_date(value)

    @field_validator("process_datetime", mode="before")
    @classmethod
    def _process_dt(cls, value: Any) -> Any:
        parsed = _parse_jst_datetime(value)
        if parsed is None:
            raise ValueError("processDateTime is required")
        return parsed

    @field_validator("list_type", mode="before")
    @classmethod
    def _list_type_str(cls, value: Any) -> Any:
        return str(value)


class DocumentEntry(_Base):
    file_date: date
    seq_number: int = Field(alias="seqNumber")
    doc_id: str = Field(alias="docID")
    edinet_code: str | None = Field(default=None, alias="edinetCode")
    sec_code: str | None = Field(default=None, alias="secCode")
    jcn: str | None = Field(default=None, alias="JCN")
    filer_name: str | None = Field(default=None, alias="filerName")
    fund_code: str | None = Field(default=None, alias="fundCode")
    ordinance_code: str | None = Field(default=None, alias="ordinanceCode")
    form_code: str | None = Field(default=None, alias="formCode")
    doc_type_code: str | None = Field(default=None, alias="docTypeCode")
    period_start: date | None = Field(default=None, alias="periodStart")
    period_end: date | None = Field(default=None, alias="periodEnd")
    submit_datetime: datetime | None = Field(default=None, alias="submitDateTime")
    doc_description: str | None = Field(default=None, alias="docDescription")
    issuer_edinet_code: str | None = Field(default=None, alias="issuerEdinetCode")
    subject_edinet_code: str | None = Field(default=None, alias="subjectEdinetCode")
    subsidiary_edinet_codes: tuple[str, ...] = Field(default=(), alias="subsidiaryEdinetCode")
    current_report_reasons: tuple[str, ...] = Field(default=(), alias="currentReportReason")
    parent_doc_id: str | None = Field(default=None, alias="parentDocID")
    ope_datetime: datetime | None = Field(default=None, alias="opeDateTime")
    withdrawal_status: WithdrawalStatus = Field(alias="withdrawalStatus")
    doc_info_edit_status: DocInfoEditStatus = Field(alias="docInfoEditStatus")
    disclosure_status: DisclosureStatus = Field(alias="disclosureStatus")
    has_xbrl: bool = Field(alias="xbrlFlag")
    has_pdf: bool = Field(alias="pdfFlag")
    has_attachment: bool = Field(alias="attachDocFlag")
    has_english: bool = Field(alias="englishDocFlag")
    has_csv: bool = Field(alias="csvFlag")
    legal_status: LegalStatus = Field(alias="legalStatus")

    @field_validator(*_OPTIONAL_STR_FIELDS, mode="before")
    @classmethod
    def _optional_str(cls, value: Any) -> Any:
        return _empty_to_none(value)

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def _optional_date(cls, value: Any) -> Any:
        return _parse_date(value)

    @field_validator("submit_datetime", "ope_datetime", mode="before")
    @classmethod
    def _optional_dt(cls, value: Any) -> Any:
        return _parse_jst_datetime(value)

    @field_validator("file_date", mode="before")
    @classmethod
    def _file_date(cls, value: Any) -> Any:
        return _parse_date(value)

    @field_validator("subsidiary_edinet_codes", "current_report_reasons", mode="before")
    @classmethod
    def _csv_tuple(cls, value: Any) -> Any:
        return _parse_csv_tuple(value)

    @field_validator("has_xbrl", "has_pdf", "has_attachment", "has_english", "has_csv", mode="before")
    @classmethod
    def _flags(cls, value: Any) -> Any:
        return _parse_flag(value)

    @property
    def key(self) -> tuple[date, int]:
        return (self.file_date, self.seq_number)

    @property
    def is_event_record(self) -> bool:
        return (
            self.withdrawal_status is WithdrawalStatus.WITHDRAWAL_NOTICE
            or self.doc_info_edit_status is DocInfoEditStatus.EDIT_NOTICE
            or self.disclosure_status in {DisclosureStatus.NONDISCLOSURE_START, DisclosureStatus.NONDISCLOSURE_END}
        )

    @property
    def is_available(self) -> bool:
        return (
            self.legal_status is not LegalStatus.EXPIRED
            and self.withdrawal_status is WithdrawalStatus.NONE
            and not self.is_event_record
        )

    @property
    def doc_type(self) -> DocType | None:
        if self.doc_type_code is None:
            return None
        try:
            return DocType(self.doc_type_code)
        except ValueError:
            return None

    @property
    def sec_code4(self) -> str | None:
        if self.sec_code is None:
            return None
        return self.sec_code[:4]

    def has_file(self, file_type: DocumentFileType) -> bool:
        if file_type is DocumentFileType.MAIN:
            return self.is_available
        if file_type is DocumentFileType.PDF:
            return self.has_pdf
        if file_type is DocumentFileType.ATTACHMENT:
            return self.has_attachment
        if file_type is DocumentFileType.ENGLISH:
            return self.has_english
        if file_type is DocumentFileType.CSV:
            # NOTE(spec-gap): partial nondisclosure can still ship XBRL/CSV; trust the flag.
            return self.has_csv
        return False


class DocumentList(_Base):
    metadata: DocumentListMetadata
    results: tuple[DocumentEntry, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _attach_file_date(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        metadata = data.get("metadata")
        if not isinstance(metadata, dict):
            return data
        try:
            parsed_meta = DocumentListMetadata.model_validate(metadata)
        except Exception:
            return data
        file_date = parsed_meta.file_date.isoformat()
        raw_results = data.get("results") or []
        stamped: list[Any] = []
        for item in raw_results:
            if isinstance(item, dict) and "file_date" not in item and "fileDate" not in item:
                stamped.append({**item, "file_date": file_date})
            else:
                stamped.append(item)
        out = dict(data)
        out["metadata"] = parsed_meta
        out["results"] = stamped
        return out


class EdinetCodeEntry(_Base):
    edinet_code: str
    sec_code: str | None = None
    jcn: str | None = None
    filer_name: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class FundCodeEntry(_Base):
    fund_code: str
    filer_name: str | None = None
    extra: dict[str, str] = Field(default_factory=dict)


class CodeAggregationEntry(_Base):
    process_date: date | None = None
    abolished_code: str
    surviving_code: str
