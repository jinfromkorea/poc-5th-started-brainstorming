"""work/ is managed as its own git repository so that failed retry attempts
can be cleanly rolled back and the final diff/patch can be produced from git
history (spec: "체크포인트/롤백 (git 기반)"): baseline commit at ingest time,
one checkpoint commit per successfully-verified migration step, reset-to-
last-checkpoint on exhausted retries, and the final diff/patch is just
`git diff <baseline>..HEAD` -- since only verified steps ever get committed,
the diff naturally contains only verified changes.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.config import Settings
from app.procenv import build_subprocess_env, resolve_executable

logger = logging.getLogger(__name__)


class GitCheckpointError(Exception):
    pass


def _run_git(work_dir: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    # encoding="utf-8" (not the default locale.getpreferredencoding(), which
    # is cp949 on Korean Windows) -- git's own output is UTF-8 regardless of
    # OS locale, and cp949 can't decode arbitrary UTF-8 bytes, which
    # otherwise crashes Popen's Windows-only background reader thread
    # (confirmed empirically: UnicodeDecodeError in Lib/subprocess.py's
    # _readerthread). errors="replace" so a still-unexpected byte degrades
    # to a garbled character instead of crashing the whole call.
    executable = resolve_executable("git")
    logger.info("실행: %s (cwd=%s)", " ".join([executable, *args]), work_dir)
    proc = subprocess.run(
        [executable, *args],
        cwd=work_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise GitCheckpointError(f"git {' '.join(args)} failed in {work_dir}: {proc.stderr.strip()}")
    return proc


def changed_file_count(work_dir: Path, settings: Settings) -> int:
    """Count of files changed (uncommitted) since the last checkpoint
    commit. Used for the "자동 적용 범위 제한" gate: if an AI fix attempt
    touches more files than COMPILE_FIX_AUTO_APPLY_MAX_FILES, the pipeline
    hands off to a human instead of continuing to retry automatically."""
    env = build_subprocess_env(settings)
    proc = _run_git(work_dir, ["diff", "--name-only", "HEAD"], env)
    return len([line for line in proc.stdout.splitlines() if line.strip()])


def _commit_env(settings: Settings) -> dict[str, str]:
    env = build_subprocess_env(settings)
    return {
        **env,
        "GIT_AUTHOR_NAME": settings.git_author_name,
        "GIT_AUTHOR_EMAIL": settings.git_author_email,
        "GIT_COMMITTER_NAME": settings.git_author_name,
        "GIT_COMMITTER_EMAIL": settings.git_author_email,
    }


def _head(work_dir: Path, env: dict[str, str]) -> str:
    return _run_git(work_dir, ["rev-parse", "HEAD"], env).stdout.strip()


def current_head(work_dir: Path, settings: Settings) -> str:
    """Public wrapper for reading the current HEAD sha (e.g. right after
    git_init_and_baseline_commit, to capture the baseline sha for later use)."""
    return _head(work_dir, build_subprocess_env(settings))


def git_init_and_baseline_commit(work_dir: Path, settings: Settings) -> str:
    """git init + commit everything currently in work_dir as the baseline,
    authored by the tool's own bot identity (not the developer running it,
    and not whatever identity the target project's original history had --
    work/ always starts a fresh, minimal history of its own). Returns the
    baseline commit hash."""
    env = build_subprocess_env(settings)
    _run_git(work_dir, ["init", "-q"], env)
    _run_git(work_dir, ["add", "-A"], env)
    _run_git(work_dir, ["commit", "-q", "-m", "baseline: ingest snapshot", "--allow-empty"], _commit_env(settings))
    return _head(work_dir, env)


def commit_checkpoint(work_dir: Path, settings: Settings, message: str) -> str:
    """Commit the current state of work_dir as a checkpoint after a
    migration step has been verified (spec: "한 단계가 검증을 통과하면 그
    상태를 커밋한다"). Returns the new commit hash. Safe to call even if
    the step made no file changes (--allow-empty), since a step can
    legitimately be a no-op (e.g. a recipe that found nothing to change)."""
    env = build_subprocess_env(settings)
    _run_git(work_dir, ["add", "-A"], env)
    _run_git(work_dir, ["commit", "-q", "-m", message, "--allow-empty"], _commit_env(settings))
    return _head(work_dir, env)


def reset_to_checkpoint(work_dir: Path, settings: Settings, checkpoint_sha: str) -> None:
    """Discard everything since checkpoint_sha, including uncommitted
    changes (spec: "재시도 한도까지 실패하면... git reset --hard로 마지막
    체크포인트까지 되돌리고"). Destructive by design -- this is exactly the
    mechanism that keeps a failed step's half-applied edits out of the final
    diff."""
    env = build_subprocess_env(settings)
    _run_git(work_dir, ["reset", "--hard", checkpoint_sha], env)
    _run_git(work_dir, ["clean", "-fd"], env)  # also remove untracked files the failed attempt created


def diff_since(work_dir: Path, settings: Settings, baseline_sha: str) -> str:
    """Unified diff of every verified change: baseline..HEAD. Only
    checkpoint commits ever land on HEAD (failed attempts are reset away),
    so this is automatically "verified changes only" without extra filtering."""
    env = build_subprocess_env(settings)
    return _run_git(work_dir, ["diff", baseline_sha, "HEAD"], env).stdout


def log_since(work_dir: Path, settings: Settings, baseline_sha: str) -> str:
    """One-line-per-checkpoint history since baseline -- a quick "what
    happened, in order" summary for the report, independent of the full diff."""
    env = build_subprocess_env(settings)
    return _run_git(work_dir, ["log", "--oneline", f"{baseline_sha}..HEAD"], env).stdout
