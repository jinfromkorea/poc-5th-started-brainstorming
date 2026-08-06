"""Outer loop over Stage 1's full migration plan (spec: "1단계: 스택
마이그레이션" end-to-end). For each planned step: run the single-step graph
(graph_stage1) -> on success, checkpoint-commit and move to the next step;
on failure, roll back to the last checkpoint, build the AI handoff guide,
and STOP (sequential migration -- later steps assume earlier ones already
landed, so there's no point attempting them once one has failed).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.checkpoint.git_repo import commit_checkpoint, diff_since, reset_to_checkpoint
from app.config import Settings
from app.handoff.guide_builder import build_handoff_guide
from app.mvnrewrite.pom_parser import DetectedVersions
from app.orchestration.graph_stage1 import run_stage1_step
from app.orchestration.planning import MigrationPlan, build_migration_plan
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
) -> MigrationRunResult:
    plan = build_migration_plan(detected, target_boot=target_boot, target_java=target_java, target_ai=target_ai)

    outcomes: list[StepOutcome] = []
    last_good_sha = baseline_commit
    handoff_guide: str | None = None
    status: RunStatus = "no_gap" if not plan.steps else "success"

    for step in plan.steps:
        result_state = await run_stage1_step(job_id, work_dir, step, settings)

        if result_state["status"] == "success":
            last_good_sha = commit_checkpoint(work_dir, settings, f"checkpoint: {step.description}")
            outcomes.append(StepOutcome(step=step, status="success"))
            continue

        # needs_handoff: undo this step's half-applied edits, stop the
        # sequential migration here, and hand the human a ready-to-paste guide.
        reset_to_checkpoint(work_dir, settings, last_good_sha)
        outcomes.append(StepOutcome(step=step, status="needs_handoff"))
        handoff_guide = build_handoff_guide(
            description=step.description,
            mechanism_used=step.recipe,
            messages=result_state.get("messages", []),
            last_build_output=result_state.get("last_build_output", ""),
            target_summary=TARGET_STACK_SUMMARY,
        )
        status = "needs_handoff"
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
