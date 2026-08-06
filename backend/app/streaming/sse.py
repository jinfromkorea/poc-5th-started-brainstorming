"""GET /jobs/{id}/events implementation: replay persisted history first
(so a client connecting after some events already happened, or reconnecting
mid-job, still sees the full timeline), then switch to live streaming from
the bus until the job reaches a terminal status.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from sqlalchemy.orm import Session, sessionmaker

from app.models.job import TERMINAL_JOB_STATUSES, Job, JobEvent
from app.streaming.bus import bus


def _format(seq: int, event_type: str, data: dict) -> dict:
    return {"event": event_type, "id": str(seq), "data": json.dumps(data, ensure_ascii=False)}


async def stream_job_events(job_id: str, session_factory: sessionmaker[Session]) -> AsyncIterator[dict]:
    # Subscribe BEFORE replaying history, so no event published between the
    # replay query and the subscribe call can be missed.
    queue = bus.subscribe(job_id)
    try:
        with session_factory() as session:
            job = session.get(Job, job_id)
            if job is None:
                yield _format(0, "error", {"message": f"unknown job_id: {job_id}"})
                return

            replayed = (
                session.query(JobEvent).filter(JobEvent.job_id == job_id).order_by(JobEvent.seq.asc()).all()
            )
            replayed_seqs = set()
            for row in replayed:
                replayed_seqs.add(row.seq)
                yield _format(row.seq, row.event_type, row.data)

            if job.status in TERMINAL_JOB_STATUSES:
                return  # nothing more will ever be published for a finished job

        while True:
            event = await queue.get()
            if event["seq"] in replayed_seqs:
                continue  # published between the replay query and subscribing -- avoid double-delivery
            yield _format(event["seq"], event["type"], event["data"])
            if event["type"] == "status" and event["data"].get("status") in TERMINAL_JOB_STATUSES:
                break
    finally:
        bus.unsubscribe(job_id, queue)

