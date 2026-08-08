"""API-level tests for GET /cache/status and POST /cache/refresh. The actual
trivy/dependency-check subprocess calls are monkeypatched (same rationale as
test_jobs_api.py using run_stage1=false/run_stage2=false to avoid invoking
real mvn) -- this is about the API/Job wiring, not the tool invocations
themselves (covered by tests/unit/test_cache_refresh.py)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.models.db import init_db
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.orchestration.concurrency import reset_job_manager


def _wait_for_terminal_status(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get("/cache/status")
        body = resp.json()
        if not body["refreshing"]:
            return body
        time.sleep(0.05)
    raise AssertionError(f"cache refresh (job {job_id}) did not finish within {timeout}s")


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    reset_job_manager()
    test_settings = Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        dependency_check_data_dir=str(tmp_path / "nvd-cache"),
        trivy_cache_dir=str(tmp_path / "trivy-cache"),
    )
    init_db(test_settings)

    async def fake_trivy_refresh(settings_, log_path=None, on_line=None):
        return [SubprocessResult(returncode=0, output="ok", log_path=None)] * 2

    async def fake_dc_update(settings_, log_path=None, on_line=None):
        return SubprocessResult(returncode=0, output="ok", log_path=None)

    monkeypatch.setattr("app.orchestration.cache_refresh.run_trivy_db_refresh", fake_trivy_refresh)
    monkeypatch.setattr("app.orchestration.cache_refresh.run_dependency_check_update_only", fake_dc_update)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = TestClient(app)
    yield client
    reset_job_manager()


def test_cache_status_with_no_history_reports_not_refreshing(app_client):
    resp = app_client.get("/cache/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["refreshing"] is False
    assert body["current_job_id"] is None
    assert body["nvd_last_updated_at"] is None
    assert body["trivy_last_updated_at"] is None


def test_refresh_runs_to_success_and_is_excluded_from_job_history(app_client):
    resp = app_client.post("/cache/refresh")
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    final = _wait_for_terminal_status(app_client, job_id)
    assert final["last_run_status"] == "success"
    assert final["current_job_id"] == job_id

    # cache_refresh rows are a utility action, not a migration job -- must
    # not pollute the job-history list.
    jobs_resp = app_client.get("/jobs")
    assert all(j["job_id"] != job_id for j in jobs_resp.json())

    # but it's still reachable directly and via SSE replay, same as a real job
    direct = app_client.get(f"/jobs/{job_id}")
    assert direct.status_code == 200
    assert direct.json()["status"] == "success"


def test_refresh_while_already_running_returns_409(app_client, monkeypatch):
    import asyncio

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_trivy_refresh(settings_, log_path=None, on_line=None):
        started.set()
        await release.wait()
        return [SubprocessResult(returncode=0, output="ok", log_path=None)] * 2

    monkeypatch.setattr("app.orchestration.cache_refresh.run_trivy_db_refresh", slow_trivy_refresh)

    first = app_client.post("/cache/refresh")
    assert first.status_code == 202

    deadline = time.monotonic() + 5
    while not started.is_set() and time.monotonic() < deadline:
        time.sleep(0.02)

    second = app_client.post("/cache/refresh")
    assert second.status_code == 409

    release.set()
