"""Lightweight pre-submission source peek: given a Git URL or ZIP (same
duality as POST /jobs), clones/extracts into a throwaway directory just long
enough to read the project's own declared <version> (or <parent><version>)
and suggest a release-ready output version -- no Job row, no work/
checkpoint, cleaned up immediately after. Used by index.html to pre-fill
"출력 아티팩트 버전" before the user submits the real job (spec:
docs/superpowers/specs/2026-08-10-output-version-suggestion-design.md).
"""

from __future__ import annotations

import shutil
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from app.api.deps import require_api_token
from app.checkpoint.git_repo import rmtree_clear_readonly
from app.config import Settings, get_settings
from app.ingest.errors import IngestError
from app.ingest.maven_detect import detect_maven_project, read_declared_version
from app.ingest.workspace import GitSourceSpec, ZipSourceSpec, create_job_workspace, populate_source
from app.versioning.artifact_version import suggest_output_version

router = APIRouter(prefix="/inspect", tags=["inspect"], dependencies=[Depends(require_api_token)])


class VersionPeekResponse(BaseModel):
    detected_version: str | None
    suggested_version: str | None
    source: str  # "version" | "parent.version" | "none"


@router.post("/artifact-version", response_model=VersionPeekResponse)
async def peek_artifact_version(
    git_url: Annotated[str | None, Form()] = None,
    git_ref: Annotated[str | None, Form()] = None,
    zip_file: Annotated[UploadFile | None, File()] = None,
    settings: Settings = Depends(get_settings),
) -> VersionPeekResponse:
    if bool(git_url) == bool(zip_file):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide exactly one of git_url or zip_file",
        )

    peek_id = uuid.uuid4().hex
    paths = create_job_workspace(f"_peek_{peek_id}", settings)
    tmp_zip: Path | None = None
    version: str | None = None
    source = "none"
    try:
        if git_url:
            spec = GitSourceSpec(url=git_url, ref=git_ref)
        else:
            tmp_zip = settings.jobs_dir / f"_peek_upload_{peek_id}.zip"
            with tmp_zip.open("wb") as f:
                shutil.copyfileobj(zip_file.file, f)
            spec = ZipSourceSpec(zip_path=tmp_zip)

        populate_source(paths, spec, settings)
        detection = detect_maven_project(paths.source)
        version, source = read_declared_version(detection.root_pom)
    except IngestError:
        version, source = None, "none"
    finally:
        shutil.rmtree(paths.root, onexc=rmtree_clear_readonly)
        if tmp_zip is not None:
            tmp_zip.unlink(missing_ok=True)

    suggested = suggest_output_version(version) if version else None
    return VersionPeekResponse(detected_version=version, suggested_version=suggested, source=source)
