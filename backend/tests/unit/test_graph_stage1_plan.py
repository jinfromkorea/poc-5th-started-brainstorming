from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.mvnrewrite.recipe_catalog import RecipeCatalog
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.orchestration.graph_stage1 import initial_state, initial_state_for_step, plan_next_step, run_stage1_single_step
from app.orchestration.planning import PlanStep


@pytest.fixture()
def settings() -> Settings:
    # A non-empty key is enough to let get_chat_model() construct without
    # raising, for the tests below that reach ai_fix_node -- it's never
    # actually called since create_agent itself is monkeypatched there.
    return Settings(_env_file=None, openai_api_key="test-key")


def _fake_agent(final_messages=None):
    agent = AsyncMock()
    agent.ainvoke.return_value = {"messages": final_messages or [AIMessage(content="fixed it")]}
    return agent


def test_plan_next_step_no_gap_returns_none():
    catalog = RecipeCatalog.load()
    assert plan_next_step("4.1.0", "4.1", catalog) is None


def test_plan_next_step_known_gap_returns_step():
    catalog = RecipeCatalog.load()
    step = plan_next_step("2.7.18", "4.1", catalog)
    assert step is not None
    assert step.to_version == "3.0"
    assert step.has_known_recipe


def test_plan_next_step_unknown_origin_returns_none():
    catalog = RecipeCatalog.load()
    assert plan_next_step("1.5.0", "4.1", catalog) is None


def test_plan_next_step_detected_none_returns_none():
    catalog = RecipeCatalog.load()
    assert plan_next_step(None, "4.1", catalog) is None


async def test_graph_short_circuits_to_success_when_already_at_target(settings, tmp_path):
    """Already-at-target requires zero I/O (no mvn, no OpenRewrite, no LLM)
    -- this exercises the real graph end-to-end, not a mock."""
    result = await run_stage1_single_step(
        job_id="job-1",
        work_dir=tmp_path,  # never touched on this path
        detected_spring_boot="4.1.0",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "success"
    assert result["attempt"] == 0


async def test_graph_routes_to_ai_fix_when_no_known_recipe(monkeypatch, settings, tmp_path):
    """A real gap with no cataloged recipe for that origin now has the AI
    attempt the version bump directly (apply_node is skipped, since there's
    no recipe to apply) instead of short-circuiting to needs_handoff."""

    async def always_succeeds(*args, **kwargs):
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_succeeds)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_single_step(
        job_id="job-2",
        work_dir=tmp_path,
        detected_spring_boot="1.5.0",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "success"
    assert result["attempt"] == 1  # one AI attempt, no recipe to apply first


async def test_graph_no_recipe_step_tolerates_more_changed_files_than_the_normal_gate(monkeypatch, settings, tmp_path):
    """Reproduces job #11: a no-recipe step (AI bumping a version from
    scratch) naturally touches more files than the normal compile-fix gate
    (default 3) allows -- it must use the separate, more generous
    no-recipe ceiling (default 20) instead of tripping on file count alone
    before verify even runs."""

    async def always_succeeds(*args, **kwargs):
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_succeeds)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 4)
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_single_step(
        job_id="job-2c",
        work_dir=tmp_path,
        detected_spring_boot="1.5.0",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "success"  # would have been needs_handoff under the normal 3-file gate


async def test_graph_needs_handoff_when_ai_bridge_never_compiles(monkeypatch, settings, tmp_path):
    """Same gap, but the AI's direct attempt never gets the build compiling
    -- exhausts retries and hands off, same as a failed recipe-based step."""

    async def always_fails(*args, **kwargs):
        return SubprocessResult(returncode=1, output="still broken", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_fails)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_single_step(
        job_id="job-2b",
        work_dir=tmp_path,
        detected_spring_boot="1.5.0",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "needs_handoff"


def test_initial_state_pulls_limits_from_settings(settings, tmp_path):
    settings.compile_fix_max_attempts = 7
    settings.compile_fix_auto_apply_max_files = 9

    state = initial_state("job-3", tmp_path, "2.7.18", "4.1", settings)

    assert state["max_attempts"] == 7
    assert state["max_auto_apply_files"] == 9
    assert state["status"] == "running"
    assert state["attempt"] == 0


def test_initial_state_for_step_uses_no_recipe_limit_when_step_has_no_recipe(settings, tmp_path):
    settings.compile_fix_auto_apply_max_files = 3
    settings.compile_fix_auto_apply_max_files_no_recipe = 20

    no_recipe_step = PlanStep(kind="spring_boot", description="4.0 -> 4.1 (AI 직접 시도)", recipe=None, artifact=None, target_version="4.1")
    recipe_step = PlanStep(
        kind="spring_boot", description="2.7 -> 3.0", recipe="org.openrewrite.Fake", artifact="fake:artifact:RELEASE", target_version="3.0"
    )

    assert initial_state_for_step("job-4", tmp_path, no_recipe_step, settings)["max_auto_apply_files"] == 20
    assert initial_state_for_step("job-4", tmp_path, recipe_step, settings)["max_auto_apply_files"] == 3
