from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest.errors import GradleProjectError, NotMavenProjectError
from app.ingest.maven_detect import ExternalParentInfo, detect_external_parent, detect_maven_project, read_declared_version

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


def test_read_declared_version_from_own_version_tag(tmp_path):
    pom_path = tmp_path / "pom.xml"
    pom_path.write_text(_single_module_pom())  # <version>1.0.0</version>

    assert read_declared_version(pom_path) == ("1.0.0", "version")


def test_read_declared_version_falls_back_to_parent_version(tmp_path):
    pom_path = tmp_path / "pom.xml"
    pom_path.write_text(f"""<project xmlns="{_POM_NS}">
        <modelVersion>4.0.0</modelVersion>
        <parent>
            <groupId>com.example</groupId>
            <artifactId>demo-parent</artifactId>
            <version>2.0.0-SNAPSHOT</version>
        </parent>
        <artifactId>demo-module</artifactId>
    </project>""")

    assert read_declared_version(pom_path) == ("2.0.0-SNAPSHOT", "parent.version")


def test_read_declared_version_returns_none_when_absent(tmp_path):
    pom_path = tmp_path / "pom.xml"
    pom_path.write_text(f"""<project xmlns="{_POM_NS}">
        <modelVersion>4.0.0</modelVersion>
        <artifactId>demo-module</artifactId>
    </project>""")

    assert read_declared_version(pom_path) == (None, "none")


def test_read_declared_version_from_multi_module_effective_pom_projects_wrapper(tmp_path):
    """Regression test for job #35: `mvn help:effective-pom` against a
    multi-module reactor wraps multiple <project> elements in a top-level
    <projects> (confirmed empirically against a real ace-parent run, root
    module always first) instead of a bare <project> root. Every caller of
    read_declared_version passes such an effective POM, never the project's
    own raw pom.xml -- treating <projects> as if it were <project> silently
    finds no <version>/<parent> and returns (None, "none") for every
    multi-module project."""
    pom_path = tmp_path / "effective-pom.xml"
    pom_path.write_text(f"""<projects>
        <project xmlns="{_POM_NS}">
            <modelVersion>4.0.0</modelVersion>
            <groupId>com.example</groupId>
            <artifactId>reactor-root</artifactId>
            <version>0.4.5</version>
            <packaging>pom</packaging>
        </project>
        <project xmlns="{_POM_NS}">
            <modelVersion>4.0.0</modelVersion>
            <artifactId>reactor-child</artifactId>
            <version>0.4.5</version>
        </project>
    </projects>""")

    assert read_declared_version(pom_path) == ("0.4.5", "version")


def test_detect_external_parent_returns_none_for_public_allowlisted_parent(tmp_path):
    pom_path = tmp_path / "pom.xml"
    pom_path.write_text(f"""<project xmlns="{_POM_NS}">
        <modelVersion>4.0.0</modelVersion>
        <parent>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId>
            <version>2.7.18</version>
        </parent>
        <artifactId>demo</artifactId>
    </project>""")

    assert detect_external_parent(pom_path) is None


def test_detect_external_parent_returns_info_for_internal_parent(tmp_path):
    """Regression test for job #35/#38: anne-agent's root pom.xml inherits
    everything from ace-parent, a separately-released internal artifact."""
    pom_path = tmp_path / "pom.xml"
    pom_path.write_text(f"""<project xmlns="{_POM_NS}">
        <modelVersion>4.0.0</modelVersion>
        <parent>
            <groupId>com.poscodx.ai.ace</groupId>
            <artifactId>ace-parent</artifactId>
            <version>0.4.5</version>
        </parent>
        <artifactId>anne-agent</artifactId>
        <packaging>pom</packaging>
    </project>""")

    assert detect_external_parent(pom_path) == ExternalParentInfo(
        group_id="com.poscodx.ai.ace", artifact_id="ace-parent", version="0.4.5"
    )


def test_detect_external_parent_returns_none_when_no_parent(tmp_path):
    pom_path = tmp_path / "pom.xml"
    pom_path.write_text(f'<project xmlns="{_POM_NS}"><modelVersion>4.0.0</modelVersion><artifactId>demo</artifactId></project>')

    assert detect_external_parent(pom_path) is None
