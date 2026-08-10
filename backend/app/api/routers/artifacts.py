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
from app.checkpoint.git_repo import diff_status_map, list_tracked_files, resolve_ingest_baseline, show_file_bytes
from app.config import Settings, get_settings
from app.models.db import get_db_session
from app.models.job import Job

router = APIRouter(prefix="/jobs", tags=["artifacts"], dependencies=[Depends(require_api_token)])

# Not source code, and can run to tens of MB per job (target/ especially) --
# excluded from the file-tree view regardless of whether the ingested
# project's own .gitignore already covers them.
_NOISE_DIR_NAMES = {".git", "target", "dist", "build", "node_modules", "__pycache__", ".venv"}
_STATUS_LABELS = {"A": "added", "M": "modified"}  # diff_status_map's raw git codes -> what the frontend renders


def _output_dir(job_id: str, settings: Settings, db) -> Path:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    return Path(settings.jobs_dir) / job_id / "output"


def _work_dir(job_id: str, settings: Settings, db) -> Path:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown job_id: {job_id}")
    return Path(settings.jobs_dir) / job_id / "work"


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


@router.get("/{job_id}/artifacts/tree")
async def get_file_tree(
    job_id: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> list[dict]:
    """Full project file tree at work/'s current (post-migration) state,
    annotated with per-file added/modified/unchanged status against the
    ingest baseline. Deleted files are intentionally absent -- they don't
    exist at HEAD, so they're naturally excluded from list_tracked_files()
    (spec: docs/superpowers/specs/2026-08-10-artifact-file-tree-viewer-
    design.md)."""
    work_dir = _work_dir(job_id, settings, db)
    baseline = resolve_ingest_baseline(work_dir, settings)
    status_map = diff_status_map(work_dir, settings, baseline)
    return [
        {"path": p, "status": _STATUS_LABELS.get(status_map.get(p, ""), "unchanged")}
        for p in list_tracked_files(work_dir, settings)
        if not any(seg in _NOISE_DIR_NAMES for seg in p.split("/"))
    ]


@router.get("/{job_id}/artifacts/file")
async def get_file_before_after(
    job_id: str,
    path: str,
    settings: Settings = Depends(get_settings),
    db=Depends(get_db_session),
) -> dict:
    """Before (ingest baseline) / after (HEAD) content of a single tracked
    file, for the file-tree viewer's side-by-side comparison."""
    work_dir = _work_dir(job_id, settings, db)
    # Whitelist against the actual tracked-file list -- guards against path
    # traversal, same pattern as get_handoff_guide's filename whitelist.
    if path not in set(list_tracked_files(work_dir, settings)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown file path: {path}")

    baseline = resolve_ingest_baseline(work_dir, settings)
    before = show_file_bytes(work_dir, settings, baseline, path)
    after = show_file_bytes(work_dir, settings, "HEAD", path)
    binary = bool((before and b"\x00" in before) or (after and b"\x00" in after))

    def _decode(raw: bytes | None) -> str | None:
        if raw is None or binary:
            return None
        return raw.decode("utf-8", errors="replace")

    return {"before": _decode(before), "after": _decode(after), "binary": binary}
