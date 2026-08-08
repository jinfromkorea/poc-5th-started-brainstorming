from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routers import artifacts, cache, health, jobs
from app.config import configure_langsmith_env, get_settings
from app.logging_conf import configure_logging
from app.models.db import init_db
from app.orchestration.concurrency import get_job_manager


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    configure_langsmith_env(settings)
    init_db(settings)
    get_job_manager(settings.max_concurrent_repos)

    app = FastAPI(
        title="Maven Stack Upgrade Tool",
        description="사내 Maven 시스템 스택 마이그레이션 / 취약점 해소 도구",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(jobs.router)
    app.include_router(artifacts.router)
    app.include_router(cache.router)

    return app


app = create_app()
