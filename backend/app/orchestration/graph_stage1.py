"""Stage 1 self-verification loop (spec: "자가검증 루프", "AI 오케스트레이션 +
OpenRewrite 실행"). Phase 3 scope -- a single step:

    plan -> (gap? known recipe?) -> apply (OpenRewrite)
         -> recipe itself failed (exit != 0)? -> ai_fix directly
         -> else -> verify (mvn test-compile)
         -> on failure, bounded AI-fix retries (COMPILE_FIX_MAX_ATTEMPTS)
         -> auto-apply file-count gate (COMPILE_FIX_AUTO_APPLY_MAX_FILES)
         -> success | needs_handoff

Phase 4 wraps this in an outer loop over the full ordered step list and adds
git checkpoint/rollback between steps + the AI handoff guide.
"""

from __future__ import annotations

import time
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.checkpoint.git_repo import changed_file_count, commit_checkpoint
from app.config import Settings
from app.mvnrewrite.mvn_client import mvn_test_compile
from app.mvnrewrite.parent_patch import patch_parent_version
from app.mvnrewrite.recipe_catalog import RecipeCatalog, RecipeStep
from app.mvnrewrite.rewrite_client import run_openrewrite_recipes
from app.mvnrewrite.subprocess_runner import build_log_path
from app.orchestration.callbacks import LocalLLMLogger
from app.orchestration.llm import get_chat_model
from app.orchestration.planning import PlanStep
from app.orchestration.progress import LogFn, noop_log
from app.orchestration.state import Stage1State
from app.orchestration.tools import build_tools

_AI_FIX_SYSTEM_PROMPT = (
    "You are performing one step of a Maven/Java stack migration. Sometimes this means directly bumping a "
    "dependency/version and fixing whatever breaks, because no automated recipe exists for this step; other "
    "times it means fixing a build that failed after an OpenRewrite migration recipe already ran. Use the "
    "tools to inspect the current state and any failing build output, read the relevant source files, and "
    "edit them to reach a compiling build, then re-run the build to confirm. Make the smallest change that "
    "achieves the target -- don't refactor unrelated code."
)


def _major_minor(version: str) -> str:
    parts = version.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version


def plan_next_step(detected_boot: str | None, target_boot: str, catalog: RecipeCatalog) -> RecipeStep | None:
    """Pure lookup, no I/O: is there a single next cataloged step from the
    detected Spring Boot version toward target_boot? Returns None both when
    already at target and when there's a gap the catalog doesn't cover --
    callers distinguish those by comparing major.minor themselves (see
    _plan_node)."""
    if detected_boot is None:
        return None
    current_mm = _major_minor(detected_boot)
    if current_mm == target_boot:
        return None
    return catalog.spring_boot_step_from(current_mm)


def build_stage1_graph(settings: Settings, on_log: LogFn = noop_log):
    async def plan_node(state: Stage1State) -> dict:
        if state.get("plan_precomputed"):
            # A step was already supplied by an outer multi-step planner
            # (Phase 4's planning.build_migration_plan) -- nothing to
            # compute, just proceed with recipe/artifact as given (which may
            # themselves be None, for a step with no known recipe).
            return {}

        catalog = RecipeCatalog.load()
        detected = state["detected_spring_boot"]
        target = state["target_spring_boot"]

        if detected is not None and _major_minor(detected) == target:
            return {"status": "success"}  # already at target -- no-op, matches spec's Stage 1 auto-skip

        step = plan_next_step(detected, target, catalog)
        if step is None or step.recipe is None:
            # A real gap exists but the catalog has no known mechanical
            # recipe for this origin -- recipe/artifact stay None, and
            # route_after_plan sends this straight to ai_fix instead of
            # apply, so the AI attempts the version bump directly. That
            # naturally touches more files than a one-error compile fix, so
            # it gets the more generous no-recipe file-count ceiling.
            return {
                "recipe": None,
                "artifact": None,
                "max_auto_apply_files": settings.compile_fix_auto_apply_max_files_no_recipe,
            }
        return {"recipe": step.recipe, "artifact": step.artifact}

    def route_after_plan(state: Stage1State) -> str:
        if state.get("status") == "success":
            return END
        if state.get("step_kind") == "parent_pom":
            # No catalog recipe either, but this isn't a "no known recipe"
            # gap -- there's a mechanical action (apply_node's parent_pom
            # branch) to try before falling back to ai_fix.
            return "apply"
        return "ai_fix" if state.get("recipe") is None else "apply"

    async def apply_node(state: Stage1State) -> dict:
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"  # sibling of work/ -- see ingest/workspace.py's WorkspacePaths

        if state.get("step_kind") == "parent_pom":
            target = state["target_spring_boot"]  # generic "this step's target_version" slot, same as java/spring_ai steps
            started_at = time.monotonic()
            try:
                patch_parent_version(work_dir / "pom.xml", target)
                returncode, output = 0, f"parent <version> set to {target}"
            except Exception as exc:  # noqa: BLE001 -- surfaced via last_build_output, same as a failed subprocess
                returncode, output = 1, str(exc)
            elapsed = time.monotonic() - started_at
            commit_checkpoint(work_dir, settings, f"checkpoint: 사내 parent POM 버전을 {target}로 교체")
            outcome = "완료" if returncode == 0 else "실패"
            await on_log(f"  parent POM 버전 교체 {outcome} ({elapsed:.1f}s)")
            return {"apply_returncode": returncode, "last_build_output": f"[parent-patch exit={returncode}]\n{output}"}

        recipe_label = (state["recipe"] or "recipe").rsplit(".", 1)[-1]
        log_path = build_log_path(output_dir, "stage1", f"openrewrite-{recipe_label}")
        started_at = time.monotonic()
        result = await run_openrewrite_recipes(work_dir, [state["recipe"]], [state["artifact"]], settings, log_path=log_path)
        elapsed = time.monotonic() - started_at
        # Committed immediately, independent of whether verify/AI-fix later
        # succeeds -- the recipe's own changes are the tool-driven, lower-risk
        # part of a step and shouldn't be discarded just because a later
        # compile error (or an unrelated AI-fix failure) sends the step to
        # needs_handoff. multi_step.run_stage1_migration's rollback-on-failure
        # now resets only back to this commit, not past it -- see there.
        commit_checkpoint(work_dir, settings, f"checkpoint: applied recipe {state['recipe']}")
        outcome = "완료" if result.returncode == 0 else "실패"
        await on_log(f"  OpenRewrite 레시피 적용 {outcome} (exit={result.returncode}, {elapsed:.1f}s)")
        return {
            "apply_returncode": result.returncode,
            "last_build_output": f"[openrewrite exit={result.returncode}]\n{result.output}",
        }

    def route_after_apply(state: Stage1State) -> str:
        # The recipe itself never actually applied -- verify would only
        # either wrongly report success (nothing changed, but nothing was
        # broken either) or, if something upstream was already broken,
        # overwrite last_build_output with an unrelated result and bury the
        # real failure reason. Go straight to ai_fix with the recipe's own
        # failure output intact (spec: docs/superpowers/specs/2026-08-09-
        # stage1-apply-verify-integrity-design.md).
        return "verify" if state["apply_returncode"] == 0 else "ai_fix"

    async def verify_node(state: Stage1State) -> dict:
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"
        log_path = build_log_path(output_dir, "stage1", "mvn-test-compile")
        result = await mvn_test_compile(work_dir, settings, log_path=log_path)
        await on_log(f"  컴파일 검증: {'통과' if result.returncode == 0 else '실패'}")
        if result.returncode == 0:
            return {"status": "success", "last_build_output": result.output}
        return {"last_build_output": result.output}

    def route_after_verify(state: Stage1State) -> str:
        if state.get("status") == "success":
            return END
        if state["attempt"] >= state["max_attempts"]:
            return "handoff"
        return "ai_fix"

    async def ai_fix_node(state: Stage1State) -> dict:
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"  # sibling of work/ -- see ingest/workspace.py's WorkspacePaths
        await on_log(f"  AI 수정 시도 {state['attempt'] + 1}/{state['max_attempts']}")
        model = get_chat_model(settings)
        tools = build_tools(work_dir, settings, output_dir, stage="stage1")
        agent = create_agent(model, tools, system_prompt=_AI_FIX_SYSTEM_PROMPT)

        if state.get("step_kind") == "parent_pom":
            # Unlike the "no recipe" branch below, apply_node already ran
            # (it mechanically set <parent><version>) before verify failed --
            # so this is a real build failure to react to, not a from-scratch
            # request. Checked before the generic "recipe is None" branches,
            # since a parent_pom step also has recipe=None.
            instruction = (
                f"Updating this project's <parent><version> to {state['target_spring_boot']} is not compiling. "
                f"Build output (may be truncated):\n{state['last_build_output'][-6000:]}"
            )
        elif state["recipe"] is None and state["attempt"] == 0:
            # No cataloged recipe for this step -- nothing has been applied
            # yet, so there's no build failure to react to. Ask the AI to
            # perform the version bump itself; verify_node checks the result.
            instruction = (
                f"There is no automated migration recipe for this step: reach target version "
                f"{state['target_spring_boot']} from the current state of this project. Edit pom.xml and "
                f"source files as needed, then the build will be verified after your changes."
            )
        elif state["recipe"] is None:
            instruction = (
                f"Your previous attempt to reach target version {state['target_spring_boot']} still isn't "
                f"compiling. Build output (may be truncated):\n{state['last_build_output'][-6000:]}"
            )
        else:
            instruction = (
                f"The build is failing after applying recipe {state['recipe']}. "
                f"Build output (may be truncated):\n{state['last_build_output'][-6000:]}"
            )

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content=instruction)]},
            config={"callbacks": [LocalLLMLogger(output_dir, stage="stage1", model=settings.llm_model)]},
        )
        return {"attempt": state["attempt"] + 1, "messages": result["messages"]}

    async def route_after_ai_fix(state: Stage1State) -> str:
        work_dir = Path(state["work_dir"])
        # Blast-radius gate: too many files touched -> hand off rather than
        # keep letting the AI iterate unsupervised (spec: "자동 적용 범위 제한").
        count = changed_file_count(work_dir, settings)
        if count > state["max_auto_apply_files"]:
            await on_log(f"  변경 파일 수({count}개)가 한도({state['max_auto_apply_files']}개)를 초과해 자동 적용 중단")
            return "handoff"
        return "verify"

    async def handoff_node(state: Stage1State) -> dict:
        return {"status": "needs_handoff"}

    graph = StateGraph(Stage1State)
    graph.add_node("plan", plan_node)
    graph.add_node("apply", apply_node)
    graph.add_node("verify", verify_node)
    graph.add_node("ai_fix", ai_fix_node)
    graph.add_node("handoff", handoff_node)

    graph.add_edge(START, "plan")
    graph.add_conditional_edges("plan", route_after_plan, {"apply": "apply", "ai_fix": "ai_fix", END: END})
    graph.add_conditional_edges("apply", route_after_apply, {"verify": "verify", "ai_fix": "ai_fix"})
    graph.add_conditional_edges("verify", route_after_verify, {END: END, "ai_fix": "ai_fix", "handoff": "handoff"})
    graph.add_conditional_edges("ai_fix", route_after_ai_fix, {"verify": "verify", "handoff": "handoff"})
    graph.add_edge("handoff", END)

    return graph.compile()


def initial_state(
    job_id: str,
    work_dir: Path,
    detected_spring_boot: str | None,
    target_spring_boot: str,
    settings: Settings,
) -> Stage1State:
    return Stage1State(
        job_id=job_id,
        work_dir=str(work_dir),
        detected_spring_boot=detected_spring_boot,
        target_spring_boot=target_spring_boot,
        recipe=None,
        artifact=None,
        plan_precomputed=False,
        step_kind="spring_boot",  # this constructor is only ever used for plan_node's own self-computed Boot lookup
        attempt=0,
        max_attempts=settings.compile_fix_max_attempts,
        max_auto_apply_files=settings.compile_fix_auto_apply_max_files,
        apply_returncode=None,
        last_build_output="",
        status="running",
        messages=[],
    )


async def run_stage1_single_step(
    job_id: str,
    work_dir: Path,
    detected_spring_boot: str | None,
    target_spring_boot: str,
    settings: Settings,
    on_log: LogFn = noop_log,
) -> Stage1State:
    graph = build_stage1_graph(settings, on_log)
    state = initial_state(job_id, work_dir, detected_spring_boot, target_spring_boot, settings)
    return await graph.ainvoke(state)


def initial_state_for_step(job_id: str, work_dir: Path, step: PlanStep, settings: Settings) -> Stage1State:
    """Same graph, but the step (recipe/artifact) comes pre-decided from an
    outer multi-step plan (planning.build_migration_plan) instead of being
    computed by plan_node itself."""
    return Stage1State(
        job_id=job_id,
        work_dir=str(work_dir),
        detected_spring_boot=None,
        target_spring_boot=step.target_version,
        recipe=step.recipe,
        artifact=step.artifact,
        plan_precomputed=True,
        step_kind=step.kind,
        attempt=0,
        max_attempts=settings.compile_fix_max_attempts,
        max_auto_apply_files=(
            settings.compile_fix_auto_apply_max_files_no_recipe
            if step.recipe is None
            else settings.compile_fix_auto_apply_max_files
        ),
        apply_returncode=None,
        last_build_output="",
        status="running",
        messages=[],
    )


async def run_stage1_step(
    job_id: str, work_dir: Path, step: PlanStep, settings: Settings, on_log: LogFn = noop_log
) -> Stage1State:
    graph = build_stage1_graph(settings, on_log)
    state = initial_state_for_step(job_id, work_dir, step, settings)
    return await graph.ainvoke(state)
