"""LocalLLMLogger mirrors every LLM call to output/logs/<stage>/llm/*.md,
independent of LangSmith (spec: 로그 및 진행 가시성). Exercised against real
fake chat model invocations (not mocks of the callback methods) so this
actually proves the on_chat_model_start/on_llm_end wiring works with
LangChain's real callback dispatch, not just that the methods are callable.
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeListChatModel, FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.orchestration.callbacks import LocalLLMLogger


async def test_successful_call_writes_one_log_file_with_input_and_output(tmp_path):
    model = FakeListChatModel(responses=["fixed the build"])
    logger = LocalLLMLogger(tmp_path, stage="stage1", model="test-model")

    await model.ainvoke([HumanMessage(content="please fix this")], config={"callbacks": [logger]})

    log_dir = tmp_path / "logs" / "stage1" / "llm"
    files = list(log_dir.glob("*.md"))
    assert len(files) == 1

    content = files[0].read_text(encoding="utf-8")
    assert "please fix this" in content
    assert "fixed the build" in content
    assert "test-model" in content
    assert "stage1" in content


async def test_multiple_calls_write_separate_files_with_increasing_call_number(tmp_path):
    model = FakeListChatModel(responses=["first", "second"])
    logger = LocalLLMLogger(tmp_path, stage="stage2", model="test-model")

    await model.ainvoke([HumanMessage(content="a")], config={"callbacks": [logger]})
    await model.ainvoke([HumanMessage(content="b")], config={"callbacks": [logger]})

    log_dir = tmp_path / "logs" / "stage2" / "llm"
    files = sorted(log_dir.glob("*.md"))
    assert len(files) == 2
    assert "(#1)" in files[0].read_text(encoding="utf-8")
    assert "(#2)" in files[1].read_text(encoding="utf-8")


async def test_log_dir_is_scoped_by_stage(tmp_path):
    model = FakeListChatModel(responses=["x"])
    logger = LocalLLMLogger(tmp_path, stage="stage1", model="test-model")

    await model.ainvoke([HumanMessage(content="a")], config={"callbacks": [logger]})

    assert (tmp_path / "logs" / "stage1" / "llm").is_dir()
    assert not (tmp_path / "logs" / "stage2").exists()


async def test_system_prompt_is_shown_in_its_own_section(tmp_path):
    model = FakeListChatModel(responses=["ok"])
    logger = LocalLLMLogger(tmp_path, stage="stage1", model="test-model")

    await model.ainvoke(
        [SystemMessage(content="you are a build fixer"), HumanMessage(content="fix it")],
        config={"callbacks": [logger]},
    )

    content = (tmp_path / "logs" / "stage1" / "llm").glob("*.md").__next__().read_text(encoding="utf-8")
    assert "## System prompt" in content
    assert "you are a build fixer" in content


async def test_tool_call_response_is_captured_even_though_text_is_empty(tmp_path):
    """A tool-calling response has empty `.text` (the decision lives in
    `.tool_calls` instead) -- this is exactly the case the old JSON-only
    logger silently dropped, making it impossible to see e.g. which file
    path the model guessed when it called read_file/edit_file."""
    tool_call_message = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"relative_path": "ace-utility/pom.xml"}, "id": "call_1"}],
    )
    model = FakeMessagesListChatModel(responses=[tool_call_message])
    logger = LocalLLMLogger(tmp_path, stage="stage1", model="test-model")

    await model.ainvoke([HumanMessage(content="fix the missing dependency")], config={"callbacks": [logger]})

    content = (tmp_path / "logs" / "stage1" / "llm").glob("*.md").__next__().read_text(encoding="utf-8")
    assert "read_file" in content
    assert "ace-utility/pom.xml" in content
