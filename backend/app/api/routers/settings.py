"""Runtime-editable app settings surfaced in the frontend's settings modal:
currently just which LLM model job runs use. Selecting a model persists it
to backend/.env (LLM_MODEL=, via write_llm_model_to_env) so it survives a
restart, and also updates the cached Settings singleton immediately so the
very next job uses it without one (spec: docs/superpowers/specs/2026-08-10-
llm-model-selection-design.md).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.deps import require_api_token
from app.config import Settings, get_settings, write_llm_model_to_env

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(require_api_token)])


class LlmModelResponse(BaseModel):
    available: list[str]
    current: str


class SetLlmModelRequest(BaseModel):
    model: str


@router.get("/llm-model", response_model=LlmModelResponse)
async def get_llm_model(settings: Settings = Depends(get_settings)) -> LlmModelResponse:
    return LlmModelResponse(available=settings.llm_available_models_list, current=settings.llm_model)


@router.post("/llm-model", response_model=LlmModelResponse)
async def set_llm_model(body: SetLlmModelRequest, settings: Settings = Depends(get_settings)) -> LlmModelResponse:
    if body.model not in settings.llm_available_models_list:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown model: {body.model!r}; available: {settings.llm_available_models_list}",
        )
    write_llm_model_to_env(body.model)
    # Depends(get_settings) hands back the @lru_cache'd singleton itself, so
    # this assignment is visible process-wide from the next request on --
    # no restart needed.
    settings.llm_model = body.model
    return LlmModelResponse(available=settings.llm_available_models_list, current=settings.llm_model)
