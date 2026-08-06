"""Downloads for a finished job's output/ tree: the unified diff, the
report, and any handoff guides. Kept separate from jobs.py since it's a
distinct concern (static file serving vs. job lifecycle) and the frontend
fetches these only after a job reaches a terminal status.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse

from app.api.deps import require_api_token
from app.config import Settings, get_settings
from app.models.db import get_db_session
from app.models.job import Job

router = APIRouter(prefix="/jobs", tags=["artifacts"], dependencies=[Depends(require_api_token)])


def _output_dir(job_id: str, settings: Settings, db) -> Path:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    return Path(settings.jobs_dir) / job_id / "output"


@router.get("/{job_id}/artifacts")
async def list_artifacts(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> dict:
    output_dir = _output_dir(job_id, settings, db)
    handoff_dir = output_dir / "handoff"
    return {
        "diff": (output_dir / "patch.diff").is_file(),
        "report": (output_dir / "report.md").is_file(),
        "handoff": sorted(p.name for p in handoff_dir.glob("*.md")) if handoff_dir.is_dir() else [],
    }


@router.get("/{job_id}/artifacts/diff")
async def get_diff(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> PlainTextResponse:
    path = _output_dir(job_id, settings, db) / "patch.diff"
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="diff not available yet")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/x-diff")


@router.get("/{job_id}/artifacts/report")
async def get_report(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> PlainTextResponse:
    path = _output_dir(job_id, settings, db) / "report.md"
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not available yet")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown")


@router.get("/{job_id}/artifacts/handoff/{filename}")
async def get_handoff_guide(
    job_id: str,
    filename: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> PlainTextResponse:
    handoff_dir = _output_dir(job_id, settings, db) / "handoff"
    # filename must be a bare name that actually exists in handoff_dir -- guards
    # against path traversal (e.g. "../../../etc/passwd") since we never join
    # unvalidated path segments onto the filesystem path below.
    if "/" in filename or "\\" in filename or filename not in {p.name for p in handoff_dir.glob("*.md")}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown handoff guide: {filename}")
    return PlainTextResponse((handoff_dir / filename).read_text(encoding="utf-8"), media_type="text/markdown")
