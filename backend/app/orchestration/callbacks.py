"""Local mirror of every LLM call, independent of LangSmith: writes one
Markdown file per call to <output_dir>/logs/<stage>/llm/ (spec: 로그 및 진행
가시성 -- "LLM 호출 시 input/응답도 로그로 남겨줬으면 해"). Markdown, not JSON --
so a person can open a file and actually read the prompt/response instead of
a raw object dump, following the format of a reference log from a sibling
project (poc-3rd-improved/spring-upgrade-agent): a header table, then
System prompt / conversation / Response sections.

Capturing tool_calls (not just plain text) matters here: this agent's
response is often a tool invocation (read_file/edit_file/run_build/
run_recipe) rather than prose, and a ChatGeneration's `.text` is empty in
that case -- the previous JSON-only version silently dropped exactly the
information needed to see e.g. which (possibly hallucinated) file path the
model decided to read/edit.

LangSmith tracing itself needs no code here -- setting LANGSMITH_TRACING=true
and LANGSMITH_API_KEY in .env is enough, since langchain-openai picks those
up via its own env-based tracer. This callback exists so a job's output/
folder is self-explanatory even to someone who never had LangSmith access.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import LLMResult

_ROLE_LABELS = {"human": "Human", "ai": "AI", "tool": "Tool", "system": "System"}


def _format_tool_calls(tool_calls: list[dict[str, Any]]) -> str:
    lines = ["**호출한 도구:**"]
    for call in tool_calls:
        args = ", ".join(f"{k}={v!r}" for k, v in (call.get("args") or {}).items())
        lines.append(f"- `{call.get('name')}({args})`")
    return "\n".join(lines)


def _format_message(message: BaseMessage) -> str:
    role = _ROLE_LABELS.get(message.type, message.type)
    lines = [f"### {role}"]
    if isinstance(message, ToolMessage):
        lines.append(f"(tool_call_id={message.tool_call_id})")
    content = message.content if isinstance(message.content, str) else str(message.content)
    if content.strip():
        lines.append(f"```text\n{content}\n```")
    if isinstance(message, AIMessage) and message.tool_calls:
        lines.append(_format_tool_calls(message.tool_calls))
    return "\n".join(lines)


class LocalLLMLogger(AsyncCallbackHandler):
    """One Markdown file per LLM call within a single agent invocation. A
    fresh instance is created per graph node invocation (see
    graph_stage1.py/graph_stage2.py's ai_fix_node), so call ordering across
    a whole job is recovered from each file's timestamp-prefixed name. The
    #N in each file's title is this instance's own call counter (1, 2, 3...
    within this one agent run -- e.g. how many back-and-forths it took to
    fix a single build failure), separate from that timestamp."""

    def __init__(self, output_dir: Path, stage: str, model: str | None = None) -> None:
        self._log_dir = Path(output_dir) / "logs" / stage / "llm"
        self._stage = stage
        self._model = model or "unknown"
        self._starts: dict[UUID, dict[str, Any]] = {}
        self._call_count = 0

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._starts[run_id] = {"messages": messages[0] if messages else [], "started_at": time.time()}

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._starts.pop(run_id, {"messages": [], "started_at": time.time()})
        self._write(run_id, start["messages"], response=response, error=None)

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._starts.pop(run_id, {"messages": [], "started_at": time.time()})
        self._write(run_id, start["messages"], response=None, error=error)

    def _render_response(self, response: LLMResult | None, error: BaseException | None) -> tuple[str, dict[str, Any]]:
        if error is not None:
            return f"```text\n(오류) {error}\n```", {}
        assert response is not None
        llm_output = response.llm_output or {}
        token_usage = llm_output.get("token_usage") or {}
        generations = response.generations[0] if response.generations else []
        if not generations:
            return "```text\n(응답 없음)\n```", token_usage
        gen = generations[0]
        message = getattr(gen, "message", None)
        if message is not None:
            return _format_message(message), token_usage
        return f"```text\n{gen.text}\n```", token_usage

    def _write(
        self, run_id: UUID, messages: list[BaseMessage], response: LLMResult | None, error: BaseException | None
    ) -> None:
        self._call_count += 1
        system_messages = [m for m in messages if isinstance(m, SystemMessage)]
        conversation = [m for m in messages if not isinstance(m, SystemMessage)]
        response_section, token_usage = self._render_response(response, error)

        lines = [
            f"# LLM 호출 — {self._stage} (#{self._call_count})",
            "",
            "| 항목 | 값 |",
            "|---|---|",
            f"| stage | {self._stage} |",
            f"| model | {self._model} |",
            f"| run_id | {run_id} |",
            f"| timestamp | {datetime.now(UTC).isoformat(timespec='seconds')} |",
            f"| tokens | in={token_usage.get('prompt_tokens', 'N/A')} out={token_usage.get('completion_tokens', 'N/A')} |",
            "",
        ]
        if system_messages:
            lines += ["## System prompt", "", f"```text\n{system_messages[0].content}\n```", ""]
        if conversation:
            lines += ["## 대화 이력", ""]
            for m in conversation:
                lines.append(_format_message(m))
                lines.append("")
        lines += ["## Response", "", response_section, ""]

        self._log_dir.mkdir(parents=True, exist_ok=True)
        millis = int(time.time() * 1000)
        (self._log_dir / f"{millis}-{run_id}.md").write_text("\n".join(lines), encoding="utf-8")
