"""Job/JobEvent persistence. A Job is one ingest+migration+patch run; a
JobEvent is one entry in its progress timeline (subprocess log line, LLM
call, status change, ...) -- persisted so GET /jobs/{id}/events can replay
history to a client that connects (or reconnects) mid-job or after it ends,
not just stream what happens to arrive while a socket is open.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, cast, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models.db import Base

# Deliberately plain strings, not a DB-level enum -- SQLite has no native
# enum type and this avoids an Alembic-less migration headache if a status
# value is ever added later.
#
# "awaiting_approval": Stage 1 ended needs_handoff and Stage 2 was requested
# -- the pipeline stops instead of auto-continuing into Stage 2, until a
# human calls POST /jobs/{id}/proceed. Deliberately NOT terminal (see below)
# -- a client with an open SSE connection just keeps waiting and picks up
# Stage 2's events live once approved, no reconnect needed.
#
# "cancelled": a human force-stopped the job via POST /jobs/{id}/cancel
# (spec: docs/superpowers/specs/2026-08-08-job-cancellation-design.md). IS
# terminal -- unlike awaiting_approval, nothing more will ever happen to
# this job.
JOB_STATUSES = ("queued", "running", "awaiting_approval", "success", "needs_handoff", "failed", "cancelled")
TERMINAL_JOB_STATUSES = frozenset({"success", "needs_handoff", "failed", "cancelled"})


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_type: Mapped[str] = mapped_column(String)  # "git" | "zip" | "cache_refresh" (api/routers/cache.py)
    source_ref: Mapped[str] = mapped_column(String)  # git URL, or original upload filename
    output_version: Mapped[str | None] = mapped_column(String, nullable=True)
    run_stage1: Mapped[bool] = mapped_column(default=True)
    run_stage2: Mapped[bool] = mapped_column(default=False)

    status: Mapped[str] = mapped_column(String, default="queued")
    error_message: Mapped[str | None] = mapped_column(String, nullable=True)
    report_markdown: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


def next_job_id(db: Session) -> str:
    """Sequential id local to this jobs.db (e.g. "7") instead of an opaque
    UUID -- this is a single-developer local tool where "which numbered run
    was this" reads better than a 32-char hex blob, and it also becomes the
    job's directory name under JOBS_DATA_DIR (source/work/output), so a
    short number is much easier to spot in a file browser. The highest
    existing id + 1, not a row count -- jobs can now be deleted (DELETE
    /jobs/{id}), and a count-based scheme would reuse a still-live id (e.g.
    job 1/2/3 with 2 deleted -> count=2 -> next id "3" collides with the
    existing job 3). Deleted ids are never reused. cast(Job.id, Integer) is
    verified against SQLite only -- this project has no plans to support any
    other DATABASE_URL dialect."""
    max_id = db.query(func.max(cast(Job.id, Integer))).scalar()
    return str((max_id or 0) + 1)


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    seq: Mapped[int] = mapped_column(Integer)  # ordering within a job, assigned by the publisher
    event_type: Mapped[str] = mapped_column(String)  # "log" | "status" | "llm" | ...
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
