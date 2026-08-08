"""run_cache_refresh -- Trivy/Dependency-Check subprocess calls stubbed,
deterministic tests of status transitions and event emission (mirrors
tests/unit/test_pipeline.py's style)."""

from __future__ import annotations

from app.config import Settings
from app.models.db import init_db, session_factory
from app.models.job import Job, JobEvent
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.orchestration.cache_refresh import run_cache_refresh


def _settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


def _db(settings):
    init_db(settings)
    return session_factory(settings)


def _create_job(db, job_id: str) -> None:
    with db() as session:
        session.add(Job(id=job_id, source_type="cache_refresh", source_ref="nvd+trivy", status="queued"))
        session.commit()


async def test_successful_refresh_sets_success_and_emits_events(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    db = _db(settings)
    _create_job(db, "job-1")

    async def fake_trivy_refresh(settings_, log_path=None, on_line=None):
        return [SubprocessResult(returncode=0, output="ok", log_path=None)] * 2

    async def fake_dc_update(settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    monkeypatch.setattr("app.orchestration.cache_refresh.run_trivy_db_refresh", fake_trivy_refresh)
    monkeypatch.setattr("app.orchestration.cache_refresh.run_dependency_check_update_only", fake_dc_update)

    await run_cache_refresh("job-1", settings, db)

    with db() as session:
        job = session.get(Job, "job-1")
        assert job.status == "success"

        events = session.query(JobEvent).filter_by(job_id="job-1").order_by(JobEvent.seq).all()
        event_types = [e.event_type for e in events]
        assert event_types[0] == "status"
        assert events[0].data == {"status": "running"}
        assert event_types[-1] == "status"
        assert events[-1].data == {"status": "success"}
        assert "log" in event_types


async def test_trivy_failure_marks_job_failed_before_dependency_check_runs(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    db = _db(settings)
    _create_job(db, "job-2")

    async def failing_trivy_refresh(settings_, log_path=None, on_line=None):
        return [SubprocessResult(returncode=1, output="network error", log_path=None)]

    async def dc_update_must_not_run(*args, **kwargs):
        raise AssertionError("dependency-check update-only must not run if trivy already failed")

    monkeypatch.setattr("app.orchestration.cache_refresh.run_trivy_db_refresh", failing_trivy_refresh)
    monkeypatch.setattr("app.orchestration.cache_refresh.run_dependency_check_update_only", dc_update_must_not_run)

    await run_cache_refresh("job-2", settings, db)

    with db() as session:
        job = session.get(Job, "job-2")
        assert job.status == "failed"
        assert "trivy" in job.error_message.lower()


async def test_dependency_check_failure_marks_job_failed(monkeypatch, tmp_path):
    settings = _settings(tmp_path)
    db = _db(settings)
    _create_job(db, "job-3")

    async def fake_trivy_refresh(settings_, log_path=None, on_line=None):
        return [SubprocessResult(returncode=0, output="ok", log_path=None)] * 2

    async def failing_dc_update(settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=1, output="mvn error", log_path=None)

    monkeypatch.setattr("app.orchestration.cache_refresh.run_trivy_db_refresh", fake_trivy_refresh)
    monkeypatch.setattr("app.orchestration.cache_refresh.run_dependency_check_update_only", failing_dc_update)

    await run_cache_refresh("job-3", settings, db)

    with db() as session:
        job = session.get(Job, "job-3")
        assert job.status == "failed"
        assert "dependency-check" in job.error_message.lower()
