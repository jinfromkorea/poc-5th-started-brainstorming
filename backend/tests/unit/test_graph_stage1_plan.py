from __future__ import annotations

import pytest

from app.config import Settings
from app.mvnrewrite.recipe_catalog import RecipeCatalog
from app.orchestration.graph_stage1 import initial_state, plan_next_step, run_stage1_single_step


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


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


async def test_graph_short_circuits_to_needs_handoff_when_no_known_recipe(settings, tmp_path):
    """A real gap with no cataloged recipe for that origin also needs zero
    I/O -- Phase 3 doesn't attempt an unguided AI fix from nothing."""
    result = await run_stage1_single_step(
        job_id="job-2",
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
