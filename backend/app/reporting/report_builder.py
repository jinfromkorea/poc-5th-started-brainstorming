"""Overall migration run report (spec: "리포트에는 각 단계에서 무엇을 올렸는지,
무엇이 막혀서 수동 개입이 필요한지... 포함").
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.orchestration.planning import MigrationPlan, PlanStep

StepStatus = Literal["success", "needs_handoff"]


@dataclass
class StepOutcome:
    step: PlanStep
    status: StepStatus


def build_report(plan: MigrationPlan, outcomes: list[StepOutcome], handoff_guide_path: Path | None) -> str:
    lines = ["# 마이그레이션 리포트", ""]

    if not outcomes and not plan.steps:
        lines.append("이미 목표 스택을 만족해 진행할 단계가 없습니다.")
    else:
        lines.append("## 진행된 단계")
        for outcome in outcomes:
            mark = "완료" if outcome.status == "success" else "중단"
            lines.append(f"- [{mark}] {outcome.step.description}")

    if handoff_guide_path is not None:
        lines.append("")
        lines.append("## 막힌 지점")
        lines.append(f"AI 인수인계 가이드: `{handoff_guide_path}`")

    return "\n".join(lines) + "\n"
