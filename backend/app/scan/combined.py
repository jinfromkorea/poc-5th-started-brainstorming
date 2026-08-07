"""Runs OWASP Dependency-Check and Trivy in parallel (spec: "OWASP
Dependency-Check와 Trivy를 병렬로 실행"), then merges/de-dups/filters by
FAIL_ON_CVSS. The two tools draw from overlapping-but-different sources
(NVD vs GHSA/etc.), so running both improves detection coverage rather than
being redundant.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from app.config import Settings
from app.mvnrewrite.subprocess_runner import build_log_path
from app.scan.dependency_check import run_dependency_check
from app.scan.merge import (
    Vulnerability,
    merge_and_filter,
    parse_dependency_check_json,
    parse_trivy_json,
)
from app.scan.trivy import run_trivy_scan


def find_dependency_check_reports(work_dir: Path) -> list[Path]:
    """Dependency-Check ignores -DoutputDirectory across reactor modules
    (confirmed empirically) -- each module writes its own report to its own
    target/, so every one has to be found and combined."""
    return sorted(work_dir.glob("**/target/dependency-check-report.json"))


async def run_combined_scan(work_dir: Path, output_dir: Path, settings: Settings) -> list[Vulnerability]:
    trivy_output_path = output_dir / "trivy" / "trivy-report.json"

    dc_result, trivy_result = await asyncio.gather(
        run_dependency_check(work_dir, settings, log_path=build_log_path(output_dir, "scan", "dependency-check")),
        run_trivy_scan(work_dir, trivy_output_path, settings, log_path=build_log_path(output_dir, "scan", "trivy")),
    )

    dc_vulns: list[Vulnerability] = []
    if dc_result.returncode == 0:
        for report_path in find_dependency_check_reports(work_dir):
            dc_vulns.extend(parse_dependency_check_json(report_path))

    trivy_vulns = (
        parse_trivy_json(trivy_output_path) if trivy_result.returncode == 0 and trivy_output_path.exists() else []
    )

    return merge_and_filter(dc_vulns, trivy_vulns, min_cvss=settings.fail_on_cvss)
