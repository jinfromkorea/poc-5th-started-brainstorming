from __future__ import annotations

import zipfile

import pytest

from app.config import Settings
from app.ingest.errors import (
    ExtractedTooLargeError,
    PathTraversalError,
    TooManyFilesError,
    UploadTooLargeError,
)
from app.ingest.zip_source import extract_zip, unwrap_single_top_level


def _make_zip(path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def test_extracts_simple_zip(tmp_path, settings):
    zip_path = tmp_path / "src.zip"
    _make_zip(zip_path, {"pom.xml": b"<project/>", "src/Main.java": b"class Main {}"})
    dest = tmp_path / "out"

    extract_zip(zip_path, dest, settings)

    assert (dest / "pom.xml").read_bytes() == b"<project/>"
    assert (dest / "src" / "Main.java").read_bytes() == b"class Main {}"


def test_rejects_path_traversal_entry(tmp_path, settings):
    zip_path = tmp_path / "evil.zip"
    _make_zip(zip_path, {"../../evil.txt": b"pwned"})
    dest = tmp_path / "out"

    with pytest.raises(PathTraversalError):
        extract_zip(zip_path, dest, settings)

    # Must not have written the escaping file outside dest.
    assert not (tmp_path / "evil.txt").exists()


def test_rejects_upload_over_size_limit(tmp_path, settings):
    settings.upload_max_mb = 0  # anything is "too large"
    zip_path = tmp_path / "src.zip"
    _make_zip(zip_path, {"pom.xml": b"<project/>"})

    with pytest.raises(UploadTooLargeError):
        extract_zip(zip_path, tmp_path / "out", settings)


def test_rejects_too_many_files(tmp_path, settings):
    settings.upload_max_files = 1
    zip_path = tmp_path / "src.zip"
    _make_zip(zip_path, {"a.txt": b"1", "b.txt": b"2"})

    with pytest.raises(TooManyFilesError):
        extract_zip(zip_path, tmp_path / "out", settings)


def test_rejects_extracted_size_over_limit(tmp_path, settings):
    settings.upload_max_extracted_mb = 0
    zip_path = tmp_path / "src.zip"
    _make_zip(zip_path, {"a.txt": b"some bytes"})

    with pytest.raises(ExtractedTooLargeError):
        extract_zip(zip_path, tmp_path / "out", settings)


def test_unwrap_single_top_level_folder(tmp_path):
    dest = tmp_path / "out"
    wrapper = dest / "repo-main"
    (wrapper / "src").mkdir(parents=True)
    (wrapper / "pom.xml").write_text("<project/>")
    (wrapper / "src" / "Main.java").write_text("class Main {}")

    unwrap_single_top_level(dest)

    assert (dest / "pom.xml").exists()
    assert (dest / "src" / "Main.java").exists()
    assert not wrapper.exists()


def test_unwrap_is_noop_when_multiple_top_level_entries(tmp_path):
    dest = tmp_path / "out"
    dest.mkdir()
    (dest / "pom.xml").write_text("<project/>")
    (dest / "src").mkdir()

    unwrap_single_top_level(dest)

    assert (dest / "pom.xml").exists()
    assert (dest / "src").exists()
