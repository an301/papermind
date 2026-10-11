import pytest

from papermind.graph import service
from papermind.graph.store import InMemoryGraphStore
from papermind.graph.testing import sample_catalog
from papermind.schemas.tools import MarkKnownServerArgs

THRESHOLD = 0.6


@pytest.fixture
def store() -> InMemoryGraphStore:
    return InMemoryGraphStore(sample_catalog())


async def know(store, user_id, *concepts, confidence=0.9):
    for concept in concepts:
        await store.mark(
            MarkKnownServerArgs(user_id=user_id, concept=concept, confidence=confidence, source="onboarding")
        )


async def test_check_prerequisites_worked_example(store):
    await know(store, "u1", "dot-product", "exponential-function")
    result = await service.check_prerequisites(store, "u1", "Transformers", known_threshold=THRESHOLD)

    assert result.query == "Transformers"
    assert result.matched.concept_id == "transformer"
    assert result.match_score == 1.0
    assert [(g.concept_id, g.depth) for g in result.gaps] == [
        ("transformer", 0),
        ("attention", 1),
        ("softmax", 2),
    ]
    assert result.gaps[2].name == "Softmax" and result.gaps[2].description
    assert [s.concept_id for s in result.known_stops] == ["dot-product", "exponential-function"]
    assert result.altitude == "scaffolded"


async def test_low_confidence_knowledge_does_not_count(store):
    await know(store, "u1", "dot-product", "exponential-function", confidence=THRESHOLD - 0.01)
    result = await service.check_prerequisites(store, "u1", "transformer", known_threshold=THRESHOLD)
    assert len(result.gaps) == 6
    assert result.known_stops == []

    await know(store, "u1", "dot-product", "exponential-function", confidence=THRESHOLD)
    result = await service.check_prerequisites(store, "u1", "transformer", known_threshold=THRESHOLD)
    assert len(result.gaps) == 3


async def test_known_target_is_technical_with_no_gaps(store):
    await know(store, "u1", "attention")
    result = await service.check_prerequisites(store, "u1", "attention mechanism", known_threshold=THRESHOLD)
    assert result.gaps == []
    assert [s.concept_id for s in result.known_stops] == ["attention"]
    assert result.altitude == "technical"


async def test_unresolved_concept_returns_suggestions_and_technical(store):
    result = await service.check_prerequisites(store, "u1", "attention head", known_threshold=THRESHOLD)
    assert result.matched is None
    assert result.gaps == [] and result.known_stops == []
    assert result.altitude == "technical"
    assert "attention" in [s.concept_id for s in result.suggestions]
    assert 0 < result.match_score < 0.9


async def test_learning_path_puts_prerequisites_first_and_target_last(store):
    await know(store, "u1", "dot-product", "exponential-function")
    path = await service.learning_path(store, "u1", "transformer", known_threshold=THRESHOLD)
    assert [s.concept_id for s in path.steps] == ["softmax", "attention", "transformer"]
    assert [s.depth for s in path.steps] == [2, 1, 0]


async def test_learning_path_from_scratch_respects_every_edge(store):
    path = await service.learning_path(store, "nobody", "batchnorm", known_threshold=THRESHOLD)
    order = [s.concept_id for s in path.steps]
    assert order[-1] == "batch-normalization"
    assert order.index("derivative") < order.index("gradient-descent")
    assert set(order) == {"batch-normalization", "mean-and-variance", "gradient-descent", "derivative"}


async def test_learning_path_for_unknown_concept_is_empty(store):
    path = await service.learning_path(store, "u1", "string theory", known_threshold=THRESHOLD)
    assert path.matched is None and path.steps == []
