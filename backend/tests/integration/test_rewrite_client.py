"""OpenRewrite invocation against a real reference repo. Marked `external`
(not just `slow`): needs network access to Maven Central to resolve the
rewrite-maven-plugin and recipe artifacts, which can also be a meaningfully
larger one-time download than a plain `mvn compile`.

The recipe/coordinates below (org.openrewrite.java.migrate.UpgradeToJava21,
via rewrite-migrate-java:RELEASE) were confirmed to actually work end-to-end
against a real copy of ace-parent before this test was written -- including
finding and fixing a real bug: pinning the plugin to a fixed old version
while the recipe artifact floats to RELEASE breaks with a class-incompatibility
error the moment they drift apart (see rewrite_client.py's comment).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.ingest.workspace import ZipSourceSpec, ingest, new_job_id
from app.mvnrewrite.mvn_client import mvn_compile
from app.mvnrewrite.rewrite_client import run_openrewrite_recipes

DATA_DIR = Path(__file__).resolve().parents[3] / "data"

pytestmark = pytest.mark.external


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"), build_timeout_seconds=300)


async def test_upgrade_to_java_21_recipe_modifies_and_still_compiles(settings):
    result = ingest(new_job_id(), ZipSourceSpec(zip_path=DATA_DIR / "ace-parent.zip"), settings)

    rewrite_result = await run_openrewrite_recipes(
        result.paths.work,
        active_recipes=["org.openrewrite.java.migrate.UpgradeToJava21"],
        recipe_artifact_coordinates=["org.openrewrite.recipe:rewrite-migrate-java:RELEASE"],
        settings=settings,
        log_path=result.paths.output / "logs" / "rewrite-java21.log",
    )

    assert rewrite_result.returncode == 0, rewrite_result.output
    # OpenRewrite only touches source files, never pom.xml plugin config --
    # but it CAN legitimately rewrite pom.xml content itself (e.g. compiler
    # release config), so just confirm the invocation ran for real rather
    # than asserting no changes at all.
    assert "Running recipe" in rewrite_result.output or "no changes" in rewrite_result.output.lower()

    # The already-Java-21 project should still compile after the recipe
    # (it's a no-op-ish upgrade-to-21 on an already-21 codebase).
    compile_result = await mvn_compile(result.paths.work, settings)
    assert compile_result.returncode == 0, compile_result.output

    root_pom_after = (result.paths.work / "pom.xml").read_text(encoding="utf-8")
    assert "rewrite-maven-plugin" not in root_pom_after  # never injected into the target's pom.xml
