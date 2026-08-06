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
    plan = _plan(java_version="21")
    assert all(step.kind != "java" for step in plan.steps)
    assert plan.skipped == []


def test_spring_boot_hops_are_sequential_not_skipped():
    plan = _plan()
    boot_steps = [s for s in plan.steps if s.kind == "spring_boot"]
    versions = [s.target_version for s in boot_steps]
    assert versions == ["3.0", "3.2", "3.4", "3.5", "4.0", "4.1"]


def test_no_spring_boot_steps_when_already_at_target():
    plan = _plan(spring_boot_version="4.1.0")
    assert all(step.kind != "spring_boot" for step in plan.steps)


def test_stops_at_catalog_gap_for_unknown_origin_and_records_it_as_skipped():
    plan = _plan(spring_boot_version="1.5.0")  # not a known catalog origin
    assert all(step.kind != "spring_boot" for step in plan.steps)
    assert any("Spring Boot" in note for note in plan.skipped)


def test_spring_cloud_bundled_onto_matching_boot_step_not_separate():
    plan = _plan(spring_cloud_version="2021.0.8")
    kinds = [s.kind for s in plan.steps]
    assert "spring_cloud" not in kinds  # never its own step

    boot_41_step = next(s for s in plan.steps if s.kind == "spring_boot" and s.target_version == "4.1")
    assert boot_41_step.spring_cloud_train == "2025.1"

    # Earlier hops should carry their own matching train too.
    boot_30_step = next(s for s in plan.steps if s.kind == "spring_boot" and s.target_version == "3.0")
    assert boot_30_step.spring_cloud_train == "2022.0"


def test_no_spring_cloud_train_attached_when_project_does_not_use_cloud():
    plan = _plan(spring_cloud_version=None)
    assert all(s.spring_cloud_train is None for s in plan.steps if s.kind == "spring_boot")


def test_spring_ai_with_no_known_recipe_is_skipped_not_silently_dropped():
    """The catalog currently has no known OpenRewrite recipe for Spring AI
    2.0 (confidence=unverified, recipe=null) -- the planner must surface
    that as a skipped/manual-work note, not just omit it with no trace."""
    plan = _plan(spring_ai_version="1.1.8")
    assert all(step.kind != "spring_ai" for step in plan.steps)
    assert any("Spring AI" in note for note in plan.skipped)


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
    assert not any("Spring AI" in note for note in plan.skipped)


def test_already_fully_at_target_produces_empty_plan():
    plan = _plan(java_version="21", spring_boot_version="4.1.0")
    assert plan.steps == []
    assert plan.skipped == []


def test_project_with_no_detected_versions_produces_empty_plan():
    plan = _plan(java_version=None, spring_boot_version=None)
    assert plan.steps == []
    assert plan.skipped == []
