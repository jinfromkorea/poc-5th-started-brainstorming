"""Multi-step Stage 1 orchestration (planning.build_migration_plan + the
per-step graph + git checkpoint/rollback + handoff guide), with mvn/
OpenRewrite/the LLM agent all stubbed -- deterministic tests of the outer
loop's own control flow. Real end-to-end multi-step runs belong in
tests/integration against the legacy-boot27-sample fixture (external/slow).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.checkpoint.git_repo import current_head, git_init_and_baseline_commit, log_since
from app.config import Settings
from app.mvnrewrite.pom_parser import DetectedVersions
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.orchestration.multi_step import run_stage1_migration


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="test-key",  # never actually called -- create_agent is monkeypatched
        compile_fix_max_attempts=2,
        compile_fix_auto_apply_max_files=10,
    )


@pytest.fixture()
def work_dir(tmp_path, settings):
    d = tmp_path / "work"
    d.mkdir()
    (d / "pom.xml").write_text("<project/>")
    git_init_and_baseline_commit(d, settings)
    return d


def _fake_agent():
    agent = AsyncMock()
    agent.ainvoke.return_value = {"messages": [AIMessage(content="attempted a fix")]}
    return agent


def _detected(spring_boot="3.4.0", java="21"):
    return DetectedVersions(
        java_version=java, spring_boot_version=spring_boot, spring_cloud_version=None, spring_ai_version=None
    )


async def test_all_steps_succeed_checkpoints_each_one(monkeypatch, settings, work_dir):
    async def always_succeeds(*args, **kwargs):
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_succeeds)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", always_succeeds)
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    baseline_sha = current_head(work_dir, settings)

    result = await run_stage1_migration(
        job_id="job-multi-1",
        work_dir=work_dir,
        detected=_detected(spring_boot="3.4.0"),  # -> hops to 3.5, 4.0 (2 steps)
        baseline_commit=baseline_sha,
        settings=settings,
        target_boot="4.0",  # catalog has no 4.0 -> 4.1 recipe (see recipe_catalog.yaml) --
        # pin the target here so this test stays about "all planned steps
        # succeed", not about that separate (and separately tested) gap.
    )

    assert result.status == "success"
    assert [o.status for o in result.outcomes] == ["success", "success"]
    assert result.handoff_guide is None

    commits = log_since(work_dir, settings, baseline_sha).strip().splitlines()
    # Two commits per successful step: graph_stage1.apply_node's own
    # "applied recipe ..." checkpoint, then this loop's "checkpoint: {step
    # description}" once the whole step verifies.
    assert len(commits) == 4


async def test_middle_step_fails_rolls_back_and_stops(monkeypatch, settings, work_dir):
    openrewrite_calls = {"n": 0}

    async def counting_openrewrite(*args, **kwargs):
        openrewrite_calls["n"] += 1
        return SubprocessResult(returncode=0, output="applied", log_path=None)

    async def compile_fails_on_second_step(work_dir_, settings_, log_path=None, on_line=None):
        # Step 1's verify succeeds (openrewrite_calls==1 when verify runs);
        # step 2's verify always fails (exhausts retries); step 3 never reached.
        return SubprocessResult(
            returncode=0 if openrewrite_calls["n"] == 1 else 1, output="build broke here", log_path=None
        )

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", compile_fails_on_second_step)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", counting_openrewrite)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir_, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    baseline_sha = current_head(work_dir, settings)

    result = await run_stage1_migration(
        job_id="job-multi-2",
        work_dir=work_dir,
        detected=_detected(spring_boot="3.4.0"),  # -> 3.5 (succeeds), 4.0 (fails), 4.1 (never attempted)
        baseline_commit=baseline_sha,
        settings=settings,
    )

    assert result.status == "needs_handoff"
    assert [o.status for o in result.outcomes] == ["success", "needs_handoff"]
    assert result.outcomes[0].step.target_version == "3.5"
    assert result.outcomes[1].step.target_version == "4.0"

    # Step 1's checkpoint survives (2 commits: recipe + step-success), AND so
    # does step 2's recipe commit -- only step 2's post-recipe AI-fix attempts
    # (never committed to begin with) were discarded, not the recipe itself.
    commits = log_since(work_dir, settings, baseline_sha).strip().splitlines()
    assert len(commits) == 3
    assert any("applied recipe" in c for c in commits)

    guide = result.handoff_guide
    assert guide is not None
    for section in ("## 1. 마이그레이션 맥락", "## 2. 여기까지", "## 3. 실패한 에러", "## 4. 이미 시도", "## 5. 다음에"):
        assert section in guide
    assert "build broke here" in guide


async def test_catalog_gap_ai_bridges_it_successfully(monkeypatch, settings, work_dir):
    """Spring Boot 4.0 -> 4.1 has no cataloged recipe (recipe_catalog.yaml) --
    build_migration_plan now still plans a step for it (recipe=None), and
    graph_stage1 has the AI attempt the bump directly (no apply_node, since
    there's no recipe to apply)."""

    async def always_succeeds(*args, **kwargs):
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_succeeds)
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    baseline_sha = current_head(work_dir, settings)

    result = await run_stage1_migration(
        job_id="job-multi-4",
        work_dir=work_dir,
        detected=_detected(spring_boot="4.0.0"),  # already at 4.0; target 4.1 has no known hop
        baseline_commit=baseline_sha,
        settings=settings,
    )

    assert result.status == "success"
    assert [o.status for o in result.outcomes] == ["success"]
    assert result.outcomes[0].step.recipe is None
    assert result.handoff_guide is None

    # No OpenRewrite recipe to apply for this step -- only the outer loop's
    # own "checkpoint: {step description}" commit once it verifies.
    commits = log_since(work_dir, settings, baseline_sha).strip().splitlines()
    assert len(commits) == 1


async def test_catalog_gap_ai_bridge_fails_promotes_to_needs_handoff(monkeypatch, settings, work_dir):
    """Same gap as above, but the AI's direct attempt never gets the build
    compiling -- exhausts retries and hands off, same as any other step."""

    async def always_fails(*args, **kwargs):
        return SubprocessResult(returncode=1, output="still broken", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_fails)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir_, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    baseline_sha = current_head(work_dir, settings)

    result = await run_stage1_migration(
        job_id="job-multi-5",
        work_dir=work_dir,
        detected=_detected(spring_boot="4.0.0"),
        baseline_commit=baseline_sha,
        settings=settings,
    )

    assert result.status == "needs_handoff"
    assert [o.status for o in result.outcomes] == ["needs_handoff"]
    assert result.handoff_guide is not None
    assert "still broken" in result.handoff_guide
    assert "알려진 자동 수단 없음" in result.handoff_guide  # mechanism_used=None wording

    # apply_node never ran for this step (no recipe), so nothing was committed.
    commits = log_since(work_dir, settings, baseline_sha).strip().splitlines()
    assert commits == []


async def test_already_at_target_produces_no_gap_status(settings, work_dir):
    baseline_sha = current_head(work_dir, settings)

    result = await run_stage1_migration(
        job_id="job-multi-3",
        work_dir=work_dir,
        detected=_detected(spring_boot="4.1.0", java="21"),
        baseline_commit=baseline_sha,
        settings=settings,
    )

    assert result.status == "no_gap"
    assert result.outcomes == []
    assert result.handoff_guide is None
