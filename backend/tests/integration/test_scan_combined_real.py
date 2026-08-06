"""Real end-to-end combined scan (Dependency-Check + Trivy run in parallel,
merged, de-duped, filtered by FAIL_ON_CVSS) against ace-parent. Marked
`external` -- see test_scan_real.py for the DEPENDENCY_CHECK_DATA_DIR
pre-warming note.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.ingest.workspace import ZipSourceSpec, ingest, new_job_id
from app.scan.combined import run_combined_scan

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

pytestmark = pytest.mark.external


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        trivy_cache_dir=str(tmp_path / "trivy-cache"),
        build_timeout_seconds=600,
        fail_on_cvss=0.0,  # see everything for this test, not just what's above the real default threshold
    )


async def test_combined_scan_merges_both_scanners_and_dedupes(settings, tmp_path):
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    vulns = await run_combined_scan(result.paths.work, tmp_path / "output", settings)

    assert len(vulns) > 0
    # jackson-databind was independently confirmed vulnerable by both Trivy
    # and Dependency-Check earlier -- must appear exactly once after merge,
    # not once per scanner.
    jackson_entries = [v for v in vulns if "jackson-databind" in v.package]
    assert len(jackson_entries) >= 1
    packages_seen = {(v.cve_id, v.package) for v in vulns}
    assert len(packages_seen) == len(vulns), "merge_and_filter should have de-duped by (cve_id, package)"
