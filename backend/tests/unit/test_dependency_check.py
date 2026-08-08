"""dependency_check.py's constructed subprocess args -- run_subprocess itself
is monkeypatched out, this only checks the command line built."""

from __future__ import annotations

from app.config import Settings
from app.scan import dependency_check


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        dependency_check_data_dir=str(tmp_path / "nvd-cache"),
    )


async def test_check_disables_auto_update(monkeypatch, tmp_path):
    captured = {}

    async def fake_run_subprocess(args, cwd, settings_, **kwargs):
        captured["args"] = args
        from app.mvnrewrite.subprocess_runner import SubprocessResult

        return SubprocessResult(returncode=0, output="", log_path=None)

    monkeypatch.setattr("app.scan.dependency_check.run_subprocess", fake_run_subprocess)

    settings = _settings(tmp_path)
    await dependency_check.run_dependency_check(tmp_path / "work", settings)

    assert "-DautoUpdate=false" in captured["args"]


async def test_update_only_uses_generous_timeout_and_correct_goal(monkeypatch, tmp_path):
    captured = {}

    async def fake_run_subprocess(args, cwd, settings_, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        captured["cwd"] = cwd
        from app.mvnrewrite.subprocess_runner import SubprocessResult

        return SubprocessResult(returncode=0, output="", log_path=None)

    monkeypatch.setattr("app.scan.dependency_check.run_subprocess", fake_run_subprocess)

    settings = _settings(tmp_path)
    await dependency_check.run_dependency_check_update_only(settings)

    assert any("update-only" in a for a in captured["args"])
    assert not any(a == "install" for a in captured["args"])  # unlike the check goal, no reactor build needed
    assert captured["kwargs"]["timeout_seconds"] == dependency_check.NVD_UPDATE_TIMEOUT_SECONDS
    assert captured["cwd"] == settings.jobs_dir
