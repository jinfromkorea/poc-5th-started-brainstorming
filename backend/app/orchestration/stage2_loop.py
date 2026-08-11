"""Outer loop over Stage 2's filtered vulnerability list (spec: "2단계
(옵션): 개별 CVE 패치"). Unlike Stage 1's sequential migration steps, CVE
patches are independent of each other -- one failing doesn't invalidate the
rest, so this loop does NOT stop at the first failure. Every vulnerability
gets attempted; each failure gets its own checkpoint rollback + handoff guide.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.checkpoint.git_repo import commit_checkpoint, diff_since, reset_to_checkpoint
from app.config import Settings
from app.handoff.guide_builder import build_handoff_guide
from app.orchestration.graph_stage2 import run_stage2_vulnerability
from app.orchestration.progress import LogFn, noop_log
from app.scan.merge import Vulnerability

TARGET_STACK_SUMMARY = "Java 21 / Spring Boot 4.1 / Spring Cloud 2025.1 / Spring AI 2"

VulnStatus = Literal["success", "needs_handoff"]


@dataclass
class VulnOutcome:
    vulnerability: Vulnerability
    status: VulnStatus
    handoff_guide: str | None = None


@dataclass
class Stage2RunResult:
    outcomes: list[VulnOutcome]
    final_diff: str
    report: str


async def run_stage2_patches(
    job_id: str,
    work_dir: Path,
    vulnerabilities: list[Vulnerability],
    baseline_commit: str,
    settings: Settings,
    on_log: LogFn = noop_log,
) -> Stage2RunResult:
    outcomes: list[VulnOutcome] = []
    last_good_sha = baseline_commit
    total = len(vulnerabilities)

    for idx, vuln in enumerate(vulnerabilities, 1):
        await on_log(f"[{idx}/{total}] {vuln.cve_id} ({vuln.package}, CVSS {vuln.cvss}) 패치 시도")
        result_state = await run_stage2_vulnerability(job_id, work_dir, vuln, settings, on_log=on_log)

        if result_state["status"] == "success":
            last_good_sha = commit_checkpoint(work_dir, settings, f"checkpoint: patch {vuln.cve_id} ({vuln.package})")
            outcomes.append(VulnOutcome(vulnerability=vuln, status="success"))
            await on_log(f"[{idx}/{total}] 완료, 체크포인트 저장")
            continue

        reset_to_checkpoint(work_dir, settings, last_good_sha)
        guide = build_handoff_guide(
            description=f"{vuln.cve_id} ({vuln.package} {vuln.installed_version})",
            mechanism_used=f"버전을 {vuln.fix_version}로 올림" if vuln.fix_version else None,
            messages=result_state.get("messages", []),
            last_build_output=result_state.get("last_build_output", ""),
            target_summary=TARGET_STACK_SUMMARY,
        )
        outcomes.append(VulnOutcome(vulnerability=vuln, status="needs_handoff", handoff_guide=guide))
        await on_log(f"[{idx}/{total}] 막힘 — AI 인수인계 가이드 생성됨")
        # deliberately no `break` here -- CVE patches are independent, keep going

    final_diff = diff_since(work_dir, settings, baseline_commit)
    report = _build_stage2_report(outcomes)

    return Stage2RunResult(outcomes=outcomes, final_diff=final_diff, report=report)


def _build_stage2_report(outcomes: list[VulnOutcome]) -> str:
    lines = ["# 2단계 취약점 패치 리포트", ""]
    if not outcomes:
        lines.append("패치 대상 취약점이 없습니다 (FAIL_ON_CVSS 임계값 이상인 항목 없음).")
        return "\n".join(lines) + "\n"

    lines.append("## 처리 결과")
    for outcome in outcomes:
        mark = "완료" if outcome.status == "success" else "중단"
        v = outcome.vulnerability
        lines.append(f"- [{mark}] {v.cve_id} — {v.package} {v.installed_version} (CVSS {v.cvss})")

    handoffs = [o for o in outcomes if o.handoff_guide]
    if handoffs:
        lines.append("")
        lines.append("## 수동 개입이 필요한 항목")
        for outcome in handoffs:
            lines.append(f"- {outcome.vulnerability.cve_id}: AI 인수인계 가이드 참고")

    return "\n".join(lines) + "\n"
