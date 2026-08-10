"""POST /jobs creates a job row and schedules the full pipeline
(ingest -> Stage 1 -> Stage 2 -> diff/report/handoff) as a background task
gated by the concurrency-limited JobManager, returning immediately (202).
GET /jobs/{id} polls status; GET /jobs/{id}/events streams progress via SSE
(replays history, then live) -- see streaming/sse.py.
"""

from __future__ import annotations

import os
import shutil
import stat
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sse_starlette.sse import EventSourceResponse

from app.api.deps import require_api_token
from app.config import Settings, get_settings
from app.ingest.workspace import GitSourceSpec, ZipSourceSpec
from app.models.db import get_db_session, session_factory
from app.models.job import TERMINAL_JOB_STATUSES, Job, JobEvent, next_job_id
from app.orchestration.concurrency import get_job_manager
from app.orchestration.pipeline import _finalize_cancelled, run_pipeline, run_pipeline_resume_stage2
from app.schemas.job import JobCreateResponse, JobStatusResponse
from app.streaming.sse import stream_job_events

router = APIRouter(prefix="/jobs", tags=["jobs"], dependencies=[Depends(require_api_token)])


def _rmtree_clear_readonly(func, path, _exc_info) -> None:
    """shutil.rmtree callback (see delete_job): git marks committed
    .git/objects/** files read-only, which raises PermissionError on Windows
    when os.unlink tries to remove them. Clear the flag and retry once."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


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

    job_id = next_job_id(db)

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
        on_queued_cancel=lambda: _finalize_cancelled(job_id, settings, factory),
    )

    return JobCreateResponse(job_id=job_id, status="queued")


@router.get("", response_model=list[JobStatusResponse])
async def list_jobs(db=Depends(get_db_session)) -> list[JobStatusResponse]:
    # cache_refresh rows (api/routers/cache.py) are a utility action, not a
    # migration job -- they don't belong in the job-history view.
    jobs = db.query(Job).filter(Job.source_type != "cache_refresh").order_by(Job.created_at.desc()).all()
    return [
        JobStatusResponse(
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
        for job in jobs
    ]


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


@router.post("/{job_id}/proceed", response_model=JobCreateResponse)
async def proceed_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> JobCreateResponse:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status != "awaiting_approval":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is not awaiting approval (status={job.status})",
        )

    factory = session_factory(settings)
    manager = get_job_manager(settings.max_concurrent_repos)
    manager.start(job_id, lambda: run_pipeline_resume_stage2(job_id, settings, factory))

    return JobCreateResponse(job_id=job_id, status="running")


@router.post("/{job_id}/cancel", response_model=JobCreateResponse)
async def cancel_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> JobCreateResponse:
    """Force-stops a non-terminal job (spec: docs/superpowers/specs/
    2026-08-08-job-cancellation-design.md). Returns as soon as cancellation
    is *requested*, not once it's *done* -- the frontend's already-open SSE
    connection picks up the confirmed "cancelled" status event once the
    underlying Task (or the awaiting_approval direct-finalize path below)
    actually finishes cleaning up."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status in TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is already terminal (status={job.status})",
        )

    factory = session_factory(settings)
    manager = get_job_manager(settings.max_concurrent_repos)

    if job.status == "awaiting_approval":
        # run_pipeline already returned after pausing here -- there's no
        # live Task to cancel and nothing running to kill.
        await _finalize_cancelled(job_id, settings, factory)
    elif not manager.cancel(job_id):
        # DB says running/queued but the manager has no Task for it (e.g.
        # the process restarted and lost its in-memory task registry) --
        # there's genuinely nothing to kill, so just correct the record.
        await _finalize_cancelled(job_id, settings, factory)
    # else: task.cancel() was called successfully -- the actual DB/marker
    # finalization happens asynchronously (pipeline.py's own except
    # asyncio.CancelledError, or JobManager's on_queued_cancel fallback),
    # and reaches the client via the already-open SSE stream.

    db.refresh(job)
    return JobCreateResponse(job_id=job_id, status=job.status)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> None:
    """Removes a terminal job's DB row and its on-disk source/work/output
    directory (spec: docs/superpowers/specs/2026-08-10-history-delete-and-
    analysis-collapse-design.md). Non-terminal jobs must be cancelled first
    -- deleting a directory a live pipeline is still writing to could leave
    orphaned processes or corrupt output."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    if job.status not in TERMINAL_JOB_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"job {job_id} is not terminal (status={job.status}); cancel it first",
        )

    # job_events has no FK/relationship to jobs (job_id is a logical
    # reference only), so there's no ORM cascade to rely on -- delete it
    # explicitly before the job row.
    db.query(JobEvent).filter(JobEvent.job_id == job_id).delete()
    db.delete(job)
    db.commit()

    job_dir = settings.jobs_dir / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, onexc=_rmtree_clear_readonly)


@router.get("/{job_id}/events")
async def job_events(job_id: str, settings: Settings = Depends(get_settings)) -> EventSourceResponse:
    factory = session_factory(settings)
    return EventSourceResponse(stream_job_events(job_id, factory))
