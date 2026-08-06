"""Central settings. All paths resolve against backend/ (this file's parent's
parent), never process CWD -- otherwise ``./data`` would collide with the
top-level ``data/`` folder that holds the 4 reference zips."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(BACKEND_DIR / ".env"), extra="ignore")

    # LLM
    openai_api_key: str = ""
    llm_model: str = "gpt-5.4-mini"
    inventory_maven_enrichment_enabled: bool = True
    inventory_deep_agent_enabled: bool = True
    inventory_confidence_threshold: float = 0.85
    plan_deep_agent_enabled: bool = True
    plan_confidence_threshold: float = 0.85
    llm_base_url: str = ""
    llm_max_tokens: int = 4096
    llm_monthly_budget_usd: float = 500

    # Embedding (reserved, unimplemented)
    embedding_api_key: str = ""
    embedding_model: str = "text-embedding-3-large"

    # SCA
    nvd_api_key: str = ""
    dependency_check_data_dir: str = "./data/nvd-cache"
    trivy_cache_dir: str = "./data/trivy-cache"

    # Git
    git_token: str = ""
    git_ssh_key_path: str = ""
    git_author_name: str = "upgrade-agent"
    git_author_email: str = "upgrade-agent@example.com"

    # Nexus (reserved, unimplemented)
    nexus_url: str = ""
    nexus_username: str = ""
    nexus_password: str = ""

    # App security
    app_secret_key: str = ""
    api_auth_token: str = ""

    # DB / job data
    database_url: str = "sqlite:///./data/app.db"
    jobs_data_dir: str = "./data/jobs"

    # Notifications (reserved, unimplemented)
    slack_webhook_url: str = ""
    smtp_host: str = ""
    smtp_user: str = ""
    smtp_password: str = ""

    # Network
    http_proxy: str = ""
    https_proxy: str = ""
    no_proxy: str = "localhost,127.0.0.1"

    # Limits / safety
    max_concurrent_repos: int = 3
    fail_on_cvss: float = 7.0
    build_timeout_seconds: int = 900
    compile_fixer_enabled: bool = True
    compile_fix_max_attempts: int = 2
    compile_fix_auto_apply_max_files: int = 3
    upload_max_mb: int = 100
    upload_max_extracted_mb: int = 500
    upload_max_files: int = 20000

    # CI/CD (reserved, unimplemented)
    ci_trigger_token: str = ""

    # LangSmith
    langsmith_api_key: str = ""
    langsmith_tracing: bool = True
    langsmith_project: str = "poscodx_tracing"
    langsmith_endpoint: str = "https://api.smith.langchain.com"

    # Server
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "INFO"
    cors_allow_origins: str = "http://localhost:5500"

    def resolve_path(self, value: str) -> Path:
        """Resolve a config path (e.g. ``./data/app.db``'s directory) against
        backend/, not CWD."""
        p = Path(value)
        return p if p.is_absolute() else (BACKEND_DIR / p).resolve()

    @property
    def jobs_dir(self) -> Path:
        return self.resolve_path(self.jobs_data_dir)

    @property
    def dependency_check_dir(self) -> Path:
        return self.resolve_path(self.dependency_check_data_dir)

    @property
    def trivy_cache_path(self) -> Path:
        return self.resolve_path(self.trivy_cache_dir)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def database_url_resolved(self) -> str:
        """A relative sqlite URL (sqlite:///./data/app.db) resolves against
        backend/, same principle as every other path setting -- otherwise it
        would land relative to process CWD and could collide with the
        top-level data/ folder. Absolute sqlite URLs (sqlite:////...) and
        non-sqlite URLs (e.g. a future postgres:// for a shared deployment)
        pass through unchanged."""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix) and not self.database_url.startswith("sqlite:////"):
            relative = self.database_url[len(prefix) :]
            return f"{prefix}{self.resolve_path(relative).as_posix()}"
        return self.database_url


def configure_langsmith_env(settings: Settings) -> None:
    """LangChain's own auto-tracing reads LANGSMITH_*/LANGCHAIN_* directly
    from os.environ (langsmith.utils.get_env_var) -- confirmed empirically
    that pydantic-settings parsing backend/.env into this Settings object
    does NOT export them there, so tracing would silently stay off despite
    .env looking correctly configured. Called once at app startup (see
    main.py's create_app()). Uses setdefault so an explicitly-exported shell
    env var always wins over .env."""
    if not settings.langsmith_api_key:
        return
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_TRACING", "true" if settings.langsmith_tracing else "false")
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
    os.environ.setdefault("LANGSMITH_ENDPOINT", settings.langsmith_endpoint)


@lru_cache
def get_settings() -> Settings:
    return Settings()
