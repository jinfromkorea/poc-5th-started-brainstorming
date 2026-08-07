"""Bumps a single dependency's version in the target project (Stage 2's
mechanical patch step). Confirmed empirically against a real property-managed
dependency (commons-lang3 in ace-parent) that Maven Versions Plugin's
`versions:use-dep-version` goal silently SKIPS any dependency whose version
is set via a `${property}` reference -- exactly the common pattern our
reference repos (and most well-organized Maven projects) use. So this picks
between two goals depending on how the version is actually declared:

- If declared via a property (`<version>${x.version}</version>`): use
  `versions:set-property -Dproperty=x.version -DnewVersion=...`.
- Otherwise (a literal version): use `versions:use-dep-version
  -Dincludes=groupId:artifactId -DdepVersion=... -DforceVersion=true`,
  which is multi-module-reactor aware.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult, run_subprocess

_PROP_REF_PREFIX = "${"


def find_version_property(pom_path: Path, group_id: str, artifact_id: str) -> str | None:
    """Searches both <dependencyManagement> and plain <dependencies> in
    pom_path for a matching groupId/artifactId whose <version> is a
    ${property} reference, and returns that property's name (without the
    ${...} wrapper), or None if the dependency isn't declared here at all,
    or is declared with a literal version."""
    root = etree.parse(str(pom_path)).getroot()
    for deps_container in (root.find("{*}dependencyManagement/{*}dependencies"), root.find("{*}dependencies")):
        if deps_container is None:
            continue
        for dep in deps_container.findall("{*}dependency"):
            gid_el = dep.find("{*}groupId")
            aid_el = dep.find("{*}artifactId")
            if gid_el is None or aid_el is None:
                continue
            if (gid_el.text or "").strip() != group_id or (aid_el.text or "").strip() != artifact_id:
                continue
            ver_el = dep.find("{*}version")
            version_text = (ver_el.text or "").strip() if ver_el is not None else ""
            if version_text.startswith(_PROP_REF_PREFIX) and version_text.endswith("}"):
                return version_text[2:-1]
            return None  # found the dependency, but it's a literal version
    return None


async def patch_dependency_version(
    work_dir: Path,
    group_id: str,
    artifact_id: str,
    new_version: str,
    settings: Settings,
    log_path: Path | None = None,
) -> SubprocessResult:
    prop_name = find_version_property(work_dir / "pom.xml", group_id, artifact_id)
    if prop_name:
        args = ["mvn", "-B", "versions:set-property", f"-Dproperty={prop_name}", f"-DnewVersion={new_version}"]
    else:
        args = [
            "mvn",
            "-B",
            "versions:use-dep-version",
            f"-Dincludes={group_id}:{artifact_id}",
            f"-DdepVersion={new_version}",
            "-DforceVersion=true",
        ]
    return await run_subprocess(args, work_dir, settings, log_path=log_path)
