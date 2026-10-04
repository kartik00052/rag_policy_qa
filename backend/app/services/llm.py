"""LLM provider access (Stage 5 / Stage 10).

PROJECT.md Section 4 and Section 4.1 define a pluggable LLM provider layer
using LangChain integrations behind a uniform LLMProvider interface:
- Groq (default, cloud LPU inference for low latency)
- Gemini (secondary cloud option via Google GenAI)
- Ollama (offline / private local fallback)

Selected by ``LLM_PROVIDER`` ("groq" | "gemini" | "ollama") in configuration.
Failures are raised, never swallowed (WORKFLOW.md Golden Rule 10). Rate limits
are caught and surfaced as :class:`LLMRateLimitError` with clear quota details
rather than failing silently or looking like an empty answer.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any, Protocol

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
)
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_ollama import ChatOllama

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: A chat message. Kept as a plain dict (role, content) for provider independence.
Message = dict[str, str]


@dataclass(frozen=True)
class ProviderStats:
    """One generation's timing and token breakdown.

    Ollama measures these server-side on its ``done`` event in nanoseconds.
    Cloud providers (Groq, Gemini) supply usage metadata (input/output tokens)
    and wall-clock duration, converted to the same nanosecond schema so every
    caller (telemetry, debug endpoint, latency scripts) formats one shape.
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
        """Build stats from an Ollama ``done`` event or dictionary."""
        if not event.get("done") and "total_duration" not in event:
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

    @classmethod
    def from_usage(
        cls,
        usage: dict | None,
        duration_seconds: float,
        done_reason: str = "stop",
        fallback_eval_count: int = 0,
    ) -> ProviderStats:
        """Build stats from LangChain usage_metadata + measured wall clock."""
        total_ns = max(0, int(duration_seconds * 1e9))
        prompt_tokens = 0
        eval_tokens = fallback_eval_count
        if usage:
            prompt_tokens = int(usage.get("input_tokens") or 0)
            eval_tokens = int(usage.get("output_tokens") or fallback_eval_count)
        return cls(
            total_duration=total_ns,
            load_duration=0,
            prompt_eval_count=prompt_tokens,
            prompt_eval_duration=0,
            eval_count=eval_tokens,
            eval_duration=total_ns,
            done_reason=done_reason,
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
        """JSON-safe view: raw nanosecond counts plus derived rates."""
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


class LLMRateLimitError(LLMUnavailableError):
    """The provider rejected the request because a rate limit was reached."""


class LLMProvider(Protocol):
    """Provider-agnostic interface required by the graph."""

    @property
    def model_name(self) -> str:
        """The identifier of the active model."""

    @property
    def provider_name(self) -> str:
        """'groq' | 'gemini' | 'ollama'."""

    def stream(
        self,
        messages: Sequence[Message],
        on_stats: Callable[[ProviderStats], None] | None = None,
    ) -> AsyncIterator[str]:
        """Yield answer text incrementally, exactly as generated."""

    async def complete(self, messages: Sequence[Message]) -> str:
        """Return the complete answer."""

    async def aclose(self) -> None:
        """Release underlying connections."""


#: Backwards compatibility with Stage 5 naming.
LLMClient = LLMProvider


def to_langchain_messages(messages: Sequence[Message]) -> list[BaseMessage]:
    """Convert plain dict messages to LangChain typed message instances.

    Preserves role semantics for all providers:
    - 'system' -> SystemMessage (carries prompt-injection untrusted context rules)
    - 'user' -> HumanMessage (carries query and context blocks)
    - 'assistant' -> AIMessage (carries prior attempts during retry)
    """
    lc_messages: list[BaseMessage] = []
    for msg in messages:
        role = msg.get("role", "user").lower()
        content = msg.get("content", "")
        if role == "system":
            lc_messages.append(SystemMessage(content=content))
        elif role == "user":
            lc_messages.append(HumanMessage(content=content))
        elif role == "assistant":
            lc_messages.append(AIMessage(content=content))
        else:
            lc_messages.append(HumanMessage(content=content))
    return lc_messages


def _extract_text(content: Any) -> str:
    """Safely extract plain text from LangChain message content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict) and "text" in p:
                parts.append(str(p["text"]))
        return "".join(parts)
    return str(content) if content is not None else ""


class GroqProvider:
    """Chat against Groq cloud LPU inference via langchain-groq."""

    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float,
        timeout_seconds: float,
        num_predict: int,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout = timeout_seconds
        self._num_predict = num_predict
        self._llm = ChatGroq(
            model=self._model,
            groq_api_key=self._api_key,
            temperature=self._temperature,
            max_tokens=self._num_predict,
            timeout=self._timeout,
            max_retries=1,
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "groq"

    async def stream(
        self,
        messages: Sequence[Message],
        on_stats: Callable[[ProviderStats], None] | None = None,
    ) -> AsyncIterator[str]:
        lc_messages = to_langchain_messages(messages)
        started = time.perf_counter()
        usage: dict | None = None
        token_count = 0
        try:
            async for chunk in self._llm.astream(lc_messages):
                text = _extract_text(chunk.content)
                if getattr(chunk, "usage_metadata", None):
                    usage = chunk.usage_metadata
                if text:
                    token_count += 1
                    yield text
        except Exception as exc:
            err_str = str(exc)
            err_lower = err_str.lower()
            if "rate_limit" in err_lower or "rate limit" in err_lower or "429" in err_lower:
                raise LLMRateLimitError(
                    f"Groq rate limit exceeded for model {self._model!r}: {err_str}. "
                    "Free-tier limits (e.g. 30 RPM, 12,000 TPM) reached. Wait before retrying."
                ) from exc
            raise LLMUnavailableError(
                f"Groq API error for model {self._model!r}: {err_str}"
            ) from exc
        finally:
            duration = time.perf_counter() - started
            if on_stats is not None:
                stats = ProviderStats.from_usage(
                    usage=usage,
                    duration_seconds=duration,
                    done_reason="stop",
                    fallback_eval_count=token_count,
                )
                on_stats(stats)

    async def complete(self, messages: Sequence[Message]) -> str:
        lc_messages = to_langchain_messages(messages)
        try:
            response = await self._llm.ainvoke(lc_messages)
            return _extract_text(response.content)
        except Exception as exc:
            err_str = str(exc)
            err_lower = err_str.lower()
            if "rate_limit" in err_lower or "rate limit" in err_lower or "429" in err_lower:
                raise LLMRateLimitError(
                    f"Groq rate limit exceeded for model {self._model!r}: {err_str}"
                ) from exc
            raise LLMUnavailableError(
                f"Groq API error for model {self._model!r}: {err_str}"
            ) from exc

    async def aclose(self) -> None:
        pass


class GeminiProvider:
    """Chat against Google Gemini API via langchain-google-genai."""

    def __init__(
        self,
        api_key: str,
        model: str,
        temperature: float,
        timeout_seconds: float,
        num_predict: int,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._temperature = temperature
        self._timeout = timeout_seconds
        self._num_predict = num_predict
        self._llm = ChatGoogleGenerativeAI(
            model=self._model,
            google_api_key=self._api_key,
            temperature=self._temperature,
            max_output_tokens=self._num_predict,
            timeout=self._timeout,
            max_retries=1,
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def stream(
        self,
        messages: Sequence[Message],
        on_stats: Callable[[ProviderStats], None] | None = None,
    ) -> AsyncIterator[str]:
        lc_messages = to_langchain_messages(messages)
        started = time.perf_counter()
        usage: dict | None = None
        token_count = 0
        try:
            async for chunk in self._llm.astream(lc_messages):
                text = _extract_text(chunk.content)
                if getattr(chunk, "usage_metadata", None):
                    usage = chunk.usage_metadata
                if text:
                    token_count += 1
                    yield text
        except Exception as exc:
            err_str = str(exc)
            err_lower = err_str.lower()
            if (
                "resource_exhausted" in err_lower
                or "resourceexhausted" in err_lower
                or "rate limit" in err_lower
                or "429" in err_lower
            ):
                raise LLMRateLimitError(
                    f"Gemini rate limit exceeded for model {self._model!r}: {err_str}. "
                    "Free-tier limits (e.g. 15 RPM, 1M TPM) reached. Wait before retrying."
                ) from exc
            raise LLMUnavailableError(
                f"Gemini API error for model {self._model!r}: {err_str}"
            ) from exc
        finally:
            duration = time.perf_counter() - started
            if on_stats is not None:
                stats = ProviderStats.from_usage(
                    usage=usage,
                    duration_seconds=duration,
                    done_reason="stop",
                    fallback_eval_count=token_count,
                )
                on_stats(stats)

    async def complete(self, messages: Sequence[Message]) -> str:
        lc_messages = to_langchain_messages(messages)
        try:
            response = await self._llm.ainvoke(lc_messages)
            return _extract_text(response.content)
        except Exception as exc:
            err_str = str(exc)
            err_lower = err_str.lower()
            if (
                "resource_exhausted" in err_lower
                or "resourceexhausted" in err_lower
                or "rate limit" in err_lower
                or "429" in err_lower
            ):
                raise LLMRateLimitError(
                    f"Gemini rate limit exceeded for model {self._model!r}: {err_str}"
                ) from exc
            raise LLMUnavailableError(
                f"Gemini API error for model {self._model!r}: {err_str}"
            ) from exc

    async def aclose(self) -> None:
        pass


class OllamaProvider:
    """Chat against local Ollama via langchain-ollama."""

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
        self._timeout = timeout_seconds
        self._keep_alive = keep_alive
        self._num_predict = num_predict
        self._llm = ChatOllama(
            base_url=self._base_url,
            model=self._model,
            temperature=self._temperature,
            num_ctx=self._num_ctx,
            keep_alive=self._keep_alive,
            num_predict=self._num_predict,
            client_kwargs={"timeout": self._timeout},
        )

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def provider_name(self) -> str:
        return "ollama"

    async def stream(
        self,
        messages: Sequence[Message],
        on_stats: Callable[[ProviderStats], None] | None = None,
    ) -> AsyncIterator[str]:
        lc_messages = to_langchain_messages(messages)
        started = time.perf_counter()
        token_count = 0
        raw_stats: dict | None = None
        try:
            async for chunk in self._llm.astream(lc_messages):
                text = _extract_text(chunk.content)
                meta = getattr(chunk, "response_metadata", {}) or {}
                if meta and ("total_duration" in meta or "prompt_eval_count" in meta):
                    raw_stats = meta
                if text:
                    token_count += 1
                    yield text
        except Exception as exc:
            raise LLMUnavailableError(
                f"cannot reach ollama at {self._base_url} for model {self._model!r}: {exc}"
            ) from exc
        finally:
            duration = time.perf_counter() - started
            if on_stats is not None:
                if raw_stats:
                    stats = ProviderStats.from_event(raw_stats)
                else:
                    stats = None
                if stats is None:
                    stats = ProviderStats.from_usage(
                        usage=None,
                        duration_seconds=duration,
                        done_reason="stop",
                        fallback_eval_count=token_count,
                    )
                on_stats(stats)

    async def complete(self, messages: Sequence[Message]) -> str:
        lc_messages = to_langchain_messages(messages)
        try:
            response = await self._llm.ainvoke(lc_messages)
            return _extract_text(response.content)
        except Exception as exc:
            raise LLMUnavailableError(
                f"cannot reach ollama at {self._base_url} for model {self._model!r}: {exc}"
            ) from exc

    async def aclose(self) -> None:
        pass


_client: LLMProvider | None = None


def get_llm() -> LLMProvider:
    """Resolve the configured provider, building it on first use."""
    global _client
    if _client is not None:
        return _client

    settings = get_settings()
    provider = settings.llm_provider.strip().lower()

    if provider == "groq":
        if not settings.groq_api_key or not settings.groq_api_key.strip():
            raise LLMUnavailableError(
                "GROQ_API_KEY is not configured in backend/.env. "
                "Add your key to backend/.env, or set LLM_PROVIDER=ollama for local offline inference."
            )
        _client = GroqProvider(
            api_key=settings.groq_api_key.strip(),
            model=settings.groq_model.strip(),
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
            num_predict=settings.llm_num_predict,
        )
    elif provider == "gemini":
        if not settings.google_api_key or not settings.google_api_key.strip():
            raise LLMUnavailableError(
                "GOOGLE_API_KEY (or GEMINI_API_KEY) is not configured in backend/.env. "
                "Add your key to backend/.env, or set LLM_PROVIDER=groq or ollama."
            )
        _client = GeminiProvider(
            api_key=settings.google_api_key.strip(),
            model=settings.gemini_model.strip(),
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
            num_predict=settings.llm_num_predict,
        )
    elif provider == "ollama":
        _client = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            temperature=settings.llm_temperature,
            num_ctx=settings.llm_num_ctx,
            timeout_seconds=settings.llm_timeout_seconds,
            keep_alive=settings.llm_keep_alive,
            num_predict=settings.llm_num_predict,
        )
    else:
        raise LLMError(
            f"Unsupported LLM_PROVIDER {provider!r}. Supported options: 'groq', 'gemini', 'ollama'."
        )

    logger.info(
        "llm provider: %s (model=%s, temp=%.2f, max_predict=%d)",
        _client.provider_name,
        _client.model_name,
        settings.llm_temperature,
        settings.llm_num_predict,
    )
    return _client


async def close_llm() -> None:
    """Release provider connections on shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
