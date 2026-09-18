"""OLE Compound File + BIFF8 sheet reader. No extra pip deps.

Reads a single-sheet BIFF8 .xls (NUMBER / RK / LABELSST / LABEL / FORMULA cache).
Optional xlrd or soffice are fallbacks if the builtin path fails.
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def _u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


def _i32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def _u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def _f64(buf: bytes, off: int) -> float:
    return struct.unpack_from("<d", buf, off)[0]


def decode_rk(val: int) -> float:
    div100 = bool(val & 1)
    as_int = bool(val & 2)
    if as_int:
        num = float(struct.unpack("<i", struct.pack("<I", val & 0xFFFFFFFC))[0] >> 2)
    else:
        num = struct.unpack("<d", struct.pack("<II", 0, val & 0xFFFFFFFC))[0]
    if div100:
        num /= 100.0
    return num


def _ole_fat(data: bytes) -> tuple[int, list[int], int, int, int, int, int]:
    if data[:8] != OLE_MAGIC:
        raise ValueError("not an OLE Compound File")
    sec_shift = _u16(data, 0x1E)
    mini_shift = _u16(data, 0x20)
    sec_size = 1 << sec_shift
    mini_size = 1 << mini_shift
    fat_count = _u32(data, 0x2C)
    dir_start = _u32(data, 0x30)
    mini_cutoff = _u32(data, 0x38)
    mini_fat_start = _u32(data, 0x3C)
    difat_start = _u32(data, 0x44)
    difat_count = _u32(data, 0x48)
    difat = [_u32(data, 0x4C + 4 * i) for i in range(109)]
    sid = difat_start
    for _ in range(difat_count):
        off = 512 + sid * sec_size
        for i in range((sec_size // 4) - 1):
            difat.append(_u32(data, off + 4 * i))
        sid = _u32(data, off + sec_size - 4)
    fat: list[int] = []
    for i in range(fat_count):
        fs = difat[i]
        off = 512 + fs * sec_size
        for j in range(sec_size // 4):
            fat.append(_u32(data, off + 4 * j))
    return sec_size, fat, dir_start, mini_cutoff, mini_size, mini_fat_start, mini_shift


def _chain(data: bytes, fat: list[int], sec_size: int, start: int, size: int | None = None) -> bytes:
    out = bytearray()
    sid = start
    seen: set[int] = set()
    while sid < 0xFFFFFFFA:
        if sid in seen:
            raise ValueError("OLE FAT loop")
        seen.add(sid)
        off = 512 + sid * sec_size
        out.extend(data[off : off + sec_size])
        sid = fat[sid]
        if size is not None and len(out) >= size:
            break
    return bytes(out[:size] if size is not None else out)


def ole_stream(data: bytes, name: str) -> bytes:
    sec_size, fat, dir_start, mini_cutoff, mini_size, mini_fat_start, _mini_shift = _ole_fat(data)
    dir_raw = _chain(data, fat, sec_size, dir_start)
    entries: list[tuple[str, int, int, int]] = []
    for i in range(0, len(dir_raw), 128):
        ent = dir_raw[i : i + 128]
        if len(ent) < 128:
            break
        nlen = _u16(ent, 64)
        nm = ent[: max(nlen - 2, 0)].decode("utf-16le") if nlen >= 2 else ""
        entries.append((nm, ent[66], _u32(ent, 116), _u32(ent, 120)))
    mini_fat: list[int] = []
    if mini_fat_start < 0xFFFFFFFA:
        raw = _chain(data, fat, sec_size, mini_fat_start)
        mini_fat = [_u32(raw, i) for i in range(0, len(raw), 4)]
    root = next(e for e in entries if e[1] == 5)
    mini_data = _chain(data, fat, sec_size, root[2], root[3]) if root[3] else b""

    def stream(start: int, size: int) -> bytes:
        if size < mini_cutoff:
            out = bytearray()
            sid = start
            seen: set[int] = set()
            while sid < 0xFFFFFFFA and len(out) < size:
                if sid in seen:
                    raise ValueError("OLE mini FAT loop")
                seen.add(sid)
                off = sid * mini_size
                out.extend(mini_data[off : off + mini_size])
                sid = mini_fat[sid]
            return bytes(out[:size])
        return _chain(data, fat, sec_size, start, size)

    want = name.rstrip("\x00")
    for nm, typ, start, size in entries:
        if typ == 2 and nm.rstrip("\x00") == want:
            return stream(start, size)
    names = [e[0] for e in entries]
    raise KeyError(f"{name} not in {names}")


def iter_biff(stream: bytes) -> Iterator[tuple[int, bytes]]:
    i = 0
    n = len(stream)
    while i + 4 <= n:
        code = _u16(stream, i)
        ln = _u16(stream, i + 2)
        i += 4
        payload = stream[i : i + ln]
        i += ln
        yield code, payload


class _SstReader:
    def __init__(self, first: bytes, continues: list[bytes]) -> None:
        self.blocks = [first[8:]] + continues
        self.continue_flags = [False] + [True] * len(continues)
        self.bi = 0
        self.off = 0

    def remaining(self) -> int:
        if self.bi >= len(self.blocks):
            return 0
        return len(self.blocks[self.bi]) - self.off

    def _next_block(self) -> None:
        self.bi += 1
        self.off = 0

    def read(self, n: int) -> bytes:
        out = bytearray()
        while n > 0:
            if self.bi >= len(self.blocks):
                raise EOFError("SST truncated")
            take = min(n, self.remaining())
            if take == 0:
                self._next_block()
                continue
            blk = self.blocks[self.bi]
            out.extend(blk[self.off : self.off + take])
            self.off += take
            n -= take
        return bytes(out)

    def at_continue(self) -> bool:
        return self.remaining() == 0 and self.bi + 1 < len(self.blocks) and self.continue_flags[self.bi + 1]

    def consume_continue_flag(self) -> int:
        self._next_block()
        flag = self.blocks[self.bi][0]
        self.off = 1
        return flag


def parse_sst(first: bytes, continues: list[bytes]) -> list[str]:
    unique = _i32(first, 4)
    r = _SstReader(first, continues)
    out: list[str] = []
    for _ in range(unique):
        nchars = _u16(r.read(2), 0)
        flags = r.read(1)[0]
        rich = _u16(r.read(2), 0) if flags & 0x08 else 0
        phonetic = _u32(r.read(4), 0) if flags & 0x04 else 0
        compressed = (flags & 1) == 0
        chunks: list[str] = []
        got = 0
        while got < nchars:
            if r.at_continue():
                compressed = (r.consume_continue_flag() & 1) == 0
            avail = r.remaining()
            if avail <= 0:
                if r.bi + 1 < len(r.blocks):
                    r._next_block()
                    continue
                raise EOFError("SST chars")
            need = nchars - got
            if compressed:
                take = min(need, avail)
                chunks.append(r.read(take).decode("latin-1"))
                got += take
            else:
                take = min(need, avail // 2)
                if take == 0:
                    if r.at_continue():
                        compressed = (r.consume_continue_flag() & 1) == 0
                        continue
                    raise EOFError("SST utf16")
                chunks.append(r.read(take * 2).decode("utf-16le"))
                got += take
        if rich:
            r.read(rich * 4)
        if phonetic:
            r.read(phonetic)
        out.append("".join(chunks))
    return out


def _boundsheet_name(payload: bytes) -> tuple[int, str]:
    pos = _u32(payload, 0)
    n = payload[6]
    flag = payload[7]
    raw = payload[8:]
    if flag & 1:
        name = raw[: n * 2].decode("utf-16le")
    else:
        name = raw[:n].decode("latin-1")
    return pos, name


def _label_text(payload: bytes) -> str:
    n = _u16(payload, 6)
    flags = payload[8] if len(payload) > 8 else 0
    raw = payload[9:]
    if flags & 1:
        return raw[: n * 2].decode("utf-16le", errors="replace")
    return raw[:n].decode("latin-1", errors="replace")


def grid_from_workbook(wb: bytes) -> list[list[Any]]:
    sst: list[str] = []
    pending: bytes | None = None
    conts: list[bytes] = []
    sheets: list[tuple[int, str]] = []
    for code, payload in iter_biff(wb):
        if code == 0x00FC:
            if pending is not None:
                sst = parse_sst(pending, conts)
            pending = payload
            conts = []
            continue
        if code == 0x003C and pending is not None:
            conts.append(payload)
            continue
        if pending is not None:
            sst = parse_sst(pending, conts)
            pending = None
            conts = []
        if code == 0x0085:
            sheets.append(_boundsheet_name(payload))
    if pending is not None:
        sst = parse_sst(pending, conts)
    if not sheets:
        raise ValueError("no BIFF8 sheet")
    cells: dict[tuple[int, int], Any] = {}
    max_r = 0
    max_c = 0

    def put(row: int, col: int, value: Any) -> None:
        nonlocal max_r, max_c
        cells[(row, col)] = value
        if row > max_r:
            max_r = row
        if col > max_c:
            max_c = col

    for code, payload in iter_biff(wb[sheets[0][0] :]):
        if code == 0x000A:
            break
        if code == 0x0203 and len(payload) >= 14:
            put(_u16(payload, 0), _u16(payload, 2), _f64(payload, 6))
        elif code == 0x027E and len(payload) >= 10:
            put(_u16(payload, 0), _u16(payload, 2), decode_rk(_u32(payload, 6)))
        elif code == 0x00BD and len(payload) >= 6:
            row = _u16(payload, 0)
            col = _u16(payload, 2)
            last = _u16(payload, len(payload) - 2)
            off = 4
            while col <= last and off + 6 <= len(payload) - 2:
                put(row, col, decode_rk(_u32(payload, off + 2)))
                off += 6
                col += 1
        elif code == 0x00FD and len(payload) >= 10:
            idx = _u32(payload, 6)
            put(_u16(payload, 0), _u16(payload, 2), sst[idx] if idx < len(sst) else "")
        elif code == 0x0204 and len(payload) >= 9:
            put(_u16(payload, 0), _u16(payload, 2), _label_text(payload))
        elif code == 0x0006 and len(payload) >= 14:
            cached = payload[6:14]
            if cached[6:] != b"\xff\xff":
                put(_u16(payload, 0), _u16(payload, 2), _f64(payload, 6))
    grid = [["" for _ in range(max_c + 1)] for _ in range(max_r + 1)]
    for (row, col), value in cells.items():
        grid[row][col] = value
    return grid


def read_sheet_builtin(source: Path | bytes) -> list[list[Any]]:
    if isinstance(source, (bytes, bytearray)):
        data = bytes(source)
    else:
        data = Path(source).read_bytes()
    try:
        wb = ole_stream(data, "Workbook")
    except KeyError:
        wb = ole_stream(data, "Book")
    return grid_from_workbook(wb)


def read_sheet_xlrd(path: Path) -> list[list[Any]]:
    import xlrd  # type: ignore[import-not-found]

    book = xlrd.open_workbook(str(path))
    sh = book.sheet_by_index(0)
    grid: list[list[Any]] = []
    for r in range(sh.nrows):
        row: list[Any] = []
        for c in range(sh.ncols):
            cell = sh.cell(r, c)
            if cell.ctype in (0, 6):
                row.append("")
            else:
                row.append(cell.value)
        grid.append(row)
    return grid


def read_sheet_soffice(path: Path) -> list[list[Any]]:
    exe = shutil.which("soffice") or shutil.which("libreoffice")
    if not exe:
        raise FileNotFoundError("soffice")
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(
            [exe, "--headless", "--convert-to", "csv", "--outdir", tmp, str(path)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        csvs = list(Path(tmp).glob("*.csv"))
        if not csvs:
            raise ValueError("soffice wrote no csv")
        text = csvs[0].read_text(encoding="utf-8", errors="replace")
    import csv
    from io import StringIO

    return [list(row) for row in csv.reader(StringIO(text))]


def read_sheet(path: Path) -> list[list[Any]]:
    data = path.read_bytes()
    try:
        return read_sheet_builtin(data)
    except Exception:
        try:
            return read_sheet_xlrd(path)
        except Exception:
            return read_sheet_soffice(path)
