"""Job/JobEvent persistence. A Job is one ingest+migration+patch run; a
JobEvent is one entry in its progress timeline (subprocess log line, LLM
call, status change, ...) -- persisted so GET /jobs/{id}/events can replay
history to a client that connects (or reconnects) mid-job or after it ends,
not just stream what happens to arrive while a socket is open.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base

# Deliberately plain strings, not a DB-level enum -- SQLite has no native
# enum type and this avoids an Alembic-less migration headache if a status
# value is ever added later.
JOB_STATUSES = ("queued", "running", "success", "needs_handoff", "failed")
TERMINAL_JOB_STATUSES = frozenset({"success", "needs_handoff", "failed"})


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    source_type: Mapped[str] = mapped_column(String)  # "git" | "zip"
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


class JobEvent(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String, index=True)
    seq: Mapped[int] = mapped_column(Integer)  # ordering within a job, assigned by the publisher
    event_type: Mapped[str] = mapped_column(String)  # "log" | "status" | "llm" | ...
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
