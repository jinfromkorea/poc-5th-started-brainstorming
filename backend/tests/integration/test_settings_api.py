"""API-level tests for GET/POST /settings/llm-model -- the settings modal's
LLM model picker (spec: docs/superpowers/specs/2026-08-10-llm-model-
selection-design.md). write_llm_model_to_env is monkeypatched out in every
test here: the router calls it with no env_path, so left unpatched it would
write to the real backend/.env."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


@pytest.fixture()
def app_client(monkeypatch):
    test_settings = Settings(_env_file=None)
    written: list[str] = []
    monkeypatch.setattr("app.api.routers.settings.write_llm_model_to_env", lambda model: written.append(model))

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: test_settings
    client = TestClient(app)
    yield client, written


def test_get_llm_model_returns_available_and_current(app_client):
    client, _written = app_client
    resp = client.get("/settings/llm-model")

    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] == ["gpt-5.4-mini", "gpt-4o-mini"]
    assert body["current"] == "gpt-5.4-mini"


def test_set_llm_model_to_available_value_updates_current_immediately(app_client):
    client, written = app_client
    resp = client.post("/settings/llm-model", json={"model": "gpt-4o-mini"})

    assert resp.status_code == 200
    assert resp.json()["current"] == "gpt-4o-mini"
    assert written == ["gpt-4o-mini"]

    # No server restart between requests in this test -- confirms the
    # cached Settings instance was updated in place, not just the file.
    again = client.get("/settings/llm-model")
    assert again.json()["current"] == "gpt-4o-mini"


def test_set_llm_model_to_unknown_value_returns_400_and_does_not_write(app_client):
    client, written = app_client
    resp = client.post("/settings/llm-model", json={"model": "not-a-real-model"})

    assert resp.status_code == 400
    assert written == []
    assert client.get("/settings/llm-model").json()["current"] == "gpt-5.4-mini"
