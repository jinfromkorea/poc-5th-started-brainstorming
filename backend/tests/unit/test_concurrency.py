from __future__ import annotations

import asyncio

import pytest

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


async def test_cancel_while_queued_calls_on_queued_cancel_without_starting_coro_factory():
    manager = JobManager(max_concurrent=1)
    started = asyncio.Event()
    blocker = asyncio.Event()
    coro_factory_called = False
    fallback_called = False

    async def first():
        started.set()
        await blocker.wait()

    async def never_should_run():
        nonlocal coro_factory_called
        coro_factory_called = True

    async def fallback():
        nonlocal fallback_called
        fallback_called = True

    first_task = manager.start("job-1", first)
    await started.wait()  # job-1 now holds the only slot

    second_task = manager.start("job-2", never_should_run, on_queued_cancel=fallback)
    await asyncio.sleep(0)  # job-2 is blocked on the semaphore, not started yet

    assert manager.cancel("job-2") is True
    with pytest.raises(asyncio.CancelledError):
        await second_task

    assert fallback_called is True
    assert coro_factory_called is False

    blocker.set()
    await first_task


async def test_cancel_while_running_also_calls_on_queued_cancel():
    """_run doesn't distinguish "cancelled while still queued" from
    "cancelled while coro_factory was already running and re-raised" --
    on_queued_cancel fires in both cases. Safety relies on the callee
    (pipeline._finalize_cancelled) being idempotent, not on JobManager
    telling the two apart (see docs/superpowers/specs/
    2026-08-08-job-cancellation-design.md)."""
    manager = JobManager(max_concurrent=1)
    started = asyncio.Event()
    fallback_calls = 0

    async def work():
        started.set()
        await asyncio.sleep(10)  # never actually reached -- cancelled first

    async def fallback():
        nonlocal fallback_calls
        fallback_calls += 1

    task = manager.start("job-1", work, on_queued_cancel=fallback)
    await started.wait()  # coro_factory is now running, past the semaphore

    assert manager.cancel("job-1") is True
    with pytest.raises(asyncio.CancelledError):
        await task

    assert fallback_calls == 1


def test_cancel_returns_false_for_unknown_job_id():
    manager = JobManager(max_concurrent=1)
    assert manager.cancel("nonexistent") is False


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
