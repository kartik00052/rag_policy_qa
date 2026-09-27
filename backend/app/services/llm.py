"""LLM provider access (Stage 5).

PROJECT.md Section 4 scopes LangChain to "providers only", so the provider is
reached over its own HTTP API rather than through a framework abstraction. That
keeps the dependency surface small and means the provider named in configuration
is the only thing that changes when the backend moves.

V1 ships one provider, ``ollama``. ``llm_provider`` selects it from config, so no
vendor is hardcoded in application logic; an unknown name fails loudly at first
use rather than silently falling back (WORKFLOW.md Golden Rule 10).

Failures are raised, never swallowed. A provider that is down, a model that is
not pulled, or a non-200 response all surface as :class:`LLMUnavailableError` so
the SSE stream can emit an error event instead of appearing to succeed.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: A chat message. Kept as a plain dict because that is what every provider
#: HTTP API this project might add expects.
Message = dict[str, str]


class LLMError(RuntimeError):
    """Base class for provider failures."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached, or refused the request."""


class LLMClient(Protocol):
    """What the graph needs from a provider."""

    def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        """Yield answer text incrementally, exactly as the model produces it."""

    async def complete(self, messages: Sequence[Message]) -> str:
        """Return the whole answer. Used for non-streaming callers and tests."""


class OllamaClient:
    """Chat against a local Ollama server.

    Ollama's ``/api/chat`` streams newline-delimited JSON, one object per token
    batch, with ``done: true`` on the last. Anything that is not a 200, or a
    stream that carries an ``error`` field, is raised: a provider that fails
    mid-stream must not look like a short answer.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float,
        num_ctx: int,
        timeout_seconds: float,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._num_ctx = num_ctx
        self._timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self._client: httpx.AsyncClient | None = None

    def _payload(self, messages: Sequence[Message], stream: bool) -> dict:
        return {
            "model": self._model,
            "messages": list(messages),
            "stream": stream,
            "options": {
                "temperature": self._temperature,
                "num_ctx": self._num_ctx,
            },
        }

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _url(self) -> str:
        return f"{self._base_url}/api/chat"

    async def stream(self, messages: Sequence[Message]) -> AsyncIterator[str]:
        client = self._http()
        payload = self._payload(messages, stream=True)
        try:
            async with client.stream("POST", self._url(), json=payload) as response:
                if response.status_code != 200:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise LLMUnavailableError(
                        f"ollama returned {response.status_code} for model "
                        f"{self._model!r}: {body[:400]}"
                    )
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    event = json.loads(line)
                    if event.get("error"):
                        raise LLMUnavailableError(
                            f"ollama stream error: {event['error']}"
                        )
                    piece = (event.get("message") or {}).get("content")
                    if piece:
                        yield piece
                    if event.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"cannot reach ollama at {self._base_url}: {exc}"
            ) from exc

    async def complete(self, messages: Sequence[Message]) -> str:
        client = self._http()
        payload = self._payload(messages, stream=False)
        try:
            response = await client.post(self._url(), json=payload)
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(
                f"cannot reach ollama at {self._base_url}: {exc}"
            ) from exc
        if response.status_code != 200:
            raise LLMUnavailableError(
                f"ollama returned {response.status_code} for model "
                f"{self._model!r}: {response.text[:400]}"
            )
        body = response.json()
        if body.get("error"):
            raise LLMUnavailableError(f"ollama error: {body['error']}")
        return (body.get("message") or {}).get("content", "")


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    """Resolve the configured provider, building it on first use."""
    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    provider = settings.llm_provider.strip().lower()
    if provider == "ollama":
        _client = OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            temperature=settings.llm_temperature,
            num_ctx=settings.llm_num_ctx,
            timeout_seconds=settings.llm_timeout_seconds,
        )
        logger.info("llm provider: %s (%s)", provider, settings.ollama_model)
        return _client
    raise LLMError(
        f"unknown LLM_PROVIDER {provider!r}; V1 implements 'ollama'. "
        "Set LLM_PROVIDER=ollama in backend/.env."
    )


async def close_llm() -> None:
    """Release provider HTTP connections on shutdown."""
    global _client
    if isinstance(_client, OllamaClient):
        await _client.aclose()
    _client = None
