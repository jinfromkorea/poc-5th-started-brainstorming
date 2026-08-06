"""Real end-to-end multi-step migration against tests/fixtures/legacy-boot27-sample
(Spring Boot 2.7.18 + Spring Cloud 2021.0.8 + Java 11, with a genuine
javax.servlet usage). Confirmed manually before this test was written: the
real `org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0` recipe
migrates javax.servlet.* -> jakarta.servlet.*, bumps the parent to 3.0.13,
and the result still compiles.

Scoped to just the first hop (2.7 -> 3.0, the catalog's one "verified"-confidence
entry involving a real structural change) rather than the full chain to
4.1 -- the 3.5->4.0 and 4.0->4.1 entries are marked `confidence: unverified`
in recipe_catalog.yaml precisely because Spring Boot 4.x doesn't exist yet;
attempting them would predictably fail to resolve, which isn't this test's
purpose. Marked `external`: real network, real OpenRewrite artifact downloads.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.checkpoint.git_repo import git_init_and_baseline_commit
from app.config import Settings
from app.mvnrewrite.mvn_client import mvn_compile, mvn_effective_pom
from app.mvnrewrite.pom_parser import extract_versions
from app.orchestration.multi_step import run_stage1_migration

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "legacy-boot27-sample"

pytestmark = pytest.mark.external


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"), build_timeout_seconds=300)


async def test_first_hop_migrates_javax_to_jakarta_for_real(settings, tmp_path):
    work_dir = tmp_path / "work"
    shutil.copytree(FIXTURE, work_dir)
    baseline_sha = git_init_and_baseline_commit(work_dir, settings)

    # Real detection, same as Phase 2 -- confirms the fixture is what it claims to be.
    effective_pom = tmp_path / "effective-pom.xml"
    await mvn_effective_pom(work_dir, effective_pom, settings)
    detected = extract_versions(effective_pom)
    assert detected.spring_boot_version == "2.7.18"
    assert detected.spring_cloud_version == "2021.0.8"

    result = await run_stage1_migration(
        job_id="legacy-real",
        work_dir=work_dir,
        detected=detected,
        baseline_commit=baseline_sha,
        settings=settings,
        target_boot="3.0",  # scoped to the one verified hop, not the full unverified chain
        target_java="21",
        target_ai="2.0",
    )

    assert result.status == "success", result.report
    assert [o.status for o in result.outcomes] == ["success"] * len(result.outcomes)

    controller = work_dir / "src/main/java/com/example/legacy/GreetingController.java"
    content = controller.read_text(encoding="utf-8")
    assert "import jakarta.servlet.http.HttpServletRequest;" in content
    assert "import javax.servlet.http.HttpServletRequest;" not in content

    compile_result = await mvn_compile(work_dir, settings)
    assert compile_result.returncode == 0, compile_result.output
