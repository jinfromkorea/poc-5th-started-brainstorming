"""Full pipeline orchestration (ingest -> stage1 -> stage2 -> diff/report/
handoff files), with every heavy dependency stubbed -- deterministic tests
of the pipeline's own control flow (status transitions, event emission,
output files written). Real end-to-end runs are already covered per-stage
by tests/integration (Phase 1-5); this phase's new surface is the wiring
itself, not the underlying mvn/AI/scan mechanics.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from app.checkpoint.git_repo import commit_checkpoint, git_init_and_baseline_commit, reset_to_checkpoint
from app.config import Settings
from app.ingest.maven_detect import MavenDetectionResult
from app.ingest.workspace import GitSourceSpec, IngestResult, WorkspacePaths, ZipSourceSpec
from app.models.db import init_db, session_factory
from app.models.job import Job, JobEvent
from app.mvnrewrite.pom_parser import DetectedVersions
from app.orchestration.multi_step import MigrationRunResult
from app.orchestration.pipeline import run_pipeline, run_pipeline_resume_after_version_confirm, run_pipeline_resume_stage2
from app.orchestration.planning import MigrationPlan
from app.orchestration.stage2_loop import Stage2RunResult
from app.scan.merge import Vulnerability


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")


@pytest.fixture()
def db(settings):
    init_db(settings)
    return session_factory(settings)


@pytest.fixture()
def job_paths(tmp_path):
    root = tmp_path / "job-root"
    paths = WorkspacePaths(root=root, source=root / "source", work=root / "work", output=root / "output")
    for d in (paths.source, paths.work, paths.output):
        d.mkdir(parents=True)
    (paths.work / "pom.xml").write_text("<project/>")
    return paths


def _create_job(db, job_id: str, run_stage1: bool = True, run_stage2: bool = False) -> None:
    with db() as session:
        session.add(
            Job(
                id=job_id,
                source_type="zip",
                source_ref="x.zip",
                status="queued",
                run_stage1=run_stage1,
                run_stage2=run_stage2,
            )
        )
        session.commit()


def _fake_ingest_result(job_id: str, paths: WorkspacePaths, baseline_commit: str = "a" * 40) -> IngestResult:
    detection = MavenDetectionResult(root_pom=paths.source / "pom.xml", packaging="pom", is_multi_module=False, modules=[])
    return IngestResult(job_id=job_id, paths=paths, detection=detection, baseline_commit=baseline_commit)


def _commit_subjects(work_dir) -> str:
    """Plain `git log` subjects -- deliberately not going through
    git_repo.py's own helpers, so this check doesn't share any code path
    with what it's verifying."""
    return subprocess.run(
        ["git", "log", "--format=%s"], cwd=work_dir, capture_output=True, text=True, check=True
    ).stdout


async def test_stage1_only_success_writes_report_and_diff(monkeypatch, settings, db, job_paths):
    # run_pipeline_resume_after_version_confirm derives work_dir/output_dir
    # as settings.jobs_dir / job_id / {work,output} independently of what
    # run_pipeline was given (it's resumed from a fresh POST, not a
    # continuation in the same call) -- job_id and settings.jobs_data_dir
    # must line up with job_paths for those derived paths to land on the
    # directories job_paths already created (same pattern as the existing
    # run_pipeline_resume_stage2 tests below).
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)
    _create_job(db, job_id)
    ingest_sha = git_init_and_baseline_commit(job_paths.work, settings)

    monkeypatch.setattr(
        "app.orchestration.pipeline.ingest",
        lambda job_id, spec, settings_: _fake_ingest_result(job_id, job_paths, baseline_commit=ingest_sha),
    )
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _async_noop_writes_file)
    monkeypatch.setattr(
        "app.orchestration.pipeline.extract_versions",
        lambda path: DetectedVersions(java_version="21", spring_boot_version="4.1.0", spring_cloud_version=None, spring_ai_version=None),
    )

    baseline_vuln = Vulnerability("CVE-2025-9999", "com.example:old-lib", "0.9.0", "0.9.1", 6.5, "MEDIUM", "trivy")

    async def fake_baseline_scan(work_dir, output_dir, settings_):
        return [baseline_vuln]

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", fake_baseline_scan)

    async def fake_stage1(job_id, work_dir, detected, baseline, settings_, on_log=None):
        return MigrationRunResult(
            plan=MigrationPlan(steps=[]),
            outcomes=[],
            status="no_gap",
            final_diff="",
            report="# stage1 report\n이미 목표 스택입니다.",
            handoff_guide=None,
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", fake_stage1)
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fake_apply_output_version)
    monkeypatch.setattr("app.orchestration.pipeline.diff_since", lambda work_dir, settings_, baseline: "diff --git a/pom.xml b/pom.xml\n")

    await run_pipeline(
        job_id=job_id,
        spec=ZipSourceSpec(zip_path=job_paths.root / "fake.zip"),
        run_stage1=True,
        run_stage2=False,
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "awaiting_version_approval"

        events = session.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.seq).all()
        event_types = [e.event_type for e in events]
        assert event_types[0] == "status"
        assert events[0].data == {"status": "running"}

        inventory_events = [e for e in events if e.event_type == "inventory"]
        assert len(inventory_events) == 1
        assert inventory_events[0].data == {
            "java_version": "21",
            "spring_boot_version": "4.1.0",
            "spring_cloud_version": None,
            "spring_ai_version": None,
        }

        baseline_vuln_events = [e for e in events if e.event_type == "vulnerabilities_baseline"]
        assert len(baseline_vuln_events) == 1
        assert baseline_vuln_events[0].data == {
            "vulnerabilities": [
                {
                    "cve_id": "CVE-2025-9999",
                    "package": "com.example:old-lib",
                    "installed_version": "0.9.0",
                    "fix_version": "0.9.1",
                    "cvss": 6.5,
                    "severity": "MEDIUM",
                    "source": "trivy",
                }
            ]
        }
        # Stage 0 runs the mvn effective-pom analysis (-> inventory) before
        # the baseline scan -- it needs that analysis to compute a version
        # suggestion first.
        assert event_types.index("inventory") < event_types.index("vulnerabilities_baseline")

    # Confirm the proposed version to let the pipeline continue into Stage 1.
    await run_pipeline_resume_after_version_confirm(
        job_id=job_id, confirmed_version="9.9.9", settings=settings, session_factory=db
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "success"
        assert job.output_version == "9.9.9"
        assert "stage1 report" in job.report_markdown

        events = session.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.seq).all()
        assert events[-1].event_type == "status"
        assert events[-1].data == {"status": "success"}

        # Stage 1 alone (no Stage 2 selected) must still report how much the
        # migration resolved -- this used to be silently skipped whenever
        # run_stage2 was False.
        post_stage1_events = [e for e in events if e.event_type == "vulnerabilities_post_stage1"]
        assert len(post_stage1_events) == 1
        assert post_stage1_events[0].data == {
            "vulnerabilities": [
                {
                    "cve_id": "CVE-2025-9999",
                    "package": "com.example:old-lib",
                    "installed_version": "0.9.0",
                    "fix_version": "0.9.1",
                    "cvss": 6.5,
                    "severity": "MEDIUM",
                    "source": "trivy",
                }
            ]
        }

    assert (job_paths.output / "patch.diff").read_text(encoding="utf-8").startswith("diff --git")
    assert "stage1 report" in (job_paths.output / "report.md").read_text(encoding="utf-8")
    assert not (job_paths.output / "handoff").exists()


async def test_stage1_needs_handoff_writes_guide_file(monkeypatch, settings, db, job_paths):
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)
    _create_job(db, job_id)
    ingest_sha = git_init_and_baseline_commit(job_paths.work, settings)

    monkeypatch.setattr(
        "app.orchestration.pipeline.ingest",
        lambda job_id, spec, settings_: _fake_ingest_result(job_id, job_paths, baseline_commit=ingest_sha),
    )
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _async_noop_writes_file)
    monkeypatch.setattr(
        "app.orchestration.pipeline.extract_versions",
        lambda path: DetectedVersions(java_version="11", spring_boot_version="2.7.18", spring_cloud_version=None, spring_ai_version=None),
    )
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fake_apply_output_version)

    async def fake_baseline_scan(work_dir, output_dir, settings_):
        return []

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", fake_baseline_scan)

    async def fake_stage1(job_id, work_dir, detected, baseline, settings_, on_log=None):
        return MigrationRunResult(
            plan=MigrationPlan(steps=[]),
            outcomes=[],
            status="needs_handoff",
            final_diff="",
            report="# stage1 report\n막혔습니다.",
            handoff_guide="# AI 인수인계 가이드\n막힌 지점 설명",
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", fake_stage1)
    monkeypatch.setattr("app.orchestration.pipeline.diff_since", lambda work_dir, settings_, baseline: "")

    await run_pipeline(
        job_id=job_id,
        spec=ZipSourceSpec(zip_path=job_paths.root / "fake.zip"),
        run_stage1=True,
        run_stage2=False,
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "awaiting_version_approval"

    await run_pipeline_resume_after_version_confirm(
        job_id=job_id,
        confirmed_version="9.9.9",
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "needs_handoff"

    guide_path = job_paths.output / "handoff" / "stage1-guide.md"
    assert guide_path.exists()
    assert "막힌 지점" in guide_path.read_text(encoding="utf-8")


async def test_stage2_only_with_vulnerabilities(monkeypatch, settings, db, job_paths):
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)
    _create_job(db, job_id, run_stage1=False, run_stage2=True)
    ingest_sha = git_init_and_baseline_commit(job_paths.work, settings)

    monkeypatch.setattr(
        "app.orchestration.pipeline.ingest",
        lambda job_id, spec, settings_: _fake_ingest_result(job_id, job_paths, baseline_commit=ingest_sha),
    )
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _async_noop_writes_file)
    monkeypatch.setattr(
        "app.orchestration.pipeline.extract_versions",
        lambda path: DetectedVersions(java_version="11", spring_boot_version="2.7.18", spring_cloud_version=None, spring_ai_version=None),
    )
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fake_apply_output_version)

    vuln = Vulnerability("CVE-2026-0001", "com.example:lib", "1.0.0", "1.0.1", 8.1, "HIGH", "trivy")

    async def fake_scan(work_dir, output_dir, settings_):
        return [vuln]

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", fake_scan)

    async def fake_stage2(job_id, work_dir, vulns, baseline, settings_, on_log=None):
        from app.orchestration.stage2_loop import VulnOutcome

        return Stage2RunResult(
            outcomes=[VulnOutcome(vulnerability=vuln, status="success")],
            final_diff="",
            report="# stage2 report\nCVE-2026-0001 패치 완료",
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_stage2_patches", fake_stage2)
    monkeypatch.setattr("app.orchestration.pipeline.diff_since", lambda work_dir, settings_, baseline: "")

    await run_pipeline(
        job_id=job_id,
        spec=GitSourceSpec(url="https://example.invalid/repo.git"),
        run_stage1=False,
        run_stage2=True,
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "awaiting_version_approval"

    await run_pipeline_resume_after_version_confirm(
        job_id=job_id,
        confirmed_version="9.9.9",
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "success"
        assert "CVE-2026-0001" in job.report_markdown

        events = session.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.seq).all()
        vuln_events = [e for e in events if e.event_type == "vulnerabilities"]
        assert len(vuln_events) == 1
        assert vuln_events[0].data == {
            "vulnerabilities": [
                {
                    "cve_id": "CVE-2026-0001",
                    "package": "com.example:lib",
                    "installed_version": "1.0.0",
                    "fix_version": "1.0.1",
                    "cvss": 8.1,
                    "severity": "HIGH",
                    "source": "trivy",
                }
            ]
        }


async def test_stage1_needs_handoff_with_stage2_requested_pauses_for_approval(monkeypatch, settings, db, job_paths):
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)
    _create_job(db, job_id, run_stage1=True, run_stage2=True)
    ingest_sha = git_init_and_baseline_commit(job_paths.work, settings)

    monkeypatch.setattr(
        "app.orchestration.pipeline.ingest",
        lambda job_id, spec, settings_: _fake_ingest_result(job_id, job_paths, baseline_commit=ingest_sha),
    )
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _async_noop_writes_file)
    monkeypatch.setattr(
        "app.orchestration.pipeline.extract_versions",
        lambda path: DetectedVersions(java_version="21", spring_boot_version="4.0.0", spring_cloud_version=None, spring_ai_version=None),
    )
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fake_apply_output_version)

    baseline_scan_calls = {"n": 0}

    async def fake_baseline_scan(work_dir, output_dir, settings_):
        baseline_scan_calls["n"] += 1
        return []

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", fake_baseline_scan)

    async def fake_stage1(job_id, work_dir, detected, baseline, settings_, on_log=None):
        return MigrationRunResult(
            plan=MigrationPlan(steps=[]),
            outcomes=[],
            status="needs_handoff",
            final_diff="",
            report="# stage1 report\n4.0에서 막혔습니다.",
            handoff_guide="# AI 인수인계 가이드\n4.0 -> 4.1 레시피 없음",
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", fake_stage1)
    monkeypatch.setattr("app.orchestration.pipeline.diff_since", lambda work_dir, settings_, baseline: "partial diff\n")

    async def stage2_must_not_run(*args, **kwargs):
        raise AssertionError("run_stage2_patches must not run before HITL approval")

    monkeypatch.setattr("app.orchestration.pipeline.run_stage2_patches", stage2_must_not_run)

    await run_pipeline(
        job_id=job_id,
        spec=ZipSourceSpec(zip_path=job_paths.root / "fake.zip"),
        run_stage1=True,
        run_stage2=True,
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "awaiting_version_approval"

    await run_pipeline_resume_after_version_confirm(
        job_id=job_id,
        confirmed_version="9.9.9",
        settings=settings,
        session_factory=db,
    )

    # run_combined_scan ran exactly twice: the pre-Stage-1 baseline scan and
    # the always-runs post-Stage-1 scan (which reports how much the
    # migration alone resolved, even though it ended needs_handoff) -- the
    # Stage 2 scan (also run_combined_scan under the hood) never starts.
    assert baseline_scan_calls["n"] == 2

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "awaiting_approval"
        assert "stage1 report" in job.report_markdown
        assert "stage2 report" not in job.report_markdown

        event_types = [e.event_type for e in session.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.seq).all()]
        assert "vulnerabilities_post_stage1" in event_types
        assert event_types[-1] == "status"

    assert (job_paths.output / "handoff" / "stage1-guide.md").exists()
    assert (job_paths.output / "patch.diff").read_text(encoding="utf-8") == "partial diff\n"
    assert "stage1 report" in (job_paths.output / "report.md").read_text(encoding="utf-8")


async def test_run_pipeline_resume_stage2_completes_after_approval(monkeypatch, settings, db, job_paths):
    # run_pipeline_resume_stage2 derives work_dir/output_dir as
    # settings.jobs_dir / job_id / {work,output} -- so the Job row's id must
    # match job_paths.root's directory name ("job-root"), and settings.jobs_dir
    # must resolve to job_paths.root's parent, for those paths to land on the
    # real directories job_paths already created.
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)

    git_init_and_baseline_commit(job_paths.work, settings)

    with db() as session:
        session.add(
            Job(
                id=job_id,
                source_type="zip",
                source_ref="x.zip",
                run_stage1=True,
                run_stage2=True,
                status="awaiting_approval",
                report_markdown="# stage1 report\n4.0에서 막혔습니다.",
            )
        )
        session.commit()

    vuln = Vulnerability("CVE-2026-0002", "com.example:lib2", "2.0.0", "2.0.1", 7.2, "HIGH", "trivy")

    async def fake_scan(work_dir, output_dir, settings_):
        return [vuln]

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", fake_scan)

    async def fake_stage2(job_id, work_dir, vulns, baseline, settings_, on_log=None):
        from app.orchestration.stage2_loop import VulnOutcome

        return Stage2RunResult(
            outcomes=[VulnOutcome(vulnerability=vuln, status="success")],
            final_diff="",
            report="# stage2 report\nCVE-2026-0002 패치 완료",
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_stage2_patches", fake_stage2)

    await run_pipeline_resume_stage2(job_id=job_id, settings=settings, session_factory=db)

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "needs_handoff"
        assert "stage1 report" in job.report_markdown
        assert "stage2 report" in job.report_markdown

    assert (job_paths.output / "patch.diff").exists()
    report_text = (job_paths.output / "report.md").read_text(encoding="utf-8")
    assert "stage1 report" in report_text
    assert "stage2 report" in report_text


async def test_stage1_success_commits_survive_a_failed_first_stage2_cve(monkeypatch, settings, db, job_paths):
    """Regression test for job #14 (spec: docs/superpowers/specs/2026-08-09-
    stage2-baseline-drift-design.md): Stage 1 commits several checkpoints and
    fully succeeds, then Stage 2's first CVE fails. Before the fix, baseline
    was never updated after Stage 1, so stage2_loop's reset_to_checkpoint
    (called with last_good_sha still equal to that stale, pre-Stage-1 value)
    would silently wipe out everything Stage 1 had already committed."""
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)
    _create_job(db, job_id, run_stage1=True, run_stage2=True)
    ingest_sha = git_init_and_baseline_commit(job_paths.work, settings)

    monkeypatch.setattr(
        "app.orchestration.pipeline.ingest",
        lambda job_id, spec, settings_: _fake_ingest_result(job_id, job_paths, baseline_commit=ingest_sha),
    )
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _async_noop_writes_file)
    monkeypatch.setattr(
        "app.orchestration.pipeline.extract_versions",
        lambda path: DetectedVersions(java_version="21", spring_boot_version="4.1.0", spring_cloud_version=None, spring_ai_version=None),
    )
    monkeypatch.setattr("app.orchestration.pipeline.apply_output_version", _fake_apply_output_version)

    async def fake_scan(work_dir, output_dir, settings_):
        return []

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", fake_scan)

    async def fake_stage1(job_id, work_dir, detected, baseline, settings_, on_log=None):
        # Mirrors what multi_step.run_stage1_migration really does for each
        # successful step: a real checkpoint commit per step.
        commit_checkpoint(work_dir, settings_, "checkpoint: Spring Boot 3.5 -> 4.0")
        commit_checkpoint(work_dir, settings_, "checkpoint: Spring AI 1.1.8 -> 2.0")
        return MigrationRunResult(
            plan=MigrationPlan(steps=[]), outcomes=[], status="success", final_diff="", report="# stage1 report\n성공", handoff_guide=None
        )

    monkeypatch.setattr("app.orchestration.pipeline.run_stage1_migration", fake_stage1)

    async def fake_stage2_first_cve_fails(job_id, work_dir, vulns, baseline_commit, settings_, on_log=None):
        # Mirrors exactly what stage2_loop.run_stage2_patches does when the
        # very first CVE fails: last_good_sha is still whatever baseline it
        # was handed (never updated by a successful CVE yet).
        reset_to_checkpoint(work_dir, settings_, baseline_commit)
        return Stage2RunResult(outcomes=[], final_diff="", report="# stage2 report\n실패")

    monkeypatch.setattr("app.orchestration.pipeline.run_stage2_patches", fake_stage2_first_cve_fails)

    await run_pipeline(
        job_id=job_id,
        spec=ZipSourceSpec(zip_path=job_paths.root / "fake.zip"),
        run_stage1=True,
        run_stage2=True,
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "awaiting_version_approval"

    await run_pipeline_resume_after_version_confirm(
        job_id=job_id,
        confirmed_version="9.9.9",
        settings=settings,
        session_factory=db,
    )

    subjects = _commit_subjects(job_paths.work)
    assert "Spring Boot 3.5 -> 4.0" in subjects
    assert "Spring AI 1.1.8 -> 2.0" in subjects


async def test_resume_stage2_after_partial_stage1_success_preserves_stage1_commits(monkeypatch, settings, db, job_paths):
    """Same regression as above, but for the HITL-approval resume path
    (run_pipeline_resume_stage2) -- Stage 1 succeeded on 2 steps before
    getting stuck on a 3rd (hence awaiting_approval), and the resumed
    Stage 2's first CVE fails."""
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)

    git_init_and_baseline_commit(job_paths.work, settings)
    commit_checkpoint(job_paths.work, settings, "checkpoint: Spring Boot 3.5 -> 4.0")
    commit_checkpoint(job_paths.work, settings, "checkpoint: Spring Boot 4.0 -> 4.1")

    with db() as session:
        session.add(
            Job(
                id=job_id,
                source_type="zip",
                source_ref="x.zip",
                run_stage1=True,
                run_stage2=True,
                status="awaiting_approval",
                report_markdown="# stage1 report\n일부 성공, 3번째 스텝에서 막힘",
            )
        )
        session.commit()

    async def fake_scan(work_dir, output_dir, settings_):
        return []

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", fake_scan)

    async def fake_stage2_first_cve_fails(job_id, work_dir, vulns, baseline_commit, settings_, on_log=None):
        reset_to_checkpoint(work_dir, settings_, baseline_commit)
        return Stage2RunResult(outcomes=[], final_diff="", report="# stage2 report\n실패")

    monkeypatch.setattr("app.orchestration.pipeline.run_stage2_patches", fake_stage2_first_cve_fails)

    await run_pipeline_resume_stage2(job_id=job_id, settings=settings, session_factory=db)

    subjects = _commit_subjects(job_paths.work)
    assert "Spring Boot 3.5 -> 4.0" in subjects
    assert "Spring Boot 4.0 -> 4.1" in subjects


async def test_cancel_during_run_marks_job_cancelled_and_writes_marker(monkeypatch, settings, db, job_paths):
    # _finalize_cancelled derives output_dir as settings.jobs_dir / job_id /
    # "output" (same pattern as run_pipeline_resume_stage2), so job_id and
    # settings.jobs_data_dir must line up with job_paths for that derived
    # path to land on the directory job_paths already created.
    job_id = job_paths.root.name
    settings.jobs_data_dir = str(job_paths.root.parent)
    _create_job(db, job_id)

    monkeypatch.setattr("app.orchestration.pipeline.ingest", lambda job_id, spec, settings_: _fake_ingest_result(job_id, job_paths))
    monkeypatch.setattr("app.orchestration.pipeline.mvn_effective_pom", _async_noop_writes_file)
    monkeypatch.setattr(
        "app.orchestration.pipeline.extract_versions",
        lambda path: DetectedVersions(java_version="11", spring_boot_version="2.7.18", spring_cloud_version=None, spring_ai_version=None),
    )

    started = asyncio.Event()

    async def slow_baseline_scan(work_dir, output_dir, settings_):
        started.set()
        await asyncio.sleep(30)  # never reached -- the Task is cancelled first
        raise AssertionError("should have been cancelled before this returned")

    monkeypatch.setattr("app.orchestration.pipeline.run_combined_scan", slow_baseline_scan)

    task = asyncio.ensure_future(
        run_pipeline(
            job_id=job_id,
            spec=ZipSourceSpec(zip_path=job_paths.root / "fake.zip"),
            run_stage1=True,
            run_stage2=False,
            settings=settings,
            session_factory=db,
        )
    )
    await started.wait()  # let run_pipeline actually reach the (now-blocked) scan call
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    with db() as session:
        job = session.get(Job, job_id)
        assert job.status == "cancelled"

        events = session.query(JobEvent).filter_by(job_id=job_id).order_by(JobEvent.seq).all()
        assert events[-1].event_type == "status"
        assert events[-1].data == {"status": "cancelled"}

    marker = job_paths.output / "CANCELLED"
    assert marker.exists()

    # a second cancellation-finalize call for an already-cancelled job must
    # be a no-op (idempotency the JobManager._run fallback relies on)
    from app.orchestration.pipeline import _finalize_cancelled

    await _finalize_cancelled(job_id, settings, db)
    with db() as session:
        assert session.get(Job, job_id).status == "cancelled"


async def test_ingest_failure_marks_job_failed(monkeypatch, settings, db, job_paths):
    _create_job(db, "job-4")

    from app.ingest.errors import GradleProjectError

    def failing_ingest(job_id, spec, settings_):
        raise GradleProjectError("Gradle project detected")

    monkeypatch.setattr("app.orchestration.pipeline.ingest", failing_ingest)

    await run_pipeline(
        job_id="job-4",
        spec=ZipSourceSpec(zip_path=job_paths.root / "fake.zip"),
        run_stage1=True,
        run_stage2=False,
        settings=settings,
        session_factory=db,
    )

    with db() as session:
        job = session.get(Job, "job-4")
        assert job.status == "failed"
        assert "Gradle" in job.error_message


async def _async_noop_writes_file(work_dir, output_path, settings_, log_path=None):
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("<projects><project/></projects>", encoding="utf-8")
    return output_path


async def _fake_apply_output_version(work_dir, new_version, settings_, output_dir=None):
    # The real apply_output_version shells out to `mvn versions:set` -- swap
    # in a plain checkpoint commit so these are still pure control-flow tests.
    return commit_checkpoint(work_dir, settings_, f"checkpoint: set artifact version to {new_version}")
