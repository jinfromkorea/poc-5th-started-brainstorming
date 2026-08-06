"""Integration tests using real `mvn` against the 4 reference zips (no
network required beyond whatever Maven already needs to resolve the
help/versions plugins, which are commonly already cached in ~/.m2). Marked
slow: these are real subprocess invocations, not fast unit tests.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.ingest.workspace import ZipSourceSpec, ingest, new_job_id
from app.mvnrewrite.mvn_client import mvn_compile, mvn_effective_pom, mvn_versions_set
from app.mvnrewrite.pom_parser import extract_versions

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

pytestmark = pytest.mark.slow


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"), build_timeout_seconds=180)


@pytest.mark.asyncio
async def test_effective_pom_detects_real_versions(settings):
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    output_path = result.paths.output / "effective-pom.xml"
    await mvn_effective_pom(result.paths.work, output_path, settings)
    detected = extract_versions(output_path)

    # Ground truth confirmed by directly reading ace-parent/pom.xml earlier.
    assert detected.java_version == "21"
    assert detected.spring_boot_version == "3.5.16"
    assert detected.spring_ai_version == "1.1.8"
    assert detected.spring_cloud_version is None  # ace-parent doesn't use Spring Cloud


@pytest.mark.asyncio
async def test_mvn_compile_succeeds_on_reference_repo(settings):
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    log_path = result.paths.output / "logs" / "mvn-compile.log"
    compile_result = await mvn_compile(result.paths.work, settings, log_path=log_path)

    assert compile_result.returncode == 0, compile_result.output
    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8").strip() != ""


@pytest.mark.asyncio
async def test_versions_set_updates_artifact_version_across_reactor(settings):
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    vset_result = await mvn_versions_set(result.paths.work, "9.9.9-test", settings)

    assert vset_result.returncode == 0, vset_result.output
    root_pom = (result.paths.work / "pom.xml").read_text(encoding="utf-8")
    assert "<version>9.9.9-test</version>" in root_pom
