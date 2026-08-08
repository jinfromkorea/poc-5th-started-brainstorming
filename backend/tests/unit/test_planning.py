from __future__ import annotations

from app.mvnrewrite.pom_parser import DetectedVersions
from app.mvnrewrite.recipe_catalog import RecipeCatalog
from app.orchestration.planning import build_migration_plan

CATALOG = RecipeCatalog.load()


def _plan(**overrides):
    detected = DetectedVersions(
        java_version=overrides.pop("java_version", "11"),
        spring_boot_version=overrides.pop("spring_boot_version", "2.7.18"),
        spring_cloud_version=overrides.pop("spring_cloud_version", None),
        spring_ai_version=overrides.pop("spring_ai_version", None),
    )
    return build_migration_plan(
        detected,
        target_boot=overrides.pop("target_boot", "4.1"),
        target_java=overrides.pop("target_java", "21"),
        target_ai=overrides.pop("target_ai", "2.0"),
        catalog=CATALOG,
    )


def test_java_step_comes_first():
    plan = _plan()
    assert plan.steps[0].kind == "java"
    assert plan.steps[0].target_version == "21"


def test_no_java_step_when_already_at_or_above_target():
    plan = _plan(java_version="21", spring_boot_version="4.1.0")
    assert all(step.kind != "java" for step in plan.steps)


def test_spring_boot_hops_are_sequential_then_ai_bridges_the_final_gap():
    plan = _plan()
    boot_steps = [s for s in plan.steps if s.kind == "spring_boot"]
    versions = [s.target_version for s in boot_steps]
    # 4.0 -> 4.1은 카탈로그에 알려진 레시피가 없어(recipe_catalog.yaml 주석
    # 참고) 마지막 홉은 recipe=None인 "AI 직접 시도" 스텝으로 채워진다.
    assert versions == ["3.0", "3.2", "3.4", "3.5", "4.0", "4.1"]
    last = boot_steps[-1]
    assert last.recipe is None
    assert "AI 직접 시도" in last.description


def test_no_spring_boot_steps_when_already_at_target():
    plan = _plan(spring_boot_version="4.1.0")
    assert all(step.kind != "spring_boot" for step in plan.steps)


def test_unknown_origin_gets_a_single_ai_bridge_step_to_target():
    plan = _plan(spring_boot_version="1.5.0")  # not a known catalog origin
    boot_steps = [s for s in plan.steps if s.kind == "spring_boot"]
    assert len(boot_steps) == 1
    assert boot_steps[0].recipe is None
    assert boot_steps[0].target_version == "4.1"


def test_spring_cloud_bundled_onto_matching_boot_step_not_separate():
    plan = _plan(spring_cloud_version="2021.0.8")
    kinds = [s.kind for s in plan.steps]
    assert "spring_cloud" not in kinds  # never its own step

    # The AI-bridge step (4.0 -> 4.1) still carries the matching Cloud train
    # for its target version, same as any other boot step.
    boot_41_step = next(s for s in plan.steps if s.kind == "spring_boot" and s.target_version == "4.1")
    assert boot_41_step.spring_cloud_train == "2025.1"

    # Earlier hops should carry their own matching train too.
    boot_30_step = next(s for s in plan.steps if s.kind == "spring_boot" and s.target_version == "3.0")
    assert boot_30_step.spring_cloud_train == "2022.0"


def test_no_spring_cloud_train_attached_when_project_does_not_use_cloud():
    plan = _plan(spring_cloud_version=None)
    assert all(s.spring_cloud_train is None for s in plan.steps if s.kind == "spring_boot")


def test_spring_ai_with_no_known_recipe_still_gets_an_ai_bridge_step():
    """The catalog currently has no known OpenRewrite recipe for Spring AI
    2.0 (confidence=unverified, recipe=null) -- the planner must still add a
    step for it (recipe=None), not omit it with no trace."""
    plan = _plan(spring_ai_version="1.1.8")
    ai_steps = [s for s in plan.steps if s.kind == "spring_ai"]
    assert len(ai_steps) == 1
    assert ai_steps[0].recipe is None
    assert "AI 직접 시도" in ai_steps[0].description


def test_spring_ai_step_would_be_inserted_right_after_first_4x_boot_step_if_a_recipe_existed():
    """Exercises the insertion-point logic itself using a catalog patched
    with a fake Spring AI recipe, independent of whether the real catalog
    currently has one cataloged yet."""
    from app.mvnrewrite.recipe_catalog import RecipeStep

    class _PatchedCatalog(RecipeCatalog):
        @property
        def spring_ai_steps(self):
            return [RecipeStep(to_version="2.0", recipe="fake.Recipe", artifact="fake:artifact:RELEASE", confidence="verified")]

    detected = DetectedVersions(
        java_version="21", spring_boot_version="2.7.18", spring_cloud_version=None, spring_ai_version="1.1.8"
    )
    plan = build_migration_plan(
        detected, target_boot="4.1", target_java="21", target_ai="2.0", catalog=_PatchedCatalog(CATALOG._data)
    )

    kinds_and_versions = [(s.kind, s.target_version) for s in plan.steps]
    boot_40_index = kinds_and_versions.index(("spring_boot", "4.0"))
    assert kinds_and_versions[boot_40_index + 1] == ("spring_ai", "2.0")
    assert kinds_and_versions.count(("spring_ai", "2.0")) == 1  # only inserted once


def test_no_spring_ai_step_when_project_does_not_use_spring_ai():
    plan = _plan(spring_ai_version=None)
    assert all(step.kind != "spring_ai" for step in plan.steps)


def test_already_fully_at_target_produces_empty_plan():
    plan = _plan(java_version="21", spring_boot_version="4.1.0")
    assert plan.steps == []


def test_project_with_no_detected_versions_produces_empty_plan():
    plan = _plan(java_version=None, spring_boot_version=None)
    assert plan.steps == []
