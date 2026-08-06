"""POST /jobs creates a job row and schedules the full pipeline
(ingest -> Stage 1 -> Stage 2 -> diff/report/handoff) as a background task
gated by the concurrency-limited JobManager, returning immediately (202).
GET /jobs/{id} polls status; GET /jobs/{id}/events streams progress via SSE
(replays history, then live) -- see streaming/sse.py.
"""

from __future__ import annotations

import shutil
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_api_token
from app.config import Settings, get_settings
from app.ingest.workspace import GitSourceSpec, ZipSourceSpec, new_job_id
from app.models.db import get_db_session, session_factory
from app.models.job import Job
from app.orchestration.concurrency import get_job_manager
from app.orchestration.pipeline import run_pipeline
from app.schemas.job import JobCreateResponse, JobStatusResponse
from app.streaming.sse import stream_job_events

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_token)])


@router.post("", response_model=JobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_job(
    git_url: Annotated[str | None, Form()] = None,
    git_ref: Annotated[str | None, Form()] = None,
    output_version: Annotated[str | None, Form()] = None,
    run_stage1: Annotated[bool, Form()] = True,
    run_stage2: Annotated[bool, Form()] = False,
    zip_file: Annotated[UploadFile | None, File()] = None,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> JobCreateResponse:
    if bool(git_url) == bool(zip_file):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide exactly one of git_url or zip_file",
        )

    job_id = new_job_id()

    if git_url:
        spec = GitSourceSpec(url=git_url, ref=git_ref)
        source_type, source_ref = "git", git_url
    else:
        settings.jobs_dir.mkdir(parents=True, exist_ok=True)
        tmp_zip = settings.jobs_dir / f"_upload_{job_id}.zip"
        with tmp_zip.open("wb") as f:
            shutil.copyfileobj(zip_file.file, f)
        spec = ZipSourceSpec(zip_path=tmp_zip)
        source_type, source_ref = "zip", zip_file.filename or "upload.zip"

    job = Job(
        id=job_id,
        source_type=source_type,
        source_ref=source_ref,
        output_version=output_version,
        run_stage1=run_stage1,
        run_stage2=run_stage2,
        status="queued",
    )
    db.add(job)
    db.commit()

    factory = session_factory(settings)
    manager = get_job_manager(settings.max_concurrent_repos)
    manager.start(
        job_id,
        lambda: run_pipeline(job_id, spec, output_version, run_stage1, run_stage2, settings, factory),
    )

    return JobCreateResponse(job_id=job_id, status="queued")


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(job_id: str, db=Depends(get_db_session)) -> JobStatusResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    return JobStatusResponse(
        job_id=job.id,
        status=job.status,
        source_type=job.source_type,
        source_ref=job.source_ref,
        run_stage1=job.run_stage1,
        run_stage2=job.run_stage2,
        output_version=job.output_version,
        error_message=job.error_message,
        report_markdown=job.report_markdown,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.get("/{job_id}/events")
async def job_events(job_id: str, settings: Settings = Depends(get_settings)) -> EventSourceResponse:
    factory = session_factory(settings)
    return EventSourceResponse(stream_job_events(job_id, factory))
