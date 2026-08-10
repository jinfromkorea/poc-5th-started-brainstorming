"""next_job_id -- MAX(id)+1 based sequential id assignment, safe against
deletion (spec: docs/superpowers/specs/2026-08-10-history-delete-and-
analysis-collapse-design.md). Verified against SQLite only -- this project
has no plans to support any other DATABASE_URL dialect."""

from __future__ import annotations

from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models.db import init_db, session_factory
from app.models.job import Job, next_job_id


def _session_factory(tmp_path) -> sessionmaker[Session]:
    settings = Settings(_env_file=None, database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    init_db(settings)
    return session_factory(settings)


def _make_job(db: Session, job_id: str) -> Job:
    job = Job(id=job_id, source_type="git", source_ref="https://example.com/repo.git", status="success")
    db.add(job)
    db.commit()
    return job


def test_next_job_id_starts_at_one(tmp_path):
    factory = _session_factory(tmp_path)
    with factory() as db:
        assert next_job_id(db) == "1"


def test_next_job_id_increments_from_max(tmp_path):
    factory = _session_factory(tmp_path)
    with factory() as db:
        _make_job(db, "1")
        _make_job(db, "2")
        assert next_job_id(db) == "3"


def test_next_job_id_does_not_reuse_a_deleted_middle_id(tmp_path):
    """job 1/2/3 중 2를 삭제해도 다음 id는 이미 존재하는 3과 충돌하지 않고
    4가 되어야 한다 -- COUNT(*) 기반 채번이었다면 count=2 -> "3"이 되어
    기존 job 3과 PK 충돌이 났을 상황."""
    factory = _session_factory(tmp_path)
    with factory() as db:
        _make_job(db, "1")
        job2 = _make_job(db, "2")
        _make_job(db, "3")
        db.delete(job2)
        db.commit()
        assert next_job_id(db) == "4"
