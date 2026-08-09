"""resolve_ingest_baseline -- reconstructing, from git history alone, the
true first commit ever made in work_dir (used for the final diff when
resuming Stage 2 after a HITL approval pause, see orchestration/pipeline.py's
run_pipeline_resume_stage2). No other test file exercises checkpoint/git_repo.py
directly."""

from __future__ import annotations

from app.checkpoint.git_repo import (
    commit_checkpoint,
    current_head,
    diff_since,
    git_init_and_baseline_commit,
    resolve_ingest_baseline,
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


def test_resolve_ingest_baseline_ignores_later_commits(tmp_path):
    """resolve_ingest_baseline must always point at the true first commit,
    no matter how many more checkpoints (output version, Stage 1 steps...)
    pile up on top -- it's used for the *final* diff, spanning the whole
    job, not any particular stage's rollback floor (that's current_head
    directly now, see docs/superpowers/specs/2026-08-09-stage2-baseline-
    drift-design.md -- resolve_stage_baseline used to reconstruct that from
    a wrong assumption and has been removed)."""
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    ingest_sha = git_init_and_baseline_commit(work_dir, settings)

    version_sha = commit_checkpoint(work_dir, settings, "checkpoint: set artifact version to 1.0.0")
    stage1_sha = commit_checkpoint(work_dir, settings, "checkpoint: some stage1 step")

    assert resolve_ingest_baseline(work_dir, settings) == ingest_sha
    assert current_head(work_dir, settings) == stage1_sha
    assert stage1_sha not in (ingest_sha, version_sha)  # genuinely a 3rd, later commit
