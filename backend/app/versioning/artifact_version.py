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


async def apply_output_version(work_dir: Path, new_version: str, settings: Settings) -> str:
    result = await mvn_versions_set(work_dir, new_version, settings)
    if result.returncode != 0:
        raise RuntimeError(f"versions:set failed for {new_version!r}: {result.output}")
    return commit_checkpoint(work_dir, settings, f"checkpoint: set artifact version to {new_version}")
