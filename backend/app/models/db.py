"""SQLAlchemy engine/session setup. SQLite by default (spec: `.env`'s
DATABASE_URL), resolved against backend/ via Settings.database_url_resolved.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from fastapi import Depends
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def create_db_engine(settings: Settings):
    url = settings.database_url_resolved
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(settings: Settings) -> None:
    """Creates tables if they don't exist yet. Called once at app startup --
    this tool has no migration framework (ddl-auto-style create-if-missing
    is enough for a single-developer local tool with a fixed schema)."""
    from app.models import job  # noqa: F401 -- import registers the tables on Base.metadata

    engine = create_db_engine(settings)
    if settings.database_url_resolved.startswith("sqlite"):
        db_path = settings.database_url_resolved.removeprefix("sqlite:///")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(engine)


def session_factory(settings: Settings) -> sessionmaker[Session]:
    engine = create_db_engine(settings)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db_session(settings: Settings = Depends(get_settings)) -> Generator[Session, None, None]:
    """FastAPI dependency. Each request gets its own short-lived session.
    Takes settings via Depends() rather than calling get_settings() directly
    -- FastAPI's dependency_overrides only rewrites the dependency graph
    reached through Depends(), so calling get_settings() as a plain function
    here would silently ignore a test's settings override."""
    factory = session_factory(settings)
    session = factory()
    try:
        yield session
    finally:
        session.close()
