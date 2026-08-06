"""Real scans against the reference zips (spec Verification: "Stage 2's real
CVE scanning"). Marked `external`: both tools need network (Trivy for its
vulnerability DB from a container registry; Dependency-Check for the NVD
dataset). Trivy's DB is a small, fast one-time download (~100MB, confirmed
empirically to take well under a minute).

Dependency-Check's first-ever run downloads the FULL NVD dataset (hundreds
of thousands of records) and can take a very long time depending on
network/NVD API throughput -- confirmed empirically. With a pre-warmed
DEPENDENCY_CHECK_DATA_DIR (an existing local cache copied in), a re-run only
needs an incremental update and completes in a few minutes -- that's the
setup this test expects; run `scripts/check_prereqs.py`-adjacent guidance in
the README if DEPENDENCY_CHECK_DATA_DIR is empty before running this.

Also confirmed empirically against ace-parent's real multi-module reactor
and folded into scan/dependency_check.py + scan/combined.py:
- `dependency-check:check` as a bare goal fails on later modules that
  depend on earlier sibling reactor modules unless `install` runs first.
- `-DoutputDirectory` is silently ignored across reactor modules; each
  module writes its own report under its own target/.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.ingest.workspace import ZipSourceSpec, ingest, new_job_id
from app.scan.combined import find_dependency_check_reports
from app.scan.dependency_check import run_dependency_check
from app.scan.merge import parse_dependency_check_json, parse_trivy_json
from app.scan.trivy import run_trivy_scan

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

pytestmark = pytest.mark.external


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        trivy_cache_dir=str(tmp_path / "trivy-cache"),
        build_timeout_seconds=600,
    )


async def test_trivy_scan_finds_known_vulnerable_dependency(settings, tmp_path):
    """ace-parent's own pom.xml pins commons-lang3 3.20.0 with a comment
    noting it was already bumped for CVE-2025-48924 -- but other
    dependencies (jackson-databind, lz4-java per an earlier manual check)
    are not pinned to the latest patch, so a real scan should find *something*."""
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    output_path = tmp_path / "trivy-report.json"
    scan_result = await run_trivy_scan(result.paths.work, output_path, settings)

    assert scan_result.returncode == 0, scan_result.output
    assert output_path.exists()

    vulns = parse_trivy_json(output_path)
    assert len(vulns) > 0, "expected at least one real vulnerability in ace-parent's dependency tree"
    assert all(v.package and v.cve_id for v in vulns)


async def test_dependency_check_scan_finds_real_vulnerabilities_across_reactor(settings, tmp_path):
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    scan_result = await run_dependency_check(result.paths.work, settings)

    assert scan_result.returncode == 0, scan_result.output

    report_paths = find_dependency_check_reports(result.paths.work)
    assert len(report_paths) == 4, f"expected one report per reactor module (4), found {report_paths}"

    all_vulns = [v for p in report_paths for v in parse_dependency_check_json(p)]
    assert len(all_vulns) > 0, "expected at least one real vulnerability in ace-parent's dependency tree"
    # jackson-databind is a real, known-vulnerable dependency confirmed by an
    # earlier manual scan against this exact fixture.
    assert any("jackson-databind" in v.package for v in all_vulns)
    assert all(v.cve_id.startswith(("CVE-", "GHSA-")) or v.cve_id for v in all_vulns)
