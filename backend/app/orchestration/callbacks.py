"""Local mirror of every LLM call, independent of LangSmith: writes one JSON
file per call to <output_dir>/logs/<stage>/llm/ (spec: 로그 및 진행 가시성 --
"LLM 호출 시 input/응답도 로그로 남겨줬으면 해"). LangSmith tracing itself needs no
code here -- setting LANGSMITH_TRACING=true and LANGSMITH_API_KEY in .env is
enough, since langchain-openai picks those up via its own env-based tracer.
This callback exists so a job's output/ folder is self-explanatory even to
someone who never had LangSmith access.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult


def _serialize_message(message: BaseMessage) -> dict:
    return {"type": message.type, "content": message.content}


def guard_log_size(record: dict[str, Any]) -> dict[str, Any]:
    """No-op for now. A prompt can carry a whole source file as context, so
    these local log files can get large -- the design spec explicitly defers
    deciding a size limit/truncation policy to a later iteration. This is
    the call site that policy will plug into once decided, so on_llm_end/
    on_llm_error don't need to change."""
    return record


class LocalLLMLogger(AsyncCallbackHandler):
    """One JSON file per LLM call. A fresh instance is created per graph
    node invocation (see graph_stage1.py/graph_stage2.py's ai_fix_node), so
    call ordering across a whole job is recovered from each file's
    timestamp-prefixed name, not from any counter kept here."""

    def __init__(self, output_dir: Path, stage: str) -> None:
        self._log_dir = Path(output_dir) / "logs" / stage / "llm"
        self._starts: dict[UUID, dict[str, Any]] = {}

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: list[list[BaseMessage]], *, run_id: UUID, **kwargs: Any
    ) -> None:
        self._starts[run_id] = {
            "input_messages": [[_serialize_message(m) for m in batch] for batch in messages],
            "started_at": time.time(),
        }

    async def on_llm_end(self, response: LLMResult, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._starts.pop(run_id, {"started_at": time.time()})
        record = {
            "run_id": str(run_id),
            "started_at": start["started_at"],
            "ended_at": time.time(),
            "input_messages": start.get("input_messages"),
            "output": [[gen.text for gen in batch] for batch in response.generations],
            "llm_output": response.llm_output,
        }
        self._write(run_id, record)

    async def on_llm_error(self, error: BaseException, *, run_id: UUID, **kwargs: Any) -> None:
        start = self._starts.pop(run_id, {"started_at": time.time()})
        record = {
            "run_id": str(run_id),
            "started_at": start["started_at"],
            "ended_at": time.time(),
            "input_messages": start.get("input_messages"),
            "error": str(error),
        }
        self._write(run_id, record)

    def _write(self, run_id: UUID, record: dict[str, Any]) -> None:
        record = guard_log_size(record)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{int(record['ended_at'] * 1000)}-{run_id}.json"
        (self._log_dir / filename).write_text(
            json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
