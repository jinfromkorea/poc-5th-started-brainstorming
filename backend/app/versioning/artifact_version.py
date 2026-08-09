"""Applies the user-supplied output artifact version (spec: "출력 아티팩트
버전 설정"). Run once, immediately after the baseline commit, as its own
tiny checkpointed commit -- so it's included in the diff no matter what
Stage 1 does afterward, and survives a `git reset --hard` rollback of any
later failed migration step (a rollback only ever returns to the last
checkpoint, never past it).
"""

from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from app.checkpoint.git_repo import commit_checkpoint
from app.config import Settings
from app.mvnrewrite.mvn_client import mvn_versions_set, mvn_versions_set_property
from app.mvnrewrite.subprocess_runner import build_log_path

_PROP_REF_RE = re.compile(r"^\$\{([^}]+)\}$")


def _project_group_id(root: etree._Element) -> str | None:
    gid_el = root.find("{*}groupId")
    if gid_el is not None and gid_el.text and gid_el.text.strip():
        return gid_el.text.strip()
    parent_gid_el = root.find("{*}parent/{*}groupId")
    if parent_gid_el is not None and parent_gid_el.text and parent_gid_el.text.strip():
        return parent_gid_el.text.strip()
    return None


def _self_referencing_version_properties(pom_path: Path) -> set[str]:
    """Scans <dependencyManagement> (not plain <dependencies> -- this
    pattern is definitionally a BOM/library self-reference, and the root
    POM of a reactor with packaging=pom has no runtime <dependencies> of
    its own anyway) for entries whose groupId matches this POM's own
    groupId and whose <version> is a ${property} reference -- the "reactor
    references its own modules as a BOM/library" pattern (e.g. ace-parent's
    ace-common/ace-ai/ace-util, pinned via ${ace.version}). `mvn
    versions:set` already reactor-propagates each module's own <version>
    (inherited from <parent>), but never follows this property indirection,
    so it's left stale unless bumped separately (spec: docs/superpowers/
    specs/2026-08-09-output-version-self-reference-sync-design.md)."""
    root = etree.parse(str(pom_path)).getroot()
    group_id = _project_group_id(root)
    if group_id is None:
        return set()

    properties: set[str] = set()
    deps = root.find("{*}dependencyManagement/{*}dependencies")
    if deps is None:
        return properties
    for dep in deps.findall("{*}dependency"):
        gid_el = dep.find("{*}groupId")
        if gid_el is None or (gid_el.text or "").strip() != group_id:
            continue
        ver_el = dep.find("{*}version")
        version_text = (ver_el.text or "").strip() if ver_el is not None and ver_el.text else ""
        m = _PROP_REF_RE.match(version_text)
        if m:
            properties.add(m.group(1))
    return properties


async def apply_output_version(
    work_dir: Path, new_version: str, settings: Settings, output_dir: Path | None = None
) -> str:
    log_path = build_log_path(output_dir, "ingest", "mvn-versions-set") if output_dir is not None else None
    result = await mvn_versions_set(work_dir, new_version, settings, log_path=log_path)
    if result.returncode != 0:
        raise RuntimeError(f"versions:set failed for {new_version!r}: {result.output}")

    for prop in sorted(_self_referencing_version_properties(work_dir / "pom.xml")):
        prop_log_path = (
            build_log_path(output_dir, "ingest", f"mvn-versions-set-property-{prop}") if output_dir is not None else None
        )
        prop_result = await mvn_versions_set_property(work_dir, prop, new_version, settings, log_path=prop_log_path)
        if prop_result.returncode != 0:
            raise RuntimeError(f"versions:set-property failed for property {prop!r}: {prop_result.output}")

    return commit_checkpoint(work_dir, settings, f"checkpoint: set artifact version to {new_version}")
