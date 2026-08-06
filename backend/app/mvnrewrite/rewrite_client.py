"""Invokes OpenRewrite recipes against work/ by full plugin coordinates on
the command line -- never by injecting <plugin> config into the target
project's pom.xml. Editing work/pom.xml to add rewrite-maven-plugin would
show up as a spurious change in every `git diff baseline..HEAD`, polluting
the final patch unless reverted flawlessly every single time; invoking by
coordinates touches only source files.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.mvnrewrite.subprocess_runner import SubprocessResult, run_subprocess

# NOT pinned to a fixed version -- confirmed empirically that this actually
# matters: pinning the plugin to an old fixed version (5.46.0) while the
# recipe artifact (recipe_catalog.yaml entries all use ":RELEASE") floats to
# whatever's current broke with a hard class-incompatibility error
# (IncompatibleClassChangeError on org.openrewrite.rpc.RpcRecipe) the moment
# the two drifted out of sync. OpenRewrite's plugin and recipe modules are
# released together and expected to be used at matching versions, so both
# float to RELEASE together. Pinning both to a specific *verified-compatible*
# pair is a worthwhile reproducibility improvement later, but only once
# there's an actual known-good pair to pin -- not before.
REWRITE_MAVEN_PLUGIN_COORDINATES = "org.openrewrite.maven:rewrite-maven-plugin:RELEASE"


async def run_openrewrite_recipes(
    work_dir: Path,
    active_recipes: list[str],
    recipe_artifact_coordinates: list[str],
    settings: Settings,
    log_path: Path | None = None,
    on_line: Callable[[str], None] | None = None,
) -> SubprocessResult:
    """``active_recipes``: fully-qualified recipe class names to run (e.g.
    ``org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0``).
    ``recipe_artifact_coordinates``: the Maven artifact(s) those recipes
    live in (e.g. ``org.openrewrite.recipe:rewrite-spring:RELEASE``) -- most
    recipes aren't bundled with rewrite-maven-plugin itself and must be
    pulled in this way."""
    if not active_recipes:
        raise ValueError("active_recipes must not be empty")

    args = [
        "mvn",
        "-B",
        "-U",
        f"{REWRITE_MAVEN_PLUGIN_COORDINATES}:run",
        f"-Drewrite.activeRecipes={','.join(active_recipes)}",
    ]
    if recipe_artifact_coordinates:
        args.append(f"-Drewrite.recipeArtifactCoordinates={','.join(recipe_artifact_coordinates)}")

    return await run_subprocess(args, work_dir, settings, log_path=log_path, on_line=on_line)
