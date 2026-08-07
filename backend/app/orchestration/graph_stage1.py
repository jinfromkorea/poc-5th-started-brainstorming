"""Stage 1 self-verification loop (spec: "자가검증 루프", "AI 오케스트레이션 +
OpenRewrite 실행"). Phase 3 scope -- a single step:

    plan -> (gap? known recipe?) -> apply (OpenRewrite) -> verify (mvn compile)
         -> on failure, bounded AI-fix retries (COMPILE_FIX_MAX_ATTEMPTS)
         -> auto-apply file-count gate (COMPILE_FIX_AUTO_APPLY_MAX_FILES)
         -> success | needs_handoff

Phase 4 wraps this in an outer loop over the full ordered step list and adds
git checkpoint/rollback between steps + the AI handoff guide.
"""

from __future__ import annotations

from pathlib import Path

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.checkpoint.git_repo import changed_file_count
from app.config import Settings
from app.mvnrewrite.mvn_client import mvn_compile
from app.mvnrewrite.recipe_catalog import RecipeCatalog, RecipeStep
from app.mvnrewrite.rewrite_client import run_openrewrite_recipes
from app.mvnrewrite.subprocess_runner import build_log_path
from app.orchestration.callbacks import LocalLLMLogger
from app.orchestration.llm import get_chat_model
from app.orchestration.planning import PlanStep
from app.orchestration.state import Stage1State
from app.orchestration.tools import build_tools

_AI_FIX_SYSTEM_PROMPT = (
    "You are fixing a Maven/Java build that failed after an OpenRewrite migration recipe was applied. "
    "Use the tools to inspect the failing build output, read the relevant source files, and edit them to "
    "fix the compile error, then re-run the build to confirm. Make the smallest change that fixes the "
    "error -- don't refactor unrelated code."
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


def build_stage1_graph(settings: Settings):
    async def plan_node(state: Stage1State) -> dict:
        if state.get("recipe") is not None:
            # A step was already supplied by an outer multi-step planner
            # (Phase 4's planning.build_migration_plan) -- nothing to
            # compute, just proceed to apply it.
            return {}

        catalog = RecipeCatalog.load()
        detected = state["detected_spring_boot"]
        target = state["target_spring_boot"]

        if detected is not None and _major_minor(detected) == target:
            return {"status": "success"}  # already at target -- no-op, matches spec's Stage 1 auto-skip

        step = plan_next_step(detected, target, catalog)
        if step is None or step.recipe is None:
            # a real gap exists but the catalog has no known mechanical
            # recipe for this origin -- Phase 3 doesn't attempt an
            # unguided AI fix from nothing, that's Phase 4/5 territory.
            return {"status": "needs_handoff"}
        return {"recipe": step.recipe, "artifact": step.artifact}

    def route_after_plan(state: Stage1State) -> str:
        return END if state.get("status") in ("success", "needs_handoff") else "apply"

    async def apply_node(state: Stage1State) -> dict:
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"  # sibling of work/ -- see ingest/workspace.py's WorkspacePaths
        recipe_label = (state["recipe"] or "recipe").rsplit(".", 1)[-1]
        log_path = build_log_path(output_dir, "stage1", f"openrewrite-{recipe_label}")
        result = await run_openrewrite_recipes(work_dir, [state["recipe"]], [state["artifact"]], settings, log_path=log_path)
        return {"last_build_output": f"[openrewrite exit={result.returncode}]\n{result.output}"}

    async def verify_node(state: Stage1State) -> dict:
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"
        log_path = build_log_path(output_dir, "stage1", "mvn-compile")
        result = await mvn_compile(work_dir, settings, log_path=log_path)
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
        model = get_chat_model(settings)
        tools = build_tools(work_dir, settings, output_dir, stage="stage1")
        agent = create_agent(model, tools, system_prompt=_AI_FIX_SYSTEM_PROMPT)

        result = await agent.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            f"The build is failing after applying recipe {state['recipe']}. "
                            f"Build output (may be truncated):\n{state['last_build_output'][-6000:]}"
                        )
                    )
                ]
            },
            config={"callbacks": [LocalLLMLogger(output_dir, stage="stage1", model=settings.llm_model)]},
        )
        return {"attempt": state["attempt"] + 1, "messages": result["messages"]}

    async def route_after_ai_fix(state: Stage1State) -> str:
        work_dir = Path(state["work_dir"])
        # Blast-radius gate: too many files touched -> hand off rather than
        # keep letting the AI iterate unsupervised (spec: "자동 적용 범위 제한").
        if changed_file_count(work_dir, settings) > state["max_auto_apply_files"]:
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
    graph.add_conditional_edges("plan", route_after_plan, {"apply": "apply", END: END})
    graph.add_edge("apply", "verify")
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
        attempt=0,
        max_attempts=settings.compile_fix_max_attempts,
        max_auto_apply_files=settings.compile_fix_auto_apply_max_files,
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
) -> Stage1State:
    graph = build_stage1_graph(settings)
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
        attempt=0,
        max_attempts=settings.compile_fix_max_attempts,
        max_auto_apply_files=settings.compile_fix_auto_apply_max_files,
        last_build_output="",
        status="running",
        messages=[],
    )


async def run_stage1_step(job_id: str, work_dir: Path, step: PlanStep, settings: Settings) -> Stage1State:
    graph = build_stage1_graph(settings)
    state = initial_state_for_step(job_id, work_dir, step, settings)
    return await graph.ainvoke(state)
