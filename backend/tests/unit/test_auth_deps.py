"""require_api_token accepts the token via either the X-API-Token header
(used by regular fetch calls) or an api_token query param (needed for the
frontend's SSE connection, since browser EventSource can't set headers)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.deps import require_api_token
from app.config import Settings


def _settings(token: str) -> Settings:
    return Settings(_env_file=None, api_auth_token=token)


def test_auth_disabled_when_token_blank():
    require_api_token(x_api_token=None, api_token=None, settings=_settings(""))


def test_header_token_accepted():
    require_api_token(x_api_token="secret", api_token=None, settings=_settings("secret"))


def test_query_param_token_accepted():
    require_api_token(x_api_token=None, api_token="secret", settings=_settings("secret"))


def test_wrong_token_rejected():
    with pytest.raises(HTTPException) as exc_info:
        require_api_token(x_api_token="wrong", api_token=None, settings=_settings("secret"))
    assert exc_info.value.status_code == 401


def test_missing_token_rejected_when_required():
    with pytest.raises(HTTPException) as exc_info:
        require_api_token(x_api_token=None, api_token=None, settings=_settings("secret"))
    assert exc_info.value.status_code == 401
