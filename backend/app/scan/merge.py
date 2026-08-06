"""Parses Dependency-Check's and Trivy's JSON reports into one common shape,
de-duplicates by (CVE id, package), and filters by FAIL_ON_CVSS (spec: "패치
대상 기준"). Also picks which fixed version to patch to when a scanner
reports several (Trivy commonly reports one fix per still-supported major/
minor line, e.g. "3.1.4, 2.18.9, 2.21.5, 2.22.1" for one CVE) -- prefer
the smallest bump that stays on the currently-installed major.minor line,
to avoid an unnecessary major-version jump as a side effect of patching a CVE.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_VERSION_PART_RE = re.compile(r"\d+")
_MAVEN_PURL_RE = re.compile(r"^pkg:maven/([^/]+)/([^@?]+)(?:@([^?]+))?")


@dataclass
class Vulnerability:
    cve_id: str
    package: str  # "groupId:artifactId"
    installed_version: str
    fix_version: str | None
    cvss: float | None
    severity: str | None
    source: str  # "trivy" | "dependency-check"


def _version_sort_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in _VERSION_PART_RE.findall(version)) or (0,)


def _major_minor(version: str) -> str:
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def pick_fix_version(installed_version: str, fixed_versions_raw: str | None) -> str | None:
    if not fixed_versions_raw:
        return None
    candidates = [v.strip() for v in fixed_versions_raw.split(",") if v.strip()]
    if not candidates:
        return None
    same_line = [v for v in candidates if _major_minor(v) == _major_minor(installed_version)]
    pool = same_line or candidates
    return min(pool, key=_version_sort_key)


def parse_trivy_json(path: Path) -> list[Vulnerability]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Vulnerability] = []
    for result in data.get("Results") or []:
        for v in result.get("Vulnerabilities") or []:
            cvss_scores = [
                score
                for source_scores in (v.get("CVSS") or {}).values()
                if (score := source_scores.get("V3Score")) is not None
            ]
            installed = v.get("InstalledVersion", "")
            out.append(
                Vulnerability(
                    cve_id=v.get("VulnerabilityID", "UNKNOWN"),
                    package=v.get("PkgName", ""),
                    installed_version=installed,
                    fix_version=pick_fix_version(installed, v.get("FixedVersion")),
                    cvss=max(cvss_scores) if cvss_scores else None,
                    severity=v.get("Severity"),
                    source="trivy",
                )
            )
    return out


def _parse_maven_purl(purl: str) -> tuple[str, str]:
    """Dependency-Check identifies packages via PURL
    (pkg:maven/<groupId>/<artifactId>@<version>); Trivy identifies them as
    plain "<groupId>:<artifactId>". Normalize to Trivy's shape so de-dup by
    (cve_id, package) actually matches across both scanners -- confirmed by
    a real test failure that they otherwise silently never collide."""
    m = _MAVEN_PURL_RE.match(purl)
    if not m:
        return purl, ""
    group_id, artifact_id, version = m.group(1), m.group(2), m.group(3)
    return f"{group_id}:{artifact_id}", version or ""


def parse_dependency_check_json(path: Path) -> list[Vulnerability]:
    data = json.loads(path.read_text(encoding="utf-8"))
    out: list[Vulnerability] = []
    for dep in data.get("dependencies") or []:
        packages = dep.get("packages") or []
        raw_id = packages[0].get("id", "") if packages else ""
        package, installed_version = _parse_maven_purl(raw_id) if raw_id else (dep.get("fileName", "unknown"), "")
        for v in dep.get("vulnerabilities") or []:
            cvss = None
            if v.get("cvssv3"):
                cvss = v["cvssv3"].get("baseScore")
            elif v.get("cvssv2"):
                cvss = v["cvssv2"].get("score")
            out.append(
                Vulnerability(
                    cve_id=v.get("name", "UNKNOWN"),
                    package=package,
                    installed_version=installed_version,
                    fix_version=None,  # dependency-check doesn't report a fixed version the way Trivy does
                    cvss=cvss,
                    severity=v.get("severity"),
                    source="dependency-check",
                )
            )
    return out


def merge_and_filter(
    dependency_check_vulns: list[Vulnerability],
    trivy_vulns: list[Vulnerability],
    min_cvss: float,
) -> list[Vulnerability]:
    """De-dup by (cve_id, package): if both scanners found the same CVE on
    the same package, keep whichever entry carries more useful data (a
    fix_version, or failing that a CVSS score) rather than an arbitrary one."""
    combined: dict[tuple[str, str], Vulnerability] = {}
    for v in [*dependency_check_vulns, *trivy_vulns]:
        key = (v.cve_id, v.package)
        existing = combined.get(key)
        if existing is None:
            combined[key] = v
            continue
        if not existing.fix_version and v.fix_version or (existing.cvss or 0) < (v.cvss or 0):
            combined[key] = v

    return [v for v in combined.values() if (v.cvss or 0) >= min_cvss]
