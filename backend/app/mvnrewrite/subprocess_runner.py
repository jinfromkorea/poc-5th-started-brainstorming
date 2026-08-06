"""Generic async subprocess wrapper shared by every external tool this
backend shells out to (mvn, OpenRewrite, git, Trivy, Dependency-Check).
Streams combined stdout/stderr line-by-line to an optional callback (Phase 6
wires this to SSE) and an optional log file, and enforces a timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.procenv import build_subprocess_env, resolve_executable


class SubprocessTimeoutError(Exception):
    pass


@dataclass
class SubprocessResult:
    returncode: int
    output: str
    log_path: Path | None


async def run_subprocess(
    args: list[str],
    cwd: Path,
    settings: Settings,
    timeout_seconds: int | None = None,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
    extra_env: dict[str, str] | None = None,
) -> SubprocessResult:
    """Runs ``args`` in ``cwd`` with stdout+stderr merged into a single
    ordered stream (so a human/log file reading it sees output in the order
    the tool actually produced it). Every line is appended to ``log_path``
    (if given) and passed to ``on_line`` (if given) as it arrives -- not
    buffered until the process exits."""
    timeout = timeout_seconds if timeout_seconds is not None else settings.build_timeout_seconds
    env = build_subprocess_env(settings)
    if extra_env:
        env.update(extra_env)

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    executable = resolve_executable(args[0])
    proc = await asyncio.create_subprocess_exec(
        executable,
        *args[1:],
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    lines: list[str] = []
    log_file = log_path.open("w", encoding="utf-8") if log_path is not None else None

    async def _pump() -> None:
        assert proc.stdout is not None
        while True:
            raw = await proc.stdout.readline()
            if not raw:
                break
            line = raw.decode(errors="replace").rstrip("\n")
            lines.append(line)
            if log_file is not None:
                log_file.write(line + "\n")
                log_file.flush()
            if on_line is not None:
                on_line(line)

    try:
        await asyncio.wait_for(_pump(), timeout=timeout)
        returncode = await asyncio.wait_for(proc.wait(), timeout=5)
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise SubprocessTimeoutError(f"{' '.join(args)!r} timed out after {timeout}s in {cwd}") from exc
    finally:
        if log_file is not None:
            log_file.close()

    return SubprocessResult(returncode=returncode, output="\n".join(lines), log_path=log_path)
