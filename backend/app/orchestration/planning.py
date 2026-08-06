"""Turns "current detected versions" into an ordered list of steps to run
(spec: "단계적 마이그레이션 계획"). Pure logic, no I/O -- consumes
recipe_catalog lookups, doesn't run mvn/OpenRewrite/git itself.

Sequencing rules from the spec:
- Spring Boot is walked one cataloged hop at a time from the detected
  version to the target (never skipping a hop).
- Java's own upgrade is inserted as its own step *before* any Spring Boot
  steps ("Java 21은 Spring Boot 업그레이드 초반에").
- Spring Cloud is never a separate step -- whichever Boot step first reaches
  a Boot version with a known Cloud train gets that train bundled onto it
  ("Boot을 다 올린 뒤 Cloud만 별도로 나중에 처리하지 않는다"), and only if the
  project uses Spring Cloud at all (detected_spring_cloud is not None).
- Spring AI is inserted as its own step right after the first Boot step that
  reaches the 4.x line ("Spring AI 2.0은 Spring Boot 4.x 단계에서"), and only
  if the project uses Spring AI at all (detected_spring_ai is not None).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.mvnrewrite.pom_parser import DetectedVersions
from app.mvnrewrite.recipe_catalog import RecipeCatalog

StepKind = Literal["java", "spring_boot", "spring_ai"]


@dataclass
class PlanStep:
    kind: StepKind
    description: str
    recipe: str | None
    artifact: str | None
    target_version: str
    spring_cloud_train: str | None = None  # only set on a spring_boot step, when applicable


@dataclass
class MigrationPlan:
    steps: list[PlanStep]
    # Human-readable notes about gaps that were deliberately left OUT of
    # `steps` -- e.g. a dimension with a real gap but no cataloged recipe.
    # These must stay visible (report_builder surfaces them), not vanish
    # silently just because there's nothing automatic to run for them yet.
    skipped: list[str]


def _major_minor(version: str) -> str:
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def _java_major(version: str) -> int:
    # Handles both modern ("21") and legacy ("1.8") java.version conventions.
    mm = _major_minor(version)
    return int(mm.split(".")[1]) if mm.startswith("1.") else int(mm.split(".")[0])


def build_migration_plan(
    detected: DetectedVersions,
    target_boot: str,
    target_java: str,
    target_ai: str,
    catalog: RecipeCatalog | None = None,
) -> MigrationPlan:
    catalog = catalog or RecipeCatalog.load()
    steps: list[PlanStep] = []
    skipped: list[str] = []

    # 1. Java, first, if behind target.
    if detected.java_version is not None and _java_major(detected.java_version) < _java_major(target_java):
        java_step = next((s for s in catalog.java_steps if s.to_version == target_java), None)
        if java_step is not None and java_step.recipe is not None:
            steps.append(
                PlanStep(
                    kind="java",
                    description=f"Java {detected.java_version} -> {target_java}",
                    recipe=java_step.recipe,
                    artifact=java_step.artifact,
                    target_version=target_java,
                )
            )
        else:
            skipped.append(f"Java {detected.java_version} -> {target_java}: 카탈로그에 알려진 레시피 없음, 수동 처리 필요")

    # 2. Spring Boot, one cataloged hop at a time, with Cloud/AI bundled in.
    boot_hops = []
    current = _major_minor(detected.spring_boot_version) if detected.spring_boot_version else None
    while current is not None and current != target_boot:
        hop = catalog.spring_boot_step_from(current)
        if hop is None or hop.recipe is None:
            skipped.append(
                f"Spring Boot {current} -> {target_boot}: {current}부터는 카탈로그에 알려진 레시피 없음, 수동 처리 필요"
            )
            break  # plan stops here -- the rest needs a human/AI without a recipe to lean on
        boot_hops.append(hop)
        current = hop.to_version

    ai_step_inserted = False
    for hop in boot_hops:
        cloud_train = (
            catalog.spring_cloud_train_for_boot(hop.to_version) if detected.spring_cloud_version is not None else None
        )
        steps.append(
            PlanStep(
                kind="spring_boot",
                description=f"Spring Boot {hop.from_version} -> {hop.to_version}",
                recipe=hop.recipe,
                artifact=hop.artifact,
                target_version=hop.to_version,
                spring_cloud_train=cloud_train,
            )
        )

        if not ai_step_inserted and detected.spring_ai_version is not None and hop.to_version.startswith("4."):
            ai_step_inserted = True  # only ever consider this once, whether or not a recipe existed
            ai_hop = next((s for s in catalog.spring_ai_steps if s.to_version == target_ai), None)
            if ai_hop is not None and ai_hop.recipe is not None:
                steps.append(
                    PlanStep(
                        kind="spring_ai",
                        description=f"Spring AI {detected.spring_ai_version} -> {target_ai}",
                        recipe=ai_hop.recipe,
                        artifact=ai_hop.artifact,
                        target_version=target_ai,
                    )
                )
            else:
                skipped.append(
                    f"Spring AI {detected.spring_ai_version} -> {target_ai}: 카탈로그에 알려진 레시피 없음, 수동 처리 필요"
                )

    return MigrationPlan(steps=steps, skipped=skipped)
