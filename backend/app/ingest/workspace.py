"""Ties the ingest steps together: per-job source/work/output directories,
population from Git or ZIP, Maven detection, and materializing work/ as its
own fresh git-checkpointed copy of source/.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.checkpoint.git_repo import git_init_and_baseline_commit
from app.config import Settings
from app.ingest.git_source import clone_git
from app.ingest.maven_detect import MavenDetectionResult, detect_maven_project
from app.ingest.zip_source import extract_zip, unwrap_single_top_level


@dataclass
class GitSourceSpec:
    url: str
    ref: str | None = None


@dataclass
class ZipSourceSpec:
    zip_path: Path


SourceSpec = GitSourceSpec | ZipSourceSpec


@dataclass
class WorkspacePaths:
    root: Path
    source: Path
    work: Path
    output: Path


@dataclass
class IngestResult:
    job_id: str
    paths: WorkspacePaths
    detection: MavenDetectionResult
    baseline_commit: str


def new_job_id() -> str:
    return uuid.uuid4().hex


def create_job_workspace(job_id: str, settings: Settings) -> WorkspacePaths:
    root = settings.jobs_dir / job_id
    paths = WorkspacePaths(root=root, source=root / "source", work=root / "work", output=root / "output")
    for d in (paths.source, paths.work, paths.output):
        d.mkdir(parents=True, exist_ok=True)
    return paths


def populate_source(paths: WorkspacePaths, spec: SourceSpec, settings: Settings) -> None:
    if isinstance(spec, GitSourceSpec):
        clone_git(spec.url, spec.ref, paths.source, settings, log_path=paths.output / "logs" / "ingest" / "git-clone.log")
    else:
        extract_zip(spec.zip_path, paths.source, settings)
        unwrap_single_top_level(paths.source)


def materialize_work_from_source(paths: WorkspacePaths, settings: Settings) -> str:
    """Copy source/ -> work/ excluding .git (a Git-sourced project brings its
    own history; we deliberately don't inherit it -- work/ gets a fresh,
    minimal history scoped to only what this tool does, per the checkpoint
    design), then git init + baseline commit. Returns the baseline commit
    hash."""
    shutil.copytree(paths.source, paths.work, dirs_exist_ok=True, ignore=shutil.ignore_patterns(".git"))
    return git_init_and_baseline_commit(paths.work, settings)


def ingest(job_id: str, spec: SourceSpec, settings: Settings) -> IngestResult:
    """Full ingest: create workspace, populate source/, detect Maven project
    (raises GradleProjectError/NotMavenProjectError if invalid -- callers
    should treat that as job-creation failure), materialize work/ with its
    baseline checkpoint commit."""
    paths = create_job_workspace(job_id, settings)
    populate_source(paths, spec, settings)
    detection = detect_maven_project(paths.source)
    baseline_commit = materialize_work_from_source(paths, settings)
    return IngestResult(job_id=job_id, paths=paths, detection=detection, baseline_commit=baseline_commit)
