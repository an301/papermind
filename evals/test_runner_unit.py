"""The agent harness end to end with a scripted model and in-process MCP servers. No API key, no money.

Exercises the plumbing the agent suite depends on: one turn through the real
``AgentRunner``, the retrieved ids per search, the three recall variants,
usage capture, the judges wired to the writer's actual passages, and the
row dict the report reads.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from evals.harness import judges as J
from evals.harness import report
from evals.harness.dataset import BenchmarkRow, answer_text_hash
from evals.harness.runner import EVAL_READERS, AgentHarness, latency_settings
from tests.unit.fakes import FakeLLM, FakeMCP, agent_turn, compose, fake_settings, tool_use

GOLD = "1706.03762:sec_3.2.1_scaled_dot_product_attention"
ANSWER = "Scores are divided by $\\sqrt{d_k}$ before the softmax [S1].\n\nThink of it as turning down a dial."


def _row(**kw: Any) -> BenchmarkRow:
    base = {
        "id": "1706.03762-g99",
        "paper_id": "1706.03762",
        "question": "Why divide by sqrt(d_k)?",
        "target_level": "expert",
        "gold_chunk": GOLD,
        "reference_answer": "The dot products are divided by sqrt(d_k) so the softmax does not saturate.",
        "origin": "generated",
        "query_type": "conceptual",
    }
    return BenchmarkRow.model_validate({**base, **kw})


def _judge(kwargs: dict[str, Any]) -> BaseModel:
    """One scripted judge response, chosen by schema (the judges run concurrently, in any order)."""
    name = kwargs["schema"].__name__
    if name == "_Claims":
        return J._Claims(
            claims=[J._Claim(claim="Scores are divided by sqrt(d_k).", marked_outside_paper=False)]
        )
    if name == "_ClaimChecks":
        assert "divide each by" in kwargs["messages"][0]["content"]  # the real chunk text from paper.json
        return J._ClaimChecks(verdicts=[J._ClaimCheck(claim_id=0, reason="stated", supported="yes")])
    if name == "_LevelJudgement":
        return J._LevelJudgement(evidence="notation used freely", level="expert")
    if name == "_CitationChecks":
        return J._CitationChecks(verdicts=[J._CitationCheck(item_id=0, reason="stated", supported="yes")])
    if name == "_ParagraphChecks":
        return J._ParagraphChecks(
            verdicts=[
                J._ParagraphCheck(paragraph_id=0, reason="method", paper_derived="yes"),
                J._ParagraphCheck(paragraph_id=1, reason="analogy", paper_derived="no"),
            ]
        )
    raise AssertionError(name)


async def test_one_row_through_the_real_runner_with_fakes() -> None:
    llm = FakeLLM(
        agent=[
            agent_turn(tool_use("search_paper", {"query": "multi-head attention subspaces"})),
            agent_turn(tool_use("search_paper", {"query": "scaled dot-product attention softmax"})),
            agent_turn(compose("Explain the scaling.", [GOLD])),
        ],
        writer=[ANSWER],
        mastery=[_judge] * 5,
    )
    mcp = FakeMCP()
    settings = fake_settings()
    async with AgentHarness(settings, llm=llm, mcp=mcp, concurrency=2, judge=True) as harness:
        out = await harness.run_row(_row())

    # Eval readers were seeded through mark_known, and this row ran as the expert reader.
    marked = {c.args["user_id"] for c in mcp.calls_to("mark_known")}
    assert marked == {EVAL_READERS["intermediate"][0], EVAL_READERS["expert"][0]}
    assert harness.readers["expert"].known and not harness.readers["beginner"].known

    assert out.ok, out.error
    assert out.answer == ANSWER and out.answer_hash == answer_text_hash(ANSWER)
    assert len(out.searches) == 2
    # The first search (multi-head) ranks the gold chunk below the other two,
    # but its top 5 still holds all three fake chunks.
    assert out.recall_first5 == 1.0 and out.recall_union == 1.0 and out.recall_writer == 1.0
    assert out.writer_chunks == [GOLD] and out.sources[0]["label"] == "S1"
    assert out.usage["model_calls"] == 4  # three agent steps + the writer; judges are billed separately
    assert out.usage["cost_usd"] > 0 and out.latency_ms is not None and out.answer_ms is not None
    assert out.f1 is not None and 0 < out.f1 < 1

    assert out.faithfulness == 1.0 and out.claims_total == 1
    assert out.calibration_label == "expert" and out.calibration_correct is True
    assert (out.citations_supported, out.citations_total) == (1, 1)
    assert (out.paragraphs_paper_derived, out.paragraphs_uncited) == (1, 0)
    assert out.judge_cost_usd > 0 and not out.judge_errors

    summary = report.summarize_agent([out.to_dict()])
    assert summary["by_level"]["expert"]["recall_writer"] == 1.0
    assert summary["gates"]["citation_precision"]["pass"] is True


async def test_a_turn_that_never_searched_is_a_recall_miss_not_missing() -> None:
    llm = FakeLLM(
        agent=[agent_turn(compose("Say the paper does not cover it.", []))], writer=["Not covered."]
    )
    async with AgentHarness(fake_settings(), llm=llm, mcp=FakeMCP(), judge=False) as harness:
        out = await harness.run_row(_row(id="1706.03762-g98"))
    assert out.ok and out.searches == []
    assert (out.recall_first5, out.recall_union, out.recall_writer) == (0.0, 0.0, 0.0)
    assert out.judgement is None


def test_latency_arms_differ_only_in_the_two_switches() -> None:
    base = fake_settings()
    a, b = latency_settings(base, "baseline"), latency_settings(base, "optimized")
    assert (a.parallel_tools, a.prompt_caching) == (False, False)
    assert (b.parallel_tools, b.prompt_caching) == (True, True)
    differing = {k for k in type(base).model_fields if getattr(a, k) != getattr(b, k)}
    assert differing == {"parallel_tools", "prompt_caching"}
