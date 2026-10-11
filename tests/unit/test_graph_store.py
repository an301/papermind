from datetime import UTC, datetime
from pathlib import Path

import pytest
from neo4j.exceptions import AuthError, ServiceUnavailable

from papermind.config import Settings
from papermind.graph import store as store_module
from papermind.graph.seed import CatalogError
from papermind.graph.store import GraphStore, InMemoryGraphStore, Neo4jGraphStore, get_graph_store
from papermind.graph.testing import sample_catalog, write_sample_files
from papermind.schemas.session import UserProfile
from papermind.schemas.tools import MarkKnownServerArgs


@pytest.fixture
def store() -> InMemoryGraphStore:
    return InMemoryGraphStore(sample_catalog())


def mark(
    user_id: str, concept: str, confidence: float = 0.9, source: str = "self_reported", evidence: str = ""
):
    return MarkKnownServerArgs(
        user_id=user_id, concept=concept, confidence=confidence, source=source, evidence=evidence
    )


def test_implements_the_protocol(store):
    assert isinstance(store, GraphStore)


async def test_resolve_by_id_name_alias_and_typo(store):
    for query in ("batch-normalization", "Batch Normalization", "batchnorm", "batch normalisation"):
        ref, score, suggestions = await store.resolve(query)
        assert ref.concept_id == "batch-normalization" and score > 0.9 and suggestions == []
    ref, score, suggestions = await store.resolve("attention head")
    assert ref is None
    assert "attention" in [s.concept_id for s in suggestions]


async def test_subgraph_is_the_transitive_closure(store):
    assert await store.subgraph("transformer") == {
        "transformer": ["attention"],
        "attention": ["softmax", "dot-product"],
        "softmax": ["exponential-function"],
        "dot-product": ["linear-algebra"],
        "exponential-function": [],
        "linear-algebra": [],
    }
    assert await store.subgraph("linear-algebra") == {"linear-algebra": []}
    assert await store.subgraph("nope") == {}


async def test_mark_add_update_and_not_found(store):
    added = await store.mark(mark("u1", "Dot Product", 0.8, "inferred", "explained it back"))
    assert (added.status, added.concept.concept_id, added.confidence) == ("added", "dot-product", 0.8)

    updated = await store.mark(mark("u1", "dot-product", 0.4, "self_reported"))
    assert (updated.status, updated.confidence) == ("updated", 0.4)

    missing = await store.mark(mark("u1", "string theory"))
    assert (missing.status, missing.concept) == ("not_found", None)

    known = await store.known("u1")
    assert list(known) == ["dot-product"]
    edge = known["dot-product"]
    assert (edge.confidence, edge.source, edge.evidence, edge.area) == (0.4, "self_reported", "", "math")
    assert edge.updated_at.tzinfo is not None


async def test_known_is_a_copy(store):
    await store.mark(mark("u1", "softmax"))
    (await store.known("u1"))["softmax"].confidence = 0.0
    assert (await store.known("u1"))["softmax"].confidence == 0.9


async def test_forget(store):
    await store.mark(mark("u1", "softmax", 0.7))
    removed = await store.forget("u1", "Softmax")
    assert (removed.status, removed.concept.concept_id, removed.confidence) == ("removed", "softmax", 0.7)
    again = await store.forget("u1", "softmax")
    assert (again.status, again.concept.concept_id) == ("not_found", "softmax")
    assert (await store.forget("u1", "string theory")).concept is None
    assert await store.known("u1") == {}


async def test_users_are_separate(store):
    await store.mark(mark("u1", "softmax"))
    assert await store.known("u2") == {}


async def test_mark_creates_a_default_user_and_upsert_keeps_created_at(store):
    await store.mark(mark("u1", "softmax"))
    created = (await store.get_user("u1")).created_at
    profile = UserProfile(
        user_id="u1",
        display_name="Ada",
        level="expert",
        onboarded=True,
        created_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    stored = await store.upsert_user(profile)
    assert (stored.display_name, stored.level, stored.onboarded) == ("Ada", "expert", True)
    assert stored.created_at == created
    assert await store.get_user("ghost") is None


async def test_concept_lookups(store):
    info = await store.concept("attention")
    assert (info.name, info.area, info.requires) == ("Attention", "dl", ["softmax", "dot-product"])
    assert info.aliases == ["attention mechanism"]
    assert await store.concept("nope") is None
    found = await store.concepts(["softmax", "nope", "softmax", "transformer"])
    assert list(found) == ["softmax", "transformer"]


async def test_search_and_areas(store):
    assert [c.concept_id for c in await store.search("attention", 3)][:2] == ["attention", "self-attention"]
    areas = {a.area_id: a.concept_count for a in await store.areas()}
    assert areas == {"math": 5, "ml": 3, "dl": 4}


async def test_load_from_files(tmp_path: Path):
    concepts_path, areas_path = write_sample_files(tmp_path)
    loaded = await InMemoryGraphStore.load(concepts_path, areas_path)
    assert len(loaded.catalog.concepts) == 12

    concepts_path.write_text('{"version": 1, "concepts": [{"id": "x"}]}')
    with pytest.raises(CatalogError):
        await InMemoryGraphStore.load(concepts_path, areas_path)


async def test_factory_falls_back_to_memory_when_neo4j_is_unreachable(tmp_path, monkeypatch, caplog):
    write_sample_files(tmp_path / "concepts")

    async def unreachable(cls, settings=None, **_):
        raise ServiceUnavailable("connection refused")

    monkeypatch.setattr(Neo4jGraphStore, "connect", classmethod(unreachable))
    settings = Settings(data_dir=tmp_path, neo4j_uri="bolt://127.0.0.1:1")
    with caplog.at_level("WARNING", logger=store_module.__name__):
        graph = await get_graph_store(settings)
    assert graph.backend == "memory"
    assert "unreachable" in caplog.text
    assert (await graph.resolve("softmax"))[0].concept_id == "softmax"


async def test_factory_does_not_hide_bad_credentials(tmp_path, monkeypatch):
    async def denied(cls, settings=None, **_):
        raise AuthError("unauthorized")

    monkeypatch.setattr(Neo4jGraphStore, "connect", classmethod(denied))
    with pytest.raises(AuthError):
        await get_graph_store(Settings(data_dir=tmp_path))
