from __future__ import annotations

from app.mvnrewrite.dependency_patch import find_version_property

_NS = "http://maven.apache.org/POM/4.0.0"


def test_finds_property_name_in_dependency_management(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <dependencyManagement>
            <dependencies>
                <dependency>
                    <groupId>org.apache.commons</groupId>
                    <artifactId>commons-lang3</artifactId>
                    <version>${{commons-lang3.version}}</version>
                </dependency>
            </dependencies>
        </dependencyManagement>
    </project>""")

    assert find_version_property(pom, "org.apache.commons", "commons-lang3") == "commons-lang3.version"


def test_finds_property_name_in_plain_dependencies(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <dependencies>
            <dependency>
                <groupId>com.example</groupId>
                <artifactId>some-lib</artifactId>
                <version>${{some-lib.version}}</version>
            </dependency>
        </dependencies>
    </project>""")

    assert find_version_property(pom, "com.example", "some-lib") == "some-lib.version"


def test_returns_none_when_dependency_has_literal_version(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f"""<project xmlns="{_NS}">
        <dependencies>
            <dependency>
                <groupId>com.example</groupId>
                <artifactId>some-lib</artifactId>
                <version>1.2.3</version>
            </dependency>
        </dependencies>
    </project>""")

    assert find_version_property(pom, "com.example", "some-lib") is None


def test_returns_none_when_dependency_not_declared_here(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(f'<project xmlns="{_NS}"></project>')

    assert find_version_property(pom, "com.example", "not-here") is None
