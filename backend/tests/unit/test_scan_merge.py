"""Unit tests against canned JSON (fast, no network) -- the Trivy shape here
is copied verbatim from a real `trivy fs` run against ace-parent (confirmed
empirically); the Dependency-Check shape follows its documented JSON report
schema.
"""

from __future__ import annotations

import json

from app.scan.merge import (
    merge_and_filter,
    parse_dependency_check_json,
    parse_trivy_json,
    pick_fix_version,
)

TRIVY_SAMPLE = {
    "Results": [
        {
            "Target": "pom.xml",
            "Class": "lang-pkgs",
            "Type": "pom",
            "Vulnerabilities": [
                {
                    "VulnerabilityID": "CVE-2026-54515",
                    "PkgName": "com.fasterxml.jackson.core:jackson-databind",
                    "InstalledVersion": "2.21.4",
                    "FixedVersion": "3.1.4, 2.18.9, 2.21.5, 2.22.1",
                    "Severity": "MEDIUM",
                    "CVSS": {"ghsa": {"V3Score": 5.3}, "redhat": {"V3Score": 5.3}},
                },
                {
                    "VulnerabilityID": "CVE-2026-59949",
                    "PkgName": "at.yawk.lz4:lz4-java",
                    "InstalledVersion": "1.11.0",
                    "FixedVersion": "1.11.1",
                    "Severity": "HIGH",
                    "CVSS": {"ghsa": {"V3Score": 8.1}},
                },
            ],
        }
    ]
}

DEPENDENCY_CHECK_SAMPLE = {
    "dependencies": [
        {
            "fileName": "jackson-databind-2.21.4.jar",
            "packages": [{"id": "pkg:maven/com.fasterxml.jackson.core/jackson-databind@2.21.4"}],
            "vulnerabilities": [
                {"name": "CVE-2026-54515", "severity": "MEDIUM", "cvssv3": {"baseScore": 5.3}},
            ],
        },
        {
            "fileName": "some-other-lib-1.0.0.jar",
            "packages": [{"id": "pkg:maven/com.example/some-other-lib@1.0.0"}],
            "vulnerabilities": [
                {"name": "CVE-2020-0001", "severity": "CRITICAL", "cvssv3": {"baseScore": 9.8}},
            ],
        },
    ]
}


def test_pick_fix_version_prefers_same_major_minor_line():
    assert pick_fix_version("2.21.4", "3.1.4, 2.18.9, 2.21.5, 2.22.1") == "2.21.5"


def test_pick_fix_version_falls_back_to_smallest_overall_when_no_same_line():
    assert pick_fix_version("2.21.4", "3.1.4, 4.0.0") == "3.1.4"


def test_pick_fix_version_single_candidate():
    assert pick_fix_version("1.11.0", "1.11.1") == "1.11.1"


def test_pick_fix_version_none_when_no_fixed_version_reported():
    assert pick_fix_version("1.0.0", None) is None


def test_parse_trivy_json(tmp_path):
    path = tmp_path / "trivy.json"
    path.write_text(json.dumps(TRIVY_SAMPLE), encoding="utf-8")

    vulns = parse_trivy_json(path)

    assert len(vulns) == 2
    jackson = next(v for v in vulns if v.cve_id == "CVE-2026-54515")
    assert jackson.package == "com.fasterxml.jackson.core:jackson-databind"
    assert jackson.installed_version == "2.21.4"
    assert jackson.fix_version == "2.21.5"
    assert jackson.cvss == 5.3
    assert jackson.source == "trivy"

    lz4 = next(v for v in vulns if v.cve_id == "CVE-2026-59949")
    assert lz4.cvss == 8.1
    assert lz4.fix_version == "1.11.1"


def test_parse_dependency_check_json(tmp_path):
    path = tmp_path / "dc.json"
    path.write_text(json.dumps(DEPENDENCY_CHECK_SAMPLE), encoding="utf-8")

    vulns = parse_dependency_check_json(path)

    assert len(vulns) == 2
    assert {v.cve_id for v in vulns} == {"CVE-2026-54515", "CVE-2020-0001"}
    critical = next(v for v in vulns if v.cve_id == "CVE-2020-0001")
    assert critical.cvss == 9.8
    assert critical.source == "dependency-check"


def test_merge_deduplicates_same_cve_and_package_preferring_fix_version(tmp_path):
    trivy_path = tmp_path / "trivy.json"
    trivy_path.write_text(json.dumps(TRIVY_SAMPLE), encoding="utf-8")
    dc_path = tmp_path / "dc.json"
    dc_path.write_text(json.dumps(DEPENDENCY_CHECK_SAMPLE), encoding="utf-8")

    trivy_vulns = parse_trivy_json(trivy_path)
    dc_vulns = parse_dependency_check_json(dc_path)

    merged = merge_and_filter(dc_vulns, trivy_vulns, min_cvss=0.0)

    # CVE-2026-54515 appears in BOTH scanners for the same package -- must
    # collapse to one entry, and that entry must be the one WITH a fix_version
    # (Trivy's), not Dependency-Check's (which never carries one).
    jackson_entries = [v for v in merged if v.cve_id == "CVE-2026-54515"]
    assert len(jackson_entries) == 1
    assert jackson_entries[0].fix_version == "2.21.5"

    cve_ids = {v.cve_id for v in merged}
    assert cve_ids == {"CVE-2026-54515", "CVE-2026-59949", "CVE-2020-0001"}


def test_merge_filters_by_min_cvss():
    from app.scan.merge import Vulnerability

    vulns = [
        Vulnerability("CVE-LOW", "pkg:a", "1.0", None, 3.1, "LOW", "trivy"),
        Vulnerability("CVE-HIGH", "pkg:b", "1.0", None, 9.8, "CRITICAL", "trivy"),
    ]
    merged = merge_and_filter([], vulns, min_cvss=7.0)

    assert {v.cve_id for v in merged} == {"CVE-HIGH"}


def test_merge_treats_missing_cvss_as_zero_and_excludes_by_default_threshold():
    from app.scan.merge import Vulnerability

    vulns = [Vulnerability("CVE-UNKNOWN", "pkg:a", "1.0", None, None, None, "trivy")]
    merged = merge_and_filter([], vulns, min_cvss=7.0)

    assert merged == []
