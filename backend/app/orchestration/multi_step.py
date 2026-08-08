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
from app.mvnrewrite.pom_parser import DetectedVersions
from app.orchestration.graph_stage1 import run_stage1_step
from app.orchestration.planning import MigrationPlan, build_migration_plan
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
    on_log: LogFn = noop_log,
) -> MigrationRunResult:
    plan = build_migration_plan(detected, target_boot=target_boot, target_java=target_java, target_ai=target_ai)

    if plan.steps:
        numbered = "\n".join(f"  {i}. {step.description}" for i, step in enumerate(plan.steps, 1))
        await on_log(f"마이그레이션 계획 수립: 총 {len(plan.steps)}단계\n{numbered}")

    outcomes: list[StepOutcome] = []
    handoff_guide: str | None = None
    status: RunStatus = "no_gap" if not plan.steps else "success"
    total = len(plan.steps)

    for idx, step in enumerate(plan.steps, 1):
        await on_log(f"[{idx}/{total}] {step.description} 시작")
        result_state = await run_stage1_step(job_id, work_dir, step, settings, on_log=on_log)

        if result_state["status"] == "success":
            commit_checkpoint(work_dir, settings, f"checkpoint: {step.description}")
            outcomes.append(StepOutcome(step=step, status="success"))
            await on_log(f"[{idx}/{total}] 완료, 체크포인트 저장")
            continue

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
        status = "needs_handoff"
        await on_log(f"[{idx}/{total}] 막힘 — AI 인수인계 가이드 생성됨")
        break

    final_diff = diff_since(work_dir, settings, baseline_commit)
    report = build_report(plan, outcomes, handoff_guide_path=Path("output/handoff") if handoff_guide else None)

    return MigrationRunResult(
        plan=plan,
        outcomes=outcomes,
        status=status,
        final_diff=final_diff,
        report=report,
        handoff_guide=handoff_guide,
    )
