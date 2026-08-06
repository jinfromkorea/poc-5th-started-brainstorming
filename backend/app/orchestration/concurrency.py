"""Bounds how many jobs actually run their heavy work (ingest/mvn/OpenRewrite/
AI) at once (spec: MAX_CONCURRENT_REPOS -- "개발자 개인 로컬 머신이 동시에 여러
mvn 빌드를 못 버티는 것을 막는 안전장치", not a multi-tenant fairness
mechanism). Requests beyond the limit queue on the semaphore rather than
being rejected -- POST /jobs always returns 202 immediately regardless of
how many jobs are already running.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


class JobManager:
    def __init__(self, max_concurrent: int) -> None:
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._tasks: dict[str, asyncio.Task] = {}

    def start(self, job_id: str, coro_factory: Callable[[], Awaitable[None]]) -> asyncio.Task:
        """Schedules coro_factory() to run as soon as a concurrency slot is
        free. Returns immediately (the task itself acquires the semaphore
        inside _run, so callers -- i.e. the API endpoint -- never block)."""
        task = asyncio.create_task(self._run(coro_factory))
        self._tasks[job_id] = task
        return task

    async def _run(self, coro_factory: Callable[[], Awaitable[None]]) -> None:
        async with self._semaphore:
            await coro_factory()

    def get_task(self, job_id: str) -> asyncio.Task | None:
        return self._tasks.get(job_id)


# Built once at app startup from settings.max_concurrent_repos (see main.py);
# a single process-wide manager is correct for this single-process local tool.
_job_manager: JobManager | None = None


def get_job_manager(max_concurrent: int | None = None) -> JobManager:
    global _job_manager
    if _job_manager is None:
        if max_concurrent is None:
            raise RuntimeError("JobManager not initialized yet -- max_concurrent required on first call")
        _job_manager = JobManager(max_concurrent)
    return _job_manager


def reset_job_manager() -> None:
    """Test-only: clears the process-wide singleton so each test gets a
    fresh semaphore instead of sharing state across test cases."""
    global _job_manager
    _job_manager = None
