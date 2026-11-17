"""バックアップ先だけの圧縮ストア。論理オフセットは SQLite のまま、ディスクは zlib。

標準ライブラリの sqlite3 は VFS を差し替えられないので、宛先接続だけ APSW で開く。
継承した実ファイルは空のままにする。mmap するとその空ファイルを読むので止める。
punch はファイルシステムブロック境界が要るので、圧縮片は 4KiB に揃えて書く。
"""
from __future__ import annotations

import ctypes
import os
import zlib
from pathlib import Path

import apsw

BLOCK = 64 * 1024
ALIGN = 4096
_PUNCH = 1 | 2  # FALLOC_FL_KEEP_SIZE | FALLOC_FL_PUNCH_HOLE
_libc = ctypes.CDLL(None, use_errno=True)
_libc.fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int64, ctypes.c_int64]
_libc.fallocate.restype = ctypes.c_int


class StoreError(Exception):
    """圧縮先が予算を超えた、または読み戻せない。"""


class BlockStore:
    """64KiB ブロックを zlib レベル 1 で足していく。書き直し分は punch で返す。"""

    def __init__(self, path: Path, budget: int) -> None:
        self.path = path
        self.budget = budget
        self.f = path.open("w+b")
        self.idx: list[tuple[int, int, int]] = []
        self.logical = 0
        self.dirty: tuple[int, bytearray] | None = None
        self._punch_ok = True

    def allocated(self) -> int:
        self.f.flush()
        return os.fstat(self.f.fileno()).st_blocks * 512

    def flush(self) -> None:
        if self.dirty is None:
            return
        bi, buf = self.dirty
        self.dirty = None
        self._store_block(bi, bytes(buf))

    def read(self, amount: int, offset: int) -> bytes:
        if amount < 0 or offset < 0:
            raise StoreError(f"bad read amount={amount} offset={offset}")
        out = bytearray()
        pos = offset
        end = offset + amount
        while pos < end:
            bi = pos // BLOCK
            boff = pos % BLOCK
            raw = self._block_bytes(bi)
            take = min(BLOCK - boff, end - pos)
            out += raw[boff : boff + take]
            pos += take
        if offset + amount > self.logical:
            keep = max(0, self.logical - offset)
            out = out[:keep] + b"\x00" * (amount - keep)
        return bytes(out)

    def write(self, data: bytes, offset: int) -> None:
        if offset < 0:
            raise StoreError(f"bad write offset={offset}")
        if not data:
            return
        end = offset + len(data)
        if end > self.logical:
            self.logical = end
        view = memoryview(data)
        src = 0
        pos = offset
        while src < len(data):
            bi = pos // BLOCK
            boff = pos % BLOCK
            take = min(BLOCK - boff, len(data) - src)
            if self.dirty is not None and self.dirty[0] != bi:
                self.flush()
            if self.dirty is None:
                self.dirty = (bi, bytearray(self._block_bytes(bi)))
            self.dirty[1][boff : boff + take] = view[src : src + take]
            pos += take
            src += take

    def truncate(self, size: int) -> None:
        if size < 0:
            raise StoreError(f"bad truncate {size}")
        if self.dirty is not None:
            bi, buf = self.dirty
            start = bi * BLOCK
            if start >= size:
                self.dirty = None
            elif start + BLOCK > size:
                buf[size - start :] = b"\x00" * (BLOCK - (size - start))
        self.logical = size
        keep = (size + BLOCK - 1) // BLOCK if size else 0
        while len(self.idx) > keep:
            _off, span, _ncomp = self.idx.pop()
            if span:
                self._punch(_off, span)

    def discard_through(self, logical_end: int) -> None:
        """logical_end より前のブロックを空きに返す。そこはもう読み戻さない。"""
        self.flush()
        last = logical_end // BLOCK
        for i in range(min(last, len(self.idx))):
            off, span, _ncomp = self.idx[i]
            if not span:
                continue
            self._punch(off, span)
            self.idx[i] = (0, 0, 0)

    def close(self) -> None:
        try:
            self.flush()
            self.f.flush()
        finally:
            self.f.close()

    def _block_bytes(self, index: int) -> bytes:
        if self.dirty is not None and self.dirty[0] == index:
            return bytes(self.dirty[1])
        if index >= len(self.idx) or self.idx[index][1] == 0:
            return b"\x00" * BLOCK
        off, _span, ncomp = self.idx[index]
        self.f.seek(off)
        raw = zlib.decompress(self.f.read(ncomp))
        if len(raw) != BLOCK:
            raise StoreError(f"block {index} len={len(raw)}")
        return raw

    def _store_block(self, index: int, raw: bytes) -> None:
        while len(self.idx) <= index:
            self.idx.append((0, 0, 0))
        old = self.idx[index]
        if raw == b"\x00" * BLOCK:
            self.idx[index] = (0, 0, 0)
        else:
            comp = zlib.compress(raw, 1)
            self.f.seek(0, os.SEEK_END)
            raw_end = self.f.tell()
            aligned = (raw_end + ALIGN - 1) // ALIGN * ALIGN
            if aligned != raw_end:
                self.f.write(b"\x00" * (aligned - raw_end))
            self.f.write(comp)
            span_end = (aligned + len(comp) + ALIGN - 1) // ALIGN * ALIGN
            if self.f.tell() < span_end:
                self.f.write(b"\x00" * (span_end - self.f.tell()))
            self.idx[index] = (aligned, span_end - aligned, len(comp))
        if old[1]:
            self._punch(old[0], old[1])
        used = self.allocated()
        if used > self.budget:
            raise StoreError(f"compress budget exceeded used={used} budget={self.budget}")

    def _punch(self, offset: int, length: int) -> None:
        if not self._punch_ok or length <= 0:
            return
        if offset % ALIGN or length % ALIGN:
            self._punch_ok = False
            return
        rc = _libc.fallocate(self.f.fileno(), _PUNCH, offset, length)
        if rc != 0:
            self._punch_ok = False


def _flag_bits(flags: int | list[int] | tuple[int, ...]) -> int:
    if isinstance(flags, int):
        return flags
    bits = 0
    for item in flags:
        bits |= int(item)
    return bits


class _File(apsw.VFSFile):
    def __init__(self, vfs: CompressVFS, name: object, flags: object) -> None:
        super().__init__("", name, flags)
        self.store: BlockStore | None = None
        bits = _flag_bits(flags)  # type: ignore[arg-type]
        if not bits & apsw.SQLITE_OPEN_MAIN_DB:
            return
        text = name.filename() if isinstance(name, apsw.URIFilename) else str(name or "")
        self.store = BlockStore(Path(text + ".blk"), vfs.budget)
        vfs.main = self.store

    def xRead(self, amount: int, offset: int) -> bytes:
        if self.store is None:
            return super().xRead(amount, offset)
        return self.store.read(amount, offset)

    def xWrite(self, data: object, offset: int) -> None:
        if self.store is None:
            super().xWrite(data, offset)
            return
        self.store.write(bytes(data), offset)  # type: ignore[arg-type]

    def xFileSize(self) -> int:
        if self.store is None:
            return super().xFileSize()
        return self.store.logical

    def xTruncate(self, size: int) -> None:
        if self.store is None:
            super().xTruncate(size)
            return
        self.store.truncate(size)

    def xSync(self, flags: int) -> None:
        if self.store is not None:
            self.store.flush()
            self.store.f.flush()
            os.fsync(self.store.f.fileno())
        super().xSync(flags)

    def xFileControl(self, op: int, ptr: int) -> bool:
        if self.store is not None and op == apsw.SQLITE_FCNTL_MMAP_SIZE:
            ctypes.cast(ptr, ctypes.POINTER(ctypes.c_int64))[0] = 0
            return True
        return bool(super().xFileControl(op, ptr))

    def xClose(self) -> None:
        if self.store is not None:
            self.store.flush()
            self.store.f.flush()
        super().xClose()


class CompressVFS(apsw.VFS):
    def __init__(self, name: str, budget: int) -> None:
        self.vfs_name = name
        self.budget = budget
        self.main: BlockStore | None = None
        super().__init__(name, "")

    def xOpen(self, name: object, flags: object) -> _File:
        return _File(self, name, flags)


def remove_work(path: Path) -> None:
    for suffix in ("", ".blk", "-wal", "-shm", "-journal"):
        Path(str(path) + suffix).unlink(missing_ok=True)
