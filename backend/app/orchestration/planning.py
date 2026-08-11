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
- Spring AI is inserted as its own step *after all* Spring Boot steps, once
  the target is the 4.x line ("Spring AI 2.0은 Spring Boot 4.x 단계에서"), and
  only if the project uses Spring AI at all (detected_spring_ai is not None).
  Deliberately last, not right after Boot first touches 4.x: Boot may still
  have a catalog-gap hop left (e.g. 4.0 -> 4.1) after that point, and since
  run_stage1_migration stops the whole migration at the first step that
  needs_handoff, a failed Spring AI step must not be able to block an
  otherwise-still-reachable Boot target.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.mvnrewrite.pom_parser import DetectedVersions
from app.mvnrewrite.recipe_catalog import RecipeCatalog, RecipeStep

StepKind = Literal["parent_pom", "java", "spring_boot", "spring_ai"]


@dataclass
class PlanStep:
    kind: StepKind
    description: str
    recipe: str | None
    artifact: str | None
    target_version: str
    spring_cloud_train: str | None = None  # only set on a spring_boot step, when applicable
    third_party: bool = False  # recipe lives outside org.openrewrite.recipe:* (see recipe_catalog.RecipeStep)


@dataclass
class MigrationPlan:
    steps: list[PlanStep]


def _major_minor(version: str) -> str:
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def _java_major(version: str) -> int:
    # Handles both modern ("21") and legacy ("1.8") java.version conventions.
    mm = _major_minor(version)
    return int(mm.split(".")[1]) if mm.startswith("1.") else int(mm.split(".")[0])


def _third_party_suffix(step: RecipeStep) -> str:
    return " (서드파티 레시피)" if step.third_party else ""


def build_migration_plan(
    detected: DetectedVersions,
    target_boot: str,
    target_java: str,
    target_ai: str,
    catalog: RecipeCatalog | None = None,
) -> MigrationPlan:
    catalog = catalog or RecipeCatalog.load()
    steps: list[PlanStep] = []

    # 1. Java, first, if behind target.
    if detected.java_version is not None and _java_major(detected.java_version) < _java_major(target_java):
        java_step = next((s for s in catalog.java_steps if s.to_version == target_java), None)
        if java_step is not None and java_step.recipe is not None:
            steps.append(
                PlanStep(
                    kind="java",
                    description=f"Java {detected.java_version} -> {target_java}{_third_party_suffix(java_step)}",
                    recipe=java_step.recipe,
                    artifact=java_step.artifact,
                    target_version=target_java,
                    third_party=java_step.third_party,
                )
            )
        else:
            # No cataloged recipe -- still a real step, just one with no
            # mechanical shortcut. graph_stage1 has an AI attempt it directly
            # instead of leaving it out of the plan entirely.
            steps.append(
                PlanStep(
                    kind="java",
                    description=f"Java {detected.java_version} -> {target_java} (AI 직접 시도, 알려진 레시피 없음)",
                    recipe=None,
                    artifact=None,
                    target_version=target_java,
                )
            )

    # 2. Spring Boot, one cataloged hop at a time, with Cloud/AI bundled in.
    boot_hops: list[RecipeStep] = []
    current = _major_minor(detected.spring_boot_version) if detected.spring_boot_version else None
    while current is not None and current != target_boot:
        hop = catalog.spring_boot_step_from(current)
        if hop is None or hop.recipe is None:
            # Catalog runs out here -- bridge the rest of the way to
            # target_boot in one AI-driven step rather than stopping the
            # plan. There's no way to know what intermediate hops the AI
            # will land on, so this is necessarily the last boot hop.
            boot_hops.append(RecipeStep(to_version=target_boot, recipe=None, artifact=None, confidence="none", from_version=current))
            break
        boot_hops.append(hop)
        current = hop.to_version

    for hop in boot_hops:
        cloud_train = (
            catalog.spring_cloud_train_for_boot(hop.to_version) if detected.spring_cloud_version is not None else None
        )
        gap_suffix = (
            f" (AI 직접 시도, {hop.from_version}부터 카탈로그에 알려진 레시피 없음)"
            if hop.recipe is None
            else _third_party_suffix(hop)
        )
        steps.append(
            PlanStep(
                kind="spring_boot",
                description=f"Spring Boot {hop.from_version} -> {hop.to_version}{gap_suffix}",
                recipe=hop.recipe,
                artifact=hop.artifact,
                target_version=hop.to_version,
                spring_cloud_train=cloud_train,
                third_party=hop.third_party,
            )
        )

    # 3. Spring AI, once Spring Boot has fully landed on its target -- not
    # right after Boot first touches the 4.x line. Both this step and a
    # catalog-gap Boot hop (e.g. 4.0 -> 4.1, see recipe_catalog.yaml) are
    # commonly "no known recipe, AI edits code directly" steps; since
    # run_stage1_migration stops the whole migration at the first step that
    # needs_handoff (later steps assume earlier ones landed), doing Spring AI
    # before Boot is fully done would let a Spring AI failure block ever
    # attempting the still-outstanding (and more foundational) Boot hops.
    # This also means the check no longer depends on iterating boot_hops --
    # a project already sitting on Boot 4.x with no Boot steps needed at all
    # still gets its Spring AI step.
    if detected.spring_ai_version is not None and target_boot.startswith("4."):
        ai_hop = next((s for s in catalog.spring_ai_steps if s.to_version == target_ai), None)
        if ai_hop is not None and ai_hop.recipe is not None:
            steps.append(
                PlanStep(
                    kind="spring_ai",
                    description=f"Spring AI {detected.spring_ai_version} -> {target_ai}{_third_party_suffix(ai_hop)}",
                    recipe=ai_hop.recipe,
                    artifact=ai_hop.artifact,
                    target_version=target_ai,
                    third_party=ai_hop.third_party,
                )
            )
        else:
            steps.append(
                PlanStep(
                    kind="spring_ai",
                    description=f"Spring AI {detected.spring_ai_version} -> {target_ai} (AI 직접 시도, 알려진 레시피 없음)",
                    recipe=None,
                    artifact=None,
                    target_version=target_ai,
                )
            )

    return MigrationPlan(steps=steps)
