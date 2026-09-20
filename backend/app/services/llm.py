"""Anthropic client wrapper. Modules never import `anthropic` directly, so tests can
patch `app.services.llm` in one place.
"""

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from anthropic import Anthropic

from app.core.config import settings


@lru_cache(maxsize=1)
def _client() -> Anthropic:
    return Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _params(
    system: str,
    messages: list[dict[str, Any]],
    model: str | None,
    max_tokens: int,
    temperature: float | None,
) -> dict[str, Any]:
    """Build request kwargs. `temperature` is sent only when a caller sets it: the
    claude-5 family rejects it, and the old TypeScript chat route never sent it."""
    params: dict[str, Any] = {
        "model": model or settings.ANTHROPIC_MODEL,
        "system": system,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        params["temperature"] = temperature
    return params


def complete(
    system: str,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int = 4096,
    temperature: float | None = None,
) -> str:
    resp = _client().messages.create(**_params(system, messages, model, max_tokens, temperature))
    return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")


def stream(
    system: str,
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    max_tokens: int = 2048,
    temperature: float | None = None,
) -> Iterator[str]:
    with _client().messages.stream(
        **_params(system, messages, model, max_tokens, temperature)
    ) as s:
        yield from s.text_stream
