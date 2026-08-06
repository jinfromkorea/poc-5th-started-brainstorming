from __future__ import annotations

import asyncio

from app.orchestration.concurrency import JobManager


async def test_concurrency_cap_limits_parallel_execution():
    """4 jobs requested with cap=2 -- at most 2 should ever be "inside" the
    work at the same instant, the rest queue on the semaphore."""
    manager = JobManager(max_concurrent=2)
    running = 0
    peak = 0
    lock = asyncio.Lock()
    completed = []

    async def work(job_id: str):
        nonlocal running, peak
        async with lock:
            running += 1
            peak = max(peak, running)
        await asyncio.sleep(0.05)
        async with lock:
            running -= 1
        completed.append(job_id)

    tasks = [manager.start(f"job-{i}", lambda i=i: work(f"job-{i}")) for i in range(4)]
    await asyncio.gather(*tasks)

    assert peak == 2
    assert sorted(completed) == [f"job-{i}" for i in range(4)]


async def test_get_task_returns_the_scheduled_task():
    manager = JobManager(max_concurrent=1)

    async def noop():
        return None

    task = manager.start("job-x", noop)

    assert manager.get_task("job-x") is task
    assert manager.get_task("nonexistent") is None

    await task


async def test_third_job_does_not_start_until_a_slot_frees(monkeypatch):
    manager = JobManager(max_concurrent=1)
    order: list[str] = []
    release_first = asyncio.Event()

    async def first():
        order.append("first-start")
        await release_first.wait()
        order.append("first-end")

    async def second():
        order.append("second-start")
        order.append("second-end")

    first_task = manager.start("job-1", first)
    await asyncio.sleep(0)  # let job-1 acquire the semaphore and start
    second_task = manager.start("job-2", second)
    await asyncio.sleep(0)  # job-2 should be blocked on the semaphore, not started yet

    assert order == ["first-start"]

    release_first.set()
    await asyncio.gather(first_task, second_task)

    assert order == ["first-start", "first-end", "second-start", "second-end"]
