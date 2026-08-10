"""versioning/artifact_version.py -- the self-referencing BOM property
detection (spec: docs/superpowers/specs/2026-08-09-output-version-self-
reference-sync-design.md) and apply_output_version's use of it. Real `mvn`
calls are monkeypatched out here; tests/integration/test_artifact_version.py
covers the real `mvn` behavior against the ace-parent.zip reference repo.
"""

from __future__ import annotations

from app.checkpoint.git_repo import git_init_and_baseline_commit
from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult
from app.versioning import artifact_version
from app.versioning.artifact_version import (
    _project_group_id,
    _self_referencing_version_properties,
    apply_output_version,
    suggest_output_version,
)

_NS = "http://maven.apache.org/POM/4.0.0"


def _settings() -> Settings:
    return Settings(_env_file=None)


def _parse(xml: str):
    from lxml import etree

    return etree.fromstring(xml.encode("utf-8"))


def test_project_group_id_from_direct_element():
    root = _parse(f'<project xmlns="{_NS}"><groupId>com.example</groupId></project>')
    assert _project_group_id(root) == "com.example"


def test_project_group_id_falls_back_to_parent():
    root = _parse(f'<project xmlns="{_NS}"><parent><groupId>com.example.parent</groupId></parent></project>')
    assert _project_group_id(root) == "com.example.parent"


def test_project_group_id_is_none_when_neither_present():
    root = _parse(f'<project xmlns="{_NS}"></project>')
    assert _project_group_id(root) is None


def test_self_referencing_version_properties_matches_ace_parent_shape(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <groupId>com.poscodx.ai.ace</groupId>
        <artifactId>ace-parent</artifactId>
        <version>1.0.0</version>
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>com.poscodx.ai.ace</groupId>
                    <artifactId>ace-common</artifactId>
                    <version>${{ace.version}}</version>
                </dependency>
                <dependency>
                    <groupId>com.poscodx.ai.ace</groupId>
                    <artifactId>ace-ai</artifactId>
                    <version>${{ace.version}}</version>
                </dependency>
                <dependency>
                    <groupId>org.apache.commons</groupId>
                    <artifactId>commons-lang3</artifactId>
                    <version>${{commons-lang3.version}}</version>
                </dependency>
                <dependency>
                    <groupId>com.poscodx.ai.ace</groupId>
                    <artifactId>ace-literal</artifactId>
                    <version>1.2.3</version>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    assert _self_referencing_version_properties(pom) == {"ace.version"}


def test_self_referencing_version_properties_empty_when_no_self_references(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <groupId>com.example</groupId>
        <artifactId>demo</artifactId>
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>org.apache.commons</groupId>
                    <artifactId>commons-lang3</artifactId>
                    <version>${{commons-lang3.version}}</version>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    assert _self_referencing_version_properties(pom) == set()


def test_self_referencing_version_properties_empty_when_no_group_id(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f'<project xmlns="{_NS}"><artifactId>demo</artifactId></project>')

    assert _self_referencing_version_properties(pom) == set()


def test_suggest_output_version_drops_snapshot_suffix():
    assert suggest_output_version("1.2.3-SNAPSHOT") == "1.2.3"


def test_suggest_output_version_pads_major_minor():
    assert suggest_output_version("1.2") == "1.2.0"


def test_suggest_output_version_leaves_full_semver_unchanged():
    assert suggest_output_version("1.2.3") == "1.2.3"


def test_suggest_output_version_leaves_other_qualifiers_unchanged():
    assert suggest_output_version("1.2.3-RC1") == "1.2.3-RC1"


def test_suggest_output_version_leaves_four_part_version_unchanged():
    assert suggest_output_version("1.2.3.4") == "1.2.3.4"


def _work_dir_with_pom(tmp_path, settings, pom_xml: str):
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    (work_dir / "pom.xml").write_text(pom_xml)
    git_init_and_baseline_commit(work_dir, settings)
    return work_dir


async def test_apply_output_version_skips_set_property_when_no_self_references(tmp_path, monkeypatch):
    settings = _settings()
    work_dir = _work_dir_with_pom(tmp_path, settings, f'<project xmlns="{_NS}"><groupId>com.example</groupId></project>')

    calls = []

    async def fake_set(work_dir_, new_version, settings_, log_path=None):
        return SubprocessResult(returncode=0, output="", log_path=None)

    async def fake_set_property(*args, **kwargs):
        calls.append((args, kwargs))
        return SubprocessResult(returncode=0, output="", log_path=None)

    monkeypatch.setattr(artifact_version, "mvn_versions_set", fake_set)
    monkeypatch.setattr(artifact_version, "mvn_versions_set_property", fake_set_property)

    sha = await apply_output_version(work_dir, "1.0.0", settings)

    assert calls == []
    assert sha  # a checkpoint commit was still made


async def test_apply_output_version_calls_set_property_for_each_self_reference_in_order(tmp_path, monkeypatch):
    settings = _settings()
    pom_xml = f"""<project xmlns="{_NS}">
        <groupId>com.poscodx.ai.ace</groupId>
        <artifactId>ace-parent</artifactId>
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>com.poscodx.ai.ace</groupId>
                    <artifactId>ace-common</artifactId>
                    <version>${{ace.version}}</version>
                </dependency>
                <dependency>
                    <groupId>com.poscodx.ai.ace</groupId>
                    <artifactId>ace-widget</artifactId>
                    <version>${{widget.version}}</version>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>"""
    work_dir = _work_dir_with_pom(tmp_path, settings, pom_xml)

    async def fake_set(work_dir_, new_version, settings_, log_path=None):
        return SubprocessResult(returncode=0, output="", log_path=None)

    calls = []

    async def fake_set_property(work_dir_, property_name, new_version, settings_, log_path=None):
        calls.append((property_name, new_version))
        return SubprocessResult(returncode=0, output="", log_path=None)

    monkeypatch.setattr(artifact_version, "mvn_versions_set", fake_set)
    monkeypatch.setattr(artifact_version, "mvn_versions_set_property", fake_set_property)

    sha = await apply_output_version(work_dir, "1.0.0", settings)

    assert calls == [("ace.version", "1.0.0"), ("widget.version", "1.0.0")]  # sorted order
    assert sha


async def test_apply_output_version_raises_when_set_property_fails(tmp_path, monkeypatch):
    settings = _settings()
    pom_xml = f"""<project xmlns="{_NS}">
        <groupId>com.poscodx.ai.ace</groupId>
        <artifactId>ace-parent</artifactId>
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>com.poscodx.ai.ace</groupId>
                    <artifactId>ace-common</artifactId>
                    <version>${{ace.version}}</version>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>"""
    work_dir = _work_dir_with_pom(tmp_path, settings, pom_xml)

    async def fake_set(work_dir_, new_version, settings_, log_path=None):
        return SubprocessResult(returncode=0, output="", log_path=None)

    async def failing_set_property(work_dir_, property_name, new_version, settings_, log_path=None):
        return SubprocessResult(returncode=1, output="boom", log_path=None)

    monkeypatch.setattr(artifact_version, "mvn_versions_set", fake_set)
    monkeypatch.setattr(artifact_version, "mvn_versions_set_property", failing_set_property)

    try:
        await apply_output_version(work_dir, "1.0.0", settings)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "ace.version" in str(exc)
