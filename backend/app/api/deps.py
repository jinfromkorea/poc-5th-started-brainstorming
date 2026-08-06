"""Shared FastAPI dependencies: auth token check, DB session."""

from __future__ import annotations

import logging

from fastapi import Depends, Header, HTTPException, Query, status

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

_warned_no_auth = False


def require_api_token(
    x_api_token: str | None = Header(default=None),
    api_token: str | None = Query(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Minimal local defense, not real security: this is a localhost
    single-developer tool, so the token's job is to stop some *other* local
    process/browser tab from hitting the API by accident -- not to
    distinguish between multiple users. If API_AUTH_TOKEN is blank, auth is
    disabled with a loud one-time warning (convenient for local dev).

    Accepts the token via the X-API-Token header (used by regular fetch
    calls) or an api_token query param (the frontend's SSE progress stream
    has to use this -- the browser's EventSource API cannot set custom
    headers)."""
    global _warned_no_auth
    if not settings.api_auth_token:
        if not _warned_no_auth:
            logger.warning(
                "API_AUTH_TOKEN is not set -- running WITHOUT authentication. "
                "Set API_AUTH_TOKEN in backend/.env before exposing this beyond localhost."
            )
            _warned_no_auth = True
        return
    if (x_api_token or api_token) != settings.api_auth_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing X-API-Token")
