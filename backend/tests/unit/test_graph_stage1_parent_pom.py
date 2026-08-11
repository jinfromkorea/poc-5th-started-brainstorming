"""Tests the "parent_pom" step kind's routing through graph_stage1 (spec:
docs/superpowers/specs/2026-08-11-internal-parent-pom-target-version-
design.md) -- apply_node's mechanical patch_parent_version branch, and
ai_fix_node's dedicated prompt when that patch doesn't compile. mvn/the LLM
agent are all stubbed out, same style as test_graph_stage1_retry_loop.py.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.orchestration.graph_stage1 import run_stage1_step
from app.orchestration.planning import PlanStep


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None, openai_api_key="test-key", compile_fix_max_attempts=2, compile_fix_auto_apply_max_files=10)


def _fake_agent(final_messages=None):
    agent = AsyncMock()
    agent.ainvoke.return_value = {"messages": final_messages or [AIMessage(content="fixed it")]}
    return agent


def _parent_step(target_version="0.5.0") -> PlanStep:
    return PlanStep(
        kind="parent_pom",
        description=f"사내 parent POM 버전을 {target_version}로 교체",
        recipe=None,
        artifact=None,
        target_version=target_version,
    )


async def test_parent_pom_step_succeeds_when_patch_and_verify_both_succeed(monkeypatch, settings, tmp_path):
    commit_calls = []

    async def succeeding_verify(work_dir, settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=0, output="build ok", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.patch_parent_version", lambda pom_path, new_version: None)
    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", succeeding_verify)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: commit_calls.append(a) or "deadbeef")

    result = await run_stage1_step(job_id="job-parent-1", work_dir=tmp_path, step=_parent_step(), settings=settings)

    assert result["status"] == "success"
    assert result["attempt"] == 0  # ai_fix never needed
    assert len(commit_calls) == 1  # only apply_node's own checkpoint, no OpenRewrite commit path taken


async def test_parent_pom_step_verify_failure_uses_parent_specific_ai_fix_prompt(monkeypatch, settings, tmp_path):
    captured_agent = _fake_agent()

    async def always_fails(work_dir, settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=1, output="cannot find symbol: new API from bumped parent", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.patch_parent_version", lambda pom_path, new_version: None)
    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_fails)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: captured_agent)

    result = await run_stage1_step(job_id="job-parent-2", work_dir=tmp_path, step=_parent_step(), settings=settings)

    assert result["status"] == "needs_handoff"
    assert result["attempt"] == settings.compile_fix_max_attempts

    prompt = captured_agent.ainvoke.call_args_list[0].args[0]["messages"][0].content
    assert "<parent><version>" in prompt
    assert "0.5.0" in prompt
    assert "cannot find symbol: new API from bumped parent" in prompt
    # must NOT get the generic "no recipe, do it from scratch" framing --
    # apply_node already did something, this is a reaction to its failure.
    assert "There is no automated migration recipe" not in prompt


async def test_parent_pom_step_patch_failure_still_goes_through_normal_retry_path(monkeypatch, settings, tmp_path):
    """patch_parent_version raising (e.g. no <parent> element) isn't given a
    special immediate-failure shortcut -- it's recorded as a failed apply
    (apply_returncode=1) and flows through the same verify -> ai_fix ->
    handoff path as any other apply failure, same as the spec's "존재하지
    않는 버전" edge case."""

    def failing_patch(pom_path, new_version):
        raise ValueError("no <parent> element to update")

    async def always_fails(work_dir, settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=1, output="never reached a real build", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.patch_parent_version", failing_patch)
    monkeypatch.setattr("app.orchestration.graph_stage1.mvn_test_compile", always_fails)
    monkeypatch.setattr("app.orchestration.graph_stage1.changed_file_count", lambda work_dir, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage1.commit_checkpoint", lambda *a, **k: "deadbeef")
    monkeypatch.setattr("app.orchestration.graph_stage1.create_agent", lambda *a, **k: _fake_agent())

    result = await run_stage1_step(job_id="job-parent-3", work_dir=tmp_path, step=_parent_step(), settings=settings)

    assert result["status"] == "needs_handoff"
    assert result["attempt"] == settings.compile_fix_max_attempts
    assert "[parent-patch exit=1]" in result["last_build_output"] or "never reached a real build" in result["last_build_output"]
