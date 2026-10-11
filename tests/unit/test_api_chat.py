from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import fastapi.routing
import pytest

from papermind.agent.loop import TurnContext
from papermind.agent.mastery import MasteryItem, MasteryJudgement
from papermind.api.chat_turn import INTERRUPTED_CODE, INTERRUPTED_MESSAGE, NO_API_KEY_MESSAGE
from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from papermind.memory.session_store import lock_key
from papermind.schemas.events import (
    AnswerDelta,
    AnswerDone,
    Done,
    ErrorEvent,
    ExternalRef,
    Followups,
    GapsFound,
    KnowledgeUpdated,
    SourceRef,
    SourcesReady,
    ToolFinished,
    ToolStarted,
    TurnStarted,
    UsageReport,
)
from papermind.schemas.paper import PaperStatus
from papermind.schemas.session import Session
from papermind.schemas.tools import ConceptRef, Gap, MarkKnownServerArgs
from tests.unit.fakes import FakeLLM, FakeMCP, agent_turn, compose, make_runner, tool_use
from tests.unit.test_api_support import (
    PAPER_ID,
    FakeRunner,
    Harness,
    drive_sse,
    happy_turn,
    make_harness,
    parse_sse,
)

CONTRACT_ORDER = [
    "turn.started",
    "tool.started",
    "tool.finished",
    "gaps",
    "sources",
    "answer.delta",
    "answer.delta",
    "answer.done",
    "knowledge.updated",
    "usage",
    "done",
]


async def _session(h: Harness, level: str = "intermediate") -> Session:
    user = await h.add_user(level)
    return await h.res.sessions.create(user.user_id, PAPER_ID, user.level)


async def _chat(h: Harness, session_id: str, message: str = "How does attention work?", **extra: Any):
    async with h.client() as client:
        return await client.post("/api/chat", json={"session_id": session_id, "message": message, **extra})


def _names(response: Any) -> list[str | None]:
    return [name for name, _ in parse_sse(response.text)]


async def test_streams_named_frames_in_contract_order(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    session = await _session(h)

    response = await _chat(h, session.session_id)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    frames = parse_sse(response.text)
    # The runner's own turn.started and done are dropped: exactly one of each.
    assert [name for name, _ in frames] == CONTRACT_ORDER
    for name, data in frames:
        assert data is not None and data["type"] == name, "event: line must name the payload's type"
    started = frames[0][1]
    ctx = runner.contexts[0]
    assert started == {
        "type": "turn.started",
        "turn_id": ctx.turn_id,
        "session_id": session.session_id,
        "level": "intermediate",
        "user_message_id": ctx.user_message_id,
    }


async def test_turn_context_carries_session_profile_paper_and_knowledge(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    session = await _session(h, level="beginner")
    await h.graph.mark(
        MarkKnownServerArgs(user_id="u_reader", concept="softmax", confidence=0.9, source="self_reported")
    )
    # Below the 0.6 threshold: known to the mastery pass, not to the agent.
    await h.graph.mark(
        MarkKnownServerArgs(user_id="u_reader", concept="attention", confidence=0.4, source="inferred")
    )
    await _chat(h, session.session_id, "First question?")

    await _chat(h, session.session_id, "What is this?", quote={"text": "Scaled Dot-Product", "page": 3})

    ctx = runner.contexts[1]
    assert isinstance(ctx, TurnContext)
    assert ctx.question == "What is this?"
    assert ctx.quote is not None and ctx.quote.text == "Scaled Dot-Product"
    assert ctx.level == "beginner" and ctx.user.user_id == "u_reader"
    assert ctx.paper.paper_id == PAPER_ID
    assert ctx.known_summary == ["Softmax"]
    assert ctx.knowledge is not None and set(ctx.knowledge) == {"softmax", "attention"}
    # History is the transcript before this question: the first turn, not the new message.
    assert [(m.role, m.text) for m in ctx.session.messages] == [
        ("user", "First question?"),
        ("assistant", "Attention weighs values [S1]."),
    ]


async def test_level_override_applies_to_the_turn_and_is_saved(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    session = await _session(h, level="intermediate")

    response = await _chat(h, session.session_id, level="expert")

    assert parse_sse(response.text)[0][1]["level"] == "expert"  # type: ignore[index]
    assert runner.contexts[0].level == "expert"
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None and saved.level == "expert"
    assert [m.level for m in saved.messages] == ["expert", "expert"]


async def test_question_is_persisted_before_the_answer_and_the_answer_after(tmp_path: Path) -> None:
    seen_mid_turn: list[Session | None] = []
    h: Harness

    async def script(ctx: Any, emit: Any) -> None:
        seen_mid_turn.append(await h.res.sessions.get(ctx.session.session_id))
        await happy_turn(ctx, emit)

    h = make_harness(tmp_path, runner=FakeRunner(script))
    session = await _session(h)

    await _chat(h, session.session_id)

    during = seen_mid_turn[0]
    assert during is not None and [m.role for m in during.messages] == ["user"]
    after = await h.res.sessions.get(session.session_id)
    assert after is not None
    question, answer = after.messages
    assert (
        question.text == "How does attention work?" and question.message_id == during.messages[0].message_id
    )
    assert answer.role == "assistant"
    assert answer.message_id == "m_answer"
    assert answer.text == "Attention weighs values [S1]."
    assert [s.label for s in answer.sources] == ["S1"]
    assert answer.gaps is not None and answer.gaps.gaps[0].concept_id == "softmax"
    assert [(a.tool, a.label, a.summary, a.ok, a.duration_ms) for a in answer.activity] == [
        ("search_paper", "Searching the paper", "Found 2 passages", True, 12)
    ]
    assert answer.error is None


async def test_runner_error_event_streams_then_done_and_is_persisted(tmp_path: Path) -> None:
    async def rate_limited(ctx: Any, emit: Any) -> None:
        await emit(TurnStarted(turn_id=ctx.turn_id, session_id="s", level=ctx.level, user_message_id="m"))
        await emit(ToolStarted(call_id="c1", tool="search_paper", label="Searching the paper"))
        await emit(ErrorEvent(code="rate_limited", message="Slow down and ask again.", retryable=True))
        await emit(Done())

    h = make_harness(tmp_path, runner=FakeRunner(rate_limited))
    session = await _session(h)

    response = await _chat(h, session.session_id)

    frames = parse_sse(response.text)
    assert [n for n, _ in frames] == ["turn.started", "tool.started", "error", "done"]
    assert frames[2][1] == {
        "type": "error",
        "code": "rate_limited",
        "message": "Slow down and ask again.",
        "retryable": True,
    }
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None and saved.messages[-1].error == "Slow down and ask again."


async def test_an_exception_mid_stream_becomes_an_error_event_then_done(tmp_path: Path) -> None:
    async def explode(ctx: Any, emit: Any) -> None:
        await emit(AnswerDelta(text="Attention is "))
        raise RuntimeError("database password is hunter2")

    h = make_harness(tmp_path, runner=FakeRunner(explode))
    session = await _session(h)

    response = await _chat(h, session.session_id)

    assert response.status_code == 200
    frames = parse_sse(response.text)
    assert [n for n, _ in frames] == ["turn.started", "answer.delta", "error", "done"]
    error = frames[2][1]
    assert error is not None and error["code"] == "internal" and error["retryable"] is True
    assert "hunter2" not in response.text, "internal details never reach the reader"
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    assert saved.messages[-1].text == "Attention is "
    assert saved.messages[-1].error == error["message"]


async def test_without_an_api_key_the_stream_explains_how_to_add_one(tmp_path: Path) -> None:
    h = make_harness(tmp_path, runner=None)
    session = await _session(h)

    response = await _chat(h, session.session_id)

    assert response.status_code == 200
    frames = parse_sse(response.text)
    assert [n for n, _ in frames] == ["turn.started", "error", "done"]
    error = frames[1][1]
    assert error is not None and error["code"] == "no_api_key" and error["retryable"] is False
    assert error["message"] == NO_API_KEY_MESSAGE
    assert "ANTHROPIC_API_KEY" in error["message"] and ".env" in error["message"]
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None and [m.role for m in saved.messages] == ["user", "assistant"]
    assert saved.messages[1].error == NO_API_KEY_MESSAGE


async def test_unknown_session_is_a_404_before_streaming(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)

    response = await _chat(h, "s_does_not_exist")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert response.json()["error"]["code"] == "session_not_found"
    assert runner.contexts == []


async def test_paper_still_processing_is_a_409_before_streaming(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    session = await h.res.sessions.create("u_reader", "2401.00001", "intermediate")
    await h.res.ingest_status.claim("2401.00001", source="arxiv", title="arXiv:2401.00001")
    await h.res.ingest_status.update("2401.00001", status=PaperStatus.PROCESSING)

    response = await _chat(h, session.session_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "paper_not_ready"
    assert runner.contexts == []
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None and saved.messages == [], "nothing is persisted for a refused question"


async def test_deleted_paper_is_a_404(tmp_path: Path) -> None:
    h = make_harness(tmp_path, runner=FakeRunner())
    session = await _session(h)
    h.store.delete(PAPER_ID)

    response = await _chat(h, session.session_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "paper_not_found"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"session_id": "s_1", "message": ""}, "message: String should have at least 1 character"),
        ({"session_id": "s_1"}, "message: is required"),
        ({"session_id": "s_1", "message": "hi", "level": "wizard"}, "level: Input should be"),
    ],
)
async def test_invalid_requests_are_readable_422s(tmp_path: Path, payload: dict, message: str) -> None:
    h = make_harness(tmp_path, runner=FakeRunner())
    async with h.client() as client:
        response = await client.post("/api/chat", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"].startswith(message)


async def test_a_session_locked_by_another_request_is_a_409(tmp_path: Path) -> None:
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    h.res.sessions._lock_wait_s = 0.1  # type: ignore[attr-defined]
    session = await _session(h)
    await h.redis.set(lock_key(session.session_id), "another-request", px=10_000)

    response = await _chat(h, session.session_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "session_busy"
    assert response.headers["retry-after"] == "1"
    assert runner.contexts == []


async def test_disconnect_while_the_agent_works_cancels_the_turn(tmp_path: Path) -> None:
    async def slow(ctx: Any, emit: Any) -> None:
        await emit(ToolStarted(call_id="c1", tool="search_paper", label="Searching the paper"))
        await asyncio.sleep(30)

    runner = FakeRunner(slow)
    h = make_harness(tmp_path, runner=runner)
    session = await _session(h)

    stream = await drive_sse(
        h.app,
        "/api/chat",
        {"session_id": session.session_id, "message": "How?"},
        disconnect_when=lambda frames: any(name == "tool.started" for name, _ in frames),
    )

    assert stream.status == 200
    assert runner.cancelled.is_set()
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    reply = saved.messages[-1]
    assert reply.role == "assistant" and reply.error == INTERRUPTED_MESSAGE
    assert [a.tool for a in reply.activity] == ["search_paper"]


async def test_disconnect_mid_answer_burst_cancels_the_turn_before_the_request_ends(tmp_path: Path) -> None:
    """The stream is parked at a yield when the client goes; cleanup must not wait for GC."""

    async def burst_then_hang(ctx: Any, emit: Any) -> None:
        for i in range(200):
            await emit(AnswerDelta(text=f"w{i} "))
        await asyncio.Event().wait()

    runner = FakeRunner(burst_then_hang)
    h = make_harness(tmp_path, runner=runner)
    session = await _session(h)

    await drive_sse(
        h.app,
        "/api/chat",
        {"session_id": session.session_id, "message": "How?"},
        disconnect_when=lambda frames: len(frames) >= 3,
    )

    # No sleep, no gc.collect(): both happened before the app returned.
    assert runner.cancelled.is_set()
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    reply = saved.messages[-1]
    assert reply.error == INTERRUPTED_MESSAGE and reply.text.startswith("w0 w1 ")


async def test_keepalive_comment_while_the_agent_is_quiet(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(fastapi.routing, "_PING_INTERVAL", 0.05)

    async def thinks_first(ctx: Any, emit: Any) -> None:
        await asyncio.sleep(0.3)
        await happy_turn(ctx, emit)

    h = make_harness(tmp_path, runner=FakeRunner(thinks_first))
    session = await _session(h)

    response = await _chat(h, session.session_id)

    assert ": ping" in response.text
    assert [n for n in _names(response) if n is not None] == CONTRACT_ORDER


async def test_a_full_turn_through_the_real_agent_loop(tmp_path: Path) -> None:
    """The real AgentRunner over agent-core's fakes: tools, hand-off, writer, mastery, all via HTTP."""
    graph = InMemoryGraphStore(sample_catalog())
    chunk_id = f"{PAPER_ID}:sec_3.2.1_scaled_dot_product_attention"
    llm = FakeLLM(
        agent=[
            agent_turn(
                tool_use("search_paper", {"query": "scaled dot-product attention"}),
                tool_use("check_prerequisites", {"concept": "attention"}),
            ),
            agent_turn(compose("Explain the scaling for a beginner.", [chunk_id])),
        ],
        writer=["Attention compares a query with every key, then scales by sqrt(d_k) [S1]."],
        mastery=[
            MasteryJudgement(
                demonstrated=[],
                unfamiliar=[MasteryItem(concept="softmax", evidence="what is softmax", strength="strong")],
            )
        ],
    )
    runner = make_runner(llm, FakeMCP(graph=graph))
    h = make_harness(tmp_path, runner=runner, graph=graph)
    session = await _session(h, level="beginner")
    await graph.mark(
        MarkKnownServerArgs(user_id="u_reader", concept="softmax", confidence=0.9, source="self_reported")
    )

    response = await _chat(h, session.session_id, "How does attention work, and what is softmax?")

    frames = parse_sse(response.text)
    names = [n for n, _ in frames]
    assert names[0] == "turn.started" and names[-1] == "done"
    assert names.count("turn.started") == 1 and names.count("done") == 1
    assert {"tool.started", "tool.finished", "gaps", "sources", "answer.done", "usage"} <= set(names)
    assert names.index("sources") < names.index("answer.delta") < names.index("answer.done")
    done = next(d for n, d in frames if n == "answer.done")
    assert done is not None and done["cited"] == ["S1"]
    sources = next(d for n, d in frames if n == "sources")
    assert sources is not None and sources["sources"][0]["chunk_id"] == chunk_id

    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    reply = saved.messages[-1]
    assert reply.text == done["text"] and reply.message_id == done["message_id"]
    assert {a.tool for a in reply.activity} == {"search_paper", "check_prerequisites"}
    assert [s.model_dump(mode="json") for s in reply.sources] == done["sources"]
    usage = next(d for n, d in frames if n == "usage")
    assert usage is not None and reply.latency_ms == usage["latency_ms"]
    # The reader asked what softmax is, but they said they know it: the
    # mastery pass asks them instead of overruling them.
    knowledge = next(d for n, d in frames if n == "knowledge.updated")
    assert knowledge is not None and [c["concept_id"] for c in knowledge["ask"]] == ["softmax"]
    known = await graph.known("u_reader")
    assert known["softmax"].confidence == 0.9 and known["softmax"].source == "self_reported"


async def test_openapi_publishes_the_event_stream(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        spec = (await client.get("/openapi.json")).json()
    content = spec["paths"]["/api/chat"]["post"]["responses"]["200"]["content"]
    assert "text/event-stream" in content
    assert set(spec["paths"]["/api/chat"]["post"]["responses"]) >= {"200", "404", "409", "422"}


async def test_prepared_turn_survives_a_graph_outage(tmp_path: Path) -> None:
    class FlakyGraph(InMemoryGraphStore):
        async def known(self, user_id: str):  # type: ignore[override]
            raise ConnectionError("neo4j went away")

    runner = FakeRunner()
    graph = FlakyGraph(sample_catalog())
    h = make_harness(tmp_path, runner=runner, graph=graph)
    session = await _session(h)

    response = await _chat(h, session.session_id)

    assert [n for n in _names(response)] == CONTRACT_ORDER
    assert runner.contexts[0].known_summary == [] and runner.contexts[0].knowledge is None


class _HangingGraph(InMemoryGraphStore):
    """A graph whose server went away: every read waits (the driver's retry window), then fails."""

    def __init__(self) -> None:
        super().__init__(sample_catalog())
        self.reads: list[str] = []

    async def get_user(self, user_id: str):  # type: ignore[override]
        self.reads.append("get_user")
        await asyncio.Event().wait()

    async def known(self, user_id: str):  # type: ignore[override]
        self.reads.append("known")
        await asyncio.Event().wait()


async def test_a_graph_that_stopped_answering_delays_the_turn_but_does_not_fail_it(
    tmp_path: Path, monkeypatch
) -> None:
    import papermind.api.chat_turn as chat_turn

    monkeypatch.setattr(chat_turn, "GRAPH_READ_TIMEOUT_S", 0.05)
    runner = FakeRunner()
    graph = _HangingGraph()
    h = make_harness(tmp_path, runner=runner, graph=graph)
    session = await h.res.sessions.create("u_reader", PAPER_ID, "beginner")

    response = await asyncio.wait_for(_chat(h, session.session_id), 5)

    assert response.status_code == 200
    assert _names(response) == CONTRACT_ORDER
    ctx = runner.contexts[0]
    assert ctx.user.user_id == "u_reader" and ctx.user.level == "beginner"
    assert ctx.knowledge is None and ctx.known_summary == []
    assert graph.reads == ["get_user"], "after the profile read timed out, the turn skips the graph"


async def test_a_graph_that_fails_fast_is_skipped_too(tmp_path: Path) -> None:
    class DownGraph(InMemoryGraphStore):
        async def get_user(self, user_id: str):  # type: ignore[override]
            raise ConnectionError("neo4j went away")

    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner, graph=DownGraph(sample_catalog()))
    session = await h.res.sessions.create("u_reader", PAPER_ID, "intermediate")

    response = await _chat(h, session.session_id)

    assert response.status_code == 200 and _names(response) == CONTRACT_ORDER
    assert runner.contexts[0].knowledge is None


async def test_the_knowledge_read_is_bounded(tmp_path: Path, monkeypatch) -> None:
    import papermind.api.chat_turn as chat_turn

    monkeypatch.setattr(chat_turn, "GRAPH_READ_TIMEOUT_S", 0.05)

    class SlowKnowledge(InMemoryGraphStore):
        async def known(self, user_id: str):  # type: ignore[override]
            await asyncio.Event().wait()

    runner = FakeRunner()
    graph = SlowKnowledge(sample_catalog())
    h = make_harness(tmp_path, runner=runner, graph=graph)
    session = await _session(h)

    response = await asyncio.wait_for(_chat(h, session.session_id), 5)

    assert _names(response) == CONTRACT_ORDER
    assert runner.contexts[0].knowledge is None
    with pytest.raises(TimeoutError):
        await chat_turn.read_knowledge(graph, "u_reader")


async def test_a_level_change_during_prepare_is_not_reverted(tmp_path: Path) -> None:
    """A PATCH that lands between the turn's session read and its locked update wins."""
    runner = FakeRunner()
    h = make_harness(tmp_path, runner=runner)
    session = await _session(h, level="intermediate")
    real_get = h.res.sessions.get
    patched: list[int] = []
    armed = True

    async def get_then_patch(session_id: str) -> Session | None:
        nonlocal armed
        found = await real_get(session_id)
        if armed:
            armed = False  # the PATCH's own read must not patch again
            async with h.client() as client:
                response = await client.patch(f"/api/sessions/{session_id}", json={"level": "expert"})
            patched.append(response.status_code)
        return found

    h.res.sessions.get = get_then_patch  # type: ignore[method-assign]
    response = await _chat(h, session.session_id)
    h.res.sessions.get = real_get  # type: ignore[method-assign]

    assert patched == [200]
    assert parse_sse(response.text)[0][1]["level"] == "expert"  # type: ignore[index]
    assert runner.contexts[0].level == "expert"
    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None and saved.level == "expert"
    assert [m.level for m in saved.messages] == ["expert", "expert"]


# ---------------------------------------------------------------- what the transcript keeps


def _source(label: str, chunk: str, cited: str | None = None) -> SourceRef:
    return SourceRef(
        label=label,
        chunk_id=f"{PAPER_ID}:{chunk}",
        section_number="3.2",
        section_title="Scaled Dot-Product Attention",
        page_start=3,
        page_end=3,
        snippet="We call our particular attention...",
        cited_sentence=cited,
    )


BAHDANAU = ExternalRef(
    label="P1",
    s2_id="fa72afa9b2cbc8f0d7b05d52548906610ffbb9c5",
    title="Neural Machine Translation by Jointly Learning to Align and Translate",
    authors=["Dzmitry Bahdanau", "Kyunghyun Cho", "Yoshua Bengio"],
    year=2014,
    venue="ICLR",
    arxiv_id="1409.0473",
)


async def rich_turn(ctx: Any, emit: Any) -> None:
    """A turn with everything a reloaded transcript has to show again."""
    await emit(ToolStarted(call_id="c1", tool="search_paper", label="Searching the paper for ‘scaling’"))
    await emit(
        ToolFinished(
            call_id="c1", tool="search_paper", ok=True, duration_ms=210, summary="Found 2 passages in §3.2"
        )
    )
    await emit(
        ToolStarted(call_id="c2", tool="lookup_paper", label="Looking up ‘Bahdanau 2014’ on Semantic Scholar")
    )
    await emit(
        ToolFinished(
            call_id="c2",
            tool="lookup_paper",
            ok=False,
            duration_ms=30000,
            summary="Couldn’t reach Semantic Scholar",
            detail={"error": "timeout"},
        )
    )
    await emit(
        GapsFound(
            concept=ConceptRef(concept_id="attention", name="Attention"),
            gaps=[Gap(concept_id="softmax", name="Softmax", depth=1)],
            teaching_order=[ConceptRef(concept_id="softmax", name="Softmax")],
            altitude="intuition",
        )
    )
    await emit(SourcesReady(sources=[_source("S1", "sec_3.2_a"), _source("S2", "sec_3.2_b")]))
    await emit(
        AnswerDelta(text="Scaling keeps softmax gradients alive [S1], unlike additive attention [P1].")
    )
    await emit(
        AnswerDone(
            message_id="m_rich",
            text="Scaling keeps softmax gradients alive [S1], unlike additive attention [P1].",
            cited=["S1"],
            external=[BAHDANAU],
            sources=[
                _source(
                    "S1", "sec_3.2_a", cited="We suspect that for large values of d_k the dot products grow."
                ),
                _source("S2", "sec_3.2_b"),
            ],
            writer="fake",
        )
    )
    await emit(
        KnowledgeUpdated(
            added=[ConceptRef(concept_id="attention", name="Attention")],
            ask=[ConceptRef(concept_id="softmax", name="Softmax")],
        )
    )
    await emit(
        Followups(
            message_id="m_rich",
            questions=["Why does additive attention avoid the scaling problem?", "What does $d_k$ control?"],
        )
    )
    await emit(
        UsageReport(
            model_calls=3,
            input_tokens=900,
            output_tokens=120,
            cache_read_input_tokens=800,
            cache_creation_input_tokens=0,
            cost_usd=0.01,
            latency_ms=4321,
            ttft_ms=2100,
            tool_calls=2,
        )
    )


async def test_the_persisted_answer_carries_everything_the_turn_showed(tmp_path: Path) -> None:
    h = make_harness(tmp_path, runner=FakeRunner(rich_turn))
    session = await _session(h)

    await _chat(h, session.session_id)
    async with h.client() as client:
        body = (await client.get(f"/api/sessions/{session.session_id}")).json()

    reply = body["messages"][-1]
    assert reply["role"] == "assistant" and reply["message_id"] == "m_rich"
    # answer.done's sources replace the earlier event's: they carry the cited sentence.
    assert [(s["label"], s["cited_sentence"]) for s in reply["sources"]] == [
        ("S1", "We suspect that for large values of d_k the dot products grow."),
        ("S2", None),
    ]
    assert [(p["label"], p["title"], p["arxiv_id"]) for p in reply["external"]] == [
        ("P1", "Neural Machine Translation by Jointly Learning to Align and Translate", "1409.0473")
    ]
    assert reply["gaps"]["gaps"][0]["concept_id"] == "softmax"
    assert [(a["tool"], a["label"], a["summary"], a["ok"], a["duration_ms"]) for a in reply["activity"]] == [
        ("search_paper", "Searching the paper for ‘scaling’", "Found 2 passages in §3.2", True, 210),
        (
            "lookup_paper",
            "Looking up ‘Bahdanau 2014’ on Semantic Scholar",
            "Couldn’t reach Semantic Scholar",
            False,
            30000,
        ),
    ]
    assert reply["followups"] == [
        "Why does additive attention avoid the scaling problem?",
        "What does $d_k$ control?",
    ]
    assert reply["latency_ms"] == 4321
    assert reply["error"] is None and reply["error_code"] is None


async def test_followups_and_the_recheck_question_are_forwarded_unchanged(tmp_path: Path) -> None:
    h = make_harness(tmp_path, runner=FakeRunner(rich_turn))
    session = await _session(h)

    frames = parse_sse((await _chat(h, session.session_id)).text)

    names = [n for n, _ in frames]
    assert names[-4:] == ["knowledge.updated", "followups", "usage", "done"]
    knowledge = frames[names.index("knowledge.updated")][1]
    assert knowledge == {
        "type": "knowledge.updated",
        "added": [{"concept_id": "attention", "name": "Attention"}],
        "removed": [],
        "ask": [{"concept_id": "softmax", "name": "Softmax"}],
    }
    assert frames[names.index("followups")][1] == {
        "type": "followups",
        "message_id": "m_rich",
        "questions": ["Why does additive attention avoid the scaling problem?", "What does $d_k$ control?"],
    }
    done = frames[names.index("answer.done")][1]
    assert done is not None and done["external"][0]["s2_id"] == BAHDANAU.s2_id


async def test_sources_fall_back_to_the_sources_event_when_answer_done_omits_them(tmp_path: Path) -> None:
    h = make_harness(tmp_path, runner=FakeRunner())  # happy_turn: answer.done without sources
    session = await _session(h)

    await _chat(h, session.session_id)

    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    reply = saved.messages[-1]
    assert [s.label for s in reply.sources] == ["S1"]
    assert reply.external == [] and reply.followups == []
    assert reply.latency_ms == 5, "usage.latency_ms from the runner"


async def test_an_error_turn_keeps_its_code_and_the_api_measured_time(tmp_path: Path) -> None:
    async def overloaded(ctx: Any, emit: Any) -> None:
        await emit(ToolStarted(call_id="c1", tool="search_paper", label="Searching the paper"))
        await asyncio.sleep(0.02)
        await emit(ErrorEvent(code="overloaded", message="The model is busy.", retryable=True))

    h = make_harness(tmp_path, runner=FakeRunner(overloaded))
    session = await _session(h)

    await _chat(h, session.session_id)

    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    reply = saved.messages[-1]
    assert reply.error == "The model is busy." and reply.error_code == "overloaded"
    # No usage event on an error: the API's own clock stands in.
    assert reply.latency_ms is not None and reply.latency_ms >= 20


async def test_an_interrupted_answer_is_marked_as_such(tmp_path: Path) -> None:
    async def slow(ctx: Any, emit: Any) -> None:
        await emit(AnswerDelta(text="Attention is "))
        await asyncio.sleep(30)

    h = make_harness(tmp_path, runner=FakeRunner(slow))
    session = await _session(h)

    await drive_sse(
        h.app,
        "/api/chat",
        {"session_id": session.session_id, "message": "How?"},
        disconnect_when=lambda frames: any(name == "answer.delta" for name, _ in frames),
    )

    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    reply = saved.messages[-1]
    assert reply.text == "Attention is "
    assert reply.error == INTERRUPTED_MESSAGE and reply.error_code == INTERRUPTED_CODE == "interrupted"
    assert reply.latency_ms is not None


async def test_a_disconnect_after_the_answer_keeps_it_complete(tmp_path: Path) -> None:
    """Hanging up during the mastery pass loses the usage report, not the answer."""

    async def answer_then_hang(ctx: Any, emit: Any) -> None:
        await emit(SourcesReady(sources=[_source("S1", "sec_3.2_a")]))
        await emit(AnswerDone(message_id="m_done", text="Done [S1].", cited=["S1"], writer="fake"))
        await asyncio.sleep(30)

    h = make_harness(tmp_path, runner=FakeRunner(answer_then_hang))
    session = await _session(h)

    await drive_sse(
        h.app,
        "/api/chat",
        {"session_id": session.session_id, "message": "How?"},
        disconnect_when=lambda frames: any(name == "answer.done" for name, _ in frames),
    )

    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    reply = saved.messages[-1]
    assert reply.text == "Done [S1]." and reply.error is None and reply.error_code is None
    assert reply.latency_ms is not None


async def test_an_empty_turn_is_recorded_as_an_internal_failure(tmp_path: Path) -> None:
    async def nothing(ctx: Any, emit: Any) -> None:
        return None

    h = make_harness(tmp_path, runner=FakeRunner(nothing))
    session = await _session(h)

    await _chat(h, session.session_id)

    saved = await h.res.sessions.get(session.session_id)
    assert saved is not None
    assert saved.messages[-1].error is not None and saved.messages[-1].error_code == "internal"
