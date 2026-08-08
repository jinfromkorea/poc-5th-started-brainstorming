"""read_cache_status reads freshness straight off disk (Trivy's own
metadata.json, Dependency-Check's DB file mtime) -- independent of any Job
history. Covers both the present and absent cases for each cache."""

from __future__ import annotations

import json

from app.config import Settings
from app.orchestration.cache_status import read_cache_status


def _settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        dependency_check_data_dir=str(tmp_path / "nvd-cache"),
        trivy_cache_dir=str(tmp_path / "trivy-cache"),
    )


def test_both_absent_when_caches_never_populated(tmp_path):
    settings = _settings(tmp_path)
    result = read_cache_status(settings)
    assert result == {"nvd_last_updated_at": None, "trivy_last_updated_at": None}


def test_trivy_reads_updated_at_from_metadata_json(tmp_path):
    settings = _settings(tmp_path)
    db_dir = tmp_path / "trivy-cache" / "db"
    db_dir.mkdir(parents=True)
    (db_dir / "metadata.json").write_text(
        json.dumps({"Version": 2, "UpdatedAt": "2026-08-07T12:54:21Z", "DownloadedAt": "2026-08-07T15:09:38Z"}),
        encoding="utf-8",
    )

    result = read_cache_status(settings)
    assert result["trivy_last_updated_at"] == "2026-08-07T12:54:21Z"


def test_trivy_handles_malformed_metadata_json_gracefully(tmp_path):
    settings = _settings(tmp_path)
    db_dir = tmp_path / "trivy-cache" / "db"
    db_dir.mkdir(parents=True)
    (db_dir / "metadata.json").write_text("not json", encoding="utf-8")

    result = read_cache_status(settings)
    assert result["trivy_last_updated_at"] is None


def test_nvd_reads_mtime_from_db_file(tmp_path):
    settings = _settings(tmp_path)
    nvd_dir = tmp_path / "nvd-cache"
    nvd_dir.mkdir(parents=True)
    (nvd_dir / "odc.mv.db").write_text("fake db content", encoding="utf-8")

    result = read_cache_status(settings)
    assert result["nvd_last_updated_at"] is not None
    assert result["nvd_last_updated_at"].endswith("+00:00")  # UTC-aware ISO timestamp
