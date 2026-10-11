"""LLM client against the real Anthropic SDK, with the HTTP layer replaced by canned SSE.

These run the SDK's own stream accumulation, so they catch request-shape and
event-handling mistakes that a fake client would hide.
"""

from __future__ import annotations

import json
from typing import Any

import httpx2
import pytest
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from papermind.llm.client import FALLBACK_BETA, LLM, PaperMindLLMError, ToolInputParseError, echo_content
from papermind.llm.pricing import Usage, cost_of
from tests.unit.fakes import fake_settings

TOOLS = [
    {
        "name": "search_paper",
        "description": "Search the paper.",
        "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "eager_input_streaming": True,
    }
]
SYSTEM = [{"type": "text", "text": "You are a test.", "cache_control": {"type": "ephemeral"}}]
MESSAGES = [{"role": "user", "content": "hi"}]


def sse(events: list[dict[str, Any]]) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode()


def message_start(usage: dict[str, int] | None = None, model: str = "claude-opus-5-5") -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": usage
            or {
                "input_tokens": 100,
                "output_tokens": 1,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 300,
            },
        },
    }


def thinking_block(index: int, signature: str = "sig-1") -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "thinking", "thinking": "", "signature": ""},
        },
        {
            "type": "content_block_delta",
            "index": index,
            "delta": {"type": "signature_delta", "signature": signature},
        },
        {"type": "content_block_stop", "index": index},
    ]


def text_block(index: int, *parts: str) -> list[dict[str, Any]]:
    return [
        {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
        *(
            {"type": "content_block_delta", "index": index, "delta": {"type": "text_delta", "text": p}}
            for p in parts
        ),
        {"type": "content_block_stop", "index": index},
    ]


def tool_block(
    index: int, *json_parts: str, name: str = "search_paper", id: str = "toolu_1"
) -> list[dict[str, Any]]:
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {"type": "tool_use", "id": id, "name": name, "input": {}},
        },
        *(
            {
                "type": "content_block_delta",
                "index": index,
                "delta": {"type": "input_json_delta", "partial_json": p},
            }
            for p in json_parts
        ),
        {"type": "content_block_stop", "index": index},
    ]


def fallback_marker(index: int, to: str = "claude-opus-4-8") -> list[dict[str, Any]]:
    """The boundary a mid-output server-side fallback leaves in the stream."""
    return [
        {
            "type": "content_block_start",
            "index": index,
            "content_block": {
                "type": "fallback",
                "from": {"model": "claude-opus-5-5"},
                "to": {"model": to},
                "trigger": {"type": "refusal"},
            },
        },
        {"type": "content_block_stop", "index": index},
    ]


def message_end(stop_reason: str, output_tokens: int = 50, **delta: Any) -> list[dict[str, Any]]:
    return [
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None, **delta},
            "usage": {"output_tokens": output_tokens},
        },
        {"type": "message_stop"},
    ]


class Transport:
    """Serves one canned response per request and records what was sent."""

    def __init__(self, *responses: httpx2.Response | list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, list):
            return httpx2.Response(
                200,
                headers={"content-type": "text/event-stream", "request-id": "req_test_1"},
                content=sse(response),
            )
        return response

    def body(self, i: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[i].content)


def make_llm(transport: Transport, **settings: Any) -> LLM:
    client = AsyncAnthropic(
        api_key="sk-test",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(transport)),
        max_retries=0,
    )
    return LLM(fake_settings(**settings), client=client)


def tool_turn() -> list[dict[str, Any]]:
    return [
        message_start(),
        *thinking_block(0),
        *text_block(1, "Let me ", "search."),
        *tool_block(2, '{"query": "scaled dot', '-product attention"}'),
        *message_end("tool_use"),
    ]


# ---------------------------------------------------------------- request shape


async def test_stream_request_shape() -> None:
    transport = Transport(tool_turn())
    llm = make_llm(transport)
    await llm.stream(
        system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=16000, cache=True
    )

    body = transport.body()
    assert body["model"] == "claude-opus-5-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"] == {"effort": "low"}
    assert body["max_tokens"] == 16000
    assert body["stream"] is True
    assert body["fallbacks"] == "default"
    assert FALLBACK_BETA in transport.requests[0].headers["anthropic-beta"]
    assert body["cache_control"] == {"type": "ephemeral"}
    assert body["system"] == SYSTEM
    assert body["tools"][0]["eager_input_streaming"] is True
    # Each of these is a 400 on this model family.
    for rejected in ("temperature", "top_p", "top_k", "tool_choice"):
        assert rejected not in body


async def test_no_fallbacks_and_no_tail_cache_when_switched_off() -> None:
    transport = Transport(tool_turn())
    llm = make_llm(transport, enable_fallbacks=False)
    await llm.stream(
        system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="medium", max_tokens=100, cache=False
    )

    body = transport.body()
    assert "fallbacks" not in body
    assert FALLBACK_BETA not in transport.requests[0].headers.get("anthropic-beta", "")
    assert "cache_control" not in body
    assert body["output_config"] == {"effort": "medium"}


# ---------------------------------------------------------------- responses


async def test_stream_returns_api_ready_blocks_usage_and_cost() -> None:
    transport = Transport(tool_turn())
    llm = make_llm(transport)
    seen: list[str] = []

    async def on_text(chunk: str) -> None:
        seen.append(chunk)

    result = await llm.stream(
        system=SYSTEM,
        messages=MESSAGES,
        tools=TOOLS,
        effort="low",
        max_tokens=100,
        cache=True,
        on_text=on_text,
    )

    # Exactly what goes back in the next request, thinking signature included.
    assert result.content == [
        {"type": "thinking", "thinking": "", "signature": "sig-1"},
        {"type": "text", "text": "Let me search."},
        {
            "type": "tool_use",
            "id": "toolu_1",
            "name": "search_paper",
            "input": {"query": "scaled dot-product attention"},
        },
    ]
    assert result.tool_uses[0]["input"] == {"query": "scaled dot-product attention"}
    assert result.text == "Let me search."
    assert seen == ["Let me ", "search."]
    assert result.stop_reason == "tool_use"
    assert result.request_id == "req_test_1"
    assert result.usage == Usage(
        input_tokens=100, output_tokens=50, cache_read_input_tokens=2000, cache_creation_input_tokens=300
    )
    # $4 in, $20 out, $5 cache write, $0.20 cache read per MTok.
    assert result.cost_usd == pytest.approx((100 * 4 + 50 * 20 + 300 * 5 + 2000 * 0.2) / 1e6)
    assert result.ttft_ms is not None and result.latency_ms >= result.ttft_ms
    assert result.fell_back is False


async def test_malformed_tool_json_raises_with_the_billed_result() -> None:
    # jiter's partial mode would read this as {"query": "att"}; the strict check must not.
    transport = Transport([message_start(), *tool_block(0, '{"query": "att" xx'), *message_end("tool_use")])
    llm = make_llm(transport)
    with pytest.raises(ToolInputParseError) as info:
        await llm.stream(
            system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
        )
    assert info.value.result is not None and info.value.result.usage.output_tokens == 50


async def test_truncated_tool_json_at_max_tokens_is_returned_for_the_caller() -> None:
    transport = Transport([message_start(), *tool_block(0, '{"query": "atte'), *message_end("max_tokens")])
    result = await make_llm(transport).stream(
        system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
    )
    assert result.stop_reason == "max_tokens"


async def test_tool_without_arguments_is_not_malformed() -> None:
    transport = Transport([message_start(), *tool_block(0, name="get_citations"), *message_end("tool_use")])
    result = await make_llm(transport).stream(
        system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
    )
    assert result.tool_uses[0]["input"] == {}


async def test_refusal_raises_with_result() -> None:
    transport = Transport([message_start(), *message_end("refusal", output_tokens=3)])
    with pytest.raises(PaperMindLLMError) as info:
        await make_llm(transport).stream(
            system=SYSTEM, messages=MESSAGES, effort="low", max_tokens=100, cache=False
        )
    assert info.value.code == "refusal"
    assert info.value.retryable is False
    assert info.value.result is not None and info.value.result.cost_usd > 0


async def test_fallback_is_detected_and_priced_per_model() -> None:
    iterations = [
        {"type": "message", "model": "claude-opus-5-5", "input_tokens": 100, "output_tokens": 0},
        {"type": "fallback_message", "model": "claude-opus-5", "input_tokens": 100, "output_tokens": 20},
    ]
    events = [message_start(), *text_block(0, "ok"), *message_end("end_turn", output_tokens=20)]
    events[-2]["usage"]["iterations"] = iterations
    result = await make_llm(Transport(events)).stream(
        system=SYSTEM, messages=MESSAGES, effort="low", max_tokens=100, cache=False
    )
    if result.message.usage.iterations is None:  # pragma: no cover - SDK did not carry iterations through
        pytest.skip("this SDK build does not accumulate usage.iterations from message_delta")
    assert result.fell_back is True
    assert result.cost_usd == pytest.approx(
        cost_of(Usage(input_tokens=100), "claude-opus-5-5") + cost_of(Usage(100, 20), "claude-opus-5")
    )


async def test_mid_output_fallback_drops_the_declined_attempt() -> None:
    # The replay rule: before the last fallback marker only text survives, so
    # the declined call is neither dispatched nor echoed back.
    events = [
        message_start(),
        *thinking_block(0, "sig-declined"),
        *text_block(1, "Let me "),
        *tool_block(2, '{"query": "declined call"}', id="toolu_declined"),
        *fallback_marker(3),
        *thinking_block(4, "sig-fallback"),
        *tool_block(5, '{"query": "fallback call"}', id="toolu_fallback"),
        *message_end("tool_use"),
    ]
    result = await make_llm(Transport(events)).stream(
        system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
    )
    assert [b["type"] for b in result.content] == ["text", "fallback", "thinking", "tool_use"]
    assert result.content[1]["from"] == {"model": "claude-opus-5-5"}  # API field name, not from_
    assert result.content[2]["signature"] == "sig-fallback"
    assert [b["id"] for b in result.tool_uses] == ["toolu_fallback"]
    assert result.model == "claude-opus-4-8"


async def test_truncated_tool_json_before_a_fallback_is_not_an_error() -> None:
    # The declined attempt stopped mid-argument; the fallback model's call is complete.
    events = [
        message_start(),
        *tool_block(0, '{"query": "decl', id="toolu_declined"),
        *fallback_marker(1),
        *tool_block(2, '{"query": "fallback call"}', id="toolu_fallback"),
        *message_end("tool_use"),
    ]
    result = await make_llm(Transport(events)).stream(
        system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
    )
    assert result.tool_uses == [
        {
            "type": "tool_use",
            "id": "toolu_fallback",
            "name": "search_paper",
            "input": {"query": "fallback call"},
        }
    ]


async def test_malformed_tool_json_after_a_fallback_is_still_an_error() -> None:
    events = [
        message_start(),
        *fallback_marker(0),
        *tool_block(1, '{"query": "att" xx'),
        *message_end("tool_use"),
    ]
    with pytest.raises(ToolInputParseError):
        await make_llm(Transport(events)).stream(
            system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
        )


async def test_refusal_mid_tool_input_is_a_refusal_not_bad_json() -> None:
    events = [
        message_start(),
        *tool_block(0, '{"query": "how to synth'),
        *message_end("refusal", output_tokens=7),
    ]
    with pytest.raises(PaperMindLLMError) as info:
        await make_llm(Transport(events)).stream(
            system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
        )
    assert info.value.code == "refusal"
    assert info.value.result is not None and info.value.result.usage.output_tokens == 7


async def test_unparseable_json_mid_stream_still_reports_the_billed_input() -> None:
    # jiter gives up on this before the block ends, inside the SDK's accumulator.
    events = [message_start(), *tool_block(0, '{"query": att', "ention}"), *message_end("tool_use")]
    with pytest.raises(ToolInputParseError) as info:
        await make_llm(Transport(events)).stream(
            system=SYSTEM, messages=MESSAGES, tools=TOOLS, effort="low", max_tokens=100, cache=True
        )
    partial = info.value.result
    assert partial is not None
    assert (partial.usage.input_tokens, partial.usage.cache_read_input_tokens) == (100, 2000)
    assert partial.cost_usd > 0 and partial.request_id == "req_test_1"


def test_echo_content_keeps_paired_server_tool_blocks_only() -> None:
    blocks: list[dict[str, Any]] = [
        {"type": "thinking", "thinking": "", "signature": "s1"},
        {"type": "server_tool_use", "id": "srv_1", "name": "web_search", "input": {}},
        {"type": "web_search_tool_result", "tool_use_id": "srv_1", "content": []},
        {"type": "server_tool_use", "id": "srv_2", "name": "web_search", "input": {}},
        {"type": "mystery_block"},
        {"type": "text", "text": "partial"},
        {"type": "fallback", "from": {"model": "a"}, "to": {"model": "b"}},
        {"type": "thinking", "thinking": "", "signature": "s2"},
        {"type": "text", "text": "rest"},
    ]
    kept = echo_content(blocks)
    assert [b.get("id") or b.get("tool_use_id") or b.get("signature") or b["type"] for b in kept] == [
        "srv_1",
        "srv_1",
        "text",
        "fallback",
        "s2",
        "text",
    ]
    no_fallback = blocks[:6]
    assert echo_content(no_fallback) is no_fallback


class _DropsMidStream(httpx2.AsyncByteStream):
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aiter__(self):  # type: ignore[override]
        yield sse([message_start(), *text_block(0, "Hello")][:3])
        raise self._exc


@pytest.mark.parametrize(
    ("exc", "says"),
    [
        (httpx2.ReadError("connection reset by peer"), "dropped"),
        (httpx2.ReadTimeout("read timed out"), "stopped responding"),
    ],
)
async def test_connection_lost_mid_stream_is_a_retryable_api_error(exc: Exception, says: str) -> None:
    # The SDK wraps transport failures only before the response starts; after
    # that they surface raw from the aiohttp transport (mapped to httpx2's).
    response = httpx2.Response(
        200,
        headers={"content-type": "text/event-stream", "request-id": "req_drop"},
        stream=_DropsMidStream(exc),
    )
    seen: list[str] = []

    async def on_text(chunk: str) -> None:
        seen.append(chunk)

    with pytest.raises(PaperMindLLMError) as info:
        await make_llm(Transport(response)).stream(
            system=SYSTEM, messages=MESSAGES, effort="low", max_tokens=100, cache=False, on_text=on_text
        )
    assert (info.value.code, info.value.retryable, info.value.request_id) == ("api_error", True, "req_drop")
    assert says in info.value.message
    assert seen == ["Hello"]


# ---------------------------------------------------------------- errors


def error_response(status: int, err_type: str) -> httpx2.Response:
    return httpx2.Response(
        status,
        headers={"request-id": "req_err"},
        json={"type": "error", "error": {"type": err_type, "message": "nope"}},
    )


@pytest.mark.parametrize(
    ("status", "err_type", "code", "retryable"),
    [
        (401, "authentication_error", "auth_failed", False),
        (403, "permission_error", "auth_failed", False),
        (429, "rate_limit_error", "rate_limited", True),
        (529, "overloaded_error", "overloaded", True),
        (500, "api_error", "api_error", True),
        (400, "invalid_request_error", "api_error", False),
    ],
)
async def test_http_errors_map_to_error_codes(status: int, err_type: str, code: str, retryable: bool) -> None:
    llm = make_llm(Transport(error_response(status, err_type)))
    with pytest.raises(PaperMindLLMError) as info:
        await llm.stream(system=SYSTEM, messages=MESSAGES, effort="low", max_tokens=100, cache=False)
    assert (info.value.code, info.value.retryable) == (code, retryable)
    assert info.value.message  # plain-language, for the error event


async def test_midstream_overload_maps_to_overloaded() -> None:
    events = [
        message_start(),
        {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}},
    ]
    with pytest.raises(PaperMindLLMError) as info:
        await make_llm(Transport(events)).stream(
            system=SYSTEM, messages=MESSAGES, effort="low", max_tokens=100, cache=False
        )
    assert info.value.code == "overloaded" and info.value.retryable


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(PaperMindLLMError) as info:
        LLM(fake_settings())
    assert info.value.code == "no_api_key"
    assert "ANTHROPIC_API_KEY" in info.value.message


# ---------------------------------------------------------------- parse and prewarm


class Verdict(BaseModel):
    concept: str
    known: bool


async def test_parse_returns_the_validated_model() -> None:
    payload = json.dumps({"concept": "softmax", "known": False})
    transport = Transport(
        [message_start(), *text_block(0, payload[:10], payload[10:]), *message_end("end_turn")]
    )
    result = await make_llm(transport).parse(
        system=SYSTEM, messages=MESSAGES, schema=Verdict, effort="low", max_tokens=1000
    )
    assert result.parsed == Verdict(concept="softmax", known=False)
    body = transport.body()
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["stream"] is True


async def test_parse_with_invalid_output_is_an_api_error() -> None:
    transport = Transport([message_start(), *text_block(0, '{"concept": 3}'), *message_end("end_turn")])
    with pytest.raises(PaperMindLLMError) as info:
        await make_llm(transport).parse(
            system=SYSTEM, messages=MESSAGES, schema=Verdict, effort="low", max_tokens=100
        )
    assert info.value.code == "api_error"


async def test_prewarm_is_a_zero_token_prefill_keyed_to_the_system_blocks() -> None:
    response = httpx2.Response(
        200,
        headers={"request-id": "req_warm"},
        json={
            "id": "msg_w",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [],
            "stop_reason": "max_tokens",
            "stop_sequence": None,
            "usage": {
                "input_tokens": 5,
                "output_tokens": 0,
                "cache_creation_input_tokens": 2500,
                "cache_read_input_tokens": 0,
            },
        },
    )
    transport = Transport(response)
    result = await make_llm(transport).prewarm(system=SYSTEM, tools=TOOLS, effort="low")

    body = transport.body()
    assert body["max_tokens"] == 0
    assert "stream" not in body or body["stream"] is False
    # A tail breakpoint would key the entry to the placeholder message.
    assert "cache_control" not in body
    assert body["system"] == SYSTEM and body["tools"] == TOOLS
    assert body["output_config"] == {"effort": "low"} and body["thinking"] == {"type": "adaptive"}
    assert result.usage.cache_creation_input_tokens == 2500


def test_an_empty_credit_balance_says_so() -> None:
    import anthropic
    import httpx2

    from papermind.llm.client import map_api_error

    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Your credit balance is too low to access the Anthropic API.",
        },
    }
    response = httpx2.Response(
        400, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"), json=body
    )
    err = map_api_error(anthropic.BadRequestError("credit", response=response, body=body))
    assert "out of credits" in err.message and not err.retryable
