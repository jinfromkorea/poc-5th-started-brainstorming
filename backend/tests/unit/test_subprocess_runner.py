"""run_subprocess's own cancellation handling (spec: docs/superpowers/specs/
2026-08-08-job-cancellation-design.md) -- when the asyncio.Task running it is
cancelled (e.g. a job's "중지" button), the real OS child process must
actually be killed, not just abandoned as an orphan, and (if a log file is
attached) get a visible "force-terminated" marker line.
"""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from app.config import Settings
from app.mvnrewrite.subprocess_runner import run_subprocess

# sys.executable is an absolute path, so resolve_executable's shutil.which()
# resolves it directly without depending on PATH state.
_SLEEP_30S = [sys.executable, "-c", "import time; time.sleep(30)"]


def _settings() -> Settings:
    return Settings(_env_file=None)


async def _cancel_after_it_starts(coro) -> float:
    """Starts coro as a Task, gives the child process a moment to actually
    launch, cancels, and returns elapsed time until the cancellation
    resolved -- a near-instant return proves the OS process was actually
    killed rather than left running for the full 30s sleep."""
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0.3)
    started_at = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return time.monotonic() - started_at


async def test_cancel_kills_the_child_process_and_marks_the_log(tmp_path):
    log_path = tmp_path / "cancel-test.log"
    elapsed = await _cancel_after_it_starts(run_subprocess(_SLEEP_30S, tmp_path, _settings(), log_path=log_path))

    assert elapsed < 5
    assert "[강제종료됨]" in log_path.read_text(encoding="utf-8")


async def test_cancel_without_a_log_path_still_propagates_cancelled_error(tmp_path):
    elapsed = await _cancel_after_it_starts(run_subprocess(_SLEEP_30S, tmp_path, _settings()))
    assert elapsed < 5
