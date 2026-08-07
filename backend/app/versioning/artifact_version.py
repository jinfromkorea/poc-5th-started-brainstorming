"""Applies the user-supplied output artifact version (spec: "출력 아티팩트
버전 설정"). Run once, immediately after the baseline commit, as its own
tiny checkpointed commit -- so it's included in the diff no matter what
Stage 1 does afterward, and survives a `git reset --hard` rollback of any
later failed migration step (a rollback only ever returns to the last
checkpoint, never past it).
"""

from __future__ import annotations

from pathlib import Path

from app.checkpoint.git_repo import commit_checkpoint
from app.config import Settings
from app.mvnrewrite.mvn_client import mvn_versions_set
from app.mvnrewrite.subprocess_runner import build_log_path


async def apply_output_version(
    work_dir: Path, new_version: str, settings: Settings, output_dir: Path | None = None
) -> str:
    log_path = build_log_path(output_dir, "ingest", "mvn-versions-set") if output_dir is not None else None
    result = await mvn_versions_set(work_dir, new_version, settings, log_path=log_path)
    if result.returncode != 0:
        raise RuntimeError(f"versions:set failed for {new_version!r}: {result.output}")
    return commit_checkpoint(work_dir, settings, f"checkpoint: set artifact version to {new_version}")
