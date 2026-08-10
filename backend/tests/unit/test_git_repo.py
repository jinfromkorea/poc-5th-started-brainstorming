"""resolve_ingest_baseline -- reconstructing, from git history alone, the
true first commit ever made in work_dir (used for the final diff when
resuming Stage 2 after a HITL approval pause, see orchestration/pipeline.py's
run_pipeline_resume_stage2). Also covers list_tracked_files/diff_status_map/
show_file_bytes, the git wrappers behind the artifact file-tree viewer (spec:
docs/superpowers/specs/2026-08-10-artifact-file-tree-viewer-design.md). No
other test file exercises checkpoint/git_repo.py directly."""

from __future__ import annotations

from app.checkpoint.git_repo import (
    commit_checkpoint,
    current_head,
    diff_since,
    diff_status_map,
    git_init_and_baseline_commit,
    list_tracked_files,
    resolve_ingest_baseline,
    show_file_bytes,
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


def test_list_tracked_files_reflects_head(tmp_path):
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    git_init_and_baseline_commit(work_dir, settings)
    # baseline always also tracks .gitignore -- git_init_and_baseline_commit
    # creates one (with *.versionsBackup) if the project didn't ship one.
    assert sorted(list_tracked_files(work_dir, settings)) == [".gitignore", "pom.xml"]

    (work_dir / "README.md").write_text("hello")
    commit_checkpoint(work_dir, settings, "checkpoint: add README")
    assert sorted(list_tracked_files(work_dir, settings)) == [".gitignore", "README.md", "pom.xml"]


def test_diff_status_map_excludes_deleted_files(tmp_path):
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    (work_dir / "old.txt").write_text("will be deleted")
    baseline_sha = git_init_and_baseline_commit(work_dir, settings)

    (work_dir / "pom.xml").write_text("<project><modified/></project>")  # M
    (work_dir / "new.txt").write_text("new file")  # A
    (work_dir / "old.txt").unlink()  # D
    commit_checkpoint(work_dir, settings, "checkpoint: modify/add/delete")

    status_map = diff_status_map(work_dir, settings, baseline_sha)
    assert status_map == {"pom.xml": "M", "new.txt": "A"}
    assert "old.txt" not in status_map
    # the deleted path also naturally disappears from the tracked-file list
    # the tree viewer builds from -- no separate filtering needed.
    assert "old.txt" not in list_tracked_files(work_dir, settings)


def test_show_file_bytes_returns_none_for_missing_ref(tmp_path):
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    baseline_sha = git_init_and_baseline_commit(work_dir, settings)

    (work_dir / "new.txt").write_text("added after baseline")
    commit_checkpoint(work_dir, settings, "checkpoint: add new.txt")

    assert show_file_bytes(work_dir, settings, baseline_sha, "new.txt") is None
    assert show_file_bytes(work_dir, settings, "HEAD", "new.txt") == b"added after baseline"


def test_show_file_bytes_preserves_binary_content(tmp_path):
    settings = _settings()
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text("<project/>")
    binary_content = bytes([0x00, 0x01, 0xFF, 0x00, 0x42])
    (work_dir / "blob.bin").write_bytes(binary_content)
    git_init_and_baseline_commit(work_dir, settings)

    assert show_file_bytes(work_dir, settings, "HEAD", "blob.bin") == binary_content
