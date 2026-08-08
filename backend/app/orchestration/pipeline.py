"""The actual end-to-end run: ingest -> (optional output version) -> Stage 1
-> Stage 2 -> diff/report/handoff files, with progress emitted throughout
via streaming.events.emit_event and the Job row's status kept current in
the DB. This is what concurrency.JobManager schedules per job.

If Stage 1 ends needs_handoff and Stage 2 was requested, the pipeline does
NOT auto-continue into Stage 2 -- it stops at status="awaiting_approval" and
waits for a human to call POST /jobs/{id}/proceed (api/routers/jobs.py),
which schedules run_pipeline_resume_stage2 below. See docs/architecture.md
§7 for why (a Stage 1 gap doesn't get less real just because Stage 2 is
about to run on top of it).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from app.checkpoint.git_repo import diff_since, resolve_ingest_baseline, resolve_stage_baseline
from app.config import Settings
from app.ingest.errors import IngestError
from app.ingest.workspace import SourceSpec, ingest
from app.models.job import Job, TERMINAL_JOB_STATUSES
from app.mvnrewrite.mvn_client import mvn_effective_pom
from app.mvnrewrite.pom_parser import extract_versions
from app.mvnrewrite.subprocess_runner import build_log_path
from app.orchestration.multi_step import run_stage1_migration
from app.orchestration.stage2_loop import run_stage2_patches
from app.scan.combined import run_combined_scan
from app.streaming.events import emit_event
from app.versioning.artifact_version import apply_output_version

logger = logging.getLogger(__name__)

EmitFn = Callable[[str, dict], Awaitable[None]]
LogFn = Callable[[str], Awaitable[None]]


async def set_job_status(
    session_factory: sessionmaker[Session],
    job_id: str,
    status: str,
    error_message: str | None = None,
    report_markdown: str | None = None,
) -> None:
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            return
        job.status = status
        if error_message is not None:
            job.error_message = error_message
        if report_markdown is not None:
            job.report_markdown = report_markdown
        session.commit()


def make_emit_log(session_factory: sessionmaker[Session], job_id: str) -> tuple[EmitFn, LogFn]:
    async def emit(event_type: str, data: dict) -> None:
        # Mirror every SSE event into the backend's own console too -- the
        # browser progress panel isn't always open/visible, and a developer
        # watching the terminal otherwise has no way to tell a job is still
        # progressing versus stuck.
        if event_type == "log":
            logger.info("[job %s] %s", job_id, data.get("message", ""))
        elif event_type == "status":
            error = data.get("error")
            if error:
                logger.warning("[job %s] status=%s (%s)", job_id, data.get("status"), error)
            else:
                logger.info("[job %s] status=%s", job_id, data.get("status"))
        await emit_event(session_factory, job_id, event_type, data)

    async def log(message: str) -> None:
        await emit("log", {"message": message})

    return emit, log


async def _finalize_cancelled(job_id: str, settings: Settings, session_factory: sessionmaker[Session]) -> None:
    """Marks job_id cancelled (spec: docs/superpowers/specs/
    2026-08-08-job-cancellation-design.md), from whichever of three places
    a POST /jobs/{id}/cancel request actually needs to finalize it:
    - directly, from the API endpoint, when there's no live Task to cancel
      (awaiting_approval, or the process restarted and lost track of it);
    - from concurrency.JobManager's on_queued_cancel fallback, if the Task
      was still waiting on the concurrency semaphore and never even started
      running this module's own pipeline coroutines;
    - from this module's own except asyncio.CancelledError below, once the
      cancellation has propagated back up through a running pipeline.

    Idempotent by design: the last two call sites can both legitimately fire
    for the same cancellation (see JobManager._run's own docstring), so this
    is a no-op once the job is already terminal."""
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None or job.status in TERMINAL_JOB_STATUSES:
            return

    await set_job_status(session_factory, job_id, "cancelled")
    emit, _log = make_emit_log(session_factory, job_id)
    await emit("status", {"status": "cancelled"})

    output_dir = settings.jobs_dir / job_id / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "CANCELLED").write_text(
        f"이 작업은 {datetime.now(UTC).isoformat(timespec='seconds')}에 사용자 요청으로 강제 종료되었습니다.\n",
        encoding="utf-8",
    )


async def _run_stage2_block(
    emit: EmitFn,
    log: LogFn,
    job_id: str,
    work_dir: Path,
    output_dir: Path,
    baseline: str,
    handoff_dir: Path,
    settings: Settings,
) -> tuple[str, bool]:
    """Scan + patch. Returns (report section text, needs_handoff). Shared by
    run_pipeline (the normal same-run path) and run_pipeline_resume_stage2
    (the HITL-approved continuation) -- identical either way."""
    await log("2단계 취약점 스캔 시작 (패치 대상 선정)")
    scan_started_at = time.monotonic()
    vulns = await run_combined_scan(work_dir, output_dir, settings)
    await emit("vulnerabilities", {"vulnerabilities": [asdict(v) for v in vulns]})
    scan_elapsed = time.monotonic() - scan_started_at
    await log(f"2단계 취약점 스캔 완료 ({scan_elapsed:.1f}s)")
    await log(f"{len(vulns)}개 취약점 발견 (임계값 이상, 패치 대상)")

    stage2_result = await run_stage2_patches(job_id, work_dir, vulns, baseline, settings, on_log=log)
    success_count = sum(1 for o in stage2_result.outcomes if o.status == "success")
    blocked_count = len(stage2_result.outcomes) - success_count
    await log(f"2단계 종료 (완료 {success_count}건 / 막힘 {blocked_count}건)")

    needs_handoff = False
    for outcome in stage2_result.outcomes:
        if outcome.status == "needs_handoff" and outcome.handoff_guide:
            handoff_dir.mkdir(parents=True, exist_ok=True)
            safe_cve = outcome.vulnerability.cve_id.replace("/", "_")
            (handoff_dir / f"stage2-{safe_cve}-guide.md").write_text(outcome.handoff_guide, encoding="utf-8")
            needs_handoff = True

    return stage2_result.report, needs_handoff


async def run_pipeline(
    job_id: str,
    spec: SourceSpec,
    output_version: str | None,
    run_stage1: bool,
    run_stage2: bool,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    emit, log = make_emit_log(session_factory, job_id)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        await log("POM 분석 시작")
        ingest_result = ingest(job_id, spec, settings)
        work_dir = ingest_result.paths.work
        output_dir = ingest_result.paths.output
        baseline = ingest_result.baseline_commit
        await log(f"모듈 {len(ingest_result.detection.modules)}개, baseline={baseline[:12]}")

        if output_version:
            await log(f"출력 아티팩트 버전 설정: {output_version}")
            baseline = await apply_output_version(work_dir, output_version, settings, output_dir=output_dir)

        report_sections: list[str] = []
        handoff_dir = output_dir / "handoff"
        needs_handoff = False

        if run_stage1:
            await log("마이그레이션 전 취약점 스캔 시작")
            baseline_scan_started_at = time.monotonic()
            baseline_vulns = await run_combined_scan(work_dir, output_dir, settings)
            await emit("vulnerabilities_baseline", {"vulnerabilities": [asdict(v) for v in baseline_vulns]})
            baseline_scan_elapsed = time.monotonic() - baseline_scan_started_at
            await log(f"마이그레이션 전 취약점 스캔 완료 ({baseline_scan_elapsed:.1f}s)")
            await log(f"{len(baseline_vulns)}개 취약점 발견 (임계값 이상, 마이그레이션 전)")

            await log("1단계 스택 마이그레이션 시작")
            effective_pom_path = output_dir / "effective-pom.xml"
            await mvn_effective_pom(
                work_dir, effective_pom_path, settings, log_path=build_log_path(output_dir, "ingest", "mvn-effective-pom")
            )
            detected = extract_versions(effective_pom_path)
            await emit("inventory", asdict(detected))

            stage1_result = await run_stage1_migration(job_id, work_dir, detected, baseline, settings, on_log=log)
            report_sections.append(stage1_result.report)
            await log(f"1단계 종료: {stage1_result.status}")

            if stage1_result.status == "needs_handoff" and stage1_result.handoff_guide:
                handoff_dir.mkdir(parents=True, exist_ok=True)
                (handoff_dir / "stage1-guide.md").write_text(stage1_result.handoff_guide, encoding="utf-8")
                needs_handoff = True

        # A Stage 1 gap doesn't stop mattering just because Stage 2 is about
        # to run on top of it -- pause for a human to explicitly opt into
        # continuing, rather than silently barreling into Stage 2 next.
        awaiting_stage2_approval = run_stage1 and needs_handoff and run_stage2

        if run_stage2 and not awaiting_stage2_approval:
            stage2_report, stage2_needs_handoff = await _run_stage2_block(
                emit, log, job_id, work_dir, output_dir, baseline, handoff_dir, settings
            )
            report_sections.append(stage2_report)
            needs_handoff = needs_handoff or stage2_needs_handoff

        if awaiting_stage2_approval:
            diff_text = diff_since(work_dir, settings, ingest_result.baseline_commit)
            (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")
            partial_report = "\n\n---\n\n".join(report_sections)
            (output_dir / "report.md").write_text(partial_report, encoding="utf-8")
            await set_job_status(session_factory, job_id, "awaiting_approval", report_markdown=partial_report)
            await emit("status", {"status": "awaiting_approval"})
            return

        await log("결과물 생성 중...")
        diff_text = diff_since(work_dir, settings, ingest_result.baseline_commit)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")

        final_report = "\n\n---\n\n".join(report_sections) if report_sections else "변경 사항 없음."
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        final_status = "needs_handoff" if needs_handoff else "success"
        await set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except IngestError as exc:
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
    except asyncio.CancelledError:
        # A human force-stopped the job (POST /jobs/{id}/cancel) -- not a
        # failure, so this is deliberately kept out of the except Exception
        # branch below (CancelledError isn't an Exception subclass anyway,
        # but the separation also documents the intent). Re-raise so the
        # Task actually ends up cancelled, not merely "returned".
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001 -- a job failure must never crash the server process
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})


async def run_pipeline_resume_stage2(job_id: str, settings: Settings, session_factory: sessionmaker[Session]) -> None:
    """Scheduled by POST /jobs/{id}/proceed for a job sitting at
    status="awaiting_approval". work_dir/output_dir are re-derived from
    job_id alone (paths are always {JOBS_DATA_DIR}/{job_id}/{work,output});
    the baseline commit(s) run_pipeline held as local variables are recovered
    from git history via resolve_ingest_baseline/resolve_stage_baseline --
    no new DB column needed."""
    emit, log = make_emit_log(session_factory, job_id)

    with session_factory() as session:
        job = session.get(Job, job_id)
        prior_report = job.report_markdown or ""
        output_version = job.output_version

    work_dir = settings.jobs_dir / job_id / "work"
    output_dir = settings.jobs_dir / job_id / "output"
    handoff_dir = output_dir / "handoff"
    stage_baseline = resolve_stage_baseline(work_dir, settings, output_version)
    ingest_baseline = resolve_ingest_baseline(work_dir, settings)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        stage2_report, _stage2_needs_handoff = await _run_stage2_block(
            emit, log, job_id, work_dir, output_dir, stage_baseline, handoff_dir, settings
        )

        await log("결과물 생성 중...")
        diff_text = diff_since(work_dir, settings, ingest_baseline)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")

        final_report = f"{prior_report}\n\n---\n\n{stage2_report}"
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        # This resume path only ever runs because Stage 1 already ended
        # needs_handoff -- that gap is still there in the codebase no matter
        # how Stage 2 turns out, so the job's final status stays needs_handoff.
        final_status = "needs_handoff"
        await set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except asyncio.CancelledError:
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001 -- same rationale as run_pipeline
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
