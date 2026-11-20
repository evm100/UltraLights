"""Ordering and rate control for device commands. Pure asyncio, no I/O, so it is unit tested.

SeqAllocator: one counter per (device, output), shared by set and live, issuing
max(last + 1, unix time in µs) so values keep increasing across restarts.

Coalescer: per (device, output) - never per device. Live updates for one output are
merged and published at most once per interval (50 ms = 20 Hz). The first update
after a quiet period goes out immediately; the rest are merged into one trailing
publish. cancel() drops a pending live update when the final set supersedes it.
"""
import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

Key = tuple[str, str]


class SeqAllocator:
    def __init__(self, clock_us: Callable[[], int] | None = None):
        self._last: dict[Key, int] = {}
        self._clock = clock_us or (lambda: time.time_ns() // 1000)

    def next(self, key: Key) -> int:
        n = max(self._last.get(key, 0) + 1, self._clock())
        self._last[key] = n
        return n


@dataclass
class _Slot:
    pending: dict[str, Any] | None = None
    src: str | None = None
    last_sent: float = -1e9
    task: asyncio.Task | None = None
    sent: int = 0
    merged: int = 0


Flush = Callable[[Key, dict[str, Any], str | None], Awaitable[None]]


@dataclass
class Coalescer:
    flush: Flush
    interval: float = 0.05
    clock: Callable[[], float] = time.monotonic
    _slots: dict[Key, _Slot] = field(default_factory=dict)

    def submit(self, key: Key, partial: dict[str, Any], src: str | None = None) -> None:
        slot = self._slots.setdefault(key, _Slot())
        if slot.pending is None:
            slot.pending = dict(partial)
        else:
            slot.pending.update(partial)
            slot.merged += 1
        slot.src = src or slot.src
        if slot.task is None or slot.task.done():
            delay = max(0.0, slot.last_sent + self.interval - self.clock())
            slot.task = asyncio.get_running_loop().create_task(self._run(key, slot, delay))

    async def _run(self, key: Key, slot: _Slot, delay: float) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        payload, src = slot.pending, slot.src
        slot.pending = None
        if payload is None:
            return
        slot.last_sent = self.clock()
        slot.sent += 1
        await self.flush(key, payload, src)

    def cancel(self, key: Key) -> None:
        slot = self._slots.get(key)
        if slot is None:
            return
        slot.pending = None
        if slot.task and not slot.task.done():
            slot.task.cancel()
        slot.task = None

    def stats(self, key: Key) -> tuple[int, int]:
        s = self._slots.get(key)
        return (s.sent, s.merged) if s else (0, 0)

    async def drain(self) -> None:
        tasks = [s.task for s in self._slots.values() if s.task and not s.task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
