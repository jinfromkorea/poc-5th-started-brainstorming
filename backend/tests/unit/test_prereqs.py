"""git was missing from CHECKS despite being a hard requirement for every
job (work/ checkpointing via git init/commit/reset, and git-URL ingest) --
this guards against that gap silently reappearing."""

from __future__ import annotations

from app.prereqs import CHECKS, check_all


def test_git_is_a_checked_prerequisite():
    names = [c["name"] for c in CHECKS]
    assert "Git" in names


def test_check_all_runs_git_check_and_reports_ok_on_this_dev_machine():
    results = {r.name: r for r in check_all()}
    assert "Git" in results
    assert results["Git"].command == "git --version"
    assert results["Git"].ok is True
