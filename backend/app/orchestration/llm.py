"""ChatOpenAI factory. Kept lazy (called only when a node actually needs the
model, never at import time) so unit tests that never touch the LLM don't
need OPENAI_API_KEY set."""

from __future__ import annotations

from langchain_openai import ChatOpenAI

from app.config import Settings


def get_chat_model(settings: Settings) -> ChatOpenAI:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set (backend/.env) -- required for AI-assisted fix steps.")

    kwargs: dict = {
        "model": settings.llm_model,
        "api_key": settings.openai_api_key,
        "max_completion_tokens": settings.llm_max_tokens,
    }
    if settings.llm_base_url:
        kwargs["base_url"] = settings.llm_base_url
    return ChatOpenAI(**kwargs)
