"""Per-job in-memory pub/sub for live progress. A single-process local tool
doesn't need anything heavier than an asyncio.Queue per subscriber -- this
is NOT a durability layer (that's JobEvent rows in the DB, written
alongside every publish by the caller); it's purely for pushing events to
whatever SSE connections happen to be open right now.
"""

from __future__ import annotations

import asyncio
from typing import Any


class JobEventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, job_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(job_id, []).append(queue)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(job_id)
        if subs and queue in subs:
            subs.remove(queue)
        if subs is not None and not subs:
            self._subscribers.pop(job_id, None)

    async def publish(self, job_id: str, event: dict[str, Any]) -> None:
        for queue in list(self._subscribers.get(job_id, [])):
            await queue.put(event)


# Single-process tool -- one shared bus instance is simplest and correct.
bus = JobEventBus()
