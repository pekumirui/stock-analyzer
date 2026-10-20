from __future__ import annotations

import csv
import io
import os
import stat
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from .enums import DocumentFileType

_ZIP_MAGIC = b"PK"
_PDF_MAGIC = b"%PDF"


class UnsafeZipError(ValueError):
    """A ZIP member pointed outside the destination or used a symlink."""


@dataclass(frozen=True)
class DownloadedFile:
    doc_id: str
    file_type: DocumentFileType
    content: bytes
    content_type: str

    @property
    def suggested_filename(self) -> str:
        return f"{self.doc_id}_{int(self.file_type)}{self.file_type.suffix}"

    def save(self, path: str | os.PathLike[str], *, overwrite: bool = False) -> Path:
        dest = Path(path)
        if dest.exists() and dest.is_dir():
            dest = dest / self.suggested_filename
        if dest.exists() and not overwrite:
            raise FileExistsError(str(dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.content)
        return dest

    def open_zip(self) -> zipfile.ZipFile:
        if self.file_type is DocumentFileType.PDF or self.content[:4] == _PDF_MAGIC:
            raise TypeError("PDF downloads cannot be opened as ZIP")
        if self.content[:2] != _ZIP_MAGIC:
            raise TypeError("body is not a ZIP archive")
        return zipfile.ZipFile(io.BytesIO(self.content))

    def extract(self, dest: str | os.PathLike[str]) -> list[Path]:
        destination = Path(dest)
        destination.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        with self.open_zip() as zf:
            for info in zf.infolist():
                target = _safe_zip_target(destination, info)
                if info.is_dir() or info.filename.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as src, target.open("wb") as out:
                    out.write(src.read())
                written.append(target)
        return written

    def iter_csv_tables(self) -> Iterator[tuple[str, list[dict[str, str]]]]:
        with self.open_zip() as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                if info.is_dir() or not name.lower().endswith(".csv"):
                    continue
                raw = zf.read(info)
                text = decode_csv_bytes(raw)
                yield Path(name).name, parse_csv_table(text)


def decode_csv_bytes(data: bytes) -> str:
    # NOTE(spec-gap): the API spec defers CSV encoding to the viewing guide.
    # Observed type-5 files are UTF-16 with BOM and tab separators.
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    for encoding in ("utf-8", "cp932"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("unable to decode CSV bytes as utf-16, utf-8, or cp932")


def parse_csv_table(text: str) -> list[dict[str, str]]:
    sample = text.splitlines()[0] if text else ""
    delimiter = "\t" if sample.count("\t") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    rows: list[dict[str, str]] = []
    for row in reader:
        rows.append({(k or ""): (v if v is not None else "") for k, v in row.items()})
    return rows


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def _safe_zip_target(dest: Path, info: zipfile.ZipInfo) -> Path:
    name = info.filename.replace("\\", "/")
    if _is_symlink_member(info):
        raise UnsafeZipError(f"refusing ZIP symlink member {info.filename!r}")
    if name.startswith("/") or name.startswith("\\"):
        raise UnsafeZipError(f"refusing absolute ZIP path {info.filename!r}")
    parts = [part for part in Path(name).parts if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise UnsafeZipError(f"refusing ZIP path with parent segments {info.filename!r}")
    root = dest.resolve()
    target = (root.joinpath(*parts)).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise UnsafeZipError(f"refusing ZIP path outside destination {info.filename!r}") from exc
    return target
