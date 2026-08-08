"""NVD/Trivy cache status + manual refresh. Modeled as a Job row
(source_type="cache_refresh") purely to reuse the existing Job/JobEvent/
JobManager/SSE machinery (jobs.py's GET /jobs/{id}/events streams a refresh's
progress exactly like a migration job's) -- see orchestration/cache_refresh.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import require_api_token
from app.config import Settings, get_settings
from app.models.db import get_db_session, session_factory
from app.models.job import Job, next_job_id
from app.orchestration.cache_refresh import run_cache_refresh
from app.orchestration.cache_status import read_cache_status
from app.orchestration.concurrency import get_job_manager
from app.schemas.job import JobCreateResponse

router = APIRouter(prefix="/cache", tags=["cache"], dependencies=[Depends(require_api_token)])


@router.get("/status")
async def cache_status(settings: Settings = Depends(get_settings), db=Depends(get_db_session)) -> dict:
    fs_status = read_cache_status(settings)
    last_job = (
        db.query(Job).filter(Job.source_type == "cache_refresh").order_by(Job.created_at.desc()).first()
    )
    return {
        **fs_status,
        "refreshing": last_job.status == "running" if last_job else False,
        "current_job_id": last_job.id if last_job else None,
        "last_run_status": last_job.status if last_job else None,
        "last_run_error": last_job.error_message if last_job else None,
    }


@router.post("/refresh", response_model=JobCreateResponse, status_code=status.HTTP_202_ACCEPTED)
async def trigger_cache_refresh(
    settings: Settings = Depends(get_settings), db=Depends(get_db_session)
) -> JobCreateResponse:
    existing = (
        db.query(Job).filter(Job.source_type == "cache_refresh", Job.status == "running").first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"cache refresh already running (job_id={existing.id})",
        )

    job_id = next_job_id(db)
    job = Job(
        id=job_id,
        source_type="cache_refresh",
        source_ref="nvd+trivy",
        run_stage1=False,
        run_stage2=False,
        status="queued",
    )
    db.add(job)
    db.commit()

    factory = session_factory(settings)
    manager = get_job_manager(settings.max_concurrent_repos)
    manager.start(job_id, lambda: run_cache_refresh(job_id, settings, factory))

    return JobCreateResponse(job_id=job_id, status="queued")
