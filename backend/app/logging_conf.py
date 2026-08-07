"""Stdlib logging setup. Called once from the app factory."""

from __future__ import annotations

import logging

# ChatOpenAI (langchain_openai) calls OpenAI through the official `openai`
# SDK, which sends requests via `httpx`/`httpcore` -- both log one INFO line
# per outbound HTTP request by default. That's not this app's own code path
# going around ChatOpenAI (see orchestration/llm.get_chat_model, the only
# place the OpenAI API is touched); it's just those libraries' own default
# verbosity leaking through once the root logger is at INFO. Quieted here,
# independent of LOG_LEVEL, so it doesn't compete with the app's own
# "실행: .../완료: ..." logging.
_NOISY_THIRD_PARTY_LOGGERS = ("httpx", "httpcore", "openai")


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    for name in _NOISY_THIRD_PARTY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
