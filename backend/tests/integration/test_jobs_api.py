"""API-level tests for the async job flow: POST /jobs returns 202
immediately; the actual ingest/stage1/stage2 pipeline runs in the
background (Phase 6), observed here by polling GET /jobs/{id} -- the same
way a real client without SSE support would."""

from __future__ import annotations

import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.models.db import init_db
from app.orchestration.concurrency import reset_job_manager

_POM = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>demo</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>
</project>"""


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _wait_for_terminal_status(client: TestClient, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.get(f"/jobs/{job_id}")
        body = resp.json()
        if body["status"] in ("success", "needs_handoff", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout}s")


@pytest.fixture()
def app_client(tmp_path):
    reset_job_manager()  # each test gets a fresh semaphore, not one shared across tests
    test_settings = Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        # stage1/stage2 are never requested=True in these tests, so mvn/AI
        # are never actually invoked -- these tests are about the job/API
        # wiring, not the pipeline internals (already covered elsewhere).
    )
    init_db(test_settings)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = TestClient(app)
    yield client
    reset_job_manager()


def test_create_job_returns_202_immediately(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})

    resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )

    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "queued"
    assert body["job_id"]


def test_job_reaches_success_for_a_valid_project_with_no_stages_requested(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})

    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]

    final = _wait_for_terminal_status(app_client, job_id)

    assert final["status"] == "success"
    assert final["source_type"] == "zip"
    assert final["run_stage1"] is False
    assert final["run_stage2"] is False


def test_job_fails_for_gradle_project(app_client):
    zip_content = _zip_bytes({"build.gradle": b"plugins { id 'java' }"})

    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]

    final = _wait_for_terminal_status(app_client, job_id)

    assert final["status"] == "failed"
    assert "Gradle" in final["error_message"]


def test_job_fails_for_upload_over_file_count_limit(tmp_path):
    reset_job_manager()
    test_settings = Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        upload_max_files=1,
    )
    init_db(test_settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = TestClient(app)

    zip_content = _zip_bytes({"pom.xml": _POM, "extra.txt": b"one file too many"})
    create_resp = client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]

    final = _wait_for_terminal_status(client, job_id)

    assert final["status"] == "failed"
    assert "UPLOAD_MAX_FILES" in final["error_message"]
    reset_job_manager()


def test_rejects_when_neither_git_url_nor_zip_given(app_client):
    resp = app_client.post("/jobs", data={})
    assert resp.status_code == 400


def test_rejects_when_both_git_url_and_zip_given(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})
    resp = app_client.post(
        "/jobs",
        data={"git_url": "https://example.invalid/repo.git"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    assert resp.status_code == 400


def test_get_unknown_job_returns_404(app_client):
    resp = app_client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_sse_events_stream_includes_status_transitions(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]

    _wait_for_terminal_status(app_client, job_id)  # let the pipeline finish before connecting

    seen_events: list[str] = []
    with app_client.stream("GET", f"/jobs/{job_id}/events") as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("event:"):
                seen_events.append(line.removeprefix("event:").strip())
            if len(seen_events) >= 2 and seen_events[-1] == "status":
                break  # a finished job's stream ends on its own, but don't hang the test if it doesn't

    assert "status" in seen_events
    assert "log" in seen_events
