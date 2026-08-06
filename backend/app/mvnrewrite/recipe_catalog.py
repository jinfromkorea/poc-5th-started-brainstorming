"""Loads/queries the static step -> recipe table (recipe_catalog.yaml).

This module only loads and looks things up. Turning "current detected
versions" into an ordered list of steps to actually run is the multi-step
migration *planner*'s job (Phase 4), which consumes these lookups.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_CATALOG_PATH = Path(__file__).parent / "recipe_catalog.yaml"


@dataclass
class RecipeStep:
    to_version: str
    recipe: str | None
    artifact: str | None
    confidence: str
    from_version: str | None = None

    @property
    def has_known_recipe(self) -> bool:
        return self.recipe is not None

    @classmethod
    def from_yaml_entry(cls, entry: dict) -> RecipeStep:
        # "from"/"to" read naturally in YAML but "from" is a Python keyword,
        # so it can't be splatted straight into RecipeStep(**entry).
        return cls(
            to_version=entry["to"],
            recipe=entry.get("recipe"),
            artifact=entry.get("artifact"),
            confidence=entry["confidence"],
            from_version=entry.get("from"),
        )


class RecipeCatalog:
    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def load(cls, path: Path | None = None) -> RecipeCatalog:
        path = path or _DEFAULT_CATALOG_PATH
        with path.open(encoding="utf-8") as f:
            return cls(yaml.safe_load(f))

    @property
    def spring_boot_steps(self) -> list[RecipeStep]:
        return [RecipeStep.from_yaml_entry(step) for step in self._data.get("spring_boot_steps", [])]

    @property
    def java_steps(self) -> list[RecipeStep]:
        return [RecipeStep.from_yaml_entry(step) for step in self._data.get("java_steps", [])]

    @property
    def spring_ai_steps(self) -> list[RecipeStep]:
        return [RecipeStep.from_yaml_entry(step) for step in self._data.get("spring_ai_steps", [])]

    def spring_cloud_train_for_boot(self, boot_version: str) -> str | None:
        """boot_version like "4.1" (major.minor, no patch) -> Cloud train
        like "2025.1", per the spec's 1:1 Boot<->Cloud-train coupling."""
        return self._data.get("spring_cloud_trains", {}).get(boot_version)

    def spring_boot_step_from(self, from_version: str) -> RecipeStep | None:
        """The single next step in the chain starting at from_version, or
        None if from_version isn't a known step origin (e.g. already at or
        past the last cataloged step)."""
        for step in self.spring_boot_steps:
            if step.from_version == from_version:
                return step
        return None
