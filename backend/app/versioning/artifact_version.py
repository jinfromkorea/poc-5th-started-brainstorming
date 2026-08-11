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
from app.ingest.maven_detect import read_declared_version
from app.mvnrewrite.mvn_client import mvn_versions_set, mvn_versions_set_property
from app.mvnrewrite.subprocess_runner import build_log_path

_PROP_REF_RE = re.compile(r"^\$\{([^}]+)\}$")


def _ensure_local_version_declared(pom_path: Path) -> None:
    """`mvn versions:set` refuses outright ("Project version is inherited
    from parent") when a project's own pom.xml has no <version> of its own
    and only inherits one from a <parent> -- confirmed empirically (job #38,
    a project parented on an external, non-reactor-local "사내 parent POM
    (BOM 겸용)" like ace-parent) and reproduced/fixed by hand against the
    same real project. There's no plugin flag that overrides this; the fix
    is to give the project an explicit <version> equal to its current
    (inherited) value first -- that's something versions:set CAN rewrite --
    which also correctly leaves <parent><version> untouched, since that
    parent is a separately-released artifact, not part of this reactor."""
    current_version, source = read_declared_version(pom_path)
    if source != "parent.version":
        return  # already declares its own <version>, has none at all, or unparseable -- nothing to fix

    tree = etree.parse(str(pom_path))
    root = tree.getroot()
    parent_el = root.find("{*}parent")
    ns = etree.QName(root.tag).namespace
    version_el = etree.Element(f"{{{ns}}}version" if ns else "version")
    version_el.text = current_version
    version_el.tail = parent_el.tail
    parent_el.tail = "\n\n    "
    parent_el.addnext(version_el)
    tree.write(str(pom_path), xml_declaration=True, encoding="UTF-8")


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
    _ensure_local_version_declared(work_dir / "pom.xml")
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


_SNAPSHOT_SUFFIX = "-SNAPSHOT"


def suggest_output_version(declared_version: str) -> str:
    """Normalizes a raw pom.xml version (from ingest/maven_detect.py's
    read_declared_version) into a release-ready suggestion for the
    pre-submission output-version field: drops a -SNAPSHOT qualifier, pads
    an incomplete MAJOR.MINOR into MAJOR.MINOR.0. Anything else (other
    qualifiers, 4-part versions, non-numeric values) passes through
    unchanged -- guessing wrong here is worse than not guessing (spec:
    docs/superpowers/specs/2026-08-10-output-version-suggestion-design.md)."""
    version = declared_version
    if version.endswith(_SNAPSHOT_SUFFIX):
        version = version[: -len(_SNAPSHOT_SUFFIX)]
    parts = version.split(".")
    if len(parts) == 2 and all(p.isdigit() for p in parts):
        version = f"{version}.0"
    return version


def compute_stage0_output_version(declared_version: str, run_stage1: bool) -> str:
    """Stage 0's automatic output-version proposal: bump MAJOR if Stage 1 (a
    real stack migration) is selected, otherwise bump MINOR -- regardless of
    whether the stack already matches the target (spec: docs/superpowers/
    specs/2026-08-10-stage0-version-scan-restructure-design.md). Falls back
    to the normalized-but-unbumped value when declared_version doesn't parse
    as MAJOR.MINOR.PATCH -- guessing wrong is worse than not guessing."""
    normalized = suggest_output_version(declared_version)
    parts = normalized.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return normalized
    major, minor, _patch = (int(p) for p in parts)
    if run_stage1:
        return f"{major + 1}.0.0"
    return f"{major}.{minor + 1}.0"
