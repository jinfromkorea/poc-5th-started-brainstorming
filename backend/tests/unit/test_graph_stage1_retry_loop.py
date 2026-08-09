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

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", fake_mvn_compile)
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

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_fails)
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

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", counting_mvn_compile)
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


async def test_apply_failure_skips_verify_and_goes_straight_to_ai_fix(monkeypatch, settings, tmp_path):
    """route_after_apply (spec: docs/superpowers/specs/2026-08-09-stage1-
    apply-verify-integrity-design.md): a recipe that fails to apply
    (exit != 0) must route straight to ai_fix, not through verify. Proven
    here by making verify unconditionally report success -- under the old
    fixed apply->verify edge, that would end the graph with status=success
    and attempt still 0 (ai_fix never even called), silently ignoring that
    the recipe never actually applied. Under the fix, ai_fix runs first
    (attempt becomes 1), and only then does verify get a chance to run."""
    verify_calls = {"n": 0}

    async def succeeding_verify(work_dir, settings_, log_path=None, on_line=None):
        verify_calls["n"] += 1
        return SubprocessResult(returncode=0, output="build output", log_path=None)

    async def failing_apply(*args, **kwargs):
        return SubprocessResult(returncode=1, output="mvn rewrite:run: BUILD FAILURE", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", succeeding_verify)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", failing_apply)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_single_step(
        job_id="job-apply-fail",
        work_dir=tmp_path,
        detected_spring_boot="2.7.18",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "success"
    assert result["attempt"] == 1  # ai_fix DID run -- the apply failure wasn't silently swallowed
    assert verify_calls["n"] == 1  # verify only ran once, after ai_fix -- not as a direct consequence of the failed apply


async def test_apply_failure_output_reaches_ai_fix_prompt(monkeypatch, settings, tmp_path):
    """The recipe's own failure output must survive into ai_fix's prompt --
    if apply always flowed through verify first (the old behavior), verify
    would overwrite last_build_output with its own (here: unrelated, since
    nothing was actually broken) result and bury the real failure reason."""
    captured_agent = _fake_agent()

    async def failing_apply(*args, **kwargs):
        return SubprocessResult(returncode=1, output="mvn rewrite:run: BUILD FAILURE reason XYZ", log_path=None)

    async def succeeding_verify(work_dir, settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=0, output="unrelated verify output", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", succeeding_verify)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", failing_apply)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: captured_agent)

    await run_stage1_single_step(
        job_id="job-apply-fail-prompt",
        work_dir=tmp_path,
        detected_spring_boot="2.7.18",
        target_spring_boot="4.1",
        settings=settings,
    )

    prompt = captured_agent.ainvoke.call_args.args[0]["messages"][0].content
    assert "mvn rewrite:run: BUILD FAILURE reason XYZ" in prompt
    assert "[openrewrite exit=1]" in prompt
    assert "unrelated verify output" not in prompt  # verify never ran before this ai_fix call


async def test_apply_success_still_flows_through_verify(monkeypatch, settings, tmp_path):
    """Regression check: a successful apply must still go through verify as
    before -- route_after_apply only changes behavior on failure."""
    verify_calls = {"n": 0}

    async def counting_verify(work_dir, settings_, log_path=None, on_line=None):
        verify_calls["n"] += 1
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    async def succeeding_apply(*args, **kwargs):
        return SubprocessResult(returncode=0, output="recipe applied", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", counting_verify)
    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", succeeding_apply)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")

    result = await run_stage1_single_step(
        job_id="job-apply-success",
        work_dir=tmp_path,
        detected_spring_boot="2.7.18",
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "success"
    assert result["attempt"] == 0  # ai_fix never needed -- apply succeeded, verify passed on the first try
    assert verify_calls["n"] == 1
