from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


class ConfirmVersionRequest(BaseModel):
    output_version: str
    parent_target_version: str | None = None


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    source_type: str
    source_ref: str
    run_stage1: bool
    run_stage2: bool
    output_version: str | None
    error_message: str | None
    report_markdown: str | None
    created_at: datetime
    updated_at: datetime
