"""API-level tests for the async job flow: POST /jobs returns 202
immediately; the actual ingest/stage1/stage2 pipeline runs in the
background (Phase 6), observed here by polling GET /jobs/{id} -- the same
way a real client without SSE support would."""

from __future__ import annotations

import asyncio
import io
import threading
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app
from app.models.db import init_db
from app.orchestration.concurrency import get_job_manager, reset_job_manager

_POM = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>demo</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>
</project>"""

_POM_WITH_INTERNAL_PARENT = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>com.example.internal</groupId>
        <artifactId>internal-parent</artifactId>
        <version>1.0.0</version>
    </parent>
    <artifactId>demo</artifactId>
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
        if body["status"] in ("success", "stage1_needs_handoff", "stage2_needs_handoff", "failed", "cancelled"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach a terminal status within {timeout}s")


def _wait_for_status(client: TestClient, job_id: str, target_status: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get(f"/jobs/{job_id}").json()
        if body["status"] == target_status:
            return body
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached status={target_status!r}, last seen: {body.get('status')!r}")


async def _fast_mvn_effective_pom(work_dir, output_path, settings_, log_path=None):
    # Stage 0 always runs this before anything else -- tests that need to
    # reach "running"/blocked-in-Stage-0 quickly (rather than waiting on a
    # real `mvn help:effective-pom`) monkeypatch this in. Writes a real
    # <version> so read_declared_version/compute_stage0_output_version have
    # something concrete to work with.
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        '<project xmlns="http://maven.apache.org/POM/4.0.0"><version>1.0.0</version></project>',
        encoding="utf-8",
    )
    return output_path


async def _no_vulns_scan(work_dir, output_dir, settings_):
    return []


async def _fast_apply_output_version(work_dir, new_version, settings_, output_dir=None):
    # The real apply_output_version shells out to `mvn versions:set` -- swap
    # in a plain checkpoint commit so tests about job/API wiring (not mvn
    # itself) don't pay for a real Maven invocation.
    from app.checkpoint.git_repo import commit_checkpoint

    return commit_checkpoint(work_dir, settings_, f"checkpoint: set artifact version to {new_version}")


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


def test_proceed_unknown_job_returns_404(app_client):
    resp = app_client.post("/jobs/does-not-exist/proceed")
    assert resp.status_code == 404


def test_proceed_job_not_awaiting_approval_returns_409(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_terminal_status(app_client, job_id)  # ends "success", never "awaiting_approval"

    resp = app_client.post(f"/jobs/{job_id}/proceed")
    assert resp.status_code == 409


def test_resume_stage1_unknown_job_returns_404(app_client):
    resp = app_client.post("/jobs/does-not-exist/resume-stage1")
    assert resp.status_code == 404


def test_resume_stage1_rejected_when_not_stage1_needs_handoff(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_terminal_status(app_client, job_id)  # ends "success", never stage1_needs_handoff

    resp = app_client.post(f"/jobs/{job_id}/resume-stage1")
    assert resp.status_code == 409


def _put_job_at_status(app_client, job_id: str, status: str, run_stage1: bool = True, run_stage2: bool = False) -> None:
    """Directly writes a Job row into the app's DB, bypassing the pipeline
    entirely -- used to reach statuses (stage1_needs_handoff,
    stage2_needs_handoff) that would otherwise require a full mocked
    ingest+Stage0+Stage1 run just to set up a gate test."""
    from app.models.db import session_factory as make_session_factory
    from app.models.job import Job

    factory = make_session_factory(app_client.app.dependency_overrides[get_settings]())
    with factory() as session:
        session.add(
            Job(
                id=job_id,
                source_type="zip",
                source_ref="x.zip",
                run_stage1=run_stage1,
                run_stage2=run_stage2,
                status=status,
                report_markdown="# stage1 report\n막혔습니다.",
            )
        )
        session.commit()


def test_resume_stage1_rejected_when_stage2_needs_handoff(app_client):
    _put_job_at_status(app_client, "resume-test-1", "stage2_needs_handoff", run_stage1=True, run_stage2=True)
    resp = app_client.post("/jobs/resume-test-1/resume-stage1")
    assert resp.status_code == 409


def test_resume_stage1_allowed_when_run_stage2_true(app_client, monkeypatch, tmp_path):
    """A job with run_stage1=true, run_stage2=true that reached
    stage1_needs_handoff means Stage 2 already ran to completion (spec:
    docs/superpowers/specs/2026-08-11-job-status-stage-split-design.md §6)
    -- the endpoint must not reject it just because run_stage2 was true."""
    from app.checkpoint.git_repo import git_init_and_baseline_commit

    job_id = "resume-test-2"
    settings_ = app_client.app.dependency_overrides[get_settings]()
    work_dir = settings_.jobs_dir / job_id / "work"
    work_dir.mkdir(parents=True)
    (work_dir / "pom.xml").write_text("<project/>")
    git_init_and_baseline_commit(work_dir, settings_)

    _put_job_at_status(app_client, job_id, "stage1_needs_handoff", run_stage1=True, run_stage2=True)

    async def passing_verify(work_dir_, settings__, on_log=None):
        return True, "BUILD SUCCESS"

    async def fake_stage1_no_gap(job_id_, work_dir_, detected, baseline, settings__, parent_target_version=None, on_log=None):
        from app.orchestration.multi_step import MigrationRunResult
        from app.orchestration.planning import MigrationPlan

        return MigrationRunResult(
            plan=MigrationPlan(steps=[]), outcomes=[], status="no_gap", final_diff="", report="# 재개 완료", handoff_guide=None
        )

    monkeypatch.setattr("app.orchestration.pipeline.verify_after_manual_fix", passing_verify)
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", fake_stage1_no_gap)

    resp = app_client.post(f"/jobs/{job_id}/resume-stage1")
    assert resp.status_code == 200, resp.text

    final = _wait_for_terminal_status(app_client, job_id)
    assert final["status"] == "success"


def test_cancel_unknown_job_returns_404(app_client):
    resp = app_client.post("/jobs/does-not-exist/cancel")
    assert resp.status_code == 404


def test_cancel_already_terminal_job_returns_409(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_terminal_status(app_client, job_id)  # ends "success"

    resp = app_client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 409


def test_cancel_running_job_marks_it_cancelled(tmp_path, monkeypatch):
    # Needs `with TestClient(...) as client:` (not the plain app_client
    # fixture) -- only the context-managed form keeps one persistent event
    # loop/portal alive *across* separate .post()/.get() calls. Without it,
    # each call runs its own short-lived loop and cancels any background
    # Task still in flight when that one call ends, which would falsely
    # "cancel" the job before this test ever calls POST .../cancel itself
    # (confirmed empirically -- see spec's implementation notes).
    reset_job_manager()
    test_settings = Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    init_db(test_settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    # threading.Event, not asyncio.Event -- this fake coroutine runs on the
    # TestClient's own event loop thread, separate from this test's thread.
    scan_started = threading.Event()

    async def slow_baseline_scan(work_dir, output_dir, settings_):
        scan_started.set()
        await asyncio.sleep(5)  # long enough to reliably cancel before it returns
        raise AssertionError("should have been cancelled before this returned")

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", slow_baseline_scan)

    with TestClient(app) as client:
        zip_content = _zip_bytes({"pom.xml": _POM})
        create_resp = client.post(
            "/jobs",
            data={"run_stage1": "true", "run_stage2": "false"},
            files={"zip_file": ("project.zip", zip_content, "application/zip")},
        )
        job_id = create_resp.json()["job_id"]

        assert scan_started.wait(timeout=5), "baseline scan never started -- job never reached running"

        resp = client.post(f"/jobs/{job_id}/cancel")
        assert resp.status_code == 200

        final = _wait_for_terminal_status(client, job_id)
        assert final["status"] == "cancelled"

    reset_job_manager()


def test_cancel_queued_job_marks_it_cancelled(tmp_path, monkeypatch):
    # Needs its own client (max_concurrent_repos=1) to reliably force a
    # second job to sit in "queued" behind the first one, and needs `with
    # TestClient(...) as client:` for the same reason as the "running" test
    # above.
    reset_job_manager()
    test_settings = Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        max_concurrent_repos=1,
    )
    init_db(test_settings)
    # create_app() itself calls get_job_manager(settings.max_concurrent_repos)
    # using the *real* get_settings(), not the dependency_overrides below
    # (those only apply to FastAPI's DI-resolved request handlers) -- so
    # max_concurrent_repos=1 would silently never take effect unless the
    # manager singleton is pre-seeded with it before create_app() runs.
    get_job_manager(max_concurrent=1)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    first_started = threading.Event()
    release_first = threading.Event()

    async def blocking_scan(work_dir, output_dir, settings_):
        first_started.set()
        while not release_first.is_set():
            await asyncio.sleep(0.02)
        return []

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", blocking_scan)

    with TestClient(app) as client:
        zip_content = _zip_bytes({"pom.xml": _POM})
        first_id = client.post(
            "/jobs",
            data={"run_stage1": "true", "run_stage2": "false"},
            files={"zip_file": ("project.zip", zip_content, "application/zip")},
        ).json()["job_id"]
        assert first_started.wait(timeout=5), "first job never started running -- never occupied the only slot"

        second_id = client.post(
            "/jobs",
            data={"run_stage1": "false", "run_stage2": "false"},
            files={"zip_file": ("project.zip", zip_content, "application/zip")},
        ).json()["job_id"]

        assert client.get(f"/jobs/{second_id}").json()["status"] == "queued"

        resp = client.post(f"/jobs/{second_id}/cancel")
        assert resp.status_code == 200

        final = _wait_for_terminal_status(client, second_id)
        assert final["status"] == "cancelled"

        release_first.set()
        # Stage 0 pauses at awaiting_version_approval (not terminal) once the
        # scan finishes -- that's as far as this job goes without a confirm
        # step; just let it get there before the app tears down.
        _wait_for_status(client, first_id, "awaiting_version_approval")

    reset_job_manager()


def test_cancel_awaiting_approval_job_finalizes_immediately(app_client, monkeypatch):
    """awaiting_approval has no live Task to cancel (run_pipeline already
    returned after pausing) -- the endpoint's direct-finalize path handles
    it, so the job is already "cancelled" by the time the response comes
    back, no polling needed."""
    from app.orchestration.multi_step import MigrationRunResult
    from app.orchestration.planning import MigrationPlan

    async def no_baseline_vulns(work_dir, output_dir, settings_):
        return []

    async def _async_noop_writes_file(work_dir, output_path, settings_, log_path=None):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("<projects><project/></projects>", encoding="utf-8")
        return output_path

    async def stage1_needs_handoff(job_id, work_dir, detected, baseline, settings_, parent_target_version=None, on_log=None):
        return MigrationRunResult(
            plan=MigrationPlan(steps=[]),
            outcomes=[],
            status="needs_handoff",
            final_diff="",
            report="# stage1 report\n막혔습니다.",
            handoff_guide="# AI 인수인계 가이드",
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", no_baseline_vulns)
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _async_noop_writes_file)
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fast_apply_output_version)
    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", stage1_needs_handoff)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "true"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]

    # Stage 0 pauses first regardless -- confirm a version before Stage 1
    # can even start (the _async_noop_writes_file fixture writes no
    # <version>, so there's no "same as current" value to avoid; anything
    # works).
    _wait_for_status(app_client, job_id, "awaiting_version_approval")
    confirm_resp = app_client.post(f"/jobs/{job_id}/confirm-version", json={"output_version": "9.9.9"})
    assert confirm_resp.status_code == 200, confirm_resp.text

    deadline = time.monotonic() + 10
    status_ = None
    while time.monotonic() < deadline:
        status_ = app_client.get(f"/jobs/{job_id}").json()["status"]
        if status_ == "awaiting_approval":
            break
        time.sleep(0.05)
    assert status_ == "awaiting_approval"

    resp = app_client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"  # confirmed synchronously -- no live Task, no polling needed

    assert app_client.get(f"/jobs/{job_id}").json()["status"] == "cancelled"


def test_job_reaches_awaiting_version_approval_with_current_and_suggested_version(tmp_path, monkeypatch):
    from app.models.db import session_factory as make_session_factory
    from app.models.job import JobEvent

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

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", _no_vulns_scan)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(client, job_id, "awaiting_version_approval")

    factory = make_session_factory(test_settings)
    with factory() as session:
        row = (
            session.query(JobEvent)
            .filter(JobEvent.job_id == job_id, JobEvent.event_type == "status")
            .order_by(JobEvent.seq.desc())
            .first()
        )
    assert row.data["status"] == "awaiting_version_approval"
    # _fast_mvn_effective_pom writes <version>1.0.0</version>; run_stage1=True -> major bump
    assert row.data["current_version"] == "1.0.0"
    assert row.data["suggested_version"] == "2.0.0"

    reset_job_manager()


def test_confirm_version_unknown_job_returns_404(app_client):
    resp = app_client.post("/jobs/does-not-exist/confirm-version", json={"output_version": "2.0.0"})
    assert resp.status_code == 404


def test_confirm_version_when_not_awaiting_returns_409(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_terminal_status(app_client, job_id)  # Stage 0 skipped entirely, ends "success" directly

    resp = app_client.post(f"/jobs/{job_id}/confirm-version", json={"output_version": "2.0.0"})
    assert resp.status_code == 409


def test_confirm_version_with_same_value_returns_409(app_client, monkeypatch):
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", _no_vulns_scan)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")

    resp = app_client.post(f"/jobs/{job_id}/confirm-version", json={"output_version": "1.0.0"})  # same as current
    assert resp.status_code == 409
    assert app_client.get(f"/jobs/{job_id}").json()["status"] == "awaiting_version_approval"


def test_confirm_version_with_different_value_proceeds_and_persists_output_version(app_client, monkeypatch):
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", _no_vulns_scan)
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fast_apply_output_version)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "true"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")

    resp = app_client.post(f"/jobs/{job_id}/confirm-version", json={"output_version": "2.0.0"})
    assert resp.status_code == 200

    final = _wait_for_terminal_status(app_client, job_id)
    assert final["output_version"] == "2.0.0"


def test_job_reaches_awaiting_version_approval_with_detected_parent(tmp_path, monkeypatch):
    """Regression coverage for job #35/#38: a project whose root pom.xml has
    a <parent> outside the known-public allowlist (e.g. ace-parent) should
    surface that in the awaiting_version_approval status event, so the
    frontend can offer the optional "사내 parent POM 목표 버전" field. A
    project with no <parent> at all must not (existing `_POM` fixture)."""
    from app.models.db import session_factory as make_session_factory
    from app.models.job import JobEvent

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

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", _no_vulns_scan)

    factory = make_session_factory(test_settings)

    def _latest_status_data(job_id: str) -> dict:
        with factory() as session:
            row = (
                session.query(JobEvent)
                .filter(JobEvent.job_id == job_id, JobEvent.event_type == "status")
                .order_by(JobEvent.seq.desc())
                .first()
            )
            return row.data

    zip_with_parent = _zip_bytes({"pom.xml": _POM_WITH_INTERNAL_PARENT})
    create_resp = client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_with_parent, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(client, job_id, "awaiting_version_approval")
    assert _latest_status_data(job_id)["detected_parent"] == {
        "group_id": "com.example.internal",
        "artifact_id": "internal-parent",
        "version": "1.0.0",
    }

    zip_without_parent = _zip_bytes({"pom.xml": _POM})
    create_resp2 = client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_without_parent, "application/zip")},
    )
    job_id2 = create_resp2.json()["job_id"]
    _wait_for_status(client, job_id2, "awaiting_version_approval")
    assert _latest_status_data(job_id2)["detected_parent"] is None

    reset_job_manager()


def test_confirm_version_with_same_parent_target_version_returns_409(app_client, monkeypatch):
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", _no_vulns_scan)

    zip_content = _zip_bytes({"pom.xml": _POM_WITH_INTERNAL_PARENT})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")

    resp = app_client.post(
        f"/jobs/{job_id}/confirm-version",
        json={"output_version": "2.0.0", "parent_target_version": "1.0.0"},  # same as detected parent version
    )
    assert resp.status_code == 409
    assert app_client.get(f"/jobs/{job_id}").json()["status"] == "awaiting_version_approval"


def test_confirm_version_with_parent_target_version_reaches_stage1(app_client, monkeypatch):
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", _no_vulns_scan)
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fast_apply_output_version)

    captured = {}

    async def fake_stage1_migration(
        job_id, work_dir, detected, baseline, settings_, parent_target_version=None, on_log=None
    ):
        captured["parent_target_version"] = parent_target_version
        from app.orchestration.multi_step import MigrationRunResult
        from app.orchestration.planning import MigrationPlan

        return MigrationRunResult(
            plan=MigrationPlan(steps=[]), outcomes=[], status="no_gap", final_diff="", report="# stage1 report", handoff_guide=None
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", fake_stage1_migration)

    zip_content = _zip_bytes({"pom.xml": _POM_WITH_INTERNAL_PARENT})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")

    resp = app_client.post(
        f"/jobs/{job_id}/confirm-version",
        json={"output_version": "2.0.0", "parent_target_version": "1.1.0"},
    )
    assert resp.status_code == 200

    _wait_for_terminal_status(app_client, job_id)
    assert captured["parent_target_version"] == "1.1.0"


def test_cancel_awaiting_version_approval_finalizes_immediately(app_client, monkeypatch):
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", _no_vulns_scan)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")

    resp = app_client.post(f"/jobs/{job_id}/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"
    assert app_client.get(f"/jobs/{job_id}").json()["status"] == "cancelled"


def test_stage2_only_job_reuses_baseline_scan_not_rescan(app_client, monkeypatch):
    call_count = {"n": 0}

    async def counting_scan(work_dir, output_dir, settings_):
        call_count["n"] += 1
        return []

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", counting_scan)
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fast_apply_output_version)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "true"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")
    assert call_count["n"] == 1  # Stage 0 baseline only so far

    resp = app_client.post(f"/jobs/{job_id}/confirm-version", json={"output_version": "2.0.0"})
    assert resp.status_code == 200
    _wait_for_terminal_status(app_client, job_id)

    # baseline (Stage 0) + final (post-Stage-2) -- no separate scan to pick
    # Stage 2's targets, reused from the baseline event instead.
    assert call_count["n"] == 2


def test_stage1_and_stage2_job_scans_exactly_three_times(app_client, monkeypatch):
    from app.orchestration.multi_step import MigrationRunResult
    from app.orchestration.planning import MigrationPlan

    call_count = {"n": 0}

    async def counting_scan(work_dir, output_dir, settings_):
        call_count["n"] += 1
        return []

    async def stage1_no_gap(job_id, work_dir, detected, baseline, settings_, parent_target_version=None, on_log=None):
        return MigrationRunResult(
            plan=MigrationPlan(steps=[]),
            outcomes=[],
            status="no_gap",
            final_diff="",
            report="# stage1 report\n변경 없음.",
            handoff_guide=None,
        )

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", counting_scan)
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fast_apply_output_version)
    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", stage1_no_gap)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "true"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")
    assert call_count["n"] == 1  # Stage 0 baseline

    resp = app_client.post(f"/jobs/{job_id}/confirm-version", json={"output_version": "2.0.0"})
    assert resp.status_code == 200
    _wait_for_terminal_status(app_client, job_id)

    # baseline (Stage 0) + post-Stage-1 (Stage 2's targets) + post-Stage-2 (final) = 3
    assert call_count["n"] == 3


def test_stage1_only_job_emits_post_stage1_vulnerabilities_event(app_client, monkeypatch):
    """Regression test: a Stage-1-only job (run_stage2=False) used to never
    scan again after Stage 1 finished, so the "마이그레이션 후" table had
    nothing to show -- only Stage 0's baseline scan ever ran. Stage 1's own
    result should be visible regardless of whether Stage 2 was requested."""
    from app.models.db import session_factory as make_session_factory
    from app.models.job import JobEvent

    call_count = {"n": 0}

    async def counting_scan(work_dir, output_dir, settings_):
        call_count["n"] += 1
        return []

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", counting_scan)
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fast_apply_output_version)

    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "true", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    _wait_for_status(app_client, job_id, "awaiting_version_approval")

    resp = app_client.post(f"/jobs/{job_id}/confirm-version", json={"output_version": "2.0.0"})
    assert resp.status_code == 200
    _wait_for_terminal_status(app_client, job_id)

    # baseline (Stage 0) + post-Stage-1 -- no Stage 2, but the post-Stage-1
    # scan must still run.
    assert call_count["n"] == 2

    factory = make_session_factory(app_client.app.dependency_overrides[get_settings]())
    with factory() as session:
        row = (
            session.query(JobEvent)
            .filter(JobEvent.job_id == job_id, JobEvent.event_type == "vulnerabilities_post_stage1")
            .first()
        )
    assert row is not None
    assert row.data == {"vulnerabilities": []}


def test_delete_unknown_job_returns_404(app_client):
    resp = app_client.delete("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_delete_terminal_job_removes_row_and_directory(app_client, tmp_path):
    zip_content = _zip_bytes({"pom.xml": _POM})
    create_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    job_id = create_resp.json()["job_id"]
    final = _wait_for_terminal_status(app_client, job_id)
    assert final["status"] == "success"

    job_dir = tmp_path / "jobs" / job_id
    assert job_dir.exists()

    resp = app_client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 204

    assert app_client.get(f"/jobs/{job_id}").status_code == 404
    assert not job_dir.exists()


def test_delete_running_job_returns_409(tmp_path, monkeypatch):
    # Same "block inside run_combined_scan" technique as
    # test_cancel_running_job_marks_it_cancelled -- reliably catches the job
    # in "running" without racing a real scan/mvn call.
    reset_job_manager()
    test_settings = Settings(
        _env_file=None,
        jobs_data_dir=str(tmp_path / "jobs"),
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
    )
    init_db(test_settings)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings

    scan_started = threading.Event()
    release_scan = threading.Event()

    async def blocking_scan(work_dir, output_dir, settings_):
        scan_started.set()
        while not release_scan.is_set():
            await asyncio.sleep(0.02)
        return []

    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _fast_mvn_effective_pom)
    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", blocking_scan)

    with TestClient(app) as client:
        zip_content = _zip_bytes({"pom.xml": _POM})
        create_resp = client.post(
            "/jobs",
            data={"run_stage1": "true", "run_stage2": "false"},
            files={"zip_file": ("project.zip", zip_content, "application/zip")},
        )
        job_id = create_resp.json()["job_id"]
        assert scan_started.wait(timeout=5), "job never reached running"

        resp = client.delete(f"/jobs/{job_id}")
        assert resp.status_code == 409

        release_scan.set()
        # Stage 0 pauses at awaiting_version_approval (not terminal) once the
        # scan finishes -- just let it get there before the app tears down.
        _wait_for_status(client, job_id, "awaiting_version_approval")

    reset_job_manager()


def test_delete_then_create_does_not_reuse_a_live_id(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})
    ids = []
    for _ in range(3):
        resp = app_client.post(
            "/jobs",
            data={"run_stage1": "false", "run_stage2": "false"},
            files={"zip_file": ("project.zip", zip_content, "application/zip")},
        )
        job_id = resp.json()["job_id"]
        _wait_for_terminal_status(app_client, job_id)
        ids.append(job_id)

    del_resp = app_client.delete(f"/jobs/{ids[1]}")
    assert del_resp.status_code == 204

    new_resp = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )
    new_id = new_resp.json()["job_id"]
    assert new_id not in ids
    assert int(new_id) > max(int(i) for i in ids)


def test_list_jobs_returns_empty_list_when_no_jobs(app_client):
    resp = app_client.get("/jobs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_jobs_returns_newest_first(app_client):
    zip_content = _zip_bytes({"pom.xml": _POM})

    first = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    ).json()
    second = app_client.post(
        "/jobs",
        data={"run_stage1": "false", "run_stage2": "false"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    ).json()

    resp = app_client.get("/jobs")
    assert resp.status_code == 200
    body = resp.json()

    assert [j["job_id"] for j in body] == [second["job_id"], first["job_id"]]
    assert body[0]["source_type"] == "zip"
    assert body[0]["run_stage1"] is False
    assert body[0]["run_stage2"] is False


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
