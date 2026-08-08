"""resolve_ingest_baseline / resolve_stage_baseline -- reconstructing, from
git history alone, the baseline sha(s) that run_pipeline's local variables
held before a job paused at needs_handoff (see orchestration/pipeline.py's
run_pipeline_resume_stage2). No other test file exercises checkpoint/git_repo.py
directly."""

from __future__ import annotations

from app.checkpoint.git_repo import (
    commit_checkpoint,
    current_head,
    diff_since,
    git_init_and_baseline_commit,
    resolve_ingest_baseline,
    resolve_stage_baseline,
)
from app.config import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


def test_baseline_commit_adds_versions_backup_to_gitignore(tmp_path):
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    (work_dir / ".gitignore").write_text("target/\n")
    baseline_sha = git_init_and_baseline_commit(work_dir, settings)

    assert "*.versionsBackup" in (work_dir / ".gitignore").read_text().splitlines()

    # a later checkpoint's `git add -A` must never pick up a backup file
    # some mvn goal left behind (e.g. Stage 2's dependency_patch.py calls)
    (work_dir / "pom.xml.versionsBackup").write_text("<project/>")
    commit_checkpoint(work_dir, settings, "checkpoint: patch dependency")
    assert "versionsBackup" not in diff_since(work_dir, settings, baseline_sha)


def test_resolve_baselines_without_output_version(tmp_path):
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    ingest_sha = git_init_and_baseline_commit(work_dir, settings)

    commit_checkpoint(work_dir, settings, "checkpoint: some stage1 step")

    assert resolve_ingest_baseline(work_dir, settings) == ingest_sha
    assert resolve_stage_baseline(work_dir, settings, output_version=None) == ingest_sha


def test_resolve_baselines_with_output_version(tmp_path):
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    ingest_sha = git_init_and_baseline_commit(work_dir, settings)

    version_sha = commit_checkpoint(work_dir, settings, "checkpoint: set artifact version to 1.0.0")
    commit_checkpoint(work_dir, settings, "checkpoint: some stage1 step")

    assert resolve_ingest_baseline(work_dir, settings) == ingest_sha
    assert resolve_stage_baseline(work_dir, settings, output_version="1.0.0") == version_sha
    assert version_sha != ingest_sha
    assert current_head(work_dir, settings) not in (ingest_sha, version_sha)
