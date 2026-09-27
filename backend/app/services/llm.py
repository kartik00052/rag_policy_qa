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
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Protocol

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: A chat message. Kept as a plain dict because that is what every provider
#: HTTP API this project might add expects.
Message = dict[str, str]


@dataclass(frozen=True)
class ProviderStats:
    """One generation's own timing breakdown, as reported by the provider.

    Ollama measures these server-side and returns them on the final ``done``
    event of ``/api/chat``, in nanoseconds. They are recorded because the
    wall-clock number alone cannot say *where* the time went, and the three
    costs have completely different fixes:

    * ``load_duration`` - resident, or evicted and reloaded from disk on this
      request. Nonzero on a repeat request is pure waste; ``keep_alive`` is the
      fix.
    * ``prompt_eval_duration`` - prefill over the whole context, which scales
      with the real prompt length and is paid on the first attempt *and* every
      retry, since a retry replays the original messages. Shrinking ``num_ctx``
      is what moves this.
    * ``eval_duration`` - decode, per generated token. A rambling generation
      inflates this until it is the entire cost, and ``num_predict`` is what
      bounds it.

    ``tokens_per_second`` is derived from the last two and is the honest measure
    of decode speed on this machine, because it excludes load and prefill.
    """

    total_duration: int = 0
    load_duration: int = 0
    prompt_eval_count: int = 0
    prompt_eval_duration: int = 0
    eval_count: int = 0
    eval_duration: int = 0
    done_reason: str = ""

    @classmethod
    def from_event(cls, event: dict) -> ProviderStats | None:
        """Build stats from a ``done`` event, or ``None`` if it carries none.

        ``None`` rather than zeros when the fields are absent, so a provider
        that reports no timings is distinguishable from one that genuinely took
        no time. A zeroed :class:`ProviderStats` would otherwise read as
        "infinitely fast" in a latency report.
        """
        if not event.get("done"):
            return None
        fields = (
            "total_duration",
            "load_duration",
            "prompt_eval_count",
            "prompt_eval_duration",
            "eval_count",
            "eval_duration",
        )
        if not any(key in event for key in fields):
            return None
        return cls(
            **{key: int(event.get(key) or 0) for key in fields},
            done_reason=str(event.get("done_reason") or ""),
        )

    @property
    def prompt_tokens_per_second(self) -> float:
        if self.prompt_eval_duration <= 0:
            return 0.0
        return self.prompt_eval_count / (self.prompt_eval_duration / 1e9)

    @property
    def tokens_per_second(self) -> float:
        if self.eval_duration <= 0:
            return 0.0
        return self.eval_count / (self.eval_duration / 1e9)

    def seconds(self, name: str) -> float:
        """Nanosecond field *name* as seconds, for readable reports."""
        return getattr(self, name) / 1e9

    def as_dict(self) -> dict:
        """JSON-safe view: raw nanosecond counts plus the derived rates.

        Raw fields are kept rather than only the seconds so a report can be
        re-derived exactly, and the derived keys are added here so every caller
        formats one shape instead of each computing its own.
        """
        data = asdict(self)
        data["total_seconds"] = self.seconds("total_duration")
        data["load_seconds"] = self.seconds("load_duration")
        data["prompt_eval_seconds"] = self.seconds("prompt_eval_duration")
        data["eval_seconds"] = self.seconds("eval_duration")
        data["prompt_tokens_per_second"] = self.prompt_tokens_per_second
        data["tokens_per_second"] = self.tokens_per_second
        return data


class LLMError(RuntimeError):
    """Base class for provider failures."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached, or refused the request."""


class LLMClient(Protocol):
    """What the graph needs from a provider."""

    def stream(
        self,
        messages: Sequence[Message],
        on_stats: Callable[[ProviderStats], None] | None = None,
    ) -> AsyncIterator[str]:
        """Yield answer text incrementally, exactly as the model produces it.

        ``on_stats`` is called once with the provider's own timing breakdown when
        the generation finishes, or not at all if the provider reports none. It
        is a callback rather than a return value because a node cannot return
        two things from a generator, and a shared mutable "last stats" attribute
        would be wrong the moment two requests are in flight.
        """

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
        keep_alive: str = "30m",
        num_predict: int = 300,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._temperature = temperature
        self._num_ctx = num_ctx
        self._timeout = httpx.Timeout(timeout_seconds, connect=10.0)
        self._keep_alive = keep_alive
        self._num_predict = num_predict
        self._client: httpx.AsyncClient | None = None

    def _payload(self, messages: Sequence[Message], stream: bool) -> dict:
        return {
            "model": self._model,
            "messages": list(messages),
            "stream": stream,
            # Resisting eviction between requests. Measured: load_duration is
            # 0.07s while the model is resident and 2.0-3.0s when it is not, so
            # an eviction costs the reload plus a first prefill that runs at
            # roughly a third of the steady-state rate while the runner warms.
            "keep_alive": self._keep_alive,
            "options": {
                "temperature": self._temperature,
                "num_ctx": self._num_ctx,
                # Bounded so a runaway generation cannot occupy the connection
                # for the whole context window before the judge sees it.
                "num_predict": self._num_predict,
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

    async def stream(
        self,
        messages: Sequence[Message],
        on_stats: Callable[[ProviderStats], None] | None = None,
    ) -> AsyncIterator[str]:
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
                        # Read before the break: the timings live on the done
                        # event, so breaking first would discard them.
                        if on_stats is not None:
                            stats = ProviderStats.from_event(event)
                            if stats is not None:
                                on_stats(stats)
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
            keep_alive=settings.llm_keep_alive,
            num_predict=settings.llm_num_predict,
        )
        logger.info(
            "llm provider: %s (%s, num_ctx=%d, num_predict=%d, keep_alive=%s)",
            provider,
            settings.ollama_model,
            settings.llm_num_ctx,
            settings.llm_num_predict,
            settings.llm_keep_alive,
        )
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
