"""Liveness + local prerequisite check, exposed over HTTP as well as the
standalone scripts/check_prereqs.py CLI (same underlying checks)."""

from __future__ import annotations

from fastapi import APIRouter

from app.prereqs import check_all

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/prereqs")
def prereqs() -> dict:
    results = check_all()
    return {
        "ok": all(r.ok for r in results),
        "checks": [r.__dict__ for r in results],
    }
