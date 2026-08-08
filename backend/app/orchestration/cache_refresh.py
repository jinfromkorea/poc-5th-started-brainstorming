"""Manual NVD/Trivy cache refresh, triggered by POST /cache/refresh and
scheduled through the same JobManager as migration jobs (see
api/routers/cache.py). Modeled as a Job row with source_type="cache_refresh"
so it reuses Job/JobEvent/JobManager/the existing GET /jobs/{id}/events SSE
endpoint verbatim -- no new streaming plumbing. list_jobs (api/routers/jobs.py)
filters these out of the job-history view since they aren't migration runs.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.orchestration.pipeline import make_emit_log, set_job_status
from app.scan.dependency_check import run_dependency_check_update_only
from app.scan.trivy import run_trivy_db_refresh


async def run_cache_refresh(job_id: str, settings: Settings, session_factory: sessionmaker[Session]) -> None:
    emit, log = make_emit_log(session_factory, job_id)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        await log("Trivy 취약점 DB 갱신 시작")
        trivy_results = await run_trivy_db_refresh(settings)
        for result in trivy_results:
            if result.returncode != 0:
                raise RuntimeError(f"trivy DB 갱신 실패 (exit={result.returncode}): {result.output[-2000:]}")
        await log("Trivy 취약점 DB 갱신 완료")

        await log("Dependency-Check NVD 캐시 갱신 시작 (최초 동기화 시 오래 걸릴 수 있음)")
        dc_result = await run_dependency_check_update_only(settings)
        if dc_result.returncode != 0:
            raise RuntimeError(f"dependency-check 갱신 실패 (exit={dc_result.returncode}): {dc_result.output[-2000:]}")
        await log("Dependency-Check NVD 캐시 갱신 완료")

        await set_job_status(session_factory, job_id, "success")
        await emit("status", {"status": "success"})

    except Exception as exc:  # noqa: BLE001 -- a refresh failure must never crash the server process
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
