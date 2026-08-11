"""Outer loop over Stage 1's full migration plan (spec: "1단계: 스택
마이그레이션" end-to-end). For each planned step: run the single-step graph
(graph_stage1) -> on success, checkpoint-commit and move to the next step;
on failure, discard only the AI-fix agent's uncommitted edit attempts (the
OpenRewrite recipe's own changes were already committed inside apply_node
and survive), build the AI handoff guide, and STOP (sequential migration --
later steps assume earlier ones already landed, so there's no point
attempting them once one has failed).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.checkpoint.git_repo import commit_checkpoint, current_head, diff_since, reset_to_checkpoint
from app.config import Settings
from app.handoff.guide_builder import build_handoff_guide
from app.mvnrewrite.mvn_client import mvn_effective_pom
from app.mvnrewrite.pom_parser import DetectedVersions, extract_versions
from app.mvnrewrite.subprocess_runner import build_log_path
from app.orchestration.graph_stage1 import run_stage1_step
from app.orchestration.planning import MigrationPlan, PlanStep, build_migration_plan
from app.orchestration.progress import LogFn, noop_log
from app.reporting.report_builder import StepOutcome, build_report

TARGET_STACK_SUMMARY = "Java 21 / Spring Boot 4.1 / Spring Cloud 2025.1 / Spring AI 2.0"

RunStatus = Literal["success", "needs_handoff", "no_gap"]


@dataclass
class MigrationRunResult:
    plan: MigrationPlan
    outcomes: list[StepOutcome]
    status: RunStatus
    final_diff: str
    report: str
    handoff_guide: str | None  # markdown content, only set when status == "needs_handoff"


async def run_stage1_migration(
    job_id: str,
    work_dir: Path,
    detected: DetectedVersions,
    baseline_commit: str,
    settings: Settings,
    target_boot: str = "4.1",
    target_java: str = "21",
    target_ai: str = "2.0",
    parent_target_version: str | None = None,
    on_log: LogFn = noop_log,
) -> MigrationRunResult:
    # graph_stage1.apply_node derives output_dir the same way -- not taken as
    # a parameter here so existing callers (and their tests) don't break.
    output_dir = work_dir.parent / "output"
    outcomes: list[StepOutcome] = []
    all_steps: list[PlanStep] = []
    handoff_guide: str | None = None

    async def _run_one_step(idx: int, total: int, step) -> bool:
        """Runs one step through graph_stage1, records its outcome, and
        returns whether it succeeded. Shared by the optional parent_pom step
        below and the main per-step loop further down so both behave
        identically (commit/rollback/handoff-guide construction)."""
        nonlocal handoff_guide
        await on_log(f"[{idx}/{total}] {step.description} 시작")
        result_state = await run_stage1_step(job_id, work_dir, step, settings, on_log=on_log)

        if result_state["status"] == "success":
            commit_checkpoint(work_dir, settings, f"checkpoint: {step.description}")
            outcomes.append(StepOutcome(step=step, status="success"))
            await on_log(f"[{idx}/{total}] 완료, 체크포인트 저장")
            return True

        # needs_handoff: undo only what's still *uncommitted* -- graph_stage1's
        # apply_node already committed the OpenRewrite recipe's own changes as
        # soon as it ran, so resetting to current HEAD (not last_good_sha, the
        # *previous* step's checkpoint) discards just the AI-fix agent's failed
        # edit attempts on top, keeping the recipe's mechanical progress instead
        # of throwing it away along with the unrelated failure that followed it.
        reset_to_checkpoint(work_dir, settings, current_head(work_dir, settings))
        outcomes.append(StepOutcome(step=step, status="needs_handoff"))
        handoff_guide = build_handoff_guide(
            description=step.description,
            mechanism_used=step.recipe,
            messages=result_state.get("messages", []),
            last_build_output=result_state.get("last_build_output", ""),
            target_summary=TARGET_STACK_SUMMARY,
        )
        await on_log(f"[{idx}/{total}] 막힘 — AI 인수인계 가이드 생성됨")
        return False

    if parent_target_version:
        parent_step = PlanStep(
            kind="parent_pom",
            description=f"사내 parent POM 버전을 {parent_target_version}로 교체",
            recipe=None,
            artifact=None,
            target_version=parent_target_version,
        )
        all_steps.append(parent_step)
        if not await _run_one_step(1, 1, parent_step):
            final_diff = diff_since(work_dir, settings, baseline_commit)
            report = build_report(MigrationPlan(steps=all_steps), outcomes, handoff_guide_path=Path("output/handoff"))
            return MigrationRunResult(
                plan=MigrationPlan(steps=all_steps),
                outcomes=outcomes,
                status="needs_handoff",
                final_diff=final_diff,
                report=report,
                handoff_guide=handoff_guide,
            )

        # The plan built from Stage 0's (now stale) detected versions would
        # redo/misjudge whatever the new parent already brought -- re-derive
        # the actual current stack before planning the rest (spec: docs/
        # superpowers/specs/2026-08-11-internal-parent-pom-target-version-
        # design.md).
        await on_log("[사내 parent POM] 완료 — 스택 재분석 중")
        effective_pom_path = output_dir / "effective-pom.xml"
        await mvn_effective_pom(
            work_dir,
            effective_pom_path,
            settings,
            log_path=build_log_path(output_dir, "stage1", "mvn-effective-pom-post-parent"),
        )
        detected = extract_versions(effective_pom_path)
        await on_log(
            f"재분석 결과: Java {detected.java_version} / Spring Boot {detected.spring_boot_version} / "
            f"Spring Cloud {detected.spring_cloud_version} / Spring AI {detected.spring_ai_version}"
        )

    plan = build_migration_plan(detected, target_boot=target_boot, target_java=target_java, target_ai=target_ai)
    all_steps.extend(plan.steps)

    if plan.steps:
        numbered = "\n".join(f"  {i}. {step.description}" for i, step in enumerate(plan.steps, 1))
        await on_log(f"마이그레이션 계획 수립: 총 {len(plan.steps)}단계\n{numbered}")

    # parent_target_version having run at all is itself real progress, even
    # if the remaining plan turns out empty (parent alone reached target) --
    # that's "success", not "no_gap" (nothing to do at all).
    status: RunStatus = "success" if (parent_target_version or plan.steps) else "no_gap"
    total = len(plan.steps)

    for idx, step in enumerate(plan.steps, 1):
        if not await _run_one_step(idx, total, step):
            status = "needs_handoff"
            break

    final_diff = diff_since(work_dir, settings, baseline_commit)
    report = build_report(
        MigrationPlan(steps=all_steps), outcomes, handoff_guide_path=Path("output/handoff") if handoff_guide else None
    )

    return MigrationRunResult(
        plan=MigrationPlan(steps=all_steps),
        outcomes=outcomes,
        status=status,
        final_diff=final_diff,
        report=report,
        handoff_guide=handoff_guide,
    )
