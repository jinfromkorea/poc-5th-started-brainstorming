"""Shared helper for building the environment dict passed to subprocesses
(git clone here in ingest; mvn/OpenRewrite/Trivy later). Settings values are
loaded via pydantic-settings from .env, NOT exported into os.environ
automatically -- so every external process call must explicitly merge them
in, or the proxy config in .env silently has no effect."""

from __future__ import annotations

import os
import shutil

from app.config import Settings


class ExecutableNotFoundError(Exception):
    pass


def resolve_executable(name: str) -> str:
    """Every subprocess call in this codebase must resolve the executable
    through this (or accept a pre-resolved path from it) rather than passing
    a bare name like "mvn" straight to subprocess.run/create_subprocess_exec.
    Confirmed empirically on Windows: neither the sync nor the async subprocess
    APIs apply PATHEXT resolution themselves -- "mvn" (really `mvn.cmd` on
    Windows) fails with WinError 2 unless resolved via shutil.which() first,
    which *does* do the same PATHEXT-aware search a shell would, on every
    platform. `git` happened to work unresolved on this dev machine only
    because Git for Windows ships a real git.EXE, not a .cmd wrapper -- not
    something to rely on for every tool/every developer's PATH."""
    resolved = shutil.which(name)
    if resolved is None:
        raise ExecutableNotFoundError(f"'{name}' not found on PATH")
    return resolved


def build_subprocess_env(settings: Settings) -> dict[str, str]:
    env = dict(os.environ)
    if settings.http_proxy:
        env["HTTP_PROXY"] = settings.http_proxy
        env["http_proxy"] = settings.http_proxy
    if settings.https_proxy:
        env["HTTPS_PROXY"] = settings.https_proxy
        env["https_proxy"] = settings.https_proxy
    if settings.no_proxy:
        env["NO_PROXY"] = settings.no_proxy
        env["no_proxy"] = settings.no_proxy
    return env
