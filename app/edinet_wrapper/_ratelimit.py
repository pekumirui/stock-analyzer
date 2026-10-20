from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable


class SyncRateLimiter:
    """NOTE(spec-gap): the spec publishes no numeric QPS cap. 1.0s is a conservative default."""

    def __init__(
        self,
        min_interval: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval = min_interval
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = threading.Lock()
        self._next_at = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = self._monotonic()
            delay = self._next_at - now
            if delay > 0:
                self._sleep(delay)
                now = self._monotonic()
            self._next_at = now + self.min_interval


class AsyncRateLimiter:
    def __init__(
        self,
        min_interval: float,
        *,
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[object]] | None = None,
    ) -> None:
        self.min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    def _now(self) -> float:
        if self._clock is not None:
            return self._clock()
        loop = asyncio.get_running_loop()
        return loop.time()

    async def wait(self) -> None:
        if self.min_interval <= 0:
            return
        async with self._lock:
            now = self._now()
            delay = self._next_at - now
            if delay > 0:
                if self._sleep is not None:
                    await self._sleep(delay)
                else:
                    await asyncio.sleep(delay)
                now = self._now()
            self._next_at = now + self.min_interval
