from __future__ import annotations

import json

import httpx
import pytest

from papermind.agent.cache_policy import load_prompt
from papermind.agent.labels import label_external, label_sources
from papermind.agent.writer import (
    LEVEL_DEFINITIONS,
    ClaudeWriter,
    MistralWriter,
    WriterInput,
    make_writer,
    mistral_messages,
    render_writer_message,
)
from papermind.llm.client import CallResult, PaperMindLLMError
from papermind.llm.pricing import Usage
from papermind.schemas.session import Quote
from tests.unit.fakes import BLEU_PAPER, DEFAULT_CHUNKS, FakeLLM, Scripted, fake_settings, make_paper, text


def writer_input(**overrides) -> WriterInput:
    fields = {
        "question": "Why divide by sqrt(d_k)?",
        "level": "beginner",
        "paper": make_paper(),
        "plan": "Explain the scaling: large dot products push softmax into flat regions.",
        "altitude": "scaffolded",
        "sources": label_sources(DEFAULT_CHUNKS[:2]),
        "concepts_to_scaffold": ["dot product", "softmax"],
    }
    fields.update(overrides)
    return WriterInput(**fields)


def test_level_definitions_are_stated_verbatim_in_the_rubric() -> None:
    rubric = load_prompt("writer")
    for level, definition in LEVEL_DEFINITIONS.items():
        assert f"**{level}**: {definition}." in rubric


def test_rubric_carries_the_altitude_policy_and_citation_rules() -> None:
    rubric = load_prompt("writer")
    assert "The prerequisite graph does not gate the answer; it selects the altitude" in rubric
    assert "Use only the labels listed in `<sources>` and `<external_papers>`" in rubric
    assert "Never invent numbers" in rubric
    assert "Cite one with its label, [P1]" in rubric
    assert "do not also write the authors, year or title yourself" in rubric


def test_rubric_sets_the_voice_and_marks_background() -> None:
    rubric = load_prompt("writer")
    assert 'Never refer to yourself: no "I"' in rubric
    assert '"Great question"' in rubric and "Never praise or grade the question" in rubric
    assert 'Never label or describe the reader: no "as a beginner"' in rubric
    # Uncited paragraphs are shown as background, so paper claims must be cited
    # and background must stand in its own paragraph, said as such.
    assert (
        "Every paragraph or list item that says something from the paper carries at least one [S] label"
        in rubric
    )
    assert "say in the prose that it is general background rather than the paper's content" in rubric


def test_message_has_every_section_in_order() -> None:
    inp = writer_input(
        quote=Quote(text="We suspect that for large values of d_k ...", page=3),
        external_papers=label_external([BLEU_PAPER]),
        history=[("user", "What is attention?"), ("assistant", "Attention weighs values [S1].")],
    )
    msg = render_writer_message(inp)
    order = [
        "<reader>",
        "<plan>",
        "<concepts_to_scaffold>",
        "<sources>",
        "<external_papers>",
        "<conversation>",
        '<selected_passage page="4">',
        "<question>",
    ]
    positions = [msg.index(tag) for tag in order]
    assert positions == sorted(positions)
    assert f"level: beginner ({LEVEL_DEFINITIONS['beginner']})" in msg
    assert "altitude: scaffolded" in msg
    assert "1. dot product\n2. softmax" in msg
    assert '<source label="S1" section="§3.2.1 Scaled Dot-Product Attention" pages="4">' in msg
    assert (
        '<paper label="P1" year="2002">\n'
        "Title: Bleu: a Method for Automatic Evaluation of Machine Translation\n"
        "Authors: Kishore Papineni, Salim Roukos, T. Ward et al.\n"
        "Summary: Human evaluations of machine translation are extensive but expensive...\n"
        "</paper>"
    ) in msg
    # Old answers lose their citation markers: the labels meant other sources then.
    assert "PaperMind: Attention weighs values." in msg


def test_empty_sections_say_none() -> None:
    msg = render_writer_message(writer_input(sources=[], concepts_to_scaffold=[]))
    assert "<sources>\nnone: no passages" in msg
    assert "<concepts_to_scaffold>\nnone\n" in msg
    assert "<conversation>" not in msg and "<selected_passage" not in msg


def test_learning_path_is_its_own_section_between_scaffold_and_sources() -> None:
    msg = render_writer_message(writer_input(learning_path=["Dot Product", "Softmax", "Attention"]))
    assert (
        "</concepts_to_scaffold>\n\n<learning_path>\n1. Dot Product\n2. Softmax\n3. Attention\n</learning_path>"
        in msg
    )
    assert msg.index("</learning_path>") < msg.index("<sources>")
    assert "the path is the answer" in load_prompt("writer")


async def test_claude_writer_streams_and_reports_its_call() -> None:
    answer = "Dividing by $\\sqrt{d_k}$ keeps the scores small [S1]."
    llm = FakeLLM(writer=[Scripted(content=[text(answer)], stop_reason="end_turn", usage=Usage(500, 80))])
    settings = fake_settings()
    writer = ClaudeWriter(llm, settings)
    calls: list[CallResult] = []
    chunks = [c async for c in writer.stream(writer_input(on_call=calls.append))]

    assert "".join(chunks) == answer and len(chunks) > 1
    assert writer.name == "claude:claude-opus-5-5"
    [req] = llm.requests("writer")
    assert req["tools"] is None
    assert req["effort"] == settings.writer_effort
    assert req["max_tokens"] == settings.writer_max_tokens
    assert req["system"][0]["text"] == load_prompt("writer")
    assert req["messages"][0]["content"].endswith("<question>\nWhy divide by sqrt(d_k)?\n</question>")
    assert [c.usage for c in calls] == [Usage(500, 80)]


async def test_writer_errors_propagate_after_reporting_usage() -> None:
    from papermind.llm.client import llm_error

    llm = FakeLLM(writer=[llm_error("overloaded", retryable=True)])
    writer = ClaudeWriter(llm, fake_settings())
    with pytest.raises(Exception) as info:
        _ = [c async for c in writer.stream(writer_input())]
    assert getattr(info.value, "code", None) == "overloaded"


def _sse(*deltas: str) -> bytes:
    frames = ["data: " + json.dumps({"choices": [{"delta": {"content": d}}]}) + "\n\n" for d in deltas]
    return ("".join(frames) + "data: [DONE]\n\n").encode()


async def test_mistral_writer_streams_the_deltas_from_vllm() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=_sse("Attention ", "weights [S1]", ""))

    settings = fake_settings(writer_backend="mistral", mistral_model="papermind")
    writer = MistralWriter(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = [c async for c in writer.stream(writer_input())]
    assert out == ["Attention ", "weights [S1]"]
    assert seen["url"].endswith("/chat/completions")
    assert seen["body"]["model"] == "papermind" and seen["body"]["stream"] is True
    assert seen["body"]["messages"] == mistral_messages(writer_input())
    assert writer.name == "mistral:papermind"


async def test_mistral_writer_failures_become_retryable_api_errors() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="model loading")

    def error_mid_answer(request: httpx.Request) -> httpx.Response:
        body = _sse("Attention is").replace(
            b"data: [DONE]", b'data: {"object": "error", "message": "CUDA OOM"}\n\ndata: [DONE]'
        )
        return httpx.Response(200, content=body)

    def nothing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not streamed"}}]})

    def garbage(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"data: {not json\n\n")

    settings = fake_settings(writer_backend="mistral", mistral_base_url="http://10.0.0.7:8001/v1")
    for handler in (down, broken, error_mid_answer, nothing, garbage):
        writer = MistralWriter(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with pytest.raises(PaperMindLLMError) as err:
            _ = [c async for c in writer.stream(writer_input())]
        assert err.value.code == "api_error" and err.value.retryable
        # Readers never see the server's address or its raw reply.
        assert "10.0.0.7" not in str(err.value) and "CUDA" not in str(err.value)


def test_mistral_prompt_is_the_compact_rubric_plus_the_claude_sections() -> None:
    [message] = mistral_messages(writer_input())
    assert message["role"] == "user"
    assert message["content"].endswith(render_writer_message(writer_input()))
    # Training data, the served prompt and the eval judge must mean the same thing by each level.
    rubric = load_prompt("writer_mistral")
    for level, definition in LEVEL_DEFINITIONS.items():
        assert f"- {level}: {definition}" in rubric


def test_make_writer_picks_mistral_when_configured() -> None:
    assert isinstance(make_writer(fake_settings(writer_backend="mistral"), FakeLLM()), MistralWriter)


def test_claude_is_the_default_writer() -> None:
    assert isinstance(make_writer(fake_settings(), FakeLLM()), ClaudeWriter)
