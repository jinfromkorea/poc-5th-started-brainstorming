"""Stage 2 outer loop (scan results -> per-CVE patch loop -> checkpoint/
rollback + handoff), with mvn/dependency-patch/the LLM agent all stubbed --
deterministic tests of the loop's own control flow. Key behavioral
difference from Stage 1 asserted here: one CVE failing does NOT stop the
rest from being attempted (they're independent, unlike Stage 1's sequential
migration steps).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from app.checkpoint.git_repo import current_head, git_init_and_baseline_commit, log_since
from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.orchestration.stage2_loop import run_stage2_patches
from app.scan.merge import Vulnerability


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        _env_file=None,
        openai_api_key="test-key",
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
    agent.ainvoke.return_value = {"messages": [AIMessage(content="attempted a patch")]}
    return agent


def _vuln(cve_id: str, fix_version: str | None = "1.2.4") -> Vulnerability:
    return Vulnerability(
        cve_id=cve_id,
        package="com.example:some-lib",
        installed_version="1.2.3",
        fix_version=fix_version,
        cvss=8.1,
        severity="HIGH",
        source="trivy",
    )


async def test_all_vulnerabilities_patched_successfully(monkeypatch, settings, work_dir):
    async def patch_ok(work_dir_, group_id, artifact_id, new_version, settings_, log_path=None):
        return SubprocessResult(returncode=0, output="patched", log_path=None)

    async def verify_ok(work_dir_, settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage2.patch_dependency_version", patch_ok)
    monkeypatch.setattr("app.orchestration.graph_stage2.mvn_verify", verify_ok)
    monkeypatch.setattr("app.orchestration.graph_stage2.create_agent", lambda *a, **k: _fake_agent())

    baseline_sha = current_head(work_dir, settings)
    vulns = [_vuln("CVE-2026-0001"), _vuln("CVE-2026-0002")]

    result = await run_stage2_patches("job-s2-1", work_dir, vulns, baseline_sha, settings)

    assert [o.status for o in result.outcomes] == ["success", "success"]
    assert all(o.handoff_guide is None for o in result.outcomes)

    commits = log_since(work_dir, settings, baseline_sha).strip().splitlines()
    assert len(commits) == 2


async def test_one_failure_does_not_stop_remaining_independent_patches(monkeypatch, settings, work_dir):
    calls = {"n": 0}

    async def patch_noop(work_dir_, group_id, artifact_id, new_version, settings_, log_path=None):
        calls["n"] += 1
        return SubprocessResult(returncode=0, output="patched", log_path=None)

    async def verify_fails_first_only(work_dir_, settings_, log_path=None, on_line=None):
        # First CVE's verify always fails (exhausts retries); second CVE's succeeds.
        ok = calls["n"] > 1  # after the first patch call, subsequent verifies pass
        return SubprocessResult(returncode=0 if ok else 1, output="broken" if not ok else "ok", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage2.patch_dependency_version", patch_noop)
    monkeypatch.setattr("app.orchestration.graph_stage2.mvn_verify", verify_fails_first_only)
    monkeypatch.setattr("app.orchestration.graph_stage2.changed_file_count", lambda work_dir_, settings_: 1)
    monkeypatch.setattr("app.orchestration.graph_stage2.create_agent", lambda *a, **k: _fake_agent())

    baseline_sha = current_head(work_dir, settings)
    vulns = [_vuln("CVE-2026-FAIL"), _vuln("CVE-2026-OK")]

    result = await run_stage2_patches("job-s2-2", work_dir, vulns, baseline_sha, settings)

    # Both were attempted -- the loop did NOT stop after the first failure.
    assert [o.status for o in result.outcomes] == ["needs_handoff", "success"]
    assert result.outcomes[0].handoff_guide is not None
    assert "CVE-2026-FAIL" in result.outcomes[0].handoff_guide
    assert result.outcomes[1].handoff_guide is None

    # Only the second (successful) patch survives in history.
    commits = log_since(work_dir, settings, baseline_sha).strip().splitlines()
    assert len(commits) == 1
    assert "CVE-2026-OK" in commits[0]


async def test_empty_vulnerability_list_produces_empty_report(settings, work_dir):
    baseline_sha = current_head(work_dir, settings)

    result = await run_stage2_patches("job-s2-3", work_dir, [], baseline_sha, settings)

    assert result.outcomes == []
    assert "패치 대상 취약점이 없습니다" in result.report
