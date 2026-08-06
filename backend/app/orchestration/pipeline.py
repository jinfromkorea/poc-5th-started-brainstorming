"""The actual end-to-end run: ingest -> (optional output version) -> Stage 1
-> Stage 2 -> diff/report/handoff files, with progress emitted throughout
via streaming.events.emit_event and the Job row's status kept current in
the DB. This is what concurrency.JobManager schedules per job.
"""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.checkpoint.git_repo import diff_since
from app.config import Settings
from app.ingest.errors import IngestError
from app.ingest.workspace import SourceSpec, ingest
from app.models.job import Job
from app.mvnrewrite.mvn_client import mvn_effective_pom
from app.mvnrewrite.pom_parser import extract_versions
from app.orchestration.multi_step import run_stage1_migration
from app.orchestration.stage2_loop import run_stage2_patches
from app.scan.combined import run_combined_scan
from app.streaming.events import emit_event
from app.versioning.artifact_version import apply_output_version


async def _set_job_status(
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


async def run_pipeline(
    job_id: str,
    spec: SourceSpec,
    output_version: str | None,
    run_stage1: bool,
    run_stage2: bool,
    settings: Settings,
    session_factory: sessionmaker[Session],
) -> None:
    async def emit(event_type: str, data: dict) -> None:
        await emit_event(session_factory, job_id, event_type, data)

    await _set_job_status(session_factory, job_id, "running")
    await emit("status", {"status": "running"})

    try:
        await emit("log", {"message": "인입 시작"})
        ingest_result = ingest(job_id, spec, settings)
        work_dir = ingest_result.paths.work
        output_dir = ingest_result.paths.output
        baseline = ingest_result.baseline_commit
        await emit("log", {"message": f"인입 완료 (모듈 {len(ingest_result.detection.modules)}개), baseline={baseline[:12]}"})

        if output_version:
            await emit("log", {"message": f"출력 아티팩트 버전 설정: {output_version}"})
            baseline = await apply_output_version(work_dir, output_version, settings)

        report_sections: list[str] = []
        handoff_dir = output_dir / "handoff"
        needs_handoff = False

        if run_stage1:
            await emit("log", {"message": "1단계 스택 마이그레이션 시작"})
            effective_pom_path = output_dir / "effective-pom.xml"
            await mvn_effective_pom(work_dir, effective_pom_path, settings)
            detected = extract_versions(effective_pom_path)

            stage1_result = await run_stage1_migration(job_id, work_dir, detected, baseline, settings)
            report_sections.append(stage1_result.report)
            await emit("log", {"message": f"1단계 종료: {stage1_result.status}"})

            if stage1_result.status == "needs_handoff" and stage1_result.handoff_guide:
                handoff_dir.mkdir(parents=True, exist_ok=True)
                (handoff_dir / "stage1-guide.md").write_text(stage1_result.handoff_guide, encoding="utf-8")
                needs_handoff = True

        if run_stage2:
            await emit("log", {"message": "2단계 취약점 스캔 시작"})
            vulns = await run_combined_scan(work_dir, output_dir, settings)
            await emit("log", {"message": f"{len(vulns)}개 취약점 발견 (임계값 이상)"})

            stage2_result = await run_stage2_patches(job_id, work_dir, vulns, baseline, settings)
            report_sections.append(stage2_result.report)
            await emit("log", {"message": "2단계 종료"})

            for outcome in stage2_result.outcomes:
                if outcome.status == "needs_handoff" and outcome.handoff_guide:
                    handoff_dir.mkdir(parents=True, exist_ok=True)
                    safe_cve = outcome.vulnerability.cve_id.replace("/", "_")
                    (handoff_dir / f"stage2-{safe_cve}-guide.md").write_text(
                        outcome.handoff_guide, encoding="utf-8"
                    )
                    needs_handoff = True

        diff_text = diff_since(work_dir, settings, ingest_result.baseline_commit)
        (output_dir / "patch.diff").write_text(diff_text, encoding="utf-8")

        final_report = "\n\n---\n\n".join(report_sections) if report_sections else "변경 사항 없음."
        (output_dir / "report.md").write_text(final_report, encoding="utf-8")

        final_status = "needs_handoff" if needs_handoff else "success"
        await _set_job_status(session_factory, job_id, final_status, report_markdown=final_report)
        await emit("status", {"status": final_status})

    except IngestError as exc:
        await _set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
    except Exception as exc:  # noqa: BLE001 -- a job failure must never crash the server process
        await _set_job_status(session_factory, job_id, "failed", error_message=str(exc))
        await emit("status", {"status": "failed", "error": str(exc)})
