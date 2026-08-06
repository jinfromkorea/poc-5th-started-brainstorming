"""Trivy filesystem scan wrapper. Confirmed empirically: `trivy fs` detects
Maven dependencies directly from pom.xml files via its "pom" language
scanner -- no build/install needed first.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult, run_subprocess


async def run_trivy_scan(
    work_dir: Path,
    output_path: Path,
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    settings.trivy_cache_path.mkdir(parents=True, exist_ok=True)
    args = [
        "trivy",
        "fs",
        "--scanners",
        "vuln",
        "--format",
        "json",
        "--output",
        str(output_path),
        "--cache-dir",
        str(settings.trivy_cache_path),
        str(work_dir),
    ]
    return await run_subprocess(args, work_dir, settings, log_path=log_path, on_line=on_line)
