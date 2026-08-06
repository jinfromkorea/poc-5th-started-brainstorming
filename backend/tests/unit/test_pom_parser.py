from __future__ import annotations

from app.mvnrewrite.pom_parser import extract_versions

_NS = "http://maven.apache.org/POM/4.0.0"


def test_extracts_versions_bom_import_style(tmp_path):
    """Matches ace-parent's real pattern: a property + BOM import inside
    dependencyManagement, single-hop ${...} resolution against the same
    file's own <properties>."""
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <properties>
            <java.version>21</java.version>
            <spring-boot.version>3.5.16</spring-boot.version>
            <spring-ai.version>1.1.8</spring-ai.version>
        </properties>
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-dependencies</artifactId>
                    <version>${{spring-boot.version}}</version>
                    <type>pom</type>
                    <scope>import</scope>
                </dependency>
                <dependency>
                    <groupId>org.springframework.ai</groupId>
                    <artifactId>spring-ai-bom</artifactId>
                    <version>${{spring-ai.version}}</version>
                    <type>pom</type>
                    <scope>import</scope>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    result = extract_versions(pom)

    assert result.java_version == "21"
    assert result.spring_boot_version == "3.5.16"
    assert result.spring_ai_version == "1.1.8"
    assert result.spring_cloud_version is None


def test_extracts_spring_boot_from_starter_parent_style(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <parent>
            <groupId>org.springframework.boot</groupId>
            <artifactId>spring-boot-starter-parent</artifactId>
            <version>2.7.18</version>
        </parent>
        <properties>
            <maven.compiler.release>11</maven.compiler.release>
        </properties>
    </project>""")

    result = extract_versions(pom)

    assert result.spring_boot_version == "2.7.18"
    assert result.java_version == "11"


def test_extracts_spring_cloud_bom(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>org.springframework.cloud</groupId>
                    <artifactId>spring-cloud-dependencies</artifactId>
                    <version>2021.0.8</version>
                    <type>pom</type>
                    <scope>import</scope>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    result = extract_versions(pom)

    assert result.spring_cloud_version == "2021.0.8"


def test_spring_cloud_property_wins_over_misleading_anchor_artifact(tmp_path):
    """Real bug, found by running against a real Spring Cloud project and
    fixed: Spring Cloud's release-train name (e.g. "2021.0.8") is a label for
    a *set* of independently-versioned components, not a version any of them
    actually carries -- a real effective-pom run showed spring-cloud-context
    resolved to "3.1.7" while the actual train was "2021.0.8". The
    spring-cloud.version property (confirmed to survive effective-pom
    resolution) must win; there is deliberately no anchor-artifact fallback
    for Spring Cloud (see pom_parser module docstring)."""
    pom = tmp_path / "effective-pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <properties>
            <spring-cloud.version>2021.0.8</spring-cloud.version>
        </properties>
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>org.springframework.cloud</groupId>
                    <artifactId>spring-cloud-context</artifactId>
                    <version>3.1.7</version>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    result = extract_versions(pom)

    assert result.spring_cloud_version == "2021.0.8"


def test_missing_fields_are_none(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f'<project xmlns="{_NS}"><artifactId>bare</artifactId></project>')

    result = extract_versions(pom)

    assert result.java_version is None
    assert result.spring_boot_version is None
    assert result.spring_cloud_version is None
    assert result.spring_ai_version is None


def test_extracts_versions_from_bom_expanded_effective_pom_shape(tmp_path):
    """Simulates what `mvn help:effective-pom` actually produces (confirmed
    empirically against ace-parent): the BOM self-reference (e.g.
    spring-boot-dependencies) disappears, replaced by its concrete managed
    artifacts with literal resolved versions -- no ${...} left anywhere."""
    pom = tmp_path / "effective-pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <properties>
            <java.version>21</java.version>
        </properties>
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot</artifactId>
                    <version>3.5.16</version>
                </dependency>
                <dependency>
                    <groupId>org.springframework.ai</groupId>
                    <artifactId>spring-ai-commons</artifactId>
                    <version>1.1.8</version>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    result = extract_versions(pom)

    assert result.spring_boot_version == "3.5.16"
    assert result.spring_ai_version == "1.1.8"
    # No spring-cloud.version property and no BOM self-reference present --
    # correctly None, not a misleading guess from spring-cloud-context's own
    # version (see test_spring_cloud_property_wins_over_misleading_anchor_artifact).
    assert result.spring_cloud_version is None


def test_extracts_from_multi_module_effective_pom_projects_wrapper(tmp_path):
    """`mvn help:effective-pom` against a multi-module reactor wraps multiple
    <project> elements in a top-level <projects> (confirmed empirically
    against a real ace-parent run) -- the root module is always first."""
    pom = tmp_path / "effective-pom.xml"
    pom.write_text(f"""<projects>
        <project xmlns="{_NS}">
            <artifactId>reactor-root</artifactId>
            <properties><java.version>21</java.version></properties>
            <dependencyManagement>
                <dependencies>
                    <dependency>
                        <groupId>org.springframework.boot</groupId>
                        <artifactId>spring-boot</artifactId>
                        <version>3.5.16</version>
                    </dependency>
                </dependencies>
            </dependencyManagement>
        </project>
        <project xmlns="{_NS}">
            <artifactId>reactor-child</artifactId>
        </project>
    </projects>""")

    result = extract_versions(pom)

    assert result.java_version == "21"
    assert result.spring_boot_version == "3.5.16"


def test_unresolvable_property_reference_returned_as_is(tmp_path):
    """A ${prop} that isn't defined locally (e.g. inherited from an external
    parent not present in the ingested source) can't be resolved by static
    parsing alone -- returned verbatim rather than silently guessed, so
    callers can tell "unresolved" apart from "genuinely absent"."""
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-dependencies</artifactId>
                    <version>${{spring-boot.version}}</version>
                    <type>pom</type>
                    <scope>import</scope>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    result = extract_versions(pom)

    assert result.spring_boot_version == "${spring-boot.version}"
