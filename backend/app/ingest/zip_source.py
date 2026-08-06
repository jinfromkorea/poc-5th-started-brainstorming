"""ZIP ingest: extract with zip-bomb / path-traversal guards, then unwrap a
single top-level wrapping folder (e.g. GitHub-style ``repo-main/...`` zips)."""

from __future__ import annotations

import shutil
import time
import zipfile
from pathlib import Path

from app.config import Settings
from app.ingest.errors import (
    ExtractedTooLargeError,
    PathTraversalError,
    TooManyFilesError,
    UploadTooLargeError,
)

_BYTES_PER_MB = 1024 * 1024


def _safe_member_path(dest_dir: Path, member_name: str) -> Path:
    """Resolve a zip member's target path and guarantee it stays inside
    dest_dir -- rejects absolute paths, ``..`` segments, and symlink-style
    escapes via realpath comparison."""
    target = (dest_dir / member_name).resolve()
    if dest_dir.resolve() not in target.parents and target != dest_dir.resolve():
        raise PathTraversalError(f"zip entry escapes extraction directory: {member_name!r}")
    return target


def extract_zip(zip_path: Path, dest_dir: Path, settings: Settings) -> Path:
    """Extract ``zip_path`` into ``dest_dir`` and return dest_dir. Validates
    upload size, extracted size, and file count against settings BEFORE
    writing any bytes (a cheap metadata-only pre-scan), so a rejected upload
    never partially extracts."""
    upload_max_bytes = settings.upload_max_mb * _BYTES_PER_MB
    extracted_max_bytes = settings.upload_max_extracted_mb * _BYTES_PER_MB

    upload_size = zip_path.stat().st_size
    if upload_size > upload_max_bytes:
        raise UploadTooLargeError(
            f"upload is {upload_size / _BYTES_PER_MB:.1f}MB, exceeds UPLOAD_MAX_MB={settings.upload_max_mb}"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()

        file_count = sum(1 for i in infos if not i.is_dir())
        if file_count > settings.upload_max_files:
            raise TooManyFilesError(
                f"zip contains {file_count} files, exceeds UPLOAD_MAX_FILES={settings.upload_max_files}"
            )

        total_extracted = sum(i.file_size for i in infos)
        if total_extracted > extracted_max_bytes:
            raise ExtractedTooLargeError(
                f"extracted size would be {total_extracted / _BYTES_PER_MB:.1f}MB, "
                f"exceeds UPLOAD_MAX_EXTRACTED_MB={settings.upload_max_extracted_mb}"
            )

        # Path-safety pre-check for every member before writing anything.
        for info in infos:
            _safe_member_path(dest_dir, info.filename)

        for info in infos:
            target = _safe_member_path(dest_dir, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    return dest_dir


def unwrap_single_top_level(dest_dir: Path) -> None:
    """If dest_dir contains exactly one entry and it's a directory (the
    common ``repo-main/...`` GitHub zip shape), hoist its contents up into
    dest_dir and remove the now-empty wrapper."""
    entries = list(dest_dir.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        return

    wrapper = entries[0]
    for child in list(wrapper.iterdir()):
        shutil.move(str(child), str(dest_dir / child.name))
    _rmdir_with_retry(wrapper)


def _rmdir_with_retry(path: Path, attempts: int = 20, delay_seconds: float = 0.3) -> None:
    """On Windows, a directory that was just heavily written to (many freshly
    extracted files just moved out of it) can transiently fail to rmdir with
    WinError 32 ("used by another process") -- typically Defender/the search
    indexer briefly holding a handle. A short retry clears this without
    weakening the traversal/size checks that matter for correctness."""
    last_error: OSError | None = None
    for _ in range(attempts):
        try:
            path.rmdir()
            return
        except OSError as exc:
            last_error = exc
            time.sleep(delay_seconds)
    assert last_error is not None
    raise last_error
