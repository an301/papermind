from __future__ import annotations

from datetime import UTC, datetime

import pytest

from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from papermind.memory.users import (
    ONBOARDING_CONFIDENCE,
    UnknownAreas,
    create_anonymous_user,
    ensure_user,
    mark_onboarding_areas,
    mark_self_reported,
    onboarding_concepts,
    visible_knowledge,
)
from papermind.schemas.session import KnownConcept
from papermind.schemas.tools import MarkKnownServerArgs


@pytest.fixture
def graph() -> InMemoryGraphStore:
    return InMemoryGraphStore(sample_catalog())


async def test_anonymous_user_ids_are_unique_and_prefixed(graph: InMemoryGraphStore) -> None:
    a = await create_anonymous_user(graph)
    b = await create_anonymous_user(graph)
    assert a.user_id.startswith("u_") and b.user_id.startswith("u_")
    assert a.user_id != b.user_id
    assert await graph.get_user(a.user_id) == a


async def test_ensure_user_recreates_a_lost_profile(graph: InMemoryGraphStore) -> None:
    profile = await ensure_user(graph, "u_lost", level="expert")
    assert profile.user_id == "u_lost" and profile.level == "expert"
    assert (await ensure_user(graph, "u_lost", level="beginner")).level == "expert"


def test_onboarding_takes_foundation_and_core_only() -> None:
    catalog = sample_catalog()
    # dl: attention (core), batch-normalization (core), transformer + self-attention (advanced)
    assert onboarding_concepts(catalog, ["dl"]) == ["attention", "batch-normalization"]
    assert "transformer" not in onboarding_concepts(catalog, ["dl", "ml", "math"])


def test_onboarding_rejects_unknown_areas() -> None:
    with pytest.raises(UnknownAreas) as err:
        onboarding_concepts(sample_catalog(), ["dl", "astrology"])
    assert err.value.area_ids == ["astrology"]
    assert "dl" in err.value.valid


async def test_mark_onboarding_areas_writes_edges(graph: InMemoryGraphStore) -> None:
    edges = await mark_onboarding_areas(graph, "u_1", graph.catalog, ["math"])
    assert [e.concept_id for e in edges] == [
        "derivative",
        "dot-product",
        "exponential-function",
        "linear-algebra",
        "mean-and-variance",
    ]
    assert all(e.source == "onboarding" and e.confidence == ONBOARDING_CONFIDENCE for e in edges)
    assert all("Mathematics" in e.evidence for e in edges)


async def test_onboarding_does_not_overwrite_self_reports(graph: InMemoryGraphStore) -> None:
    await graph.mark(
        MarkKnownServerArgs(user_id="u_1", concept="softmax", confidence=0.95, source="self_reported")
    )
    await graph.mark(
        MarkKnownServerArgs(user_id="u_1", concept="derivative", confidence=0.3, source="inferred")
    )

    await mark_onboarding_areas(graph, "u_1", graph.catalog, ["ml", "math"])

    known = await graph.known("u_1")
    assert known["softmax"].source == "self_reported" and known["softmax"].confidence == 0.95
    # A weak inference is upgraded by the coarse onboarding signal.
    assert known["derivative"].source == "onboarding"


async def test_self_reported_mark_resolves_names(graph: InMemoryGraphStore) -> None:
    result = await mark_self_reported(graph, "u_1", "batchnorm")
    assert result.concept is not None and result.concept.concept_id == "batch-normalization"
    edge = (await graph.known("u_1"))["batch-normalization"]
    assert edge.source == "self_reported" and edge.confidence == 0.9


def _edge(concept_id: str, name: str, confidence: float) -> KnownConcept:
    return KnownConcept(
        concept_id=concept_id,
        name=name,
        confidence=confidence,
        source="inferred",
        updated_at=datetime(2026, 10, 1, tzinfo=UTC),
    )


def test_visible_knowledge_hides_not_yet_unless_asked() -> None:
    edges = [_edge("b", "beta", 0.59), _edge("a", "Alpha", 0.6), _edge("c", "Gamma", 1.0)]
    assert [e.concept_id for e in visible_knowledge(edges, threshold=0.6)] == ["a", "c"]
    assert [e.concept_id for e in visible_knowledge(edges, threshold=0.6, include_not_yet=True)] == [
        "a",
        "b",
        "c",
    ]
