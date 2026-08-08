"""trivy.py's constructed subprocess args -- run_subprocess itself is
monkeypatched out, this only checks the command line built."""

from __future__ import annotations

from app.config import Settings
from app.scan import trivy


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        trivy_cache_dir=str(tmp_path / "trivy-cache"),
    )


async def test_scan_skips_db_update(monkeypatch, tmp_path):
    captured = {}

    async def fake_run_subprocess(args, cwd, settings_, **kwargs):
        captured["args"] = args
        from app.mvnrewrite.subprocess_runner import SubprocessResult

        return SubprocessResult(returncode=0, output="", log_path=None)

    monkeypatch.setattr("app.scan.trivy.run_subprocess", fake_run_subprocess)

    settings = _settings(tmp_path)
    await trivy.run_trivy_scan(tmp_path / "work", tmp_path / "out.json", settings)

    assert "--skip-db-update" in captured["args"]
    assert "--skip-java-db-update" in captured["args"]


async def test_db_refresh_makes_two_separate_calls(monkeypatch, tmp_path):
    calls = []

    async def fake_run_subprocess(args, cwd, settings_, **kwargs):
        calls.append(args)
        from app.mvnrewrite.subprocess_runner import SubprocessResult

        return SubprocessResult(returncode=0, output="", log_path=None)

    monkeypatch.setattr("app.scan.trivy.run_subprocess", fake_run_subprocess)

    settings = _settings(tmp_path)
    results = await trivy.run_trivy_db_refresh(settings)

    assert len(results) == 2
    assert len(calls) == 2
    # confirmed empirically these can't be combined in one invocation
    assert any("--download-db-only" in c and "--download-java-db-only" not in c for c in calls)
    assert any("--download-java-db-only" in c and "--download-db-only" not in c for c in calls)
