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
import os
import stat
import subprocess
from pathlib import Path

from app.config import Settings
from app.procenv import build_subprocess_env, resolve_executable

logger = logging.getLogger(__name__)


def rmtree_clear_readonly(func, path, _exc_info) -> None:
    """shutil.rmtree callback: git marks committed .git/objects/** files
    read-only, which raises PermissionError on Windows when os.unlink tries
    to remove them. Clear the flag and retry once. Shared by any caller that
    deletes a directory that may contain a git repo (job deletion, the
    artifact-version pre-submission peek)."""
    os.chmod(path, stat.S_IWRITE)
    func(path)


class GitCheckpointError(Exception):
    pass


# Tool-generated files that are never part of the actual migration and must
# never end up in the diff -- e.g. Maven Versions Plugin's own backup copy of
# a pom it just rewrote (org.codehaus.mojo:versions-maven-plugin defaults to
# <file>.versionsBackup; only mvn_client.py's own versions:set call opts out
# via -DgenerateBackupPoms=false, but dependency_patch.py's versions:set-property
# / versions:use-dep-version calls -- used by Stage 2's CVE fixes -- don't, so
# these backups can still appear on disk mid-run).
_GITIGNORE_EXTRA_LINES = ["*.versionsBackup"]


def _ensure_gitignore(work_dir: Path, lines: list[str]) -> None:
    """Appends any of `lines` missing from work_dir/.gitignore (creating the
    file if the ingested project didn't ship one), so that git add -A -- at
    baseline and every checkpoint after it -- never picks these files up."""
    gitignore_path = work_dir / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8").splitlines() if gitignore_path.exists() else []
    missing = [line for line in lines if line not in existing]
    if not missing:
        return
    with gitignore_path.open("a", encoding="utf-8") as f:
        if existing and existing[-1] != "":
            f.write("\n")
        f.write("\n".join(missing) + "\n")


def _run_git(work_dir: Path, args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    # encoding="utf-8" (not the default locale.getpreferredencoding(), which
    # is cp949 on Korean Windows) -- git's own output is UTF-8 regardless of
    # OS locale, and cp949 can't decode arbitrary UTF-8 bytes, which
    # otherwise crashes Popen's Windows-only background reader thread
    # (confirmed empirically: UnicodeDecodeError in Lib/subprocess.py's
    # _readerthread). errors="replace" so a still-unexpected byte degrades
    # to a garbled character instead of crashing the whole call.
    executable = resolve_executable("git")
    logger.info("실행: %s", " ".join([executable, *args]))
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
    work/ always starts a fresh, minimal history of its own). Also patches
    work_dir/.gitignore with entries for known tool-generated backup files
    (see _GITIGNORE_EXTRA_LINES) before the very first add, so nothing ever
    commits them, even from checkpoints made later in the pipeline. Returns
    the baseline commit hash."""
    env = build_subprocess_env(settings)
    _run_git(work_dir, ["init", "-q"], env)
    _ensure_gitignore(work_dir, _GITIGNORE_EXTRA_LINES)
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


def _ordered_shas(work_dir: Path, settings: Settings) -> list[str]:
    env = build_subprocess_env(settings)
    return _run_git(work_dir, ["log", "--reverse", "--format=%H"], env).stdout.split()


def resolve_ingest_baseline(work_dir: Path, settings: Settings) -> str:
    """The very first commit ever made in work_dir's history -- always equal
    to IngestResult.baseline_commit, regardless of whether an output_version
    checkpoint was later added on top. Used to recover that value when
    resuming Stage 2 after a HITL approval pause (orchestration/pipeline.py's
    run_pipeline_resume_stage2), since it isn't otherwise persisted anywhere."""
    return _ordered_shas(work_dir, settings)[0]


def list_tracked_files(work_dir: Path, settings: Settings) -> list[str]:
    """git ls-files result: every file tracked at HEAD (work_dir's own
    .gitignore is already reflected here). Used by the artifact file-tree
    viewer (spec: docs/superpowers/specs/2026-08-10-artifact-file-tree-
    viewer-design.md) to enumerate what to show without walking the
    filesystem directly."""
    env = build_subprocess_env(settings)
    return [line for line in _run_git(work_dir, ["ls-files"], env).stdout.splitlines() if line.strip()]


def diff_status_map(work_dir: Path, settings: Settings, baseline_sha: str) -> dict[str, str]:
    """Path -> raw git status code ("A" or "M") for every file added/modified
    between baseline and HEAD. --no-renames simplifies a rename into a
    delete+add pair (the file-tree viewer only needs per-file status badges,
    not rename tracking). Deleted paths ("D") are omitted from the result --
    a deleted path doesn't exist at HEAD, so it's already absent from
    list_tracked_files() too, and the tree viewer doesn't show deleted
    files."""
    env = build_subprocess_env(settings)
    out = _run_git(work_dir, ["diff", "--name-status", "--no-renames", baseline_sha, "HEAD"], env).stdout
    result: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            continue
        status_code, path = line.split("\t", 1)
        if status_code in ("A", "M"):
            result[path] = status_code
    return result


def show_file_bytes(work_dir: Path, settings: Settings, ref: str, path: str) -> bytes | None:
    """Raw bytes of git show {ref}:{path}. Returns None if path didn't exist
    at that ref (e.g. the baseline-time content of a file added later, or
    vice versa). Uses a separate bytes-mode subprocess call rather than
    _run_git (which is text-mode) so binary file content isn't corrupted by
    forced UTF-8 decoding."""
    env = build_subprocess_env(settings)
    executable = resolve_executable("git")
    proc = subprocess.run(
        [executable, "show", f"{ref}:{path}"], cwd=work_dir, capture_output=True, env=env, check=False
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def log_since(work_dir: Path, settings: Settings, baseline_sha: str) -> str:
    """One-line-per-checkpoint history since baseline -- a quick "what
    happened, in order" summary for the report, independent of the full diff."""
    env = build_subprocess_env(settings)
    return _run_git(work_dir, ["log", "--oneline", f"{baseline_sha}..HEAD"], env).stdout
