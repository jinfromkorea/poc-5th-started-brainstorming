"""API-level tests for POST /inspect/artifact-version -- the pre-submission
version peek that pre-fills index.html's "출력 아티팩트 버전" field (spec:
docs/superpowers/specs/2026-08-10-output-version-suggestion-design.md).
Only the ZIP path is covered here (git clone needs network); the version-
reading logic itself is shared with the git path and already covered by
tests/unit/test_maven_detect.py.
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


_POM_WITH_OWN_VERSION = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>com.example</groupId>
    <artifactId>demo</artifactId>
    <version>1.0.0</version>
    <packaging>jar</packaging>
</project>"""

_POM_WITH_PARENT_VERSION_ONLY = b"""<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>com.example</groupId>
        <artifactId>demo-parent</artifactId>
        <version>2.0.0-SNAPSHOT</version>
    </parent>
    <artifactId>demo-module</artifactId>
</project>"""


@pytest.fixture()
def app_client(tmp_path):
    test_settings = Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"))
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = TestClient(app)
    yield client, test_settings


def test_peek_returns_own_declared_version(app_client):
    client, _settings = app_client
    zip_content = _zip_bytes({"pom.xml": _POM_WITH_OWN_VERSION})

    resp = client.post("/inspect/artifact-version", files={"zip_file": ("project.zip", zip_content, "application/zip")})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"detected_version": "1.0.0", "suggested_version": "1.0.0", "source": "version"}


def test_peek_falls_back_to_parent_version_and_normalizes_suggestion(app_client):
    client, _settings = app_client
    zip_content = _zip_bytes({"pom.xml": _POM_WITH_PARENT_VERSION_ONLY})

    resp = client.post("/inspect/artifact-version", files={"zip_file": ("project.zip", zip_content, "application/zip")})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"detected_version": "2.0.0-SNAPSHOT", "suggested_version": "2.0.0", "source": "parent.version"}


def test_peek_gradle_project_returns_no_version_not_an_error(app_client):
    client, _settings = app_client
    zip_content = _zip_bytes({"build.gradle": b"plugins { id 'java' }"})

    resp = client.post("/inspect/artifact-version", files={"zip_file": ("project.zip", zip_content, "application/zip")})

    assert resp.status_code == 200
    assert resp.json() == {"detected_version": None, "suggested_version": None, "source": "none"}


def test_peek_rejects_when_neither_git_url_nor_zip_given(app_client):
    client, _settings = app_client
    resp = client.post("/inspect/artifact-version", data={})
    assert resp.status_code == 400


def test_peek_rejects_when_both_git_url_and_zip_given(app_client):
    client, _settings = app_client
    zip_content = _zip_bytes({"pom.xml": _POM_WITH_OWN_VERSION})

    resp = client.post(
        "/inspect/artifact-version",
        data={"git_url": "https://example.invalid/repo.git"},
        files={"zip_file": ("project.zip", zip_content, "application/zip")},
    )

    assert resp.status_code == 400


def test_peek_cleans_up_temp_workspace(app_client):
    client, settings = app_client
    zip_content = _zip_bytes({"pom.xml": _POM_WITH_OWN_VERSION})

    client.post("/inspect/artifact-version", files={"zip_file": ("project.zip", zip_content, "application/zip")})

    jobs_dir = settings.jobs_dir
    leftovers = list(jobs_dir.glob("_peek_*")) if jobs_dir.exists() else []
    assert leftovers == []
