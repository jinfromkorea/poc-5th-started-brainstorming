"""LangGraph state for Stage 1's self-verification loop (spec: "자가검증
루프"). Phase 3 scope: a *single* step (one recipe, or none) applied and
verified with a bounded AI-assisted retry loop. Phase 4 wraps this in an
outer loop over the full ordered step list from recipe_catalog.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

Stage1Status = Literal["running", "success", "failed", "needs_handoff"]


class Stage1State(TypedDict):
    job_id: str
    work_dir: str

    # What this single step is meant to accomplish.
    detected_spring_boot: str | None  # e.g. "3.5.16", from pom_parser.DetectedVersions
    target_spring_boot: str  # e.g. "4.1"
    recipe: str | None  # fully-qualified OpenRewrite recipe class, or None
    artifact: str | None  # recipe's Maven artifact coordinates, or None
    # True when recipe/artifact were already decided by an outer multi-step
    # planner (planning.build_migration_plan) -- plan_node then skips its own
    # single-step planning logic entirely, whether or not recipe is None.
    plan_precomputed: bool

    # Retry bookkeeping (spec: COMPILE_FIX_MAX_ATTEMPTS / _AUTO_APPLY_MAX_FILES).
    attempt: int
    max_attempts: int
    max_auto_apply_files: int

    # Exit code of the most recent apply_node run (None until apply_node has
    # actually run once -- a step with no cataloged recipe skips apply_node
    # entirely, going plan -> ai_fix directly, so this stays None for it).
    # route_after_apply (spec: docs/superpowers/specs/2026-08-09-stage1-
    # apply-verify-integrity-design.md) uses this to skip verify entirely
    # when the recipe itself never actually applied.
    apply_returncode: int | None

    last_build_output: str
    status: Stage1Status

    messages: Annotated[list, add_messages]
