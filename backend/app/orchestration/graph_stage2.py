"""Stage 2 per-vulnerability patch loop (spec: "2단계(옵션): 개별 CVE 패치").
Mirrors graph_stage1's shape:

    apply (mechanical version bump, if a fix_version is known)
        -> verify (mvn verify: compile + test)
        -> on failure, bounded AI-fix retries (COMPILE_FIX_MAX_ATTEMPTS)
        -> auto-apply file-count gate (COMPILE_FIX_AUTO_APPLY_MAX_FILES)
        -> success | needs_handoff

Note on "verify" here vs the spec's "mvn verify(...Dependency-Check/Trivy
재실행)": re-running a full scan on every retry of every single vulnerability
would be prohibitively slow even with a warm NVD cache. This loop's `verify`
checks build integrity only (compile+test); confirming the vulnerabilities
are actually gone happens via one before/after scan across the WHOLE batch
in the outer loop (stage2_loop.py), not per-attempt here.
"""

from __future__ import annotations

import time
from pathlib import Path

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph

from app.checkpoint.git_repo import changed_file_count
from app.config import Settings
from app.mvnrewrite.dependency_patch import patch_dependency_version
from app.mvnrewrite.mvn_client import mvn_verify
from app.mvnrewrite.subprocess_runner import build_log_path
from app.orchestration.callbacks import LocalLLMLogger
from app.orchestration.llm import get_chat_model
from app.orchestration.progress import LogFn, noop_log
from app.orchestration.state2 import Stage2State
from app.orchestration.tools import build_tools
from app.scan.merge import Vulnerability

_AI_PATCH_SYSTEM_PROMPT = (
    "You are patching a Maven Java project to resolve a specific known OSS vulnerability (CVE). "
    "Use the tools to inspect the project, find how the vulnerable dependency's version is declared, "
    "and edit pom.xml (or related files) to bump it to a safe version, then re-run the build to confirm "
    "nothing broke. Make the smallest change that resolves the vulnerability."
)


def build_stage2_graph(settings: Settings, on_log: LogFn = noop_log):
    async def apply_node(state: Stage2State) -> dict:
        if state["fix_version"] is None:
            await on_log("  자동 수정 버전 없음, AI 직접 수정 필요")
            return {}  # no known fix version -- nothing mechanical to try, straight to verify (will fail) -> ai_fix
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"  # sibling of work/ -- see ingest/workspace.py's WorkspacePaths
        group_id, artifact_id = state["package"].split(":", 1)
        log_path = build_log_path(output_dir, "stage2", f"dependency-patch-{state['cve_id']}")
        started_at = time.monotonic()
        result = await patch_dependency_version(
            work_dir, group_id, artifact_id, state["fix_version"], settings, log_path=log_path
        )
        elapsed = time.monotonic() - started_at
        outcome = "완료" if result.returncode == 0 else "실패"
        await on_log(f"  버전 패치 적용 {outcome}: {state['installed_version']} → {state['fix_version']} ({elapsed:.1f}s)")
        return {"last_build_output": f"[dependency patch exit={result.returncode}]\n{result.output}"}

    async def verify_node(state: Stage2State) -> dict:
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"
        log_path = build_log_path(output_dir, "stage2", f"mvn-verify-{state['cve_id']}")
        result = await mvn_verify(work_dir, settings, log_path=log_path)
        await on_log(f"  검증(mvn verify): {'통과' if result.returncode == 0 else '실패'}")
        if result.returncode == 0:
            return {"status": "success", "last_build_output": result.output}
        return {"last_build_output": result.output}

    def route_after_verify(state: Stage2State) -> str:
        if state.get("status") == "success":
            return END
        if state["attempt"] >= state["max_attempts"]:
            return "handoff"
        return "ai_fix"

    async def ai_fix_node(state: Stage2State) -> dict:
        work_dir = Path(state["work_dir"])
        output_dir = work_dir.parent / "output"  # sibling of work/ -- see ingest/workspace.py's WorkspacePaths
        await on_log(f"  AI 수정 시도 {state['attempt'] + 1}/{state['max_attempts']}")
        model = get_chat_model(settings)
        tools = build_tools(work_dir, settings, output_dir, stage="stage2")
        agent = create_agent(model, tools, system_prompt=_AI_PATCH_SYSTEM_PROMPT)

        fix_hint = f", a scanner-suggested fix version is {state['fix_version']}" if state["fix_version"] else ""
        result = await agent.ainvoke(
            {
                "messages": [
                    HumanMessage(
                        content=(
                            f"Resolve {state['cve_id']} in dependency {state['package']} "
                            f"(currently {state['installed_version']}{fix_hint}). "
                            f"Build/verify output so far (may be truncated):\n{state['last_build_output'][-6000:]}"
                        )
                    )
                ]
            },
            config={"callbacks": [LocalLLMLogger(output_dir, stage="stage2", model=settings.llm_model)]},
        )
        return {"attempt": state["attempt"] + 1, "messages": result["messages"]}

    async def route_after_ai_fix(state: Stage2State) -> str:
        work_dir = Path(state["work_dir"])
        count = changed_file_count(work_dir, settings)
        if count > state["max_auto_apply_files"]:
            await on_log(f"  변경 파일 수({count}개)가 한도({state['max_auto_apply_files']}개)를 초과해 자동 적용 중단")
            return "handoff"
        return "verify"

    async def handoff_node(state: Stage2State) -> dict:
        return {"status": "needs_handoff"}

    graph = StateGraph(Stage2State)
    graph.add_node("apply", apply_node)
    graph.add_node("verify", verify_node)
    graph.add_node("ai_fix", ai_fix_node)
    graph.add_node("handoff", handoff_node)

    graph.add_edge(START, "apply")
    graph.add_edge("apply", "verify")
    graph.add_conditional_edges("verify", route_after_verify, {END: END, "ai_fix": "ai_fix", "handoff": "handoff"})
    graph.add_conditional_edges("ai_fix", route_after_ai_fix, {"verify": "verify", "handoff": "handoff"})
    graph.add_edge("handoff", END)

    return graph.compile()


def initial_state_for_vulnerability(
    job_id: str, work_dir: Path, vuln: Vulnerability, settings: Settings
) -> Stage2State:
    return Stage2State(
        job_id=job_id,
        work_dir=str(work_dir),
        cve_id=vuln.cve_id,
        package=vuln.package,
        installed_version=vuln.installed_version,
        fix_version=vuln.fix_version,
        attempt=0,
        max_attempts=settings.compile_fix_max_attempts,
        max_auto_apply_files=settings.compile_fix_auto_apply_max_files,
        last_build_output="",
        status="running",
        messages=[],
    )


async def run_stage2_vulnerability(
    job_id: str, work_dir: Path, vuln: Vulnerability, settings: Settings, on_log: LogFn = noop_log
) -> Stage2State:
    graph = build_stage2_graph(settings, on_log)
    state = initial_state_for_vulnerability(job_id, work_dir, vuln, settings)
    return await graph.ainvoke(state)
