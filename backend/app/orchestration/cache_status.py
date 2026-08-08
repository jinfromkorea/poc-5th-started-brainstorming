"""Reads NVD/Trivy cache freshness straight off disk -- independent of Job
history, so it reflects the real cache state even if the server was
restarted or a refresh was run before this process started. See
cache_refresh.py for the code that actually updates these caches, and
docs/architecture.md's cache-refresh section for why scans no longer update
them implicitly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from app.config import Settings


def _trivy_last_updated_at(settings: Settings) -> str | None:
    """Trivy writes its own metadata.json with a real UpdatedAt timestamp --
    no need to approximate via file mtime."""
    metadata_path = settings.trivy_cache_path / "db" / "metadata.json"
    if not metadata_path.is_file():
        return None
    try:
        data = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data.get("UpdatedAt")


def _nvd_last_updated_at(settings: Settings) -> str | None:
    """Dependency-Check's H2 database has no equivalent metadata file, so
    this approximates "last updated" via the DB file's own mtime -- not
    exact, but the closest signal available without parsing the H2 file."""
    db_path = settings.dependency_check_dir / "odc.mv.db"
    if not db_path.is_file():
        return None
    return datetime.fromtimestamp(db_path.stat().st_mtime, tz=UTC).isoformat()


def read_cache_status(settings: Settings) -> dict:
    return {
        "nvd_last_updated_at": _nvd_last_updated_at(settings),
        "trivy_last_updated_at": _trivy_last_updated_at(settings),
    }
