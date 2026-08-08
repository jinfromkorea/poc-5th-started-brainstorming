from __future__ import annotations

from app.mvnrewrite.recipe_catalog import RecipeCatalog


def test_loads_default_catalog():
    catalog = RecipeCatalog.load()

    boot_steps = catalog.spring_boot_steps
    assert len(boot_steps) > 0
    assert all(step.to_version for step in boot_steps)


def test_spring_boot_step_from_known_origin():
    catalog = RecipeCatalog.load()

    step = catalog.spring_boot_step_from("2.7")

    assert step is not None
    assert step.to_version == "3.0"
    assert step.has_known_recipe is True
    assert step.confidence == "verified"
    assert step.third_party is False  # official org.openrewrite.recipe:rewrite-spring


def test_spring_boot_step_from_unknown_origin_is_none():
    catalog = RecipeCatalog.load()

    assert catalog.spring_boot_step_from("1.5") is None


def test_spring_ai_step_has_a_known_recipe():
    catalog = RecipeCatalog.load()

    ai_steps = catalog.spring_ai_steps
    assert len(ai_steps) == 1
    assert ai_steps[0].to_version == "2.0"
    assert ai_steps[0].has_known_recipe is True
    assert ai_steps[0].confidence == "verified"
    assert ai_steps[0].third_party is True  # Arconia Migrations, not org.openrewrite.recipe:*


def test_spring_cloud_train_lookup_by_boot_version():
    catalog = RecipeCatalog.load()

    assert catalog.spring_cloud_train_for_boot("4.1") == "2025.1"
    assert catalog.spring_cloud_train_for_boot("9.9") is None


def test_java_steps_include_21():
    catalog = RecipeCatalog.load()

    to_versions = {step.to_version for step in catalog.java_steps}
    assert "21" in to_versions
