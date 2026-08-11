"""Maven root detection: root pom.xml existence, packaging, module tree.
Out-of-scope Gradle projects are detected and rejected explicitly here
rather than falling through to a generic "not found" error.

Note: only direct (one-level) <modules> are collected, matching every
reference fixture's shape (ace-parent/ace-portal/anne-agent/daisy-agent are
all exactly one level deep). Nested multi-module reactors are not walked
recursively -- extend _list_direct_modules if that's ever needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from app.ingest.errors import GradleProjectError, NotMavenProjectError
from app.mvnrewrite.pom_parser import _project_root

_GRADLE_MARKERS = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")


@dataclass
class ModuleInfo:
    relative_path: str
    pom_path: Path
    exists: bool


@dataclass
class MavenDetectionResult:
    root_pom: Path
    packaging: str
    is_multi_module: bool
    modules: list[ModuleInfo] = field(default_factory=list)


def _parse_pom(pom_path: Path) -> etree._Element:
    return etree.parse(str(pom_path)).getroot()


def _text(root: etree._Element, tag: str) -> str | None:
    # lxml's `{*}` wildcard namespace match handles pom.xml's default
    # xmlns="http://maven.apache.org/POM/4.0.0" without hardcoding it.
    el = root.find(f"{{*}}{tag}")
    return el.text.strip() if el is not None and el.text else None


def _list_direct_modules(root_pom: Path) -> list[ModuleInfo]:
    root = _parse_pom(root_pom)
    modules_el = root.find("{*}modules")
    if modules_el is None:
        return []
    result = []
    for module_el in modules_el.findall("{*}module"):
        rel = (module_el.text or "").strip()
        if not rel:
            continue
        module_pom = (root_pom.parent / rel / "pom.xml").resolve()
        result.append(ModuleInfo(relative_path=rel, pom_path=module_pom, exists=module_pom.exists()))
    return result


def detect_maven_project(root_dir: Path) -> MavenDetectionResult:
    root_pom = root_dir / "pom.xml"

    if not root_pom.exists():
        if any((root_dir / marker).exists() for marker in _GRADLE_MARKERS):
            raise GradleProjectError(
                f"Gradle project detected at {root_dir} (build.gradle*/settings.gradle* present) "
                "-- Gradle is out of scope for this tool."
            )
        raise NotMavenProjectError(f"no pom.xml found at {root_dir} -- not a Maven project.")

    root = _parse_pom(root_pom)
    packaging = _text(root, "packaging") or "jar"  # Maven's own default when <packaging> is omitted
    modules = _list_direct_modules(root_pom) if packaging == "pom" else []

    return MavenDetectionResult(
        root_pom=root_pom,
        packaging=packaging,
        is_multi_module=bool(modules),
        modules=modules,
    )


def read_declared_version(root_pom: Path) -> tuple[str | None, str]:
    """Returns (version, source). source is "version" if root_pom declares
    its own <version>, "parent.version" if only inherited via <parent> (the
    literal XML value, not mvn-resolved), or "none" if neither is present.
    Used by the pre-submission output-version suggestion (spec:
    docs/superpowers/specs/2026-08-10-output-version-suggestion-design.md).

    Every caller passes an *effective* POM (mvn_client.mvn_effective_pom),
    not the project's own raw pom.xml -- and `mvn help:effective-pom` run
    against a multi-module reactor wraps its output in a top-level
    <projects> holding one <project> per module (root module first) instead
    of a bare <project> root (see pom_parser._project_root's docstring,
    confirmed empirically against a real ace-parent reactor). Using
    _parse_pom's raw .getroot() here would silently treat <projects> as if
    it were <project>, find no <version>/<parent> as direct children, and
    return (None, "none") for every multi-module project -- job #35's
    "no current version detected" bug."""
    root = _project_root(root_pom)
    version = _text(root, "version")
    if version:
        return version, "version"
    parent_el = root.find("{*}parent")
    if parent_el is not None:
        parent_version = _text(parent_el, "version")
        if parent_version:
            return parent_version, "parent.version"
    return None, "none"


# Deliberately small and conservative -- "unknown parent" defaults to
# "possibly internal", not the other way around (spec: docs/superpowers/
# specs/2026-08-11-internal-parent-pom-target-version-design.md). Extend as
# other legitimate public parents come up.
_PUBLIC_PARENT_ALLOWLIST = {
    ("org.springframework.boot", "spring-boot-starter-parent"),
}


@dataclass
class ExternalParentInfo:
    group_id: str
    artifact_id: str
    version: str | None  # <parent>에 <version> 텍스트가 비어 있는(malformed pom.xml) 방어적 케이스만 None


def detect_external_parent(root_pom: Path) -> ExternalParentInfo | None:
    """Stage 0 calls this right after mvn_effective_pom/extract_versions,
    against the project's own *raw* (non-effective) root pom.xml. A <parent>
    on the ingested reactor's own root is, by definition, an artifact
    outside this job's ingested source (git/zip) -- it's released and
    resolved separately, unlike a multi-module child's <parent> pointing at
    its own reactor root, which stays inside the ingested tree via
    <modules> and is already handled fine. If that <parent> isn't a known
    public one, treat it as "possibly an internal parent POM (BOM 겸용)"
    whose properties may be the actual source of this project's detected
    stack versions -- Stage 1 can't touch that artifact's own files, only
    point at a newer released version of it (spec: docs/superpowers/specs/
    2026-08-11-internal-parent-pom-target-version-design.md). Confirmed
    against a real case: anne-agent inherits java/spring-boot/spring-ai
    entirely from ace-parent (job #35/#38)."""
    root = _parse_pom(root_pom)
    parent_el = root.find("{*}parent")
    if parent_el is None:
        return None
    group_id = _text(parent_el, "groupId")
    artifact_id = _text(parent_el, "artifactId")
    if group_id is None or artifact_id is None:
        return None
    if (group_id, artifact_id) in _PUBLIC_PARENT_ALLOWLIST:
        return None
    return ExternalParentInfo(group_id=group_id, artifact_id=artifact_id, version=_text(parent_el, "version"))
