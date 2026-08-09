"""Integration test using real `mvn` against the ace-parent.zip reference
repo (no network required beyond whatever Maven already needs, commonly
cached in ~/.m2). Marked slow: a real subprocess invocation, not a fast
unit test. Covers apply_output_version end-to-end -- tests/unit/
test_artifact_version.py already covers the detection logic and the
mvn-calling control flow with mvn itself monkeypatched out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.ingest.workspace import ZipSourceSpec, ingest, new_job_id
from app.versioning.artifact_version import apply_output_version

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

pytestmark = pytest.mark.slow


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"), build_timeout_seconds=180)


@pytest.mark.asyncio
async def test_apply_output_version_syncs_self_referencing_bom_property(settings):
    # ingest() already does git init + baseline commit in work/, so
    # apply_output_version's own commit_checkpoint has something to commit on top of.
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    checkpoint_sha = await apply_output_version(result.paths.work, "1.0.0", settings)

    root_pom = (result.paths.work / "pom.xml").read_text(encoding="utf-8")
    assert "<version>1.0.0</version>" in root_pom
    # The actual regression this fixes: ace-parent's dependencyManagement
    # self-references ace-common/ace-ai/ace-util via ${ace.version} -- mvn
    # versions:set alone leaves that stale (confirmed empirically, job #11).
    assert "<ace.version>1.0.0</ace.version>" in root_pom
    assert checkpoint_sha
