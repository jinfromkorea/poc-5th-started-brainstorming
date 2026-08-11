"""Bumps a project's own <parent><version> to a specific, already-released
target version (Stage 1's optional "사내 parent POM 목표 버전" step). Confirmed
empirically (against a real ace-parent/anne-agent project) that `mvn
versions:update-parent -DparentVersion=X` does NOT reliably pin to exactly
X -- it resolves against version metadata and can silently jump to a
different, numerically "higher" version available locally/remotely instead
of the one actually requested. A direct, exact XML edit has no such
ambiguity -- the <parent>'s groupId/artifactId are left untouched, only
<version> changes (spec: docs/superpowers/specs/2026-08-11-internal-
parent-pom-target-version-design.md).
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree


def patch_parent_version(pom_path: Path, new_version: str) -> None:
    tree = etree.parse(str(pom_path))
    root = tree.getroot()
    parent_el = root.find("{*}parent")
    if parent_el is None:
        raise ValueError(f"{pom_path}: no <parent> element to update")
    version_el = parent_el.find("{*}version")
    if version_el is None:
        raise ValueError(f"{pom_path}: <parent> has no <version> to update")
    version_el.text = new_version
    tree.write(str(pom_path), xml_declaration=True, encoding="UTF-8")
