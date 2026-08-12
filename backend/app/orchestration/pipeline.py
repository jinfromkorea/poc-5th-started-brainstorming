"""The actual end-to-end run: ingest -> Stage 0 -> Stage 1 -> Stage 2 ->
diff/report/handoff files, with progress emitted throughout via
streaming.events.emit_event and the Job row's status kept current in the
DB. This is what concurrency.JobManager schedules per job.

run_pipeline only covers ingest + Stage 0 (mvn effective-pom analysis,
baseline vuln scan, output-version proposal) -- if either stage was
requested, it stops at status="awaiting_version_approval" and waits for a
human to call POST /jobs/{id}/confirm-version, which schedules
run_pipeline_resume_after_version_confirm (spec: docs/superpowers/specs/
2026-08-10-stage0-version-scan-restructure-design.md). That function does
the rest: apply the confirmed version, then Stage 1, then Stage 2.

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
from typing import Literal

from sqlalchemy.orm import Session, sessionmaker

from app.checkpoint.git_repo import commit_checkpoint, current_head, diff_since, resolve_ingest_baseline
from app.config import Settings
from app.handoff.guide_builder import build_handoff_guide
from app.ingest.errors import IngestError
from app.ingest.maven_detect import detect_external_parent, read_declared_parent, read_declared_version
from app.ingest.workspace import SourceSpec, ingest
from app.models.job import Job, JobEvent, TERMINAL_JOB_STATUSES
from app.mvnrewrite.mvn_client import mvn_effective_pom
from app.mvnrewrite.pom_parser import extract_versions
from app.mvnrewrite.subprocess_runner import build_log_path
from app.orchestration.multi_step import TARGET_STACK_SUMMARY, run_stage1_migration, verify_after_manual_fix
from app.orchestration.stage2_loop import run_stage2_patches
from app.scan.combined import run_combined_scan
from app.scan.merge import Vulnerability
from app.streaming.events import emit_event
from app.versioning.artifact_version import apply_output_version, compute_stage0_output_version

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
    emit, _ = make_emit_log(session_factory, job_id)
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
    vulns: list[Vulnerability],
) -> tuple[str, bool]:
    """Patch + final verification scan. Returns (report section text,
    needs_handoff). Shared by run_pipeline_resume_after_version_confirm and
    run_pipeline_resume_stage2 (the HITL-approved continuation) -- identical
    either way. Unlike before, this no longer scans to build its own patch-
    target list -- the caller already has one (either a fresh scan, or Stage
    0's baseline scan reused when Stage 1 didn't run), so scanning here too
    would just re-scan an unchanged work/ (spec: docs/superpowers/specs/
    2026-08-10-stage0-version-scan-restructure-design.md)."""
    await emit("vulnerabilities", {"vulnerabilities": [asdict(v) for v in vulns]})
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

    await log("2단계 패치 후 최종 취약점 재스캔")
    final_vulns = await run_combined_scan(work_dir, output_dir, settings)
    await emit("vulnerabilities_final", {"vulnerabilities": [asdict(v) for v in final_vulns]})
    await log(f"{len(final_vulns)}개 취약점 남음 (최종)")

    return stage2_result.report, needs_handoff


async def run_pipeline(
    job_id: str,
    spec: SourceSpec,
    run_stage1: bool,
    run_stage2: bool,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    """Ingest + Stage 0 only. If neither stage was requested, finalizes
    immediately (nothing to version/scan). Otherwise stops at
    "awaiting_version_approval" once Stage 0's analysis is done -- the rest
    (apply the confirmed version, Stage 1, Stage 2) happens in
    run_pipeline_resume_after_version_confirm, scheduled by POST
    /jobs/{id}/confirm-version (spec: docs/superpowers/specs/2026-08-10-
    stage0-version-scan-restructure-design.md)."""
    emit, log = make_emit_log(session_factory, job_id)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        await log("POM 분석 시작")
        ingest_result = ingest(job_id, spec, settings)
        source_dir = ingest_result.paths.source
        output_dir = ingest_result.paths.output
        baseline = ingest_result.baseline_commit
        # Raw (unfiltered) <parent> coordinates -- unlike detect_external_parent
        # (Stage 0's internal-parent gate below), this shows public parents
        # (e.g. spring-boot-starter-parent) too, since this line is purely
        # informational.
        declared_parent = read_declared_parent(source_dir / "pom.xml")
        parent_label = f"{declared_parent.group_id}:{declared_parent.artifact_id}" if declared_parent else "none"
        await log(f"모듈 {len(ingest_result.detection.modules)}개, parent={parent_label}, baseline={baseline[:12]}")

        if not (run_stage1 or run_stage2):
            await log("1·2단계 모두 선택되지 않아 변경 사항 없이 종료")
            (output_dir / "patch.diff").write_text("", encoding="utf-8")
            (output_dir / "report.md").write_text("변경 사항 없음.", encoding="utf-8")
            await set_job_status(session_factory, job_id, "success", report_markdown="변경 사항 없음.")
            await emit("status", {"status": "success"})
            return

        await log("Stage 0: 현재 버전/스택 분석 시작")
        # Stage 0 analyzes source/ end to end (mvn effective-pom, parent
        # detection, vulnerability scan below) -- work/ was already copied
        # from source/ during ingest, before any of this runs, so nothing
        # Stage 0 does here (including the real `mvn install` the
        # vulnerability scan needs) can leak into work/'s state. source/'s
        # own files are never edited, only build artifacts (target/ etc.)
        # get added alongside them -- and nothing else in this codebase
        # reads source/ again after ingest, so that's harmless.
        effective_pom_path = output_dir / "effective-pom.xml"
        await mvn_effective_pom(
            source_dir, effective_pom_path, settings, log_path=build_log_path(output_dir, "ingest", "mvn-effective-pom")
        )
        detected = extract_versions(effective_pom_path)

        # A <parent> on the ingested project's own root pom.xml that isn't a
        # known public one may be a "사내 parent POM(BOM 겸용)" whose
        # properties are the actual source of the detected stack above --
        # Stage 1 can't touch that artifact's own files, only point at a
        # newer released version of it (spec: docs/superpowers/specs/
        # 2026-08-11-internal-parent-pom-target-version-design.md). Computed
        # here (before the inventory emit) so the "분석" panel can show it
        # alongside the stack it actually explains, not just later in the
        # version-approval gate.
        detected_parent = detect_external_parent(source_dir / "pom.xml")
        await emit("inventory", {**asdict(detected), "detected_parent": asdict(detected_parent) if detected_parent else None})

        current_version, _version_source = read_declared_version(effective_pom_path)
        suggested_version = compute_stage0_output_version(current_version, run_stage1) if current_version else None

        await log("마이그레이션 전 취약점 스캔 시작")
        baseline_scan_started_at = time.monotonic()
        baseline_vulns = await run_combined_scan(source_dir, output_dir, settings)
        await emit("vulnerabilities_baseline", {"vulnerabilities": [asdict(v) for v in baseline_vulns]})
        baseline_scan_elapsed = time.monotonic() - baseline_scan_started_at
        await log(f"마이그레이션 전 취약점 스캔 완료 ({baseline_scan_elapsed:.1f}s)")
        await log(f"{len(baseline_vulns)}개 취약점 발견 (임계값 이상, 마이그레이션 전)")

        await set_job_status(session_factory, job_id, "awaiting_version_approval")
        await emit(
            "status",
            {
                "status": "awaiting_version_approval",
                "current_version": current_version,
                "suggested_version": suggested_version,
                "detected_parent": asdict(detected_parent) if detected_parent else None,
            },
        )
        return

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


def _latest_event_data(session_factory: sessionmaker[Session], job_id: str, event_type: str) -> dict | None:
    """Reads back the most recent persisted JobEvent of a given type for a
    job -- used to reuse Stage 0's baseline vulnerability scan as Stage 2's
    patch-target list when Stage 1 didn't run (so work/ never changed since
    that scan), instead of paying for a redundant re-scan."""
    with session_factory() as session:
        row = (
            session.query(JobEvent)
            .filter(JobEvent.job_id == job_id, JobEvent.event_type == event_type)
            .order_by(JobEvent.seq.desc())
            .first()
        )
        return row.data if row is not None else None


async def run_pipeline_resume_after_version_confirm(
    job_id: str,
    confirmed_version: str,
    settings: Settings,
    session_factory: sessionmaker[Session],
    parent_target_version: str | None = None,
) -> None:
    """Scheduled by POST /jobs/{id}/confirm-version for a job sitting at
    status="awaiting_version_approval". work_dir is exactly as Stage 0 left
    it (baseline commit only, nothing else touched yet)."""
    emit, log = make_emit_log(session_factory, job_id)

    with session_factory() as session:
        job = session.get(Job, job_id)
        run_stage1, run_stage2 = job.run_stage1, job.run_stage2

    work_dir = settings.jobs_dir / job_id / "work"
    output_dir = settings.jobs_dir / job_id / "output"
    handoff_dir = output_dir / "handoff"
    ingest_baseline = resolve_ingest_baseline(work_dir, settings)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        await log(f"출력 아티팩트 버전 설정: {confirmed_version}")
        baseline = await apply_output_version(work_dir, confirmed_version, settings, output_dir=output_dir)
        with session_factory() as session:
            job = session.get(Job, job_id)
            job.output_version = confirmed_version
            session.commit()

        detected = extract_versions(output_dir / "effective-pom.xml")  # Stage 0가 이미 만들어둔 파일 재사용

        report_sections: list[str] = []
        # Tracks *which* stage last set a handoff, not just whether one
        # happened -- job status distinguishes stage1_needs_handoff from
        # stage2_needs_handoff (spec: docs/superpowers/specs/2026-08-11-job-
        # status-stage-split-design.md). Only one of the two blocks below can
        # ever run per call (Stage 1 handoff routes into awaiting_approval
        # before Stage 2 would run), so a single slot is enough.
        handoff_stage: Literal["stage1", "stage2"] | None = None
        stage2_vulns: list[Vulnerability] = []

        if run_stage1:
            await log("1단계 스택 마이그레이션 시작")
            stage1_result = await run_stage1_migration(
                job_id, work_dir, detected, baseline, settings, parent_target_version=parent_target_version, on_log=log
            )
            # Keep baseline in sync with whatever Stage 1 actually left in
            # work/ (spec: docs/superpowers/specs/2026-08-09-stage2-
            # baseline-drift-design.md) -- regardless of no_gap/success/
            # needs_handoff, this HEAD is the correct floor for Stage 2's
            # own rollback to protect.
            baseline = current_head(work_dir, settings)
            report_sections.append(stage1_result.report)
            await log(f"1단계 종료: {stage1_result.status}")

            if not parent_target_version:
                # work/의 <parent>는 parent_target_version을 안 줬으면 Stage 1이
                # 안 건드렸으므로(§4.1), Stage 0가 감지했던 것과 동일한 상태 --
                # 사람이 다음 실행 때 참고할 수 있게 리포트에 남겨둔다(spec:
                # docs/superpowers/specs/2026-08-11-internal-parent-pom-
                # target-version-design.md).
                detected_parent = detect_external_parent(work_dir / "pom.xml")
                if detected_parent is not None:
                    report_sections.append(
                        f"이 프로젝트의 스택 버전 일부는 사내 parent POM(`{detected_parent.group_id}:"
                        f"{detected_parent.artifact_id}`)에서 관리됩니다 — 이 프로젝트만으로는 목표에 도달할 "
                        "수 없습니다. parent를 먼저 올리거나, 이미 올라간 parent의 새 버전을 알고 있다면 "
                        "다음 실행 시 입력하세요."
                    )

            if stage1_result.status == "needs_handoff" and stage1_result.handoff_guide:
                handoff_dir.mkdir(parents=True, exist_ok=True)
                (handoff_dir / "stage1-guide.md").write_text(stage1_result.handoff_guide, encoding="utf-8")
                handoff_stage = "stage1"

            # Always scan right after Stage 1, regardless of whether Stage 2
            # was requested -- this is how much the migration alone resolved,
            # a number worth showing on its own (spec: docs/superpowers/
            # specs/2026-08-10-stage1-post-migration-scan-design.md). When
            # Stage 2 does run, this same scan doubles as its patch-target
            # list, so it never gets scanned twice for the same work/ state.
            await log("1단계 이후 취약점 재스캔")
            post_stage1_vulns = await run_combined_scan(work_dir, output_dir, settings)
            await emit("vulnerabilities_post_stage1", {"vulnerabilities": [asdict(v) for v in post_stage1_vulns]})
            await log(f"{len(post_stage1_vulns)}개 취약점 남음 (마이그레이션 후)")

            if run_stage2 and handoff_stage is None:
                stage2_vulns = post_stage1_vulns
        elif run_stage2:
            # Stage 1을 안 돌렸으므로 work/는 Stage 0의 베이스라인 스캔 이후
            # 안 바뀌었다(버전 적용은 의존성을 안 건드림) -- 재스캔 대신 그
            # 결과를 재사용한다.
            baseline_data = _latest_event_data(session_factory, job_id, "vulnerabilities_baseline")
            stage2_vulns = [Vulnerability(**v) for v in baseline_data["vulnerabilities"]] if baseline_data else []

        # A Stage 1 gap doesn't stop mattering just because Stage 2 is about
        # to run on top of it -- pause for a human to explicitly opt into
        # continuing, rather than silently barreling into Stage 2 next.
        awaiting_stage2_approval = handoff_stage == "stage1" and run_stage2

        if run_stage2 and not awaiting_stage2_approval:
            stage2_report, stage2_needs_handoff = await _run_stage2_block(
                emit, log, job_id, work_dir, output_dir, baseline, handoff_dir, settings, stage2_vulns
            )
            report_sections.append(stage2_report)
            if stage2_needs_handoff:
                handoff_stage = "stage2"

        if awaiting_stage2_approval:
            diff_text = diff_since(work_dir, settings, ingest_baseline)
            (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")
            partial_report = "\n\n---\n\n".join(report_sections)
            (output_dir / "report.md").write_text(partial_report, encoding="utf-8")
            await set_job_status(session_factory, job_id, "awaiting_approval", report_markdown=partial_report)
            await emit("status", {"status": "awaiting_approval"})
            return

        await log("결과물 생성 중...")
        diff_text = diff_since(work_dir, settings, ingest_baseline)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")

        final_report = "\n\n---\n\n".join(report_sections) if report_sections else "변경 사항 없음."
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        final_status = f"{handoff_stage}_needs_handoff" if handoff_stage else "success"
        await set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except asyncio.CancelledError:
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001 -- a job failure must never crash the server process
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})


async def run_pipeline_resume_stage2(job_id: str, settings: Settings, session_factory: sessionmaker[Session]) -> None:
    """Scheduled by POST /jobs/{id}/proceed for a job sitting at
    status="awaiting_approval". work_dir/output_dir are re-derived from
    job_id alone (paths are always {JOBS_DATA_DIR}/{job_id}/{work,output}).
    ingest_baseline (the true first commit, for the final diff) is recovered
    from git history via resolve_ingest_baseline -- no new DB column needed.
    stage_baseline (Stage 2's own rollback floor) doesn't need reconstructing
    at all: work/ has sat untouched since Stage 1 paused here, so its
    current HEAD already *is* the correct value (spec: docs/superpowers/
    specs/2026-08-09-stage2-baseline-drift-design.md)."""
    emit, log = make_emit_log(session_factory, job_id)

    with session_factory() as session:
        job = session.get(Job, job_id)
        prior_report = job.report_markdown or ""

    work_dir = settings.jobs_dir / job_id / "work"
    output_dir = settings.jobs_dir / job_id / "output"
    handoff_dir = output_dir / "handoff"
    stage_baseline = current_head(work_dir, settings)
    ingest_baseline = resolve_ingest_baseline(work_dir, settings)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        # work/ has sat untouched since Stage 1 finished and emitted this
        # same scan as "vulnerabilities_post_stage1" -- reuse it instead of
        # paying for a redundant re-scan (same reasoning as the Stage-0-
        # baseline reuse in run_pipeline_resume_after_version_confirm).
        post_stage1_data = _latest_event_data(session_factory, job_id, "vulnerabilities_post_stage1")
        if post_stage1_data is not None:
            vulns = [Vulnerability(**v) for v in post_stage1_data["vulnerabilities"]]
        else:
            await log("취약점 재스캔 (2단계 패치 대상 선정)")
            vulns = await run_combined_scan(work_dir, output_dir, settings)
        stage2_report, stage2_needs_handoff = await _run_stage2_block(
            emit, log, job_id, work_dir, output_dir, stage_baseline, handoff_dir, settings, vulns
        )

        await log("결과물 생성 중...")
        diff_text = diff_since(work_dir, settings, ingest_baseline)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")

        final_report = f"{prior_report}\n\n---\n\n{stage2_report}"
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        # This resume path only ever runs because Stage 1 already ended
        # stage1_needs_handoff -- if Stage 2 also hands off here, that's the
        # more actionable/newer problem so it wins; otherwise Stage 1's own
        # gap is still there (approving /proceed doesn't make it go away),
        # so the status reverts to reflecting that.
        final_status = "stage2_needs_handoff" if stage2_needs_handoff else "stage1_needs_handoff"
        await set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except asyncio.CancelledError:
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001 -- same rationale as run_pipeline
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})


async def run_pipeline_resume_stage1_after_handoff(
    job_id: str, settings: Settings, session_factory: sessionmaker[Session]
) -> None:
    """Scheduled by POST /jobs/{id}/resume-stage1 for a job sitting at
    status="stage1_needs_handoff". work_dir is whatever a human left it as
    after manually fixing the code that blocked Stage 1 -- see
    docs/superpowers/specs/2026-08-11-stage1-handoff-resume-design.md. Only
    ever reachable from stage1_needs_handoff, which (per docs/superpowers/
    specs/2026-08-11-job-status-stage-split-design.md §6) means Stage 2 --
    if it was requested at all -- has already run to completion, so this
    never needs to touch Stage 2."""
    emit, log = make_emit_log(session_factory, job_id)

    with session_factory() as session:
        job = session.get(Job, job_id)
        prior_report = job.report_markdown or ""

    work_dir = settings.jobs_dir / job_id / "work"
    output_dir = settings.jobs_dir / job_id / "output"
    handoff_dir = output_dir / "handoff"
    ingest_baseline = resolve_ingest_baseline(work_dir, settings)

    await set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        ok, build_output = await verify_after_manual_fix(work_dir, settings, on_log=log)

        if not ok:
            guide = build_handoff_guide(
                description="인수인계 후 수동 수정 확인",
                mechanism_used=None,
                messages=[],
                last_build_output=build_output,
                target_summary=TARGET_STACK_SUMMARY,
            )
            handoff_dir.mkdir(parents=True, exist_ok=True)
            (handoff_dir / "stage1-guide.md").write_text(guide, encoding="utf-8")
            await set_job_status(session_factory, job_id, "stage1_needs_handoff")
            await emit("status", {"status": "stage1_needs_handoff"})
            return

        # Commit the human's own fix as its own checkpoint -- without this it
        # sits uncommitted in the working tree, and if the very next Stage 1
        # step then fails, multi_step._run_one_step's rollback (reset to
        # current HEAD) would silently discard the human's fix along with
        # the AI's failed attempt. Confirmed live against job #44: an
        # earlier version of this function used current_head() here instead
        # and the manual fix only survived because the next step happened to
        # succeed on its first try.
        baseline = commit_checkpoint(work_dir, settings, "checkpoint: 인수인계 후 수동 수정 확인됨")

        effective_pom_path = output_dir / "effective-pom.xml"
        await mvn_effective_pom(
            work_dir, effective_pom_path, settings,
            log_path=build_log_path(output_dir, "stage1", "mvn-effective-pom-resume"),
        )
        detected = extract_versions(effective_pom_path)
        await log(
            f"재분석 결과: Java {detected.java_version} / Spring Boot {detected.spring_boot_version} / "
            f"Spring Cloud {detected.spring_cloud_version} / Spring AI {detected.spring_ai_version}"
        )

        stage1_result = await run_stage1_migration(job_id, work_dir, detected, baseline, settings, on_log=log)

        stage1_guide_path = handoff_dir / "stage1-guide.md"
        if stage1_result.status == "needs_handoff" and stage1_result.handoff_guide:
            handoff_dir.mkdir(parents=True, exist_ok=True)
            stage1_guide_path.write_text(stage1_result.handoff_guide, encoding="utf-8")
        elif stage1_guide_path.exists():
            # The first attempt's guide is now stale -- leaving it would make
            # output/handoff/ claim "still needs a manual fix" for a job that
            # just finished successfully.
            stage1_guide_path.unlink()

        await log("결과물 생성 중...")
        diff_text = diff_since(work_dir, settings, ingest_baseline)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")

        final_report = f"{prior_report}\n\n---\n\n{stage1_result.report}"
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        final_status = "stage1_needs_handoff" if stage1_result.status == "needs_handoff" else "success"
        await set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except asyncio.CancelledError:
        await _finalize_cancelled(job_id, settings, session_factory)
        raise
    except Exception as exc:  # noqa: BLE001 -- same rationale as run_pipeline
        await set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
