"""Emits one progress event: persists it as a JobEvent row (so it survives
for later replay/reconnect) AND publishes it to any live SSE subscribers
via the bus, in that order -- a client that subscribes right after this
call is guaranteed to see the event either via replay or via the live
queue, never neither.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models.job import JobEvent
from app.streaming.bus import bus


def _next_seq(session: Session, job_id: str) -> int:
    last = (
        session.query(JobEvent.seq)
        .filter(JobEvent.job_id == job_id)
        .order_by(JobEvent.seq.desc())
        .limit(1)
        .scalar()
    )
    return (last or 0) + 1


async def emit_event(
    session_factory: sessionmaker[Session], job_id: str, event_type: str, data: dict[str, Any]
) -> None:
    with session_factory() as session:
        seq = _next_seq(session, job_id)
        session.add(JobEvent(job_id=job_id, seq=seq, event_type=event_type, data=data))
        session.commit()

    await bus.publish(job_id, {"seq": seq, "type": event_type, "data": data})
