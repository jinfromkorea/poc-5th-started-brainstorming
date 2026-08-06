"""Integration tests (real files, no network/LLM): run the full ingest()
pipeline against the 4 reference ZIPs in data/. These are strong fixtures
for ingest/detection (real multi-module Maven trees, wrapped in a single
top-level folder like a GitHub zip download) but say nothing about the
multi-step migration engine -- see tests/fixtures/legacy-boot27-sample for
that (added in Phase 4).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.ingest.workspace import ZipSourceSpec, ingest, new_job_id

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

EXPECTED_MODULES = {
    "ace-parent": {"ace-ai", "ace-common", "ace-util"},
    "ace-portal": {"portal-api", "portal-web"},
    "anne-agent": {"anne-api", "anne-ai", "anne-web"},
    "daisy-agent": {"daisy-api", "daisy-ai", "daisy-web"},
}


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"))


@pytest.mark.parametrize("repo_name", sorted(EXPECTED_MODULES))
def test_ingest_reference_zip(repo_name, settings):
    zip_path = DATA_DIR / f"{repo_name}.zip"
    assert zip_path.exists(), f"reference fixture missing: {zip_path}"

    result = ingest(new_job_id(), ZipSourceSpec(zip_path=zip_path), settings)

    assert result.detection.packaging == "pom"
    assert result.detection.is_multi_module is True
    assert {m.relative_path for m in result.detection.modules} == EXPECTED_MODULES[repo_name]
    assert all(m.exists for m in result.detection.modules)

    # unwrap_single_top_level should have hoisted contents up: source/pom.xml
    # directly present, not source/<repo_name>/pom.xml.
    assert (result.paths.source / "pom.xml").exists()

    # work/ is a materialized, git-checkpointed copy of source/.
    assert (result.paths.work / "pom.xml").exists()
    assert (result.paths.work / ".git").is_dir()
    assert len(result.baseline_commit) == 40  # full git sha


def test_ingest_all_four_zips_are_independent_jobs(settings):
    """Each job gets its own isolated workspace under jobs_dir/<job_id>/."""
    ids = set()
    for repo_name in EXPECTED_MODULES:
        result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / f"{repo_name}.zip"), settings)
        ids.add(result.job_id)
        assert result.paths.root == settings.jobs_dir / result.job_id

    assert len(ids) == 4
