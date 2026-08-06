"""LangGraph state for Stage 2's per-vulnerability patch loop."""

from __future__ import annotations

from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages

Stage2Status = Literal["running", "success", "needs_handoff"]


class Stage2State(TypedDict):
    job_id: str
    work_dir: str

    cve_id: str
    package: str  # "groupId:artifactId"
    installed_version: str
    fix_version: str | None  # None means no scanner-reported fix -- AI must find one

    attempt: int
    max_attempts: int
    max_auto_apply_files: int

    last_build_output: str
    status: Stage2Status

    messages: Annotated[list, add_messages]
