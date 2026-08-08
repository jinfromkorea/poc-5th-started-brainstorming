"""OWASP Dependency-Check via the Maven plugin (spec: 2단계 스캔, NVD-based).
Confirmed empirically: the plugin resolves fine from Maven Central even
though the target project's own <repositories> may point at an unreachable
internal Nexus -- Central is tried regardless. The first run downloads the
full NVD dataset (hundreds of thousands of records) into DEPENDENCY_CHECK_DATA_DIR;
this is a one-time cost, cached for all future runs (spec: "DB 캐시").

Two more things confirmed empirically against a real multi-module reactor
(ace-parent) that were NOT obvious from documentation alone:

1. Running `dependency-check:check` as a bare goal (not bound to a
   lifecycle phase) fails on any module that depends on an earlier sibling
   reactor module -- e.g. ace-ai depends on ace-common, but ace-common was
   never actually compiled/installed, so Maven can't resolve it. Fix:
   run `install` first in the SAME invocation so the reactor's own modules
   get built and installed to the local repo before dependency-check scans
   anything that depends on them.
2. `-DoutputDirectory=<path>` is silently ignored across reactor modules --
   every module writes its own report to its own `target/dependency-check-report.json`
   regardless of what was passed. So report discovery has to glob for
   `**/target/dependency-check-report.json` under work_dir afterward
   (see scan/combined.py), not trust a single predicted output path.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult, run_subprocess

# Pinned to a specific plugin version (unlike OpenRewrite's, which floats to
# RELEASE) -- dependency-check's CLI flags/report shape are what we parse
# directly in merge.py, so an unpinned version drifting out from under us
# would silently break parsing rather than fail loudly like the OpenRewrite
# plugin/recipe mismatch did.
DEPENDENCY_CHECK_PLUGIN_VERSION = "12.1.9"


async def run_dependency_check(
    work_dir: Path,
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    """Reports land at ``**/target/dependency-check-report.json`` under
    work_dir (one per reactor module) -- use
    ``scan.combined.find_dependency_check_reports`` to locate them afterward."""
    settings.dependency_check_dir.mkdir(parents=True, exist_ok=True)
    args = [
        "mvn",
        "-B",
        "install",
        "-DskipTests",
        f"org.owasp:dependency-check-maven:{DEPENDENCY_CHECK_PLUGIN_VERSION}:check",
        "-Dformat=JSON",
        f"-DdataDirectory={settings.dependency_check_dir}",
        # NVD updates happen only via the explicit cache-refresh flow
        # (orchestration/cache_refresh.run_cache_refresh), same rationale as
        # trivy.py's --skip-db-update.
        "-DautoUpdate=false",
    ]
    if settings.nvd_api_key:
        args.append(f"-DnvdApiKey={settings.nvd_api_key}")
    return await run_subprocess(args, work_dir, settings, log_path=log_path, on_line=on_line)



# A cold NVD cache's first full sync can take 30+ minutes (backend/README.md
# §5) -- BUILD_TIMEOUT_SECONDS (900s/15min default) is sized for a target
# project's mvn build, not this. Use a generous fixed ceiling instead.
NVD_UPDATE_TIMEOUT_SECONDS = 3600


async def run_dependency_check_update_only(
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    """Refreshes the NVD cache only, independent of any project. Confirmed
    empirically: run from a directory with no pom.xml, Maven auto-generates
    a "standalone-pom" stub and the goal runs fine -- no dummy pom.xml needed."""
    settings.dependency_check_dir.mkdir(parents=True, exist_ok=True)
    settings.jobs_dir.mkdir(parents=True, exist_ok=True)
    args = [
        "mvn",
        "-B",
        f"org.owasp:dependency-check-maven:{DEPENDENCY_CHECK_PLUGIN_VERSION}:update-only",
        f"-DdataDirectory={settings.dependency_check_dir}",
    ]
    if settings.nvd_api_key:
        args.append(f"-DnvdApiKey={settings.nvd_api_key}")
    return await run_subprocess(
        args, settings.jobs_dir, settings, timeout_seconds=NVD_UPDATE_TIMEOUT_SECONDS, log_path=log_path, on_line=on_line
    )
