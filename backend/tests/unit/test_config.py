from __future__ import annotations

import os

import pytest

from app.config import BACKEND_DIR, Settings, configure_langsmith_env


def test_defaults_load_without_env_file():
    s = Settings(_env_file=None)
    assert s.max_concurrent_repos == 3
    assert s.fail_on_cvss == 7.0
    assert s.compile_fix_max_attempts == 2


def test_paths_resolve_against_backend_dir_not_cwd():
    """Must resolve against backend/, not process CWD -- otherwise
    ./data/... would collide with the top-level data/ folder holding the
    4 reference zips when launched from the repo root."""
    s = Settings(_env_file=None)
    resolved = s.resolve_path("./data/app.db")
    assert resolved == (BACKEND_DIR / "data" / "app.db").resolve()
    assert str(resolved).startswith(str(BACKEND_DIR))


def test_database_url_relative_sqlite_resolves_against_backend_dir():
    s = Settings(_env_file=None, database_url="sqlite:///./data/app.db")
    resolved = s.database_url_resolved
    assert resolved.startswith("sqlite:///")
    assert str(BACKEND_DIR).replace("\\", "/") in resolved


def test_database_url_absolute_sqlite_passes_through():
    s = Settings(_env_file=None, database_url="sqlite:////already/absolute/app.db")
    assert s.database_url_resolved == "sqlite:////already/absolute/app.db"


def test_database_url_non_sqlite_passes_through():
    s = Settings(_env_file=None, database_url="postgresql://user:pass@host/db")
    assert s.database_url_resolved == "postgresql://user:pass@host/db"


@pytest.fixture()
def clean_langsmith_env():
    keys = ["LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_PROJECT", "LANGSMITH_ENDPOINT"]
    saved = {k: os.environ.pop(k, None) for k in keys}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def test_configure_langsmith_env_exports_settings_to_os_environ(clean_langsmith_env):
    """Regression test: pydantic-settings parsing backend/.env into a
    Settings object does NOT, by itself, make those values visible to
    LangChain's own env-based auto-tracing -- that reads os.environ
    directly. Confirmed empirically before writing this fix."""
    s = Settings(
        _env_file=None,
        langsmith_api_key="lsv2_test_key",
        langsmith_tracing=True,
        langsmith_project="my-project",
        langsmith_endpoint="https://api.smith.langchain.com",
    )

    configure_langsmith_env(s)

    assert os.environ["LANGSMITH_API_KEY"] == "lsv2_test_key"
    assert os.environ["LANGSMITH_TRACING"] == "true"
    assert os.environ["LANGSMITH_PROJECT"] == "my-project"
    assert os.environ["LANGSMITH_ENDPOINT"] == "https://api.smith.langchain.com"


def test_configure_langsmith_env_noop_when_api_key_blank(clean_langsmith_env):
    s = Settings(_env_file=None, langsmith_api_key="")

    configure_langsmith_env(s)

    assert "LANGSMITH_API_KEY" not in os.environ
    assert "LANGSMITH_TRACING" not in os.environ


def test_configure_langsmith_env_does_not_override_explicit_shell_env(clean_langsmith_env):
    os.environ["LANGSMITH_PROJECT"] = "shell-wins"
    s = Settings(_env_file=None, langsmith_api_key="lsv2_test_key", langsmith_project="dotenv-value")

    configure_langsmith_env(s)

    assert os.environ["LANGSMITH_PROJECT"] == "shell-wins"
