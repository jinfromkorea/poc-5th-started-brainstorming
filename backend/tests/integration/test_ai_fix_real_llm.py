"""Real end-to-end check of the AI-fix loop: real OpenAI API call, real
tool-calling agent (create_react_agent), real `mvn compile`. Everything
EXCEPT the OpenRewrite recipe application is real -- that step is stubbed
to a no-op since rewrite_client.py is already separately verified
(test_rewrite_client.py) and re-exercising it here would just add an
unrelated network round-trip/cost to what's meant to be a focused check of
the AI-fix mechanism itself.

Uses a tiny synthetic project (tests/fixtures/ai-fix-sample) with one
deliberate, cheap-to-fix compile error (a missing semicolon) rather than a
full reference repo, to keep this real-money test fast and near-deterministic.

Marked `external`: real network + real OPENAI_API_KEY spend, opt-in only.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.checkpoint.git_repo import git_init_and_baseline_commit
from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ai-fix-sample"

pytestmark = pytest.mark.external


@pytest.fixture()
def settings() -> Settings:
    s = Settings()  # loads backend/.env for real -- OPENAI_API_KEY must be set
    if not s.openai_api_key:
        pytest.skip("OPENAI_API_KEY not set in backend/.env -- copy draft/.env's value in to run this test")
    return s


async def test_ai_fix_loop_actually_fixes_a_real_compile_error(monkeypatch, settings, tmp_path):
    work_dir = tmp_path / "work"
    shutil.copytree(FIXTURE, work_dir)
    git_init_and_baseline_commit(work_dir, settings)

    broken_file = work_dir / "src" / "main" / "java" / "com" / "example" / "Greeter.java"
    assert "!\"" in broken_file.read_text(encoding="utf-8")  # sanity: bug is there before we start

    async def fake_run_openrewrite_recipes(*args, **kwargs):
        # Not under test here -- rewrite_client.py is verified separately.
        return SubprocessResult(returncode=0, output="(stubbed, no-op)", log_path=None)

    monkeypatch.setattr("app.orchestration.graph_stage1.run_openrewrite_recipes", fake_run_openrewrite_recipes)

    from app.orchestration.graph_stage1 import run_stage1_single_step

    result = await run_stage1_single_step(
        job_id="ai-fix-real",
        work_dir=work_dir,
        detected_spring_boot="2.7.18",  # any real catalog origin -- gets us past `plan` into `apply`/`verify`
        target_spring_boot="4.1",
        settings=settings,
    )

    assert result["status"] == "success", result["last_build_output"][-3000:]
    assert result["attempt"] >= 1  # the fix genuinely required at least one AI turn
    fixed_content = broken_file.read_text(encoding="utf-8")
    assert fixed_content != FIXTURE.joinpath(
        "src/main/java/com/example/Greeter.java"
    ).read_text(encoding="utf-8")  # the AI actually changed the file, not just got lucky on a rebuild
