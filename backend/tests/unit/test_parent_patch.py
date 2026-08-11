from __future__ import annotations

import pytest

from app.mvnrewrite.parent_patch import patch_parent_version

_NS = "http://maven.apache.org/POM/4.0.0"


def test_patch_parent_version_updates_only_version(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <modelVersion>4.0.0</modelVersion>
        <parent>
            <groupId>com.poscodx.ai.ace</groupId>
            <artifactId>ace-parent</artifactId>
            <version>0.4.5</version>
        </parent>
        <artifactId>anne-agent</artifactId>
        <packaging>pom</packaging>
    </project>""")

    patch_parent_version(pom, "0.5.0")

    text = pom.read_text(encoding="utf-8")
    assert "<version>0.5.0</version>" in text
    assert "<groupId>com.poscodx.ai.ace</groupId>" in text
    assert "<artifactId>ace-parent</artifactId>" in text


def test_patch_parent_version_raises_when_no_parent(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f'<project xmlns="{_NS}"><artifactId>demo</artifactId></project>')

    with pytest.raises(ValueError, match="no <parent>"):
        patch_parent_version(pom, "1.0.0")


def test_patch_parent_version_raises_when_parent_has_no_version(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <parent>
            <groupId>com.example</groupId>
            <artifactId>demo-parent</artifactId>
        </parent>
        <artifactId>demo</artifactId>
    </project>""")

    with pytest.raises(ValueError, match="no <version>"):
        patch_parent_version(pom, "1.0.0")
