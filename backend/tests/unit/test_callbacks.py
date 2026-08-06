"""LocalLLMLogger mirrors every LLM call to output/logs/<stage>/llm/*.json,
independent of LangSmith (spec: 로그 및 진행 가시성). Exercised against a real
FakeListChatModel invocation (not a mock of the callback methods) so this
actually proves the on_chat_model_start/on_llm_end wiring works with
LangChain's real callback dispatch, not just that the methods are callable.
"""

from __future__ import annotations

import json

from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import HumanMessage

from app.orchestration.callbacks import LocalLLMLogger


async def test_successful_call_writes_one_log_file_with_input_and_output(tmp_path):
    model = FakeListChatModel(responses=["fixed the build"])
    logger = LocalLLMLogger(tmp_path, stage="stage1")

    await model.ainvoke([HumanMessage(content="please fix this")], config={"callbacks": [logger]})

    log_dir = tmp_path / "logs" / "stage1" / "llm"
    files = list(log_dir.glob("*.json"))
    assert len(files) == 1

    record = json.loads(files[0].read_text(encoding="utf-8"))
    assert record["input_messages"][0][0]["content"] == "please fix this"
    assert record["output"] == [["fixed the build"]]
    assert record["started_at"] <= record["ended_at"]


async def test_multiple_calls_write_separate_files(tmp_path):
    model = FakeListChatModel(responses=["first", "second"])
    logger = LocalLLMLogger(tmp_path, stage="stage2")

    await model.ainvoke([HumanMessage(content="a")], config={"callbacks": [logger]})
    await model.ainvoke([HumanMessage(content="b")], config={"callbacks": [logger]})

    log_dir = tmp_path / "logs" / "stage2" / "llm"
    assert len(list(log_dir.glob("*.json"))) == 2


async def test_log_dir_is_scoped_by_stage(tmp_path):
    model = FakeListChatModel(responses=["x"])
    logger = LocalLLMLogger(tmp_path, stage="stage1")

    await model.ainvoke([HumanMessage(content="a")], config={"callbacks": [logger]})

    assert (tmp_path / "logs" / "stage1" / "llm").is_dir()
    assert not (tmp_path / "logs" / "stage2").exists()
