"""Integration test using real `mvn` against the ace-parent.zip/anne-agent.zip
reference repos (no network required beyond whatever Maven already needs,
commonly cached in ~/.m2). Marked slow: real subprocess invocations, not a
fast unit test. tests/unit/test_parent_patch.py already covers the XML edit
itself in isolation.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.ingest.workspace import ZipSourceSpec, ingest, new_job_id
from app.mvnrewrite.parent_patch import patch_parent_version
from app.mvnrewrite.subprocess_runner import run_subprocess
from app.procenv import resolve_executable

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

pytestmark = pytest.mark.slow


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"), build_timeout_seconds=180)


def _install_ace_parent_at_version(tmp_path: Path, version: str) -> None:
    """Installs a copy of ace-parent.zip's own pom.xml, with its <version>
    rewritten, into the local Maven repo (`mvn -N install`) -- simulates a
    platform team having already released a new ace-parent version to Nexus
    with the target stack, which is exactly the scenario this feature is
    for (spec: docs/superpowers/specs/2026-08-11-internal-parent-pom-
    target-version-design.md)."""
    settings = Settings(_env_file=None, jobs_data_dir=str(tmp_path / f"jobs-parent-{version}"))
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)
    pom_path = result.paths.work / "pom.xml"
    text = pom_path.read_text(encoding="utf-8")
    assert "<version>0.4.5</version>" in text  # sanity: this is the version we're about to overwrite
    pom_path.write_text(text.replace("<version>0.4.5</version>", f"<version>{version}</version>", 1), encoding="utf-8")
    subprocess.run(
        [resolve_executable("mvn"), "-B", "-N", "install", "-q"],
        cwd=result.paths.work,
        check=True,
        timeout=120,
        capture_output=True,
        text=True,
    )


@pytest.mark.asyncio
async def test_patch_parent_version_then_build_resolves_the_new_parent(settings, tmp_path):
    """Regression test for the mvn versions:update-parent failure mode found
    while designing this feature: it does not reliably pin to the exact
    version requested, it resolves against version metadata and can jump to
    a different, numerically "higher" version instead (confirmed empirically
    against this same ace-parent/anne-agent pair). A direct XML edit +
    normal build has no such ambiguity."""
    _install_ace_parent_at_version(tmp_path, "0.5.0")

    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "anne-agent.zip"), settings)
    pom_path = result.paths.work / "pom.xml"
    assert "<version>0.4.5</version>" in pom_path.read_text(encoding="utf-8")  # sanity: still the original parent version

    patch_parent_version(pom_path, "0.5.0")
    assert "<version>0.5.0</version>" in pom_path.read_text(encoding="utf-8")

    # help:evaluate on project.parent.version requires the parent to actually
    # resolve (confirmed empirically: pointing it at a nonexistent version
    # fails here with "Non-resolvable parent POM") -- a real mvn_test_compile
    # would also prove this, but anne-web's frontend-maven-plugin step needs
    # an internal-network Node.js download unrelated to this feature, so this
    # lighter check is the more hermetic way to prove the same thing.
    evaluate_result = await run_subprocess(
        ["mvn", "-B", "help:evaluate", "-Dexpression=project.parent.version", "-q", "-DforceStdout"],
        result.paths.work,
        settings,
    )
    assert evaluate_result.returncode == 0, evaluate_result.output
    assert evaluate_result.output.strip() == "0.5.0"
