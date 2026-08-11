"""API-level tests for GET /jobs/{id}/artifacts* -- diff/report/handoff
downloads served from a finished job's output/ tree (Phase 7, added once
the frontend needed something to fetch after a job completes)."""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.checkpoint.git_repo import commit_checkpoint
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
        if body["status"] in ("success", "stage1_needs_handoff", "stage2_needs_handoff", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout}s")


@pytest.fixture()
def app_client(tmp_path):
    reset_job_manager()
    test_settings = Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    init_db(test_settings)

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = TestClient(app)
    yield client
    reset_job_manager()


def _create_finished_job(client: TestClient) -> str:
    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    final = _wait_for_terminal_status(client, job_id)
    assert final["status"] == "success"
    return job_id


def test_list_artifacts_for_finished_job(app_client):
    job_id = _create_finished_job(app_client)

    resp = app_client.get(f"/jobs/{job_id}/artifacts")

    assert resp.status_code == 200
    body = resp.json()
    assert body["diff"] is True
    assert body["report"] is True
    assert body["handoff"] == []


def test_get_diff_returns_patch_text(app_client):
    job_id = _create_finished_job(app_client)

    resp = app_client.get(f"/jobs/{job_id}/artifacts/diff")

    assert resp.status_code == 200
    assert "text/x-diff" in resp.headers["content-type"]


def test_get_report_returns_markdown_text(app_client):
    job_id = _create_finished_job(app_client)

    resp = app_client.get(f"/jobs/{job_id}/artifacts/report")

    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]


def test_artifacts_for_unknown_job_returns_404(app_client):
    resp = app_client.get("/jobs/does-not-exist/artifacts")
    assert resp.status_code == 404


def test_handoff_guide_path_traversal_is_rejected(app_client):
    job_id = _create_finished_job(app_client)

    resp = app_client.get(f"/jobs/{job_id}/artifacts/handoff/..%2F..%2F..%2Fetc%2Fpasswd")

    assert resp.status_code == 404


def test_unknown_handoff_guide_returns_404(app_client):
    job_id = _create_finished_job(app_client)

    resp = app_client.get(f"/jobs/{job_id}/artifacts/handoff/nonexistent-guide.md")

    assert resp.status_code == 404


def _simulate_migration_change(work_dir: Path) -> None:
    """Commits a change directly into work_dir's git history, standing in
    for what a real stage1/stage2 step would do -- avoids invoking real
    mvn/AI just to produce a "modified" file for these endpoint tests (see
    test_artifact_version.py for the real-mvn version, marked slow). Also
    adds a target/ file, to double as noise-dir-filtering fixture data."""
    git_settings = Settings(_env_file=None)
    pom_path = work_dir / "pom.xml"
    pom_path.write_text(pom_path.read_text(encoding="utf-8").replace("1.0.0", "2.0.0"), encoding="utf-8")
    target_dir = work_dir / "target"
    target_dir.mkdir(exist_ok=True)
    (target_dir / "build-output.txt").write_text("noise", encoding="utf-8")
    commit_checkpoint(work_dir, git_settings, "checkpoint: simulate migration change")


def test_get_file_tree_marks_modified_and_excludes_noise_dirs(app_client, tmp_path):
    job_id = _create_finished_job(app_client)
    _simulate_migration_change(tmp_path / "jobs" / job_id / "work")

    resp = app_client.get(f"/jobs/{job_id}/artifacts/tree")

    assert resp.status_code == 200
    entries = {e["path"]: e["status"] for e in resp.json()}
    assert entries["pom.xml"] == "modified"
    assert entries.get(".gitignore") == "unchanged"
    assert not any(path.split("/")[0] == "target" for path in entries)


def test_get_file_content_returns_before_and_after(app_client, tmp_path):
    job_id = _create_finished_job(app_client)
    _simulate_migration_change(tmp_path / "jobs" / job_id / "work")

    resp = app_client.get(f"/jobs/{job_id}/artifacts/file", params={"path": "pom.xml"})

    assert resp.status_code == 200
    body = resp.json()
    assert "1.0.0" in body["before"]
    assert "2.0.0" in body["after"]
    assert body["binary"] is False


def test_get_file_content_for_unknown_path_returns_404(app_client):
    job_id = _create_finished_job(app_client)

    resp = app_client.get(f"/jobs/{job_id}/artifacts/file", params={"path": "does-not-exist.txt"})

    assert resp.status_code == 404


def test_get_file_tree_and_content_for_unknown_job_returns_404(app_client):
    assert app_client.get("/jobs/does-not-exist/artifacts/tree").status_code == 404
    assert app_client.get("/jobs/does-not-exist/artifacts/file", params={"path": "pom.xml"}).status_code == 404
