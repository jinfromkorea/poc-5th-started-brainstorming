"""with_public_mirror injects `-s <settings.xml>` into `mvn` invocations only
when MVN_PUBLIC_MIRROR_ENABLED is set -- for running this tool outside the
corporate network, where a target project's own pom.xml may declare an
internal Nexus that's unreachable even for artifacts that are actually
public (confirmed real incident: com.azure:azure-json, a public Azure SDK
artifact, failed outright because the project's pom.xml only declared an
internal Nexus repository and that host couldn't be resolved).
"""

from __future__ import annotations

from pathlib import Path

from app.config import Settings
from app.mvnrewrite.mvn_settings import public_mirror_settings_path, with_public_mirror


def _settings(tmp_path, **overrides) -> Settings:
    return Settings(_env_file=None, jobs_data_dir=str(tmp_path / "jobs"), **overrides)


def test_disabled_by_default_leaves_args_untouched(tmp_path):
    settings = _settings(tmp_path)

    args = with_public_mirror(["mvn", "-B", "compile"], settings)

    assert args == ["mvn", "-B", "compile"]


def test_enabled_inserts_settings_flag_right_after_mvn(tmp_path):
    settings = _settings(tmp_path, mvn_public_mirror_enabled=True)

    args = with_public_mirror(["mvn", "-B", "compile"], settings)

    assert args[0] == "mvn"
    assert args[1] == "-s"
    assert args[3:] == ["-B", "compile"]
    assert Path(args[2]).is_file()


def test_non_mvn_commands_are_never_touched(tmp_path):
    settings = _settings(tmp_path, mvn_public_mirror_enabled=True)

    args = with_public_mirror(["trivy", "fs", "."], settings)

    assert args == ["trivy", "fs", "."]


def test_generated_settings_xml_mirrors_everything_to_the_configured_url(tmp_path):
    settings = _settings(tmp_path, mvn_public_mirror_enabled=True, mvn_public_mirror_url="https://example.invalid/repo")

    path = public_mirror_settings_path(settings)

    content = path.read_text(encoding="utf-8")
    assert "<mirrorOf>*</mirrorOf>" in content
    assert "https://example.invalid/repo" in content


def test_settings_file_is_reused_not_regenerated_every_call(tmp_path):
    settings = _settings(tmp_path, mvn_public_mirror_enabled=True)

    first = public_mirror_settings_path(settings)
    first.write_text("<!-- sentinel -->", encoding="utf-8")
    second = public_mirror_settings_path(settings)

    assert first == second
    assert second.read_text(encoding="utf-8") == "<!-- sentinel -->"
