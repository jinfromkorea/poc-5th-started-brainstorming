from __future__ import annotations

import asyncio
import json

import pytest

from app.config import Settings
from app.models.db import init_db, session_factory
from app.models.job import Job
from app.streaming.events import emit_event
from app.streaming.sse import stream_job_events


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


@pytest.fixture()
def db(settings):
    init_db(settings)
    return session_factory(settings)


def _create_job(db, job_id: str, status: str = "running") -> None:
    with db() as session:
        session.add(Job(id=job_id, source_type="zip", source_ref="x.zip", status=status))
        session.commit()


async def test_replay_then_live_and_stops_at_terminal_status(db):
    _create_job(db, "job-1", status="running")
    await emit_event(db, "job-1", "log", {"message": "step 1"})
    await emit_event(db, "job-1", "log", {"message": "step 2"})

    events: list[dict] = []

    async def consume():
        async for e in stream_job_events("job-1", db):
            events.append(e)

    consumer_task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # let it replay the 2 persisted events and subscribe for live ones

    await emit_event(db, "job-1", "log", {"message": "step 3"})
    await emit_event(db, "job-1", "status", {"status": "success"})

    await asyncio.wait_for(consumer_task, timeout=2)

    assert len(events) == 4
    messages = [json.loads(e["data"]).get("message") for e in events[:3]]
    assert messages == ["step 1", "step 2", "step 3"]
    assert events[3]["event"] == "status"
    assert json.loads(events[3]["data"]) == {"status": "success"}
    # SSE `id` fields must be strictly increasing -- proves replay + live share one sequence.
    assert [int(e["id"]) for e in events] == [1, 2, 3, 4]


async def test_already_terminal_job_replays_and_returns_immediately(db):
    _create_job(db, "job-2", status="success")
    await emit_event(db, "job-2", "log", {"message": "done already"})

    events = [e async for e in stream_job_events("job-2", db)]

    assert len(events) == 1
    assert json.loads(events[0]["data"]) == {"message": "done already"}


async def test_unknown_job_yields_error_event(db):
    events = [e async for e in stream_job_events("does-not-exist", db)]

    assert len(events) == 1
    assert events[0]["event"] == "error"


async def test_two_subscribers_both_receive_live_events(db):
    _create_job(db, "job-3", status="running")

    events_a: list[dict] = []
    events_b: list[dict] = []

    async def consume(events: list[dict]):
        async for e in stream_job_events("job-3", db):
            events.append(e)

    task_a = asyncio.create_task(consume(events_a))
    task_b = asyncio.create_task(consume(events_b))
    await asyncio.sleep(0.05)

    await emit_event(db, "job-3", "status", {"status": "failed"})
    await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=2)

    assert len(events_a) == 1
    assert len(events_b) == 1
