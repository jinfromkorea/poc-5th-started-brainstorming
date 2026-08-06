"""Extracts current Java/Spring Boot/Spring Cloud/Spring AI versions from a
pom.xml. Works on two kinds of input:

- An *effective* POM (mvn_client.mvn_effective_pom) -- the full local+remote
  parent/BOM inheritance chain is already resolved, so every value here is a
  literal, never a ``${...}`` reference. This is the authoritative path (see
  spec: "버전 감지"), because a project's own pom.xml often only *inherits*
  these versions from an external parent that isn't present in the ingested
  source at all (e.g. any project with ``<parent>spring-boot-starter-parent`
  or an internal corporate parent BOM not shipped alongside the repo).

  IMPORTANT, confirmed empirically against a real `mvn help:effective-pom`
  run: a ``<dependency><type>pom</type><scope>import</scope>`` BOM import
  (e.g. importing ``spring-boot-dependencies``) is *expanded* by effective-pom
  resolution -- the BOM self-reference disappears and is replaced by its
  hundreds of individual managed entries. So on an effective pom we can't
  look for "spring-boot-dependencies" itself.

  Two ways to recover the version anyway, tried in order:
  1. A conventionally-named property (spring-boot.version / spring-cloud.version
     / spring-ai.version) -- confirmed empirically to survive into the
     effective pom's own <properties> unchanged, for every dimension. This is
     the primary signal.
  2. A concrete anchor artifact that the BOM manages, read as a fallback for
     projects that don't use the conventional property name. Confirmed
     empirically for Spring Boot (plain "spring-boot" artifact's resolved
     version equals the BOM version, 3.5.16 for ace-parent) and Spring AI
     ("spring-ai-commons", 1.1.8 for ace-parent). **Confirmed NOT to work for
     Spring Cloud**: its release train name (e.g. "2021.0.8") is a marketing
     label for a *set* of independently-versioned component artifacts, not a
     version shared by any of them -- "spring-cloud-context"'s own resolved
     version was "3.1.7" while the real train was "2021.0.8". So Spring Cloud
     has no anchor-artifact fallback; only the property-name path can recover it.
- A raw pom.xml directly -- handled best-effort via single-hop ``${prop}``
  resolution against that same file's own <properties>, which is enough for
  the common "parent pom.xml declares its own BOM-import properties"
  pattern (e.g. ace-parent's own pom.xml) and keeps this module unit
  testable without invoking real `mvn`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

_PROP_REF_RE = re.compile(r"^\$\{([^}]+)\}$")


@dataclass
class DetectedVersions:
    java_version: str | None
    spring_boot_version: str | None
    spring_cloud_version: str | None
    spring_ai_version: str | None


def _properties(root: etree._Element) -> dict[str, str]:
    props_el = root.find("{*}properties")
    if props_el is None:
        return {}
    return {
        etree.QName(child.tag).localname: child.text.strip()
        for child in props_el
        if child.text and child.text.strip()
    }


def _resolve(value: str | None, props: dict[str, str]) -> str | None:
    if value is None:
        return None
    m = _PROP_REF_RE.match(value)
    if not m:
        return value
    return props.get(m.group(1), value)  # unresolved (multi-hop/external) -> return the raw ref as-is


def _detect_java_version(root: etree._Element, props: dict[str, str]) -> str | None:
    # Priority: maven.compiler.release supersedes source/target since Java 9+.
    for key in ("maven.compiler.release", "java.version", "maven.compiler.target", "maven.compiler.source"):
        if key in props:
            return props[key]
    return None


def _dependency_management_version(root: etree._Element, artifact_id: str, props: dict[str, str]) -> str | None:
    dm = root.find("{*}dependencyManagement")
    if dm is None:
        return None
    deps = dm.find("{*}dependencies")
    if deps is None:
        return None
    for dep in deps.findall("{*}dependency"):
        aid_el = dep.find("{*}artifactId")
        if aid_el is not None and (aid_el.text or "").strip() == artifact_id:
            ver_el = dep.find("{*}version")
            return _resolve(ver_el.text.strip() if ver_el is not None and ver_el.text else None, props)
    return None


def _dependency_management_version_any(
    root: etree._Element, artifact_ids: list[str], props: dict[str, str]
) -> str | None:
    """Tries each candidate artifactId in order, returns the first hit.
    First entries should be the BOM's own self-reference (works on a raw,
    unexpanded pom.xml); later entries should be a concrete anchor artifact
    that BOM manages (works after effective-pom expansion removes the BOM
    self-reference)."""
    for artifact_id in artifact_ids:
        version = _dependency_management_version(root, artifact_id, props)
        if version is not None:
            return version
    return None


def _parent_version(root: etree._Element, parent_artifact_id: str, props: dict[str, str]) -> str | None:
    parent_el = root.find("{*}parent")
    if parent_el is None:
        return None
    aid_el = parent_el.find("{*}artifactId")
    if aid_el is None or (aid_el.text or "").strip() != parent_artifact_id:
        return None
    ver_el = parent_el.find("{*}version")
    return _resolve(ver_el.text.strip() if ver_el is not None and ver_el.text else None, props)


def _project_root(pom_path: Path) -> etree._Element:
    """A raw pom.xml's root element IS <project>. But `mvn help:effective-pom`
    run against a multi-module reactor (confirmed empirically) instead
    writes a top-level <projects> wrapping one <project> per reactor module
    (root module first, then each child) -- not documented anywhere obvious,
    found by inspecting real output against ace-parent. We only care about
    the reactor root's own effective POM, which is always the first
    <project> child in that case."""
    root = etree.parse(str(pom_path)).getroot()
    if etree.QName(root.tag).localname == "projects":
        first_project = root.find("{*}project")
        if first_project is None:
            raise ValueError(f"{pom_path}: <projects> root has no <project> children")
        return first_project
    return root


def extract_versions(pom_path: Path) -> DetectedVersions:
    root = _project_root(pom_path)
    props = _properties(root)

    spring_boot = (
        props.get("spring-boot.version")
        or _dependency_management_version_any(root, ["spring-boot-dependencies", "spring-boot"], props)
        or _parent_version(root, "spring-boot-starter-parent", props)
    )
    spring_cloud = props.get("spring-cloud.version") or _dependency_management_version(
        root, "spring-cloud-dependencies", props
    )  # no anchor-artifact fallback here -- confirmed unreliable for Spring Cloud, see module docstring
    spring_ai = props.get("spring-ai.version") or _dependency_management_version_any(
        root, ["spring-ai-bom", "spring-ai-commons"], props
    )

    return DetectedVersions(
        java_version=_detect_java_version(root, props),
        spring_boot_version=spring_boot,
        spring_cloud_version=spring_cloud,
        spring_ai_version=spring_ai,
    )
