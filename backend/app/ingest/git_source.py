"""Git ingest: shallow clone of a single branch/ref directly into source/."""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from app.config import Settings
from app.ingest.errors import GitCloneError
from app.procenv import build_subprocess_env, resolve_executable

logger = logging.getLogger(__name__)


def clone_git(url: str, ref: str | None, dest_dir: Path, settings: Settings, log_path: Path | None = None) -> Path:
    """``git clone --depth 1 [--branch {ref}] {url} {dest_dir}``. dest_dir
    must not already exist (git clone creates it). If ``log_path`` is given,
    the clone's combined stdout+stderr is written there regardless of
    success/failure -- useful for debugging a failed clone after the fact,
    same as every other external tool's log under output/logs/."""
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise GitCloneError(f"destination already exists and is non-empty: {dest_dir}")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = [resolve_executable("git"), "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest_dir)]

    logger.info("실행: %s", " ".join(cmd))
    started_at = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",  # not locale.getpreferredencoding() (cp949 on Korean
            errors="replace",  # Windows) -- see checkpoint/git_repo.py's _run_git for why
            timeout=settings.build_timeout_seconds,
            env=build_subprocess_env(settings),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        logger.warning("시간 초과: %s (%ds 경과)", " ".join(cmd), settings.build_timeout_seconds)
        raise GitCloneError(f"git clone timed out after {settings.build_timeout_seconds}s: {url}") from exc

    elapsed = time.monotonic() - started_at
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")

    if proc.returncode != 0:
        logger.warning("종료: exit=%s, %.1fs", proc.returncode, elapsed)
        raise GitCloneError(f"git clone failed for {url!r} (ref={ref!r}): {proc.stderr.strip()}")

    logger.info("종료: exit=0, %.1fs", elapsed)
    return dest_dir
