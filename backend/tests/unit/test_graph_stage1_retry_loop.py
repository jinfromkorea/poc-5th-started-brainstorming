"""Tests the graph's own control flow (retry counting, auto-apply-file-count
gate, success/needs_handoff routing) with mvn/OpenRewrite/the LLM agent all
stubbed out -- these are deterministic unit tests of orchestration logic, not
real builds or real model calls. Real end-to-end runs belong in
tests/integration (marked slow/external).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.orchestration.graph_stage1 import run_stage1_single_step


@pytest.fixture()
def settings() -> Settings:
    # A non-empty key is enough to let get_chat_model() construct a
    # ChatOpenAI object without raising -- it's never actually called,
    # since create_agent itself is monkeypatched below.
    return Settings(_env_file=None, openai_api_key="test-key", compile_fix_max_attempts=3, compile_fix_auto_apply_max_files=10)


def _fake_agent(final_messages=None):
    agent = AsyncMock()
    agent.ainvoke.return_value = {"messages": final_messages or [AIMessage(content="fixed it")]}
    return agent


async def test_retries_until_build_succeeds(monkeypatch, settings, tmp_path):
    call_count = {"n": 0}

    async def fake_mvn_compile(work_dir, settings_, log_path=None, on_line=None):
        call_count["n"] += 1
        ok = call_count["n"] >= 3  # fails twice, succeeds on the 3rd verify
        return SubprocessResult(returncode=0 if ok else 1, output="build output", log_path=None)

    async def fake_run_openrewrite_recipes(*args, **kwargs):
        return SubprocessResult(returncode=0, output="recipe applied", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_compile", fake_mvn_compile)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", fake_run_openrewrite_recipes)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_single_step(
        job_id="job-retry",
        work_dir=tmp_path,
        detected_spring_boot="2.7.18",  # -> known step to 3.0 in the catalog
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "success"
    assert call_count["n"] == 3  # 2 failed verifies + 1 successful verify
    assert result["attempt"] == 2  # ai_fix ran twice (after the 2 failures)


async def test_exhausts_retries_and_needs_handoff(monkeypatch, settings, tmp_path):
    async def always_fails(work_dir, settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=1, output="still broken", log_path=None)

    async def fake_run_openrewrite_recipes(*args, **kwargs):
        return SubprocessResult(returncode=0, output="recipe applied", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_compile", always_fails)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", fake_run_openrewrite_recipes)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_single_step(
        job_id="job-exhausted",
        work_dir=tmp_path,
        detected_spring_boot="2.7.18",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "needs_handoff"
    assert result["attempt"] == settings.compile_fix_max_attempts


async def test_auto_apply_file_count_gate_short_circuits_to_handoff(monkeypatch, settings, tmp_path):
    """If the AI's fix touches more files than allowed, hand off immediately
    instead of re-verifying and potentially looping further."""

    async def always_fails(work_dir, settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=1, output="still broken", log_path=None)

    async def fake_run_openrewrite_recipes(*args, **kwargs):
        return SubprocessResult(returncode=0, output="recipe applied", log_path=None)

    verify_calls = {"n": 0}

    async def counting_mvn_compile(work_dir, settings_, log_path=None, on_line=None):
        verify_calls["n"] += 1
        return SubprocessResult(returncode=1, output="still broken", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_compile", counting_mvn_compile)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", fake_run_openrewrite_recipes)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 999)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_single_step(
        job_id="job-gate",
        work_dir=tmp_path,
        detected_spring_boot="2.7.18",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "needs_handoff"
    assert result["attempt"] == 1  # ai_fix ran exactly once before the gate stopped it
    assert verify_calls["n"] == 1  # only the initial verify (the one that triggered ai_fix); no re-verify after the gate trips
