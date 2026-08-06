"""Git ingest: shallow clone of a single branch/ref directly into source/."""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import Settings
from app.ingest.errors import GitCloneError
from app.procenv import build_subprocess_env, resolve_executable


def clone_git(url: str, ref: str | None, dest_dir: Path, settings: Settings) -> Path:
    """``git clone --depth 1 [--branch {ref}] {url} {dest_dir}``. dest_dir
    must not already exist (git clone creates it)."""
    if dest_dir.exists() and any(dest_dir.iterdir()):
        raise GitCloneError(f"destination already exists and is non-empty: {dest_dir}")
    dest_dir.parent.mkdir(parents=True, exist_ok=True)

    cmd = [resolve_executable("git"), "clone", "--depth", "1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [url, str(dest_dir)]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=settings.build_timeout_seconds,
            env=build_subprocess_env(settings),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitCloneError(f"git clone timed out after {settings.build_timeout_seconds}s: {url}") from exc

    if proc.returncode != 0:
        raise GitCloneError(f"git clone failed for {url!r} (ref={ref!r}): {proc.stderr.strip()}")

    return dest_dir
