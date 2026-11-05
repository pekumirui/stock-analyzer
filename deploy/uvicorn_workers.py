"""uvicorn のワーカー数。VPS のコアと MemAvailable から決める（決め打ちしない）。

UVICORN_WORKERS があればそれを使う。無ければ:

  N = max(1, min(nproc, MemAvailableMiB // PER_WORKER_MIB, MAX_WORKERS))

1 ワーカーは pandas 込みで数百 MiB。SQLite はライター 1 本なので、コアより多くしても
CPU は足りず RAM だけ食う。MCP（stock-analyzer-mcp.service）は対象外。

MemAvailable は起動時の値で、19:30 の日次バッチなどのピークは見えない。4 本だと
バッチ中に待機ワーカーがスワップへ出され、次のリクエストで戻す間にヘルスチェックに
答えられず kill される（KAB-366）。そのため MAX_WORKERS で頭打ちにする。
"""
from __future__ import annotations

import os
from pathlib import Path

PER_WORKER_MIB = 384
MAX_WORKERS = 2
MEMINFO_PATH = Path("/proc/meminfo")


def parse_env_workers(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        n = int(text, 10)
    except ValueError as exc:
        raise ValueError(f"UVICORN_WORKERS must be a positive integer, got {raw!r}") from exc
    if n < 1:
        raise ValueError(f"UVICORN_WORKERS must be >= 1, got {n}")
    return n


def host_nproc() -> int:
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    return max(1, int(n))


def mem_available_kib(text: str) -> int | None:
    available = None
    free = None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            available = int(line.split()[1])
        elif line.startswith("MemFree:"):
            free = int(line.split()[1])
    if available is not None:
        return available
    return free


def choose_workers(
    *,
    env_value: str | None,
    nproc: int,
    mem_available_kib: int | None,
    per_worker_mib: int = PER_WORKER_MIB,
    max_workers: int = MAX_WORKERS,
) -> int:
    pinned = parse_env_workers(env_value)
    if pinned is not None:
        return pinned
    by_cpu = max(1, min(int(nproc), int(max_workers)))
    if mem_available_kib is None:
        return by_cpu
    by_mem = max(1, int(mem_available_kib) // 1024 // max(1, int(per_worker_mib)))
    return max(1, min(by_cpu, by_mem))


def workers_from_host(
    environ: dict[str, str] | None = None,
    *,
    meminfo_path: Path = MEMINFO_PATH,
) -> int:
    env = os.environ if environ is None else environ
    try:
        text = meminfo_path.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return choose_workers(
        env_value=env.get("UVICORN_WORKERS"),
        nproc=host_nproc(),
        mem_available_kib=mem_available_kib(text) if text else None,
    )


def main() -> None:
    print(workers_from_host())


if __name__ == "__main__":
    main()
