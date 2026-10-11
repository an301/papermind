from __future__ import annotations

import itertools
import time
from typing import Any

import pytest
from pydantic import ValidationError

from papermind.agent.dispatch import DispatchContext, Dispatcher, Gathered, format_validation_error
from papermind.agent.registry import build_registry
from papermind.agent.trace import Trace
from papermind.mcp_client.manager import ToolExecutionError
from papermind.schemas.tools import SearchPaperArgs
from tests.unit.fakes import PAPER_ID, USER_ID, EventLog, FakeMCP, compose, fake_settings, tool_use

CTX = DispatchContext(paper_id=PAPER_ID, user_id=USER_ID, default_paper=PAPER_ID, mode="hybrid")


def dispatcher(mcp: FakeMCP, **settings: Any) -> tuple[Dispatcher, EventLog, Trace, Gathered]:
    events, trace, gathered = EventLog(), Trace(turn_id="t_1"), Gathered()
    d = Dispatcher(
        registry=build_registry(),
        mcp=mcp,
        settings=fake_settings(**settings),
        context=CTX,
        emit=events,
        trace=trace,
        gathered=gathered,
    )
    return d, events, trace, gathered


SEARCH = tool_use("search_paper", {"query": "scaled dot-product attention"}, id="toolu_search")
PREREQ = tool_use("check_prerequisites", {"concept": "attention"}, id="toolu_prereq")
LOOKUP = tool_use("lookup_paper", {"query": "BLEU"}, id="toolu_lookup")


# ---------------------------------------------------------------- concurrency


async def test_parallel_calls_overlap() -> None:
    mcp = FakeMCP(delays={"search_paper": 0.3, "check_prerequisites": 0.3, "lookup_paper": 0.3})
    d, *_ = dispatcher(mcp, parallel_tools=True)
    started = time.perf_counter()
    await d.run_step(1, [SEARCH, PREREQ, LOOKUP])
    elapsed = time.perf_counter() - started
    assert elapsed < 0.6
    calls = mcp.calls
    # Every call started before any finished.
    assert max(c.started for c in calls) < min(c.finished for c in calls)


async def test_sequential_calls_do_not_overlap() -> None:
    mcp = FakeMCP(delays={"search_paper": 0.15, "check_prerequisites": 0.15, "lookup_paper": 0.15})
    d, *_ = dispatcher(mcp, parallel_tools=False)
    started = time.perf_counter()
    await d.run_step(1, [SEARCH, PREREQ, LOOKUP])
    assert time.perf_counter() - started >= 0.45
    calls = mcp.calls
    assert all(a.finished <= b.started for a, b in itertools.pairwise(calls))


async def test_results_come_back_in_tool_use_order_whatever_finishes_first() -> None:
    mcp = FakeMCP(delays={"search_paper": 0.2, "check_prerequisites": 0.0, "lookup_paper": 0.1})
    d, events, *_ = dispatcher(mcp)
    out = await d.run_step(1, [SEARCH, PREREQ, LOOKUP])
    assert [r["tool_use_id"] for r in out.tool_results] == ["toolu_search", "toolu_prereq", "toolu_lookup"]
    assert all(r["type"] == "tool_result" and "is_error" not in r for r in out.tool_results)
    finished = [e.call_id for e in events.of("tool.finished")]
    assert finished == ["toolu_prereq", "toolu_lookup", "toolu_search"]


# ---------------------------------------------------------------- validation and retries


async def test_invalid_input_is_an_error_result_the_model_can_fix() -> None:
    mcp = FakeMCP()
    d, events, trace, _ = dispatcher(mcp)
    bad = tool_use("search_paper", {"query": "attention", "k": "five"}, id="toolu_bad")
    out = await d.run_step(1, [bad])

    [result] = out.tool_results
    assert result["is_error"] is True
    assert result["content"].startswith(
        "ValidationError: k - Input should be a valid integer, unable to parse string as an integer (got 'five')"
    )
    assert "call search_paper again" in result["content"]
    assert mcp.calls == []  # nothing reached the server
    [retry] = events.of("tool.retry")
    assert (retry.tool, retry.attempt) == ("search_paper", 1)
    assert trace.tool_calls[0].error_kind == "validation"


async def test_model_cannot_choose_injected_context() -> None:
    mcp = FakeMCP()
    d, *_ = dispatcher(mcp)
    sneaky = tool_use("search_paper", {"query": "attention", "paper_id": "other-paper"})
    [result] = (await d.run_step(1, [sneaky])).tool_results
    assert result["is_error"] is True
    assert "paper_id - Extra inputs are not permitted; it is filled in automatically" in result["content"]
    assert mcp.calls == []


async def test_context_is_injected_into_server_args() -> None:
    mcp = FakeMCP()
    d, *_ = dispatcher(mcp)
    graph_call = tool_use("get_references", {})
    await d.run_step(1, [SEARCH, PREREQ, graph_call])
    by_tool = {c.tool: c.args for c in mcp.calls}
    assert by_tool["search_paper"] == {
        "query": "scaled dot-product attention",
        "k": 5,
        "paper_id": PAPER_ID,
        "mode": "hybrid",
    }
    assert by_tool["check_prerequisites"] == {"concept": "attention", "user_id": USER_ID}
    assert by_tool["get_references"] == {"limit": 10, "default_paper": PAPER_ID}


async def test_consecutive_failures_switch_a_tool_off() -> None:
    mcp = FakeMCP()
    d, events, *_ = dispatcher(mcp, max_tool_retries=2)
    contents = []
    for step in range(1, 5):
        out = await d.run_step(step, [tool_use("search_paper", {"query": "x"})])  # min_length=2
        contents.append(out.tool_results[0]["content"])
    assert "call search_paper again" in contents[0] and "call search_paper again" in contents[1]
    assert "stop calling search_paper and continue without it" in contents[2]
    assert contents[3].startswith("Not run: search_paper failed 3 times")
    assert len(events.of("tool.retry")) == 3


async def test_a_success_resets_the_failure_count() -> None:
    d, *_ = dispatcher(FakeMCP(), max_tool_retries=1)
    bad = {"query": "x"}
    await d.run_step(1, [tool_use("search_paper", bad)])
    await d.run_step(2, [SEARCH])
    [result] = (await d.run_step(3, [tool_use("search_paper", bad)])).tool_results
    assert "call search_paper again" in result["content"]


async def test_unknown_tool_lists_the_valid_ones() -> None:
    d, *_ = dispatcher(FakeMCP())
    [result] = (await d.run_step(1, [tool_use("search_web", {"q": "x"})])).tool_results
    assert result["is_error"] is True
    assert result["content"] == (
        "Unknown tool 'search_web'. Valid tools: check_prerequisites, compose_answer, get_citations, "
        "get_learning_path, get_references, lookup_paper, search_paper."
    )


def test_validation_error_text_lists_every_problem() -> None:
    with pytest.raises(ValidationError) as info:
        SearchPaperArgs.model_validate({"k": 99, "extra": 1})
    text = format_validation_error(info.value)
    assert text.startswith("ValidationError: ")
    assert "query - Field required" in text
    assert "k - Input should be less than or equal to 10 (got 99)" in text
    assert "extra - Extra inputs are not permitted (got 1)" in text


# ---------------------------------------------------------------- server failures


@pytest.mark.parametrize(
    ("failure", "kind", "content"),
    [
        (
            ToolExecutionError(
                "semantic_scholar", "lookup_paper", "Semantic Scholar is down", kind="tool_error"
            ),
            "tool_error",
            "Error: Semantic Scholar is down.",
        ),
        (
            ToolExecutionError("semantic_scholar", "lookup_paper", "timed out after 30s", kind="timeout"),
            "timeout",
            "Error: timed out after 30s.",
        ),
        (RuntimeError("bug"), "internal", "Error: the tool failed unexpectedly."),
    ],
)
async def test_server_failures_are_error_results_not_exceptions(
    failure: BaseException, kind: str, content: str
) -> None:
    mcp = FakeMCP(failures={"lookup_paper": failure})
    d, events, trace, _ = dispatcher(mcp)
    out = await d.run_step(1, [SEARCH, LOOKUP])
    ok, failed = out.tool_results
    assert "is_error" not in ok
    assert failed == {
        "type": "tool_result",
        "tool_use_id": "toolu_lookup",
        "content": content,
        "is_error": True,
    }
    finished = {e.call_id: e for e in events.of("tool.finished")}
    # The trail line is fixed copy (DESIGN.md §5.11); why it failed rides in the detail.
    lookup = finished["toolu_lookup"]
    assert lookup.ok is False and lookup.summary == "Couldn’t reach Semantic Scholar"
    assert lookup.detail == {"error": kind}
    assert trace.tool_calls[-1].error_kind == "execution"


async def test_malformed_server_result_is_an_error_result() -> None:
    async def broken(_args: dict[str, Any]) -> dict[str, Any]:
        return {"unexpected": True}

    mcp = FakeMCP(handlers={("retriever", "search_paper"): broken})
    d, *_ = dispatcher(mcp)
    [result] = (await d.run_step(1, [SEARCH])).tool_results
    assert result["is_error"] is True and "unexpected shape" in result["content"]


# ---------------------------------------------------------------- what the step gathers


async def test_search_results_feed_the_turn_and_the_trace() -> None:
    d, events, trace, gathered = dispatcher(FakeMCP())
    out = await d.run_step(1, [SEARCH])
    [result] = out.tool_results
    body = result["content"]
    assert '"chunk_id":"1706.03762:sec_3.2.1_scaled_dot_product_attention"' in body
    assert '"section":"3.2.1 Scaled Dot-Product Attention"' in body
    assert next(iter(gathered.chunks)) == "1706.03762:sec_3.2.1_scaled_dot_product_attention"
    assert trace.retrieved == [list(gathered.chunks)]
    [started] = events.of("tool.started")
    assert started.label == "Searching the paper for ‘scaled dot-product attention’…"
    assert started.args == {"query": "scaled dot-product attention", "k": 5, "section_filter": None}
    [finished] = events.of("tool.finished")
    assert finished.detail == {
        "passages": 3,
        "chunk_ids": list(gathered.chunks),
        "sections": ["§3.2.1", "§3.2.2", "§5"],
    }
    assert gathered.activity[0].summary == finished.summary


async def test_prerequisites_emit_gaps() -> None:
    d, events, _, gathered = dispatcher(FakeMCP())
    await d.run_step(1, [PREREQ])
    [gaps] = events.of("gaps")
    assert gaps.concept is not None and gaps.concept.concept_id == "attention"
    # The sample graph: attention -> softmax -> exponential function, attention -> dot product -> linear algebra.
    assert [g.concept_id for g in gaps.teaching_order][-1] == "attention"
    assert gaps.altitude == "scaffolded"
    assert gathered.prerequisites is not None


async def test_unmatched_concept_emits_no_gaps() -> None:
    d, events, *_ = dispatcher(FakeMCP())
    await d.run_step(1, [tool_use("check_prerequisites", {"concept": "flux capacitor"})])
    assert events.of("gaps") == []


async def test_compose_answer_is_handled_locally() -> None:
    mcp = FakeMCP()
    d, events, trace, _ = dispatcher(mcp)
    block = compose("Explain the scaling for a beginner.", ["c1"], id="toolu_compose")
    out = await d.run_step(1, [block])
    assert out.compose is not None and out.compose.source_chunk_ids == ["c1"]
    assert out.tool_results == [
        {"type": "tool_result", "tool_use_id": "toolu_compose", "content": "Handed to the writer."}
    ]
    assert mcp.calls == [] and events.of("tool.started") == []
    assert trace.executed_tool_calls == 0


async def test_compose_answer_that_keeps_failing_is_flagged() -> None:
    d, *_ = dispatcher(FakeMCP(), max_tool_retries=1)
    bad = tool_use("compose_answer", {"plan": "short"})  # min_length=10
    first = await d.run_step(1, [bad])
    second = await d.run_step(2, [bad])
    assert not first.compose_exhausted
    assert second.compose_exhausted


async def test_an_expert_is_held_at_the_technical_altitude() -> None:
    from dataclasses import replace

    events, trace, gathered = EventLog(), Trace(turn_id="t_1"), Gathered()
    d = Dispatcher(
        registry=build_registry(),
        mcp=FakeMCP(),
        settings=fake_settings(),
        context=replace(CTX, level="expert"),
        emit=events,
        trace=trace,
        gathered=gathered,
    )
    [result] = (await d.run_step(1, [PREREQ])).tool_results
    # The graph alone says "scaffolded" for this reader (see the test above).
    assert '"altitude":"technical"' in result["content"]
    assert events.of("gaps")[0].altitude == "technical"
    assert gathered.prerequisites is not None and gathered.prerequisites.altitude == "technical"
    # The gaps themselves are still reported.
    assert len(events.of("gaps")[0].gaps) > 2


async def test_each_lookups_best_match_is_kept_for_the_writer() -> None:
    from tests.unit.fakes import BLEU_PAPER

    d, _, _, gathered = dispatcher(FakeMCP())
    await d.run_step(
        1,
        [
            tool_use("lookup_paper", {"query": "BLEU machine translation"}),
            tool_use("lookup_paper", {"query": "nothing matches zzz"}),
            tool_use("get_references", {}),
        ],
    )
    assert gathered.lookups == [BLEU_PAPER]  # one per lookup with a match; references never
    assert list(gathered.papers) == [BLEU_PAPER.s2_id]
