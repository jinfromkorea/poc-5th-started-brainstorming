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
    docs/superpowers/specs/2026-08-10-output-version-suggestion-design.md)."""
    root = _parse_pom(root_pom)
    version = _text(root, "version")
    if version:
        return version, "version"
    parent_el = root.find("{*}parent")
    if parent_el is not None:
        parent_version = _text(parent_el, "version")
        if parent_version:
            return parent_version, "parent.version"
    return None, "none"
