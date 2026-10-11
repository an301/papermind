"""The ReAct loop end to end over the fakes: events, messages, hand-off, limits, errors, trace."""

from __future__ import annotations

import asyncio
import re
import time
from datetime import UTC, datetime
from typing import Any

import pytest

from papermind.agent.loop import (
    MAX_JSON_RETRIES,
    UNFINISHED_ANSWER,
    build_messages,
    previous_answer,
    reader_state,
)
from papermind.agent.mastery import MasteryItem, MasteryJudgement
from papermind.config import get_settings
from papermind.llm.client import ToolInputParseError, llm_error
from papermind.llm.pricing import Usage
from papermind.schemas.session import ChatMessage, Quote
from papermind.schemas.tools import MarkKnownServerArgs
from tests.unit.fakes import (
    DEFAULT_CHUNKS,
    USER_ID,
    EventLog,
    FakeLLM,
    FakeMCP,
    Scripted,
    agent_turn,
    compose,
    fake_settings,
    make_ctx,
    make_runner,
    make_session,
    text,
    thinking,
    tool_use,
)

NOTHING_LEARNED = MasteryJudgement(demonstrated=[], unfamiliar=[])
SDPA = DEFAULT_CHUNKS[0].chunk_id
MHA = DEFAULT_CHUNKS[1].chunk_id


def research_then_compose(*sources: str) -> list[Any]:
    return [
        agent_turn(
            thinking("I should search and check prerequisites."),
            tool_use("search_paper", {"query": "scaled dot-product attention"}, id="toolu_s"),
            tool_use("check_prerequisites", {"concept": "attention"}, id="toolu_p"),
        ),
        agent_turn(
            compose("Explain attention for a beginner, scaffolding softmax first.", sources or [SDPA])
        ),
    ]


async def run_turn(llm: FakeLLM, mcp: FakeMCP | None = None, ctx: Any = None, **settings: Any):
    mcp = mcp or FakeMCP()
    events = EventLog()
    result = await make_runner(llm, mcp, **settings).run_turn(ctx or make_ctx(), events)
    return result, events, mcp


def stream_shape(types: list[str]) -> str:
    """The event stream collapsed to the grammar in papermind/schemas/events.py."""
    return " ".join(types)


GRAMMAR = re.compile(
    r"^turn\.started( (tool\.started|tool\.retry|tool\.finished|gaps))* sources( answer\.delta)+ answer\.done"
    r"( knowledge\.updated)?( followups)? usage done$"
)


# ---------------------------------------------------------------- events


async def test_event_order_follows_the_contract() -> None:
    llm = FakeLLM(
        agent=research_then_compose(),
        writer=["Attention scores keys [S1]. More [S9]."],
        mastery=[NOTHING_LEARNED],
    )
    result, events, _ = await run_turn(llm)
    assert GRAMMAR.match(stream_shape(events.types)), events.types
    [done] = events.of("answer.done")
    assert done.text == "Attention scores keys [S1]. More."
    assert done.cited == ["S1"]
    assert done.writer == "claude:claude-opus-5-5"
    assert result.error is None and result.answer == done.text


async def test_concurrent_tools_start_before_either_finishes() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    mcp = FakeMCP(delays={"search_paper": 0.1, "check_prerequisites": 0.1})
    _, events, _ = await run_turn(llm, mcp)
    tool_events = [e.type for e in events.events if e.type.startswith("tool.")]
    assert tool_events == ["tool.started", "tool.started", "tool.finished", "tool.finished"]


async def test_parallel_turn_is_faster_than_sequential() -> None:
    async def timed(parallel: bool) -> float:
        llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
        mcp = FakeMCP(delays={"search_paper": 0.2, "check_prerequisites": 0.2})
        started = time.perf_counter()
        await run_turn(llm, mcp, parallel_tools=parallel)
        return time.perf_counter() - started

    assert await timed(True) + 0.15 < await timed(False)


# ---------------------------------------------------------------- messages


async def test_first_call_ends_with_the_question_then_reader_state() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    ctx = make_ctx("How does attention work?", known=["softmax", "Dot Product", "softmax"])
    await run_turn(llm, ctx=ctx)
    first = llm.requests("agent")[0]["messages"]
    assert first[-2] == {"role": "user", "content": "How does attention work?"}
    assert first[-1] == {
        "role": "system",
        "content": "Reader state for this turn:\n- Level: beginner\n"
        "- Concepts the reader already knows (2): Dot Product, softmax",
    }


def test_reader_state_is_capped_and_sorted() -> None:
    names = [f"concept {i:03d}" for i in range(100)]
    state = reader_state("expert", list(reversed(names)))
    assert "(100): concept 000, concept 001" in state
    assert state.endswith("concept 079, and 20 more")
    assert reader_state("beginner", []).endswith("none recorded yet")


async def test_assistant_content_is_appended_unchanged_and_results_go_in_one_message() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    await run_turn(llm)
    first_response = research_then_compose()[0].content
    second = llm.requests("agent")[1]["messages"]
    assistant, results = second[-2], second[-1]
    assert assistant["role"] == "assistant"
    # Thinking block and signature included, byte for byte (ids differ per script build).
    assert [b["type"] for b in assistant["content"]] == [b["type"] for b in first_response]
    assert assistant["content"][0] == thinking("I should search and check prerequisites.")
    assert results["role"] == "user"
    assert [r["tool_use_id"] for r in results["content"]] == ["toolu_s", "toolu_p"]
    assert all(r["type"] == "tool_result" for r in results["content"])
    # Everything before the new tail is the previous request's prefix, unchanged.
    first = llm.requests("agent")[0]["messages"]
    assert second[: len(first)] == first


async def test_system_message_placement_is_valid_on_every_call() -> None:
    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("search_paper", {"query": "attention softmax"})),
            agent_turn(tool_use("search_paper", {"query": "multi-head attention"})),
            agent_turn(compose("Explain multi-head attention.", [MHA])),
        ],
        writer=["ok"],
        mastery=[NOTHING_LEARNED],
    )
    history = [
        ChatMessage(message_id="m1", role="user", text="hi", created_at=datetime(2026, 10, 1, tzinfo=UTC)),
        ChatMessage(
            message_id="m2", role="assistant", text="Hello!", created_at=datetime(2026, 10, 1, tzinfo=UTC)
        ),
    ]
    await run_turn(llm, ctx=make_ctx(session=make_session(messages=history)))
    for request in llm.requests("agent"):
        messages = request["messages"]
        assert messages[0]["role"] == "user"
        for i, m in enumerate(messages):
            if m["role"] == "system":
                assert messages[i - 1]["role"] == "user"
                assert i == len(messages) - 1 or messages[i + 1]["role"] == "assistant"


def test_history_is_condensed() -> None:
    def msg(i: int, role: str, text: str, quote: Quote | None = None) -> ChatMessage:
        return ChatMessage(
            message_id=f"m{i}",
            role=role,
            text=text,
            quote=quote,
            created_at=datetime(2026, 10, 1, tzinfo=UTC),
        )  # type: ignore[arg-type]

    messages = []
    for i in range(5):
        messages += [msg(2 * i, "user", f"question {i}"), msg(2 * i + 1, "assistant", f"answer {i} [S1].")]
    messages[-2] = msg(8, "user", "what is this?", Quote(text="We scale by 1/sqrt(d_k).", page=3))
    ctx = make_ctx("next question", session=make_session(messages=messages))
    built = build_messages(ctx, fake_settings(history_turns=2))
    assert [m["role"] for m in built] == ["user", "assistant", "user", "assistant", "user", "system"]
    assert built[0]["content"] == "question 3"
    assert built[1]["content"] == "answer 3."  # citation markers stripped
    assert built[2]["content"].startswith(
        'The reader selected this passage in the paper (page 4):\n"""\nWe scale'
    )
    assert built[2]["content"].endswith("what is this?")
    assert built[4]["content"] == "next question"


def test_unfinished_answers_are_not_replayed_as_answers() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    refused = "The model declined to answer this. Try rephrasing the question."
    session = make_session(
        messages=[
            ChatMessage(message_id="m1", role="user", text="How is X synthesised?", created_at=now),
            # Refused mid-stream: the partial text is not an answer.
            ChatMessage(
                message_id="m2", role="assistant", text="The first step is to", error=refused, created_at=now
            ),
            ChatMessage(message_id="m3", role="user", text="And Y?", created_at=now),
            # Refused before any output: kept as a marker so the unanswered question shows.
            ChatMessage(message_id="m4", role="assistant", text="", error=refused, created_at=now),
        ]
    )
    ctx = make_ctx("go on", session=session)
    built = build_messages(ctx, fake_settings(history_turns=3))
    assert [(m["role"], m["content"]) for m in built[:-1]] == [
        ("user", "How is X synthesised?"),
        ("assistant", UNFINISHED_ANSWER),
        ("user", "And Y?"),
        ("assistant", UNFINISHED_ANSWER),
        ("user", "go on"),
    ]
    assert previous_answer(ctx) is None  # nor is it context for the mastery classifier


async def test_selected_quote_reaches_agent_and_writer() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    quote = Quote(text="We suspect that for large values of d_k, the dot products grow large.", page=4)
    await run_turn(llm, ctx=make_ctx("what does this mean?", quote=quote))
    agent_user = llm.requests("agent")[0]["messages"][-2]["content"]
    assert "We suspect that for large values of d_k" in agent_user
    writer_user = llm.requests("writer")[0]["messages"][0]["content"]
    assert '<selected_passage page="5">' in writer_user


# ---------------------------------------------------------------- the hand-off


async def test_compose_answer_chooses_the_sources() -> None:
    llm = FakeLLM(
        agent=research_then_compose(MHA, "invented:chunk", SDPA), writer=["ok"], mastery=[NOTHING_LEARNED]
    )
    result, events, _ = await run_turn(llm)
    [sources] = events.of("sources")
    assert [(s.label, s.chunk_id) for s in sources.sources] == [("S1", MHA), ("S2", SDPA)]
    assert any("invented:chunk" in e for e in result.trace.errors)
    writer_msg = llm.requests("writer")[0]["messages"][0]["content"]
    assert '<source label="S1" section="§3.2.2 Multi-Head Attention"' in writer_msg
    assert "Explain attention for a beginner, scaffolding softmax first." in writer_msg


async def test_scaffold_falls_back_to_the_teaching_order() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    result, _, _ = await run_turn(llm)
    assert result.altitude == "scaffolded"
    writer_msg = llm.requests("writer")[0]["messages"][0]["content"]
    block = writer_msg.split("<concepts_to_scaffold>\n")[1].split("\n</concepts_to_scaffold>")[0]
    names = [line.split(". ", 1)[1] for line in block.splitlines()]
    # Prerequisites before what needs them; the asked-about concept is not scaffolded.
    assert "Attention" not in names
    assert names.index("Exponential Function") < names.index("Softmax")
    assert names.index("Linear Algebra") < names.index("Dot Product")


async def test_learning_path_reaches_the_writer() -> None:
    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("get_learning_path", {"concept": "transformer"}, id="toolu_lp")),
            agent_turn(compose("Give the reader the path to transformers.", [])),
        ],
        writer=["ok"],
        mastery=[NOTHING_LEARNED],
    )
    await run_turn(llm, ctx=make_ctx("What should I learn before transformers?"))
    writer_msg = llm.requests("writer")[0]["messages"][0]["content"]
    path = writer_msg.split("<learning_path>\n")[1].split("\n</learning_path>")[0]
    assert path.splitlines() == [
        "1. Exponential Function",
        "2. Linear Algebra",
        "3. Dot Product",
        "4. Softmax",
        "5. Attention",
        "6. Transformer",
    ]
    # Concepts on the path are what the reader's message may reveal something about.
    assert "Dot Product" in llm.requests("mastery")[0]["messages"][0]["content"]


async def test_no_learning_path_section_without_the_tool() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    await run_turn(llm)
    assert "<learning_path>" not in llm.requests("writer")[0]["messages"][0]["content"]


async def test_plain_text_end_turn_is_the_plan() -> None:
    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("search_paper", {"query": "label smoothing"})),
            agent_turn(text("Explain label smoothing using the training section.")),
        ],
        writer=["ok"],
        mastery=[NOTHING_LEARNED],
    )
    result, events, _ = await run_turn(llm)
    assert result.plan == "Explain label smoothing using the training section."
    # No compose_answer: the top retrieved chunks stand in.
    assert len(events.of("sources")[0].sources) == 3


async def test_small_talk_hands_off_with_no_sources() -> None:
    llm = FakeLLM(
        agent=[agent_turn(compose("Reply warmly and offer to help with the paper.", []))],
        writer=["You're welcome!"],
        mastery=[NOTHING_LEARNED],
    )
    result, events, mcp = await run_turn(llm, ctx=make_ctx("thanks!"))
    assert events.of("sources")[0].sources == []
    assert mcp.calls == []
    assert result.answer == "You're welcome!" and result.usage.tool_calls == 0
    assert "<sources>\nnone" in llm.requests("writer")[0]["messages"][0]["content"]


async def test_external_papers_reach_the_writer() -> None:
    from tests.unit.fakes import BLEU_PAPER

    llm = FakeLLM(
        agent=[
            agent_turn(
                tool_use("lookup_paper", {"query": "BLEU"}), tool_use("search_paper", {"query": "BLEU score"})
            ),
            agent_turn(compose("Define BLEU briefly.", [], external_papers=[BLEU_PAPER.s2_id, "unknown"])),
        ],
        writer=["BLEU compares n-grams."],
        mastery=[NOTHING_LEARNED],
    )
    await run_turn(llm, ctx=make_ctx("What is BLEU?"))
    writer_msg = llm.requests("writer")[0]["messages"][0]["content"]
    assert '<paper label="P1" year="2002">\nTitle: Bleu: a Method for Automatic Evaluation' in writer_msg
    assert 'label="P2"' not in writer_msg  # the invented id was dropped


async def test_cited_external_papers_come_back_on_answer_done() -> None:
    from tests.unit.fakes import BLEU_PAPER

    llm = FakeLLM(
        agent=[
            agent_turn(
                tool_use("lookup_paper", {"query": "BLEU"}),
                tool_use("search_paper", {"query": "BLEU training"}),
            ),
            agent_turn(
                compose(
                    "Define BLEU, then say how the paper uses it.",
                    [DEFAULT_CHUNKS[2].chunk_id],
                    external_papers=[BLEU_PAPER.s2_id],
                )
            ),
        ],
        writer=[
            "BLEU scores n-gram overlap [P1]. The paper reports BLEU on WMT 2014 [S1]. Also [P2] and [S7]."
        ],
        mastery=[NOTHING_LEARNED],
    )
    result, events, _ = await run_turn(llm, ctx=make_ctx("What is BLEU and why do they use it?"))
    [done] = events.of("answer.done")
    assert done.text == "BLEU scores n-gram overlap [P1]. The paper reports BLEU on WMT 2014 [S1]. Also and."
    assert done.cited == ["S1"]  # passages only; papers are listed in external
    assert [(e.label, e.s2_id, e.year) for e in done.external] == [("P1", BLEU_PAPER.s2_id, 2002)]
    assert result.external == done.external == result.assistant_message.external


async def test_a_lookup_the_agent_did_not_name_still_reaches_the_writer() -> None:
    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("lookup_paper", {"query": "BLEU"})),
            # compose_answer with external_papers left out: the agent never chose.
            agent_turn(
                tool_use(
                    "compose_answer",
                    {"plan": "Define BLEU from the looked-up paper.", "source_chunk_ids": []},
                )
            ),
        ],
        writer=["BLEU measures n-gram overlap [P1]."],
        mastery=[NOTHING_LEARNED],
    )
    result, _, _ = await run_turn(llm, ctx=make_ctx("What is BLEU?"))
    assert 'label="P1"' in llm.requests("writer")[0]["messages"][0]["content"]
    assert [e.label for e in result.external] == ["P1"]


async def test_an_explicitly_empty_paper_list_is_respected() -> None:
    from tests.unit.fakes import BLEU_PAPER

    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("lookup_paper", {"query": "BLEU"})),
            agent_turn(compose("Say the paper alone answers this.", [], external_papers=[])),
        ],
        writer=["Nothing to cite [P1]."],
        mastery=[NOTHING_LEARNED],
    )
    result, _, _ = await run_turn(llm, ctx=make_ctx("What is BLEU?"))
    assert BLEU_PAPER.title not in llm.requests("writer")[0]["messages"][0]["content"]
    assert result.answer == "Nothing to cite." and result.external == []


async def test_answer_done_pins_each_cited_passage_to_a_sentence() -> None:
    llm = FakeLLM(
        agent=research_then_compose(SDPA, MHA),
        writer=[
            "Each query's dot products with the keys are divided by $\\sqrt{d_k}$, then a softmax gives the weights [S1]."
        ],
        mastery=[NOTHING_LEARNED],
    )
    result, events, _ = await run_turn(llm)
    [early] = events.of("sources")
    [done] = events.of("answer.done")
    assert all(s.cited_sentence is None for s in early.sources)  # sent before the answer existed
    assert [s.label for s in done.sources] == [s.label for s in early.sources] == ["S1", "S2"]
    assert done.sources[0].cited_sentence == (
        "We compute the dot products of the query with all keys, divide each by sqrt(d_k), "
        "and apply a softmax function to obtain the weights."
    )
    assert done.sources[1].cited_sentence is None  # not cited
    assert result.sources == done.sources == result.assistant_message.sources


# ---------------------------------------------------------------- retries and limits


async def test_model_fixes_a_bad_call_after_a_validation_error() -> None:
    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("search_paper", {"query": "attention", "k": "five"}, id="toolu_bad")),
            agent_turn(tool_use("search_paper", {"query": "attention", "k": 5}, id="toolu_good")),
            agent_turn(compose("Explain the scaled dot-product.", [SDPA])),
        ],
        writer=["ok"],
        mastery=[NOTHING_LEARNED],
    )
    result, events, mcp = await run_turn(llm)
    retry_feedback = llm.requests("agent")[1]["messages"][-1]["content"][0]
    assert retry_feedback["is_error"] is True and retry_feedback["content"].startswith(
        "ValidationError: k - "
    )
    assert [e.type for e in events.events if e.type.startswith("tool.")] == [
        "tool.retry",
        "tool.started",
        "tool.finished",
    ]
    assert len(mcp.calls_to("search_paper")) == 1
    assert (result.usage.tool_calls, result.usage.tool_retries) == (1, 1)


async def test_out_of_steps_hands_off_with_what_was_gathered() -> None:
    searches = [agent_turn(tool_use("search_paper", {"query": f"attention {i}"})) for i in range(3)]
    llm = FakeLLM(agent=searches, writer=["ok"], mastery=[NOTHING_LEARNED])
    result, events, _ = await run_turn(llm, max_agent_steps=3)
    assert result.trace.steps == 3 and len(llm.requests("agent")) == 3
    assert any("ran out of steps" in e for e in result.trace.errors)
    assert events.of("sources")[0].sources  # fell back to the top retrieved
    assert result.answer == "ok"


async def test_unparseable_tool_json_reissues_the_same_request() -> None:
    llm = FakeLLM(
        agent=[ToolInputParseError("bad json"), *research_then_compose()],
        writer=["ok"],
        mastery=[NOTHING_LEARNED],
    )
    result, _, _ = await run_turn(llm)
    first, retried = llm.requests("agent")[:2]
    assert first["messages"] == retried["messages"]
    assert result.trace.json_retries == 1 and result.error is None


async def test_persistent_json_errors_hand_off_without_research() -> None:
    llm = FakeLLM(
        agent=[ToolInputParseError("bad")] * (MAX_JSON_RETRIES + 1), writer=["ok"], mastery=[NOTHING_LEARNED]
    )
    result, events, _ = await run_turn(llm)
    assert result.answer == "ok" and result.trace.json_retries == MAX_JSON_RETRIES + 1
    assert events.of("sources")[0].sources == []


async def test_max_tokens_does_not_run_a_truncated_call() -> None:
    llm = FakeLLM(
        agent=[agent_turn(tool_use("search_paper", {"query": "atte"}), stop_reason="max_tokens")],
        writer=["ok"],
        mastery=[NOTHING_LEARNED],
    )
    result, _, mcp = await run_turn(llm)
    assert mcp.calls == []
    assert any("max_tokens" in e for e in result.trace.errors)


# ---------------------------------------------------------------- errors


async def test_refusal_becomes_an_error_event() -> None:
    llm = FakeLLM(agent=[agent_turn(text(""), stop_reason="refusal")])
    result, events, _ = await run_turn(llm)
    assert events.types == ["turn.started", "error", "done"]
    [error] = events.of("error")
    assert error.code == "refusal" and error.retryable is False
    assert result.error == error and result.answer == ""
    # The refused call was billed and is in the trace.
    assert len(result.trace.model_calls) == 1


async def test_writer_failure_after_sources_is_an_error_event() -> None:
    llm = FakeLLM(
        agent=research_then_compose(),
        writer=[llm_error("overloaded", retryable=True)],
        mastery=[NOTHING_LEARNED],
    )
    result, events, _ = await run_turn(llm)
    assert events.types[-3:] == ["sources", "error", "done"]
    assert events.of("error")[0].code == "overloaded" and events.of("error")[0].retryable
    assert result.assistant_message.error is not None


async def test_unexpected_bug_is_an_internal_error_not_a_crash() -> None:
    llm = FakeLLM(agent=[lambda _kwargs: RuntimeError("boom")])
    result, events, _ = await run_turn(llm)
    assert events.types == ["turn.started", "error", "done"]
    assert result.error is not None and result.error.code == "internal"
    assert "boom" not in result.error.message


async def test_cancellation_propagates() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    mcp = FakeMCP(delays={"search_paper": 5})
    task = asyncio.create_task(make_runner(llm, mcp).run_turn(make_ctx(), EventLog()))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------- mastery


async def test_knowledge_update_then_followups_are_reported_after_the_answer() -> None:
    mcp = FakeMCP()
    await mcp.graph.mark(
        MarkKnownServerArgs(user_id=USER_ID, concept="softmax", confidence=0.9, source="inferred")
    )
    judgement = MasteryJudgement(
        demonstrated=[],
        unfamiliar=[MasteryItem(concept="softmax", evidence="what's softmax?", strength="strong")],
        followups=["Why does attention need softmax at all?", "What do the weights add up to?"],
    )
    llm = FakeLLM(
        agent=research_then_compose(), writer=["Softmax turns scores into weights [S1]."], mastery=[judgement]
    )
    result, events, _ = await run_turn(llm, mcp, ctx=make_ctx("wait, what's softmax?"))
    assert events.types[-5:] == ["answer.done", "knowledge.updated", "followups", "usage", "done"]
    [update] = events.of("knowledge.updated")
    assert [c.concept_id for c in update.removed] == ["softmax"]
    assert result.knowledge == update
    [followups] = events.of("followups")
    assert (
        followups.message_id == events.of("answer.done")[0].message_id == result.assistant_message.message_id
    )
    assert followups.questions == result.followups == result.assistant_message.followups
    # One call reviews the whole exchange: the reader's words are evidence, the answer is context.
    review = llm.requests("mastery")[0]["messages"][0]["content"]
    assert "<reader_message>\nwait, what's softmax?\n</reader_message>" in review
    assert "(for follow-ups only; never evidence)\nSoftmax turns scores into weights." in review
    # Gaps the answer built on, for the follow-ups; softmax is known, so not among them.
    assert review.split("<not_yet_met>\n")[1].startswith("Linear Algebra, Dot Product\n")
    assert "level: beginner" in review


async def test_doubt_about_what_the_reader_said_they_know_is_asked() -> None:
    mcp = FakeMCP()
    await mcp.graph.mark(
        MarkKnownServerArgs(user_id=USER_ID, concept="softmax", confidence=0.9, source="onboarding")
    )
    judgement = MasteryJudgement(
        demonstrated=[],
        unfamiliar=[MasteryItem(concept="softmax", evidence="what's softmax?", strength="strong")],
    )
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[judgement])
    _, events, _ = await run_turn(llm, mcp, ctx=make_ctx("wait, what's softmax?"))
    [update] = events.of("knowledge.updated")
    assert ([c.concept_id for c in update.ask], update.removed) == (["softmax"], [])
    assert (await mcp.graph.known(USER_ID))["softmax"].confidence == 0.9
    assert "followups" not in events.types  # none suggested, none sent


async def test_a_failed_review_never_fails_the_turn() -> None:
    llm = FakeLLM(
        agent=research_then_compose(), writer=["ok [S1]"], mastery=[llm_error("overloaded", retryable=True)]
    )
    result, events, _ = await run_turn(llm)
    assert events.types[-3:] == ["answer.done", "usage", "done"]
    assert result.error is None and result.followups == [] and result.knowledge is None


async def test_latency_is_the_readers_wait_for_the_answer() -> None:
    slow_review = Scripted(content=[text("{}")], stop_reason="end_turn", parsed=NOTHING_LEARNED, delay_s=0.3)
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[slow_review])
    started = time.perf_counter()
    result, events, _ = await run_turn(llm)
    total_ms = (time.perf_counter() - started) * 1000
    # The review after answer.done is not time the reader spent waiting for the answer.
    assert result.usage.latency_ms < 250 <= total_ms
    assert result.assistant_message.latency_ms == result.usage.latency_ms == events.of("usage")[0].latency_ms


LEARNS_DOT_PRODUCT = MasteryJudgement(
    demonstrated=[MasteryItem(concept="dot product", evidence="q·k sums the products", strength="strong")],
    unfamiliar=[],
)


async def test_a_failed_answer_records_no_knowledge() -> None:
    # The classifier is done long before the writer fails; its verdict must
    # not reach the graph, because the reader is never shown it.
    mcp = FakeMCP()
    llm = FakeLLM(
        agent=research_then_compose(),
        writer=[Scripted(content=[text("partial")], stop_reason="refusal", delay_s=0.1)],
        mastery=[LEARNS_DOT_PRODUCT],
    )
    result, events, _ = await run_turn(llm, mcp, ctx=make_ctx("so q·k sums the products?"))
    assert events.types[-2:] == ["error", "done"] and "knowledge.updated" not in events.types
    assert result.knowledge is None and result.followups == []
    assert await mcp.graph.known(USER_ID) == {}
    assert [c.tool for c in mcp.calls if c.tool in ("mark_known", "forget_concept")] == []
    # The review runs only after a finished answer, so it was never called (or billed).
    assert llm.requests("mastery") == []
    assert result.assistant_message.error_code == "refusal"
    assert result.assistant_message.text == "partial"


async def test_a_disconnect_while_the_writer_streams_records_no_knowledge() -> None:
    mcp = FakeMCP()
    writing = asyncio.Event()

    def slow_writer(_kwargs: dict[str, Any]) -> Scripted:
        writing.set()
        return Scripted(content=[text("never delivered")], stop_reason="end_turn", delay_s=0.3)

    llm = FakeLLM(agent=research_then_compose(), writer=[slow_writer], mastery=[LEARNS_DOT_PRODUCT])
    task = asyncio.create_task(
        make_runner(llm, mcp).run_turn(make_ctx("so q·k sums the products?"), EventLog())
    )
    async with asyncio.timeout(2):
        await writing.wait()
    await asyncio.sleep(0.05)
    assert llm.requests("mastery") == []  # the review waits for a finished answer
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert await mcp.graph.known(USER_ID) == {}
    assert [c.tool for c in mcp.calls if c.tool in ("mark_known", "forget_concept")] == []


async def test_a_disconnect_during_the_writes_lets_them_finish() -> None:
    writing = asyncio.Event()

    async def slow_mark(args: dict[str, Any]) -> dict[str, Any]:
        writing.set()
        await asyncio.sleep(0.2)
        return (await mcp.graph.mark(MarkKnownServerArgs.model_validate(args))).model_dump(mode="json")

    mcp = FakeMCP(handlers={("prereq_graph", "mark_known"): slow_mark})
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[LEARNS_DOT_PRODUCT])
    ctx = make_ctx("so q·k sums the products?")
    task = asyncio.create_task(make_runner(llm, mcp).run_turn(ctx, EventLog()))
    await writing.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.3)
    # All or nothing: the write that was in flight when the reader left completed.
    assert (await mcp.graph.known(USER_ID))["dot-product"].confidence == 0.8


async def test_without_current_knowledge_nothing_is_written_but_followups_still_come() -> None:
    judgement = LEARNS_DOT_PRODUCT.model_copy(
        update={"followups": ["How do the dot products become weights?"]}
    )
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[judgement])
    events = EventLog()

    async def no_source(_user_id: str) -> dict:
        raise ConnectionError("graph down")

    mcp = FakeMCP()
    runner = make_runner(llm, mcp, knowledge_source=no_source)
    result = await runner.run_turn(make_ctx("so q·k sums the products?"), events)
    assert result.error is None and "knowledge.updated" not in events.types
    assert [c.tool for c in mcp.calls if c.tool in ("mark_known", "forget_concept")] == []
    assert events.of("followups")[0].questions == ["How do the dot products become weights?"]


# ---------------------------------------------------------------- the trace and usage


async def test_turn_result_carries_the_trace_for_evals() -> None:
    usage = Usage(
        input_tokens=100, output_tokens=30, cache_read_input_tokens=3000, cache_creation_input_tokens=200
    )
    script = research_then_compose()
    for s in script:
        s.usage = usage
    llm = FakeLLM(agent=script, writer=["ok [S1]"], mastery=[NOTHING_LEARNED])
    result, events, _ = await run_turn(llm)

    assert result.retrieved == [[c.chunk_id for c in DEFAULT_CHUNKS]]
    # The review reads the finished answer, so it always comes last.
    assert [c.purpose for c in result.trace.model_calls] == ["agent", "agent", "writer", "mastery"]
    assert [c.step for c in result.trace.model_calls[:2]] == [1, 2]
    [report] = events.of("usage")
    assert report == result.usage
    assert report.model_calls == 4
    assert report.cache_read_input_tokens == 6000 and report.cache_creation_input_tokens == 400
    assert report.cost_usd == pytest.approx(result.trace.cost_usd)
    assert report.ttft_ms is not None and report.latency_ms >= report.ttft_ms
    assert report.tool_calls == 2
    assert [t.tool for t in result.trace.tool_calls] == [
        "search_paper",
        "check_prerequisites",
        "compose_answer",
    ]
    assert result.sources[0].label == "S1" and result.cited == ["S1"]
    assert (
        result.assistant_message.activity[0].label
        == "Searching the paper for ‘scaled dot-product attention’…"
    )
    assert result.user_message.text == "How does attention work?"
    assert result.trace.to_dict()["turn_id"] == result.turn_id
    # Everything the transcript stores about the answer.
    message = result.assistant_message
    assert message.gaps == result.gaps and message.gaps is not None
    assert [a.tool for a in result.activity] == ["search_paper", "check_prerequisites"]
    assert message.latency_ms == report.latency_ms
    assert message.error is None and message.error_code is None


async def test_caching_off_sends_no_cache_control() -> None:
    llm = FakeLLM(agent=research_then_compose(), writer=["ok"], mastery=[NOTHING_LEARNED])
    await run_turn(llm, prompt_caching=False)
    for call in llm.calls:
        assert "cache_control" not in repr(call.kwargs.get("system"))
        assert call.kwargs.get("cache") in (False, None)


async def test_prewarm_writes_agent_and_writer_prefixes() -> None:
    from tests.unit.fakes import make_paper

    llm = FakeLLM()
    warmed = await make_runner(llm).prewarm(make_paper())
    assert len(warmed) == 2
    agent_warm, writer_warm = llm.requests("prewarm")
    assert agent_warm["tools"] and writer_warm["tools"] is None
    # A pre-warm must match real traffic's effort or it writes a cache entry nothing reads.
    settings = get_settings()
    assert agent_warm["effort"] == settings.agent_effort and writer_warm["effort"] == settings.writer_effort
    assert await make_runner(FakeLLM(), prompt_caching=False).prewarm(make_paper()) == []


async def test_an_expert_gets_no_prerequisite_lesson() -> None:
    llm = FakeLLM(
        agent=[
            agent_turn(
                tool_use("search_paper", {"query": "scaled dot-product attention"}),
                tool_use("check_prerequisites", {"concept": "attention"}),
            ),
            agent_turn(compose("Explain the variance argument precisely.", [SDPA])),
        ],
        writer=["ok"],
        mastery=[NOTHING_LEARNED],
    )
    result, _, _ = await run_turn(llm, ctx=make_ctx("Why scale by sqrt(d_k)?", level="expert"))
    assert result.altitude == "technical"
    writer_msg = llm.requests("writer")[0]["messages"][0]["content"]
    assert "altitude: technical" in writer_msg
    assert "<concepts_to_scaffold>\nnone\n" in writer_msg
    # Nor are an expert's follow-ups steered toward the foundations.
    assert "<not_yet_met>\nnone\n" in llm.requests("mastery")[0]["messages"][0]["content"]
