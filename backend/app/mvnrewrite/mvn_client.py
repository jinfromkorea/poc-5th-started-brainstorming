"""Thin async wrappers around the `mvn` invocations this tool needs:
compile/test/verify (build gates), versions:set (output artifact version,
spec: "출력 아티팩트 버전 설정"), and help:effective-pom (version detection --
resolves the full local+remote parent/BOM inheritance chain, which a static
read of the project's own pom.xml alone cannot do when it inherits from an
external parent not present in the ingested source).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult, run_subprocess

_BATCH = ["mvn", "-B"]  # -B: batch mode, never prompts interactively


async def mvn_compile(
    work_dir: Path, settings: Settings, log_path: Path | None = None, on_line: Callable[[str], None] | None = None
) -> SubprocessResult:
    return await run_subprocess([*_BATCH, "compile"], work_dir, settings, log_path=log_path, on_line=on_line)


async def mvn_test_compile(
    work_dir: Path, settings: Settings, log_path: Path | None = None, on_line: Callable[[str], None] | None = None
) -> SubprocessResult:
    """Compiles both main AND test sources -- unlike mvn_compile above,
    which only compiles src/main -- without actually running the tests
    (spec: docs/superpowers/specs/2026-08-09-stage1-apply-verify-integrity-
    design.md). Used where a build-health check needs to catch a broken
    test source file (e.g. a stale import an OpenRewrite recipe didn't
    relocate) without the side effects of actually executing tests."""
    return await run_subprocess([*_BATCH, "test-compile"], work_dir, settings, log_path=log_path, on_line=on_line)


async def mvn_test(
    work_dir: Path, settings: Settings, log_path: Path | None = None, on_line: Callable[[str], None] | None = None
) -> SubprocessResult:
    return await run_subprocess([*_BATCH, "test"], work_dir, settings, log_path=log_path, on_line=on_line)


async def mvn_verify(
    work_dir: Path, settings: Settings, log_path: Path | None = None, on_line: Callable[[str], None] | None = None
) -> SubprocessResult:
    return await run_subprocess([*_BATCH, "verify"], work_dir, settings, log_path=log_path, on_line=on_line)


async def mvn_versions_set(
    work_dir: Path,
    new_version: str,
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    """org.codehaus.mojo:versions-maven-plugin -- groupId is one of Maven's
    default plugin groups, so the short goal name `versions:set` resolves
    without full coordinates (unlike OpenRewrite, see rewrite_client.py)."""
    return await run_subprocess(
        [*_BATCH, "versions:set", f"-DnewVersion={new_version}", "-DgenerateBackupPoms=false"],
        work_dir,
        settings,
        log_path=log_path,
        on_line=on_line,
    )


async def mvn_versions_set_property(
    work_dir: Path,
    property_name: str,
    new_version: str,
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    """versions:set-property -- bumps a single property's value directly.
    versions:set (above) reactor-propagates each module's own <version>, but
    never follows a property indirection like a dependencyManagement entry
    that references the reactor's own modules as a BOM/library via
    ${some.version} (e.g. ace-parent's ${ace.version}) -- that's left stale
    unless bumped separately. Called from versioning/artifact_version.py's
    apply_output_version, once per such property found."""
    return await run_subprocess(
        [*_BATCH, "versions:set-property", f"-Dproperty={property_name}", f"-DnewVersion={new_version}"],
        work_dir,
        settings,
        log_path=log_path,
        on_line=on_line,
    )


async def mvn_effective_pom(
    work_dir: Path,
    output_path: Path,
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> Path:
    """Writes the fully-resolved effective POM (parent chain + BOM property
    interpolation all resolved) to output_path and returns it. This is how
    version detection gets real values even when e.g. spring-boot.version is
    only declared in an external parent POM not present in the ingested
    source."""
    result = await run_subprocess(
        [*_BATCH, "help:effective-pom", f"-Doutput={output_path}"],
        work_dir,
        settings,
        log_path=log_path,
        on_line=on_line,
    )
    if result.returncode != 0 or not output_path.exists():
        raise RuntimeError(f"mvn help:effective-pom failed in {work_dir} (exit {result.returncode}):\n{result.output}")
    return output_path
