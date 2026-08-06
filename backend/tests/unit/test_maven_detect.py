from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.errors import GradleProjectError, NotMavenProjectError
from app.ingest.maven_detect import detect_maven_project

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"

_POM_NS = "http://maven.apache.org/POM/4.0.0"


def _single_module_pom() -> str:
    return f"""<project xmlns="{_POM_NS}">
        <modelVersion>4.0.0</modelVersion>
        <groupId>com.example</groupId>
        <artifactId>demo</artifactId>
        <version>1.0.0</version>
        <packaging>jar</packaging>
    </project>"""


def _multi_module_pom(modules: list[str]) -> str:
    modules_xml = "".join(f"<module>{m}</module>" for m in modules)
    return f"""<project xmlns="{_POM_NS}">
        <modelVersion>4.0.0</modelVersion>
        <groupId>com.example</groupId>
        <artifactId>demo-parent</artifactId>
        <version>1.0.0</version>
        <packaging>pom</packaging>
        <modules>{modules_xml}</modules>
    </project>"""


def test_single_module_project(tmp_path):
    (tmp_path / "pom.xml").write_text(_single_module_pom())

    result = detect_maven_project(tmp_path)

    assert result.packaging == "jar"
    assert result.is_multi_module is False
    assert result.modules == []


def test_multi_module_project(tmp_path):
    (tmp_path / "pom.xml").write_text(_multi_module_pom(["module-a", "module-b"]))
    for m in ("module-a", "module-b"):
        d = tmp_path / m
        d.mkdir()
        (d / "pom.xml").write_text(_single_module_pom())

    result = detect_maven_project(tmp_path)

    assert result.packaging == "pom"
    assert result.is_multi_module is True
    assert {m.relative_path for m in result.modules} == {"module-a", "module-b"}
    assert all(m.exists for m in result.modules)


def test_multi_module_project_flags_missing_module_pom(tmp_path):
    (tmp_path / "pom.xml").write_text(_multi_module_pom(["ghost-module"]))

    result = detect_maven_project(tmp_path)

    assert len(result.modules) == 1
    assert result.modules[0].exists is False


def test_gradle_project_rejected(tmp_path):
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }")

    with pytest.raises(GradleProjectError):
        detect_maven_project(tmp_path)


def test_gradle_fixture_rejected():
    with pytest.raises(GradleProjectError):
        detect_maven_project(FIXTURES / "gradle-sample")


def test_neither_maven_nor_gradle_rejected(tmp_path):
    (tmp_path / "README.md").write_text("not a build at all")

    with pytest.raises(NotMavenProjectError):
        detect_maven_project(tmp_path)
