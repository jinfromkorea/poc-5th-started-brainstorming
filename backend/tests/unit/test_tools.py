"""orchestration/tools.py's read_file/edit_file must never raise -- a
hallucinated/wrong path from the AI-fix agent should come back as a normal
"Error: ..." tool result the agent can see and retry with, not an exception
that crashes the whole job (confirmed real incident: the agent guessed
`ace-utility` for the real module `ace-util` and the job died outright).
"""

from __future__ import annotations

import pytest

from app.config import Settings
from app.orchestration.tools import build_tools


@pytest.fixture()
def settings() -> Settings:
    return Settings(_env_file=None)


def _tools(work_dir, settings):
    tools = build_tools(work_dir, settings, work_dir / "output", stage="stage1")
    return {t.name: t for t in tools}


async def test_read_file_returns_contents_for_a_real_file(tmp_path, settings):
    (tmp_path / "pom.xml").write_text("<project/>", encoding="utf-8")
    tools = _tools(tmp_path, settings)

    result = await tools["read_file"].ainvoke({"relative_path": "pom.xml"})

    assert result == "<project/>"


async def test_read_file_on_hallucinated_path_returns_error_with_real_siblings(tmp_path, settings):
    (tmp_path / "ace-util").mkdir()
    (tmp_path / "ace-util" / "pom.xml").write_text("<project/>", encoding="utf-8")
    tools = _tools(tmp_path, settings)

    result = await tools["read_file"].ainvoke({"relative_path": "ace-utility/pom.xml"})

    assert result.startswith("Error: file not found: ace-utility/pom.xml.")
    assert "ace-util" in result


async def test_read_file_on_directory_lists_its_contents_instead_of_raising(tmp_path, settings):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Main.java").write_text("class Main {}", encoding="utf-8")
    tools = _tools(tmp_path, settings)

    result = await tools["read_file"].ainvoke({"relative_path": "src"})

    assert "is a directory" in result
    assert "Main.java" in result


async def test_read_file_on_binary_file_returns_error_instead_of_raising(tmp_path, settings):
    (tmp_path / "app.class").write_bytes(b"\xca\xfe\xba\xbe\x00\x00\x00\x34")
    tools = _tools(tmp_path, settings)

    result = await tools["read_file"].ainvoke({"relative_path": "app.class"})

    assert result.startswith("Error:")
    assert "binary" in result


async def test_read_file_path_escape_returns_error_instead_of_raising(tmp_path, settings):
    tools = _tools(tmp_path, settings)

    result = await tools["read_file"].ainvoke({"relative_path": "../outside.txt"})

    assert result.startswith("Error:")
    assert "escapes" in result


async def test_edit_file_writes_new_file_and_creates_parent_dirs(tmp_path, settings):
    tools = _tools(tmp_path, settings)

    result = await tools["edit_file"].ainvoke({"relative_path": "new/nested/file.txt", "content": "hello"})

    assert "wrote 5 chars" in result
    assert (tmp_path / "new" / "nested" / "file.txt").read_text(encoding="utf-8") == "hello"


async def test_edit_file_path_escape_returns_error_instead_of_raising(tmp_path, settings):
    tools = _tools(tmp_path, settings)

    result = await tools["edit_file"].ainvoke({"relative_path": "../outside.txt", "content": "pwned"})

    assert result.startswith("Error:")
    assert "escapes" in result
    assert not (tmp_path.parent / "outside.txt").exists()
