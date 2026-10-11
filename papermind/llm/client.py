"""Hosted Claude behind three calls: ``stream``, ``parse``, ``prewarm``.

Every request has the same shape, because thinking and effort are rendered
into the prompt and a request that differs there cannot read a cache entry
written by one that does not:

* ``thinking={"type": "adaptive"}`` -- always on for this model; depth is set
  with ``output_config.effort``. No sampling parameters, no forced
  ``tool_choice``, no prefill: each is a 400 on this model family.
* the beta namespace with ``fallbacks="default"`` when ``enable_fallbacks``, so
  a safety-classifier decline is re-run on a fallback model inside the same
  call instead of failing the turn. A decline of the whole chain still arrives
  as ``stop_reason == "refusal"`` and becomes ``PaperMindLLMError("refusal")``.

Content blocks come back as API-ready dicts (``CallResult.content``) so the
agent loop can append them to the next request unchanged -- thinking blocks
included, signatures intact -- and so the trace stays JSON. The one exception
is a mid-output fallback: the declined model's thinking and tool_use blocks
before the last ``fallback`` marker must not be echoed (the API's replay
rule), so they are dropped here and never reach the loop or the dispatcher.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import aiohttp
import anthropic
import httpx2
import pydantic
from anthropic import AsyncAnthropic, DefaultAioHttpClient
from pydantic import BaseModel

from papermind.config import Settings, get_settings
from papermind.llm.pricing import Usage, call_cost
from papermind.schemas.events import ErrorCode

log = logging.getLogger(__name__)

# The scalar ``fallbacks="default"`` form needs exactly this header; the array
# form uses an older one and mixing them is a 400.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Read timeout between streamed chunks, not a cap on the whole response: the
# API sends pings while the model thinks, so a quiet socket this long is dead.
REQUEST_TIMEOUT = anthropic.Timeout(120.0, connect=10.0)
PREWARM_PLACEHOLDER = "warmup"

TextCallback = Callable[[str], Awaitable[None]]


class PaperMindLLMError(Exception):
    """A model call failed in a way the reader should hear about."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        request_id: str | None = None,
        result: CallResult | None = None,
    ) -> None:
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.retryable = retryable
        self.request_id = request_id
        # Set for refusals: the call completed and was billed.
        self.result = result


class ToolInputParseError(Exception):
    """A tool_use block whose streamed input is not valid JSON.

    With ``eager_input_streaming`` the API forwards the model's argument JSON
    unvalidated. The SDK parses it leniently (partial mode), so a malformed
    input can surface as a ValueError mid-stream or, worse, as a silently
    truncated dict. Either way there is no faithful tool_use block to answer
    with an error result, so the caller re-issues the request. ``result`` is
    the billed call: complete when the stream ended normally, the usage seen
    so far (exact input, partial output) when the SDK gave up mid-stream.
    """

    def __init__(self, message: str, *, result: CallResult | None = None) -> None:
        super().__init__(message)
        self.result = result


@dataclass
class CallResult:
    message: Any
    usage: Usage
    latency_ms: int
    ttft_ms: int | None
    request_id: str | None
    cost_usd: float
    content: list[dict[str, Any]] = field(default_factory=list)
    stop_reason: str | None = None
    model: str = ""
    fell_back: bool = False
    parsed: BaseModel | None = None

    @property
    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    @property
    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]


class LLMClient(Protocol):
    """What the agent needs from a model client; ``tests/unit/fakes.py`` implements it too."""

    model: str

    async def stream(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        effort: str,
        max_tokens: int,
        cache: bool,
        on_text: TextCallback | None = None,
    ) -> CallResult: ...

    async def parse(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
    ) -> CallResult: ...

    async def prewarm(
        self, *, system: list[dict[str, Any]], tools: list[dict[str, Any]] | None, effort: str
    ) -> CallResult: ...


_MESSAGES: dict[str, str] = {
    "no_api_key": "No Anthropic API key is configured. Set ANTHROPIC_API_KEY in .env and restart the server.",
    "auth_failed": "The Anthropic API rejected the API key. Check ANTHROPIC_API_KEY in .env.",
    "rate_limited": "The model is rate-limited right now. Wait a few seconds and ask again.",
    "overloaded": "The model is overloaded right now. Try again in a minute.",
    "api_error": "The model API returned an error. Try again; if it keeps happening, check the server logs.",
    "refusal": "The model declined to answer this. Try rephrasing the question.",
}


def llm_error(code: ErrorCode, *, retryable: bool = False, **kwargs: Any) -> PaperMindLLMError:
    return PaperMindLLMError(code, _MESSAGES.get(code, _MESSAGES["api_error"]), retryable=retryable, **kwargs)


def map_api_error(exc: anthropic.AnthropicError) -> PaperMindLLMError:
    """SDK exception -> the error code the UI understands.

    Mid-stream failures arrive as an ``error`` event on an HTTP 200, so the
    error ``type`` in the body is checked before the status class.
    """
    request_id = getattr(exc, "request_id", None)
    body = getattr(exc, "body", None)
    err_type = body.get("error", {}).get("type") if isinstance(body, dict) else None
    if err_type == "overloaded_error" or isinstance(
        exc, anthropic.OverloadedError | anthropic.ServiceUnavailableError
    ):
        return llm_error("overloaded", retryable=True, request_id=request_id)
    if err_type == "rate_limit_error" or isinstance(exc, anthropic.RateLimitError):
        return llm_error("rate_limited", retryable=True, request_id=request_id)
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return llm_error("auth_failed", request_id=request_id)
    if isinstance(exc, anthropic.APIConnectionError):
        return llm_error("api_error", retryable=True, request_id=request_id)
    detail = body.get("error", {}).get("message", "") if isinstance(body, dict) else ""
    if "credit balance" in detail.lower():
        # A 400, not a 402: without this it reads as a generic API error and every
        # retry fails the same way. Name the fix instead.
        return PaperMindLLMError(
            "api_error",
            "The Anthropic account is out of credits. Add credits under Plans & Billing in the Anthropic Console, "
            "then try again.",
            request_id=request_id,
        )
    status = getattr(exc, "status_code", None)
    retryable = err_type == "api_error" or (isinstance(status, int) and status >= 500)
    return llm_error("api_error", retryable=retryable, request_id=request_id)


# A dropped connection mid-stream surfaces from the transport, not as an
# SDK error: the SDK only wraps failures that happen before the response
# starts. The aiohttp backend maps its errors to httpx2's, but not all of them.
_TRANSPORT_ERRORS: tuple[type[BaseException], ...] = (httpx2.TransportError, aiohttp.ClientError)


def map_transport_error(exc: BaseException, request_id: str | None = None) -> PaperMindLLMError:
    log.warning("connection to the model API failed mid-response (request_id=%s): %r", request_id, exc)
    message = (
        "The model stopped responding partway through. Try again."
        if isinstance(exc, httpx2.TimeoutException | TimeoutError)
        else "The connection to the model dropped partway through. Try again."
    )
    return PaperMindLLMError("api_error", message, retryable=True, request_id=request_id)


def _is_server_result(block: dict[str, Any]) -> bool:
    # web_search_tool_result, code_execution_tool_result, ...: matched by
    # suffix so a server tool added later is still paired correctly.
    return str(block.get("type", "")).endswith("_tool_result") and block.get("type") != "tool_result"


def echo_content(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The assistant content as it may be sent back, after a mid-output fallback.

    The replay rule: before the last ``fallback`` block, only text blocks,
    paired server-tool blocks (a ``server_tool_use`` and its result) and the
    marker itself are kept. The declined attempt's thinking, tool_use, an
    unpaired server-tool call and any block type we do not know are dropped.
    Everything after the marker is the serving model's and stays as it is.
    Without a fallback the content is returned unchanged.
    """
    boundary = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if boundary < 0:
        return blocks
    declined = blocks[:boundary]
    calls = {b.get("id") for b in declined if b.get("type") == "server_tool_use"}
    answered = {b.get("tool_use_id") for b in declined if _is_server_result(b)}
    paired = calls & answered

    def keep(block: dict[str, Any]) -> bool:
        kind = block.get("type")
        if kind in ("text", "fallback"):
            return True
        if kind == "server_tool_use":
            return block.get("id") in paired
        return _is_server_result(block) and block.get("tool_use_id") in paired

    return [b for b in declined if keep(b)] + blocks[boundary:]


def _malformed_json(raw: str) -> str | None:
    """The raw input, clipped for logs, if it is not one complete JSON value; else None."""
    if not raw.strip():
        return None  # no deltas means the tool takes no arguments: {}
    try:
        json.loads(raw)
    except ValueError:
        return raw if len(raw) <= 200 else raw[:199] + "…"
    return None


def _block_dict(block: Any) -> dict[str, Any]:
    data = block.to_dict(mode="json", exclude_none=True)
    # A client-side parse artefact on ParsedBetaTextBlock, not part of the API block.
    data.pop("parsed_output", None)
    return data


def _fell_back(usage: Any) -> bool:
    return any(
        getattr(it, "type", None) == "fallback_message" for it in (getattr(usage, "iterations", None) or [])
    )


class LLM:
    """The one place that talks to the Anthropic API."""

    def __init__(self, settings: Settings | None = None, *, client: AsyncAnthropic | None = None) -> None:
        self._settings = settings or get_settings()
        self.model = self._settings.model
        if client is None:
            if not self._settings.has_api_key:
                raise llm_error("no_api_key")
            assert self._settings.anthropic_api_key is not None
            client = AsyncAnthropic(
                api_key=self._settings.anthropic_api_key.get_secret_value(),
                http_client=DefaultAioHttpClient(),
                timeout=REQUEST_TIMEOUT,
                max_retries=2,
            )
        self._client = client

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------ requests

    def _request(
        self, *, system: list[dict[str, Any]], messages: list[dict[str, Any]], effort: str, max_tokens: int
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
        if self._settings.enable_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def stream(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        effort: str,
        max_tokens: int,
        cache: bool,
        on_text: TextCallback | None = None,
    ) -> CallResult:
        """One streamed call. ``cache`` adds top-level automatic caching for the message tail."""
        kwargs = self._request(system=system, messages=messages, effort=effort, max_tokens=max_tokens)
        if tools:
            kwargs["tools"] = tools
        if cache:
            kwargs["cache_control"] = {"type": "ephemeral"}
        return await self._run_stream(kwargs, on_text=on_text)

    async def parse(
        self,
        *,
        system: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        schema: type[BaseModel],
        effort: str,
        max_tokens: int,
    ) -> CallResult:
        """Structured output validated into ``schema``; the model is on ``CallResult.parsed``."""
        kwargs = self._request(system=system, messages=messages, effort=effort, max_tokens=max_tokens)
        kwargs["output_format"] = schema
        result = await self._run_stream(kwargs)
        if result.parsed is None:
            log.warning(
                "structured output missing (stop_reason=%s, request_id=%s)",
                result.stop_reason,
                result.request_id,
            )
            raise llm_error("api_error", request_id=result.request_id, result=result)
        return result

    async def prewarm(
        self, *, system: list[dict[str, Any]], tools: list[dict[str, Any]] | None, effort: str
    ) -> CallResult:
        """Write the tools+system cache entries before the first real question.

        ``max_tokens=0`` runs prefill only. It cannot be streamed (the API
        rejects ``stream: true`` with it), so this is the one non-streaming
        call. The placeholder message is never answered, and the breakpoints
        must sit on the system blocks, not on automatic tail caching, or the
        entry would be keyed to the placeholder.
        """
        kwargs = self._request(
            system=system,
            messages=[{"role": "user", "content": PREWARM_PLACEHOLDER}],
            effort=effort,
            max_tokens=0,
        )
        if tools:
            kwargs["tools"] = tools
        started = time.perf_counter()
        try:
            message = await self._client.beta.messages.create(**kwargs)
        except anthropic.AnthropicError as exc:
            raise map_api_error(exc) from exc
        except _TRANSPORT_ERRORS as exc:
            raise map_transport_error(exc) from exc
        return self._result(
            message,
            started=started,
            first_event=None,
            request_id=getattr(message, "_request_id", None),
        )

    # ------------------------------------------------------------ plumbing

    async def _run_stream(self, kwargs: dict[str, Any], *, on_text: TextCallback | None = None) -> CallResult:
        started = time.perf_counter()
        first_event: float | None = None
        tool_json: dict[int, list[str]] = {}
        malformed: dict[int, str] = {}
        request_id: str | None = None
        try:
            async with self._client.beta.messages.stream(**kwargs) as stream:
                request_id = stream.request_id
                events = stream.__aiter__()
                while True:
                    # Only the SDK's own iteration is guarded: a ValueError from
                    # on_text is our bug and must not look like bad tool JSON.
                    try:
                        event = await anext(events)
                    except StopAsyncIteration:
                        break
                    except pydantic.ValidationError as exc:
                        # Most often JSON cut off at max_tokens (thinking counts
                        # toward it). The call was billed: carry its usage.
                        log.warning("structured output failed validation: %s", exc)
                        raise llm_error(
                            "api_error",
                            request_id=request_id,
                            result=self._partial(stream, started, first_event, request_id),
                        ) from exc
                    except ValueError as exc:
                        # The SDK's accumulator gave up on the JSON and the stream
                        # is gone; what was billed so far is in the snapshot.
                        raise ToolInputParseError(
                            str(exc), result=self._partial(stream, started, first_event, request_id)
                        ) from exc
                    if first_event is None and event.type == "content_block_start":
                        first_event = time.perf_counter()
                    if event.type == "content_block_start" and event.content_block.type == "fallback":
                        # Everything before the marker was the declined attempt and
                        # is dropped from the echo, so its JSON no longer matters.
                        tool_json.clear()
                        malformed.clear()
                    elif event.type == "content_block_delta" and event.delta.type == "input_json_delta":
                        tool_json.setdefault(event.index, []).append(event.delta.partial_json)
                    elif event.type == "content_block_stop" and event.index in tool_json:
                        if bad := _malformed_json("".join(tool_json.pop(event.index))):
                            malformed[event.index] = bad
                    elif on_text is not None and event.type == "text" and event.text:
                        await on_text(event.text)
                final = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise map_api_error(exc) from exc
        except _TRANSPORT_ERRORS as exc:
            raise map_transport_error(exc, request_id) from exc

        result = self._result(final, started=started, first_event=first_event, request_id=request_id)
        # A refusal mid-argument also leaves the input unterminated; it is a
        # refusal, and re-issuing it as bad JSON would just be refused again.
        if result.stop_reason == "refusal":
            details = getattr(final, "stop_details", None)
            log.warning(
                "model refused (category=%s, request_id=%s)", getattr(details, "category", None), request_id
            )
            raise llm_error("refusal", request_id=request_id, result=result)
        # A max_tokens cut leaves the last input unterminated; the caller handles
        # that stop reason itself, and re-issuing would only hit the limit again.
        if malformed and result.stop_reason != "max_tokens":
            bad = next(iter(malformed.values()))
            log.warning("unparseable tool input (request_id=%s): %s", request_id, bad)
            raise ToolInputParseError(f"tool input is not valid JSON: {bad}", result=result)
        return result

    def _partial(
        self, stream: Any, started: float, first_event: float | None, request_id: str | None
    ) -> CallResult | None:
        """The billed-so-far result of a stream abandoned mid-way, for usage accounting.

        Input and cache tokens are exact (``message_start`` carries them);
        output tokens are only what had been reported, so the cost is a lower bound.
        """
        try:
            snapshot = stream.current_message_snapshot
        except Exception:
            return None
        if snapshot is None:
            return None
        return self._result(snapshot, started=started, first_event=first_event, request_id=request_id)

    def _result(
        self, message: Any, *, started: float, first_event: float | None, request_id: str | None
    ) -> CallResult:
        now = time.perf_counter()
        model = getattr(message, "model", None) or self.model
        usage = Usage.from_api(message.usage)
        parsed = getattr(message, "parsed_output", None)
        result = CallResult(
            message=message,
            usage=usage,
            latency_ms=round((now - started) * 1000),
            ttft_ms=round((first_event - started) * 1000) if first_event is not None else None,
            request_id=request_id,
            cost_usd=call_cost(message.usage, model),
            content=echo_content([_block_dict(b) for b in message.content]),
            stop_reason=message.stop_reason,
            model=model,
            fell_back=_fell_back(message.usage),
            parsed=parsed if isinstance(parsed, BaseModel) else None,
        )
        log.debug(
            "llm call model=%s stop=%s in=%d out=%d cache_read=%d cache_write=%d %dms request_id=%s",
            model,
            result.stop_reason,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_read_input_tokens,
            usage.cache_creation_input_tokens,
            result.latency_ms,
            request_id,
        )
        return result
