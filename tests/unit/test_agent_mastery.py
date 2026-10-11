from __future__ import annotations

from datetime import UTC, datetime

import pytest
from anthropic.lib._parse._transform import transform_schema

from papermind.agent.cache_policy import load_prompt
from papermind.agent.mastery import (
    Decision,
    MasteryItem,
    MasteryJudgement,
    MasteryPass,
    clean_followups,
    decide,
    own_words_only,
    quoted_from,
    render_classifier_message,
)
from papermind.mcp_client.manager import ToolExecutionError
from papermind.schemas.session import KnownConcept
from papermind.schemas.tools import MarkKnownServerArgs
from tests.unit.fakes import USER_ID, FakeLLM, FakeMCP, fake_settings

# ---------------------------------------------------------------- the policy as arithmetic


@pytest.mark.parametrize(
    ("current", "kind", "strength", "expected"),
    [
        # No edge counts as 0.3.
        (None, "demonstrated", "weak", Decision("mark", 0.5)),
        (None, "demonstrated", "strong", Decision("mark", 0.8)),
        # Inference alone never reaches certainty.
        (0.7, "demonstrated", "strong", Decision("mark", 0.9)),
        (0.85, "demonstrated", "weak", Decision("mark", 0.9)),
        # Positive evidence never lowers an edge (a self-reported 1.0 stays 1.0).
        (1.0, "demonstrated", "strong", Decision("none")),
        (0.9, "demonstrated", "weak", Decision("none")),
        # Negative evidence weighs more: a question is ground truth.
        (0.9, "unfamiliar", "weak", Decision("mark", 0.5)),
        (0.3, "unfamiliar", "weak", Decision("forget")),
        (1.0, "unfamiliar", "strong", Decision("forget")),
        (None, "unfamiliar", "strong", Decision("none")),
        (None, "unfamiliar", "weak", Decision("none")),
    ],
)
def test_decide(current, kind, strength, expected) -> None:
    assert decide(current, kind, strength) == expected


@pytest.mark.parametrize("strength", ["weak", "strong"])
def test_what_the_reader_said_they_know_is_asked_about_never_lowered(strength) -> None:
    assert decide(0.9, "unfamiliar", strength, reader_owned=True) == Decision("ask")
    # Nor is it rewritten as an inference, which would strip that protection.
    assert decide(0.7, "demonstrated", strength, reader_owned=True) == Decision("none")


def test_weak_agreement_alone_does_not_make_a_concept_known() -> None:
    # 0.3 + 0.2 = 0.5, below the 0.6 threshold: "ok that makes sense" is a guess.
    decision = decide(None, "demonstrated", "weak")
    assert decision.confidence is not None and decision.confidence < fake_settings().known_threshold


def test_prompt_has_the_rules() -> None:
    prompt = load_prompt("mastery")
    assert "Never treat something the assistant explained as something the reader knows" in prompt
    assert '"ok that makes sense"' in prompt
    assert "Return `followups`: zero, one or two questions." in prompt
    assert "Never generic" in prompt


def test_classifier_message_marks_the_previous_answer_as_context_only() -> None:
    msg = render_classifier_message(
        "what's softmax?", "Attention uses softmax [S1].", ["Softmax", "Softmax", ""]
    )
    assert "<concepts_in_play>\nSoftmax\n</concepts_in_play>" in msg
    assert "(context only; never evidence)\nAttention uses softmax." in msg
    assert msg.endswith("<reader_message>\nwhat's softmax?\n</reader_message>")


def test_review_message_puts_the_exchange_in_order() -> None:
    msg = render_classifier_message(
        "why the square root?",
        "Attention scales the scores [S1].",
        ["Softmax"],
        answer="Dividing by $\\sqrt{d_k}$ keeps the variance at 1 [S1][P1].",
        not_yet_met=["Dot Product", "Softmax", ""],
        level="beginner",
        quote="We scale the dot products by 1/sqrt(d_k).",
    )
    tags = [
        "<reader>",
        "<concepts_in_play>",
        "<previous_answer>",
        "<selected_passage>",
        "<reader_message>",
        "<answer>",
        "<not_yet_met>",
    ]
    positions = [msg.index(tag) for tag in tags]
    assert positions == sorted(positions)
    assert "level: beginner" in msg
    assert "(the paper's words, not the reader's)\nWe scale the dot products" in msg
    # The answer is context for the follow-ups only, without its markers.
    assert "(for follow-ups only; never evidence)\nDividing by $\\sqrt{d_k}$ keeps the variance at 1." in msg
    assert "<not_yet_met>\nDot Product, Softmax\n</not_yet_met>" in msg


def test_the_model_must_always_decide_on_followups() -> None:
    # Python callers may omit followups, the API schema may not.
    assert MasteryJudgement(demonstrated=[], unfamiliar=[]).followups == []
    assert transform_schema(MasteryJudgement)["required"] == ["demonstrated", "unfamiliar", "followups"]


# ---------------------------------------------------------------- guards on the model's output


def test_evidence_must_be_quoted_from_the_reader() -> None:
    message = "Wait -- what's a SOFTMAX? I get that q·k sums the products."
    assert quoted_from("what's a softmax", message)
    assert quoted_from("“q·k sums the products.”", message)
    assert not quoted_from("softmax normalises the scores", message)
    assert not quoted_from("...", message)
    judgement = MasteryJudgement(
        demonstrated=[
            MasteryItem(concept="dot product", evidence="q·k sums the products", strength="strong"),
            # Copied from the answer, not the reader: the classic mistake the guard exists for.
            MasteryItem(concept="softmax", evidence="softmax turns scores into weights", strength="strong"),
        ],
        unfamiliar=[MasteryItem(concept="softmax", evidence="what's a SOFTMAX?", strength="strong")],
        followups=["kept as is"],
    )
    kept = own_words_only(judgement, message)
    assert [i.concept for i in kept.demonstrated] == ["dot product"]
    assert [i.concept for i in kept.unfamiliar] == ["softmax"]
    assert kept.followups == ["kept as is"]


def test_followups_are_cleaned() -> None:
    raw = [
        "  - Why divide by $\\sqrt{d_k}$?  ",
        "",
        "How does attention work?",  # the question just asked
        "why divide by $\\sqrt{d_k}$",  # a repeat
        "x" * 200,  # not a short question
        "What does softmax do to the scores?",
        "A third question?",
    ]
    assert clean_followups(raw, "How does attention work?") == [
        "Why divide by $\\sqrt{d_k}$?",
        "What does softmax do to the scores?",
    ]


# ---------------------------------------------------------------- the pass


def item(concept: str, strength: str = "strong", evidence: str | None = None) -> MasteryItem:
    evidence = evidence or f"about {concept}"
    return MasteryItem(concept=concept, evidence=evidence, strength=strength)  # type: ignore[arg-type]


async def know(mcp: FakeMCP, concept: str, confidence: float, source: str = "inferred") -> None:
    await mcp.graph.mark(
        MarkKnownServerArgs(user_id=USER_ID, concept=concept, confidence=confidence, source=source)  # type: ignore[arg-type]
    )


async def run(mcp: FakeMCP, judgement: MasteryJudgement | BaseException, message: str | None = None):
    if message is None:
        # Every item's evidence is a quote of the message, as the pass requires.
        items = [] if isinstance(judgement, BaseException) else judgement.demonstrated + judgement.unfamiliar
        message = " ".join(i.evidence for i in items) or "ok"
    llm = FakeLLM(mastery=[judgement])
    mastery = MasteryPass(llm=llm, mcp=mcp, settings=fake_settings())
    knowledge = await mcp.graph.known(USER_ID)
    update = await mastery.run(user_id=USER_ID, message=message, previous_answer=None, knowledge=knowledge)
    return update, llm


async def test_explicit_question_forgets_a_known_concept() -> None:
    mcp = FakeMCP()
    await know(mcp, "softmax", 0.9)
    update, llm = await run(
        mcp, MasteryJudgement(demonstrated=[], unfamiliar=[item("softmax", evidence="what's softmax?")])
    )
    assert [c.concept_id for c in update.removed] == ["softmax"] and update.added == []
    assert "softmax" not in await mcp.graph.known(USER_ID)
    [req] = llm.requests("mastery")
    assert req["effort"] == fake_settings().classifier_effort
    assert req["schema"] is MasteryJudgement


async def test_strong_demonstration_of_a_new_concept_crosses_to_known() -> None:
    mcp = FakeMCP()
    update, _ = await run(
        mcp,
        MasteryJudgement(
            demonstrated=[item("Dot Product", evidence="so q·k sums the elementwise products")], unfamiliar=[]
        ),
    )
    assert [c.concept_id for c in update.added] == ["dot-product"]
    edge = (await mcp.graph.known(USER_ID))["dot-product"]
    assert (edge.confidence, edge.source, edge.evidence) == (
        0.8,
        "inferred",
        "so q·k sums the elementwise products",
    )


async def test_weak_signals_move_confidence_without_a_crossing() -> None:
    mcp = FakeMCP()
    update, _ = await run(mcp, MasteryJudgement(demonstrated=[item("softmax", "weak")], unfamiliar=[]))
    assert update.added == [] and update.removed == []
    assert (await mcp.graph.known(USER_ID))["softmax"].confidence == 0.5


async def test_aliases_resolve_to_the_existing_edge() -> None:
    mcp = FakeMCP()
    await know(mcp, "dot-product", 1.0, source="self_reported")
    # "inner product" is an alias: it must not be scored from the no-edge baseline.
    update, _ = await run(mcp, MasteryJudgement(demonstrated=[item("inner product")], unfamiliar=[]))
    assert update.added == []
    edge = (await mcp.graph.known(USER_ID))["dot-product"]
    assert (edge.confidence, edge.source) == (1.0, "self_reported")


async def test_negative_evidence_wins_when_a_message_shows_both() -> None:
    mcp = FakeMCP()
    await know(mcp, "softmax", 0.9)
    update, _ = await run(
        mcp, MasteryJudgement(demonstrated=[item("softmax")], unfamiliar=[item("softmax", "strong")])
    )
    assert [c.concept_id for c in update.removed] == ["softmax"]


async def test_two_names_for_one_concept_make_one_decision() -> None:
    # "softmax function" resolves to softmax through the graph. Decided apart,
    # the two verdicts raced a mark_known against a forget_concept.
    mcp = FakeMCP()
    await know(mcp, "softmax", 0.5, source="inferred")
    update, _ = await run(
        mcp,
        MasteryJudgement(
            demonstrated=[item("softmax", evidence="softmax normalises the scores")],
            unfamiliar=[item("softmax function", evidence="what is the softmax function?")],
        ),
    )
    writes = [c.tool for c in mcp.calls if c.tool in ("mark_known", "forget_concept")]
    assert writes == ["forget_concept"]
    assert update.added == [] and update.removed == []  # 0.5 was below the threshold already
    assert "softmax" not in await mcp.graph.known(USER_ID)


async def test_the_strongest_negative_wins_for_one_concept() -> None:
    mcp = FakeMCP()
    await know(mcp, "softmax", 0.9)
    update, _ = await run(
        mcp,
        MasteryJudgement(
            demonstrated=[],
            unfamiliar=[item("softmax", "weak"), item("softmax function", "strong")],
        ),
    )
    assert [c.tool for c in mcp.calls if c.tool in ("mark_known", "forget_concept")] == ["forget_concept"]
    assert [c.concept_id for c in update.removed] == ["softmax"]


async def test_a_failed_lookup_skips_that_name_only() -> None:
    mcp = FakeMCP(
        failures={
            "check_prerequisites": ToolExecutionError(
                "prereq_graph", "check_prerequisites", "down", kind="timeout"
            )
        }
    )
    await know(mcp, "softmax", 0.9)
    # "softmax" is an existing edge and needs no lookup; "dot product" does.
    update, _ = await run(
        mcp, MasteryJudgement(demonstrated=[item("dot product")], unfamiliar=[item("softmax")])
    )
    assert [c.concept_id for c in update.removed] == ["softmax"] and update.added == []


async def test_unknown_concepts_are_ignored() -> None:
    mcp = FakeMCP()
    update, _ = await run(mcp, MasteryJudgement(demonstrated=[item("quantum chromodynamics")], unfamiliar=[]))
    assert update.added == [] and await mcp.graph.known(USER_ID) == {}


async def test_nothing_to_learn_makes_no_writes() -> None:
    mcp = FakeMCP()
    update, _ = await run(mcp, MasteryJudgement(demonstrated=[], unfamiliar=[]))
    assert update.added == [] and update.removed == []
    assert mcp.calls == []


async def test_classifier_failure_never_fails_the_turn() -> None:
    from papermind.llm.client import llm_error

    update, _ = await run(FakeMCP(), llm_error("overloaded", retryable=True))
    assert update.added == [] and update.removed == []


async def test_a_failed_write_skips_that_concept_only() -> None:
    mcp = FakeMCP(
        failures={
            "forget_concept": ToolExecutionError("prereq_graph", "forget_concept", "down", kind="timeout")
        }
    )
    await know(mcp, "softmax", 0.9)
    update, _ = await run(
        mcp, MasteryJudgement(demonstrated=[item("dot product")], unfamiliar=[item("softmax")])
    )
    assert [c.concept_id for c in update.added] == ["dot-product"]
    assert update.removed == []


async def test_knowledge_keyed_by_name_finds_the_edge_without_a_lookup() -> None:
    mcp = FakeMCP()
    llm = FakeLLM(
        mastery=[MasteryJudgement(demonstrated=[], unfamiliar=[item("Softmax", evidence="what's softmax?")])]
    )
    knowledge = {
        "softmax": KnownConcept(
            concept_id="softmax",
            name="Softmax",
            confidence=0.7,
            source="inferred",
            updated_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    }
    await know(mcp, "softmax", 0.7, source="inferred")
    mastery = MasteryPass(llm=llm, mcp=mcp, settings=fake_settings())
    update = await mastery.run(
        user_id=USER_ID, message="what's softmax?", previous_answer=None, knowledge=knowledge
    )
    assert [c.concept_id for c in update.removed] == ["softmax"]
    assert [c.tool for c in mcp.calls] == ["forget_concept"]


# ---------------------------------------------------------------- what the reader told us is theirs


@pytest.mark.parametrize(("source", "strength"), [("self_reported", "strong"), ("onboarding", "weak")])
async def test_doubt_about_a_reader_owned_concept_is_asked_not_written(source, strength) -> None:
    mcp = FakeMCP()
    await know(mcp, "softmax", 0.9, source=source)
    update, _ = await run(
        mcp,
        MasteryJudgement(demonstrated=[], unfamiliar=[item("softmax", strength, "what's softmax again?")]),
    )
    assert [c.concept_id for c in update.ask] == ["softmax"]
    assert update.removed == [] and update.added == []
    assert [c.tool for c in mcp.calls if c.tool in ("mark_known", "forget_concept")] == []
    edge = (await mcp.graph.known(USER_ID))["softmax"]
    assert (edge.confidence, edge.source) == (0.9, source)


async def test_a_reader_owned_concept_below_the_line_is_not_asked_about() -> None:
    mcp = FakeMCP()
    await know(mcp, "softmax", 0.4, source="self_reported")
    update, _ = await run(mcp, MasteryJudgement(demonstrated=[], unfamiliar=[item("softmax")]))
    assert update.ask == [] and mcp.calls == []


async def test_positive_evidence_never_rewrites_a_reader_owned_edge() -> None:
    mcp = FakeMCP()
    await know(mcp, "softmax", 0.7, source="onboarding")
    update, _ = await run(mcp, MasteryJudgement(demonstrated=[item("softmax")], unfamiliar=[]))
    assert update == update.model_copy(update={"added": [], "removed": [], "ask": []})
    assert (await mcp.graph.known(USER_ID))["softmax"].source == "onboarding"


# ---------------------------------------------------------------- the review call


async def test_classify_sends_the_exchange_and_returns_cleaned_output() -> None:
    judgement = MasteryJudgement(
        demonstrated=[item("dot product", evidence="the answer said dot products")],
        unfamiliar=[item("softmax", evidence="what is softmax")],
        followups=["What does softmax do to the scores?", "what is softmax", "Why scale?", "One more?"],
    )
    llm = FakeLLM(mastery=[judgement])
    mastery = MasteryPass(llm=llm, mcp=FakeMCP(), settings=fake_settings())
    reviewed = await mastery.classify(
        message="what is softmax",
        previous_answer=None,
        answer="Softmax turns scores into weights [S1]. The answer said dot products.",
        not_yet_met=["Softmax"],
        level="beginner",
    )
    assert reviewed is not None
    assert reviewed.demonstrated == [] and [i.concept for i in reviewed.unfamiliar] == ["softmax"]
    assert reviewed.followups == ["What does softmax do to the scores?", "Why scale?"]
    [req] = llm.requests("mastery")
    assert "<answer>" in req["messages"][0]["content"] and req["schema"] is MasteryJudgement


async def test_a_failed_review_returns_none() -> None:
    from papermind.llm.client import llm_error

    llm = FakeLLM(mastery=[llm_error("rate_limited", retryable=True)])
    mastery = MasteryPass(llm=llm, mcp=FakeMCP(), settings=fake_settings())
    assert await mastery.classify(message="hi", previous_answer=None, answer="Hello.") is None
