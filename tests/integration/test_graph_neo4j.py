"""Neo4jGraphStore and the seeder against the local Neo4j.

Every node these tests create is namespaced with a unique ``pmtest-<hex>``
prefix (ids, names and aliases) and deleted by that prefix afterwards; nothing
else in the database is modified. Seeding uses ``retire_missing=False`` except
inside a dry run, which rolls back.
"""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest

from papermind.config import get_settings
from papermind.graph import service
from papermind.graph.seed import Catalog, seed_neo4j
from papermind.graph.store import InMemoryGraphStore, Neo4jGraphStore, get_graph_store
from papermind.graph.testing import sample_catalog
from papermind.schemas.session import UserProfile
from papermind.schemas.tools import MarkKnownServerArgs

pytestmark = pytest.mark.integration

THRESHOLD = 0.6


@pytest.fixture
def ns() -> str:
    return f"pmtest-{uuid.uuid4().hex[:8]}"


@pytest.fixture
async def neo(ns):
    store = await Neo4jGraphStore.connect(get_settings())
    try:
        await seed_neo4j(store.driver, sample_catalog(ns), retire_missing=False)
        await store.refresh_index()
        yield store
    finally:
        await store.driver.execute_query(
            "MATCH (n) WHERE (n:Concept OR n:Area OR n:User) AND n.id STARTS WITH $prefix DETACH DELETE n",
            {"prefix": f"{ns}-"},
        )
        await store.aclose()


def mark(user_id, concept, confidence=0.9, source="self_reported", evidence=""):
    return MarkKnownServerArgs(
        user_id=user_id, concept=concept, confidence=confidence, source=source, evidence=evidence
    )


def edited(catalog: Catalog, **changes) -> Catalog:
    """A copy of ``catalog`` with some concepts' fields replaced: ``edited(c, x={"requires": []})``."""
    concepts = [
        spec.model_copy(update=changes.get(cid.split("-", 2)[-1], {}))
        for cid, spec in catalog.concepts.items()
    ]
    return Catalog.build(catalog.areas, concepts)


async def test_factory_prefers_neo4j():
    store = await get_graph_store(get_settings())
    try:
        assert store.backend == "neo4j"
    finally:
        await store.aclose()


async def test_reseeding_is_idempotent(neo, ns):
    report = await seed_neo4j(neo.driver, sample_catalog(ns), retire_missing=False)
    assert not report.changed, report


async def test_reseed_updates_props_and_prunes_edges_but_keeps_knowledge(neo, ns):
    await neo.mark(mark(f"{ns}-u", f"{ns}-dot-product"))
    catalog = edited(
        sample_catalog(ns),
        **{"attention": {"requires": [f"{ns}-softmax"], "description": "Rewritten."}},
    )

    dry = await seed_neo4j(neo.driver, catalog, retire_missing=False, dry_run=True)
    assert (dry.concepts_changed, dry.edges_deleted, dry.edges_created) == (1, 1, 0)
    assert f"{ns}-dot-product" in (await neo.subgraph(f"{ns}-attention"))  # rolled back

    report = await seed_neo4j(neo.driver, catalog, retire_missing=False)
    assert (report.concepts_changed, report.edges_deleted, report.edges_created) == (1, 1, 0)
    assert await neo.subgraph(f"{ns}-attention") == {
        f"{ns}-attention": [f"{ns}-softmax"],
        f"{ns}-softmax": [f"{ns}-exponential-function"],
        f"{ns}-exponential-function": [],
    }
    assert (await neo.concept(f"{ns}-attention")).description == "Rewritten."
    assert f"{ns}-dot-product" in await neo.known(f"{ns}-u")  # seeding never touches KNOWS


async def test_retire_runs_and_rolls_back_in_a_dry_run(neo, ns):
    # A committed retire would retire every concept outside this test's
    # namespace, so it is only exercised inside the rolled-back dry run.
    catalog = sample_catalog(ns)
    smaller = Catalog.build(
        catalog.areas, [s for cid, s in catalog.concepts.items() if not cid.endswith("-self-attention")]
    )
    records, _, _ = await neo.driver.execute_query(
        "MATCH (c:Concept) WHERE NOT coalesce(c.retired, false) AND NOT c.id IN $ids RETURN count(c) AS n",
        {"ids": list(smaller.concepts)},
    )
    report = await seed_neo4j(neo.driver, smaller, retire_missing=True, dry_run=True)
    assert report.concepts_retired == records[0]["n"] >= 1
    assert report.edges_deleted >= 1  # self-attention -> attention
    # Rolled back: the concept is not retired and keeps its edge.
    await neo.refresh_index()
    assert (await neo.resolve(f"{ns} self attention"))[0].concept_id == f"{ns}-self-attention"
    assert (await neo.subgraph(f"{ns}-self-attention"))[f"{ns}-self-attention"] == [f"{ns}-attention"]


async def test_retired_concepts_leave_the_index_and_known_and_come_back_on_reseed(neo, ns):
    await neo.mark(mark(f"{ns}-u", f"{ns}-softmax"))
    await neo.driver.execute_query(
        "MATCH (c:Concept {id: $id}) SET c.retired = true", {"id": f"{ns}-softmax"}
    )
    await neo.refresh_index()
    assert (await neo.resolve(f"{ns} softmax"))[0] is None
    assert await neo.known(f"{ns}-u") == {}

    report = await seed_neo4j(neo.driver, sample_catalog(ns), retire_missing=False)
    assert report.concepts_changed == 1
    await neo.refresh_index()
    assert (await neo.resolve(f"{ns} softmax"))[0].concept_id == f"{ns}-softmax"
    assert list(await neo.known(f"{ns}-u")) == [f"{ns}-softmax"]


async def test_backends_agree_on_every_subgraph_and_concept(neo, ns):
    memory = InMemoryGraphStore(sample_catalog(ns))
    for cid in memory.catalog.concepts:
        assert await neo.subgraph(cid) == await memory.subgraph(cid), cid
        assert await neo.concept(cid) == await memory.concept(cid), cid
    assert await neo.subgraph(f"{ns}-missing") == {}

    def rows(areas):
        return sorted((a.area_id, a.name, a.description, a.concept_count) for a in areas)

    assert rows(a for a in await neo.areas() if a.area_id.startswith(ns)) == rows(await memory.areas())


async def test_backends_agree_on_prerequisite_analysis(neo, ns):
    memory = InMemoryGraphStore(sample_catalog(ns))
    for store in (neo, memory):
        await store.mark(mark(f"{ns}-u", f"{ns}-dot-product"))
        await store.mark(mark(f"{ns}-u", f"{ns} exponentials"))
        await store.mark(mark(f"{ns}-u", f"{ns}-derivative", confidence=0.3))
    for query in (
        f"{ns} transformers",
        f"{ns}-batch-normalization",
        f"{ns} neural net",
        f"{ns}-self-attention",
    ):
        a = await service.check_prerequisites(neo, f"{ns}-u", query, known_threshold=THRESHOLD)
        b = await service.check_prerequisites(memory, f"{ns}-u", query, known_threshold=THRESHOLD)
        assert a == b, query
        pa = await service.learning_path(neo, f"{ns}-u", query, known_threshold=THRESHOLD)
        pb = await service.learning_path(memory, f"{ns}-u", query, known_threshold=THRESHOLD)
        assert pa == pb, query

    worked = await service.check_prerequisites(neo, f"{ns}-u", f"{ns}-transformer", known_threshold=THRESHOLD)
    assert [g.concept_id.removeprefix(f"{ns}-") for g in worked.gaps] == [
        "transformer",
        "attention",
        "softmax",
    ]


async def test_mark_known_forget_round_trip(neo, ns):
    user = f"{ns}-u"
    added = await neo.mark(mark(user, f"{ns} dot product", 0.8, "inferred", "explained it"))
    assert (added.status, added.concept.concept_id) == ("added", f"{ns}-dot-product")
    updated = await neo.mark(mark(user, f"{ns}-dot-product", 0.5, "self_reported"))
    assert updated.status == "updated"
    assert (await neo.mark(mark(user, "certainly not a concept zzz"))).status == "not_found"

    edge = (await neo.known(user))[f"{ns}-dot-product"]
    assert (edge.confidence, edge.source, edge.evidence) == (0.5, "self_reported", "")
    assert edge.updated_at.tzinfo is not None and edge.area == f"{ns}-math"

    removed = await neo.forget(user, f"{ns}-dot-product")
    assert (removed.status, removed.confidence) == ("removed", 0.5)
    assert (await neo.forget(user, f"{ns}-dot-product")).status == "not_found"
    assert await neo.known(user) == {}


async def test_concurrent_marks_create_one_edge(neo, ns):
    user = f"{ns}-u"
    results = await asyncio.gather(
        *(neo.mark(mark(user, f"{ns}-softmax", confidence=i / 20)) for i in range(1, 11))
    )
    assert sum(r.status == "added" for r in results) == 1
    records, _, _ = await neo.driver.execute_query(
        "MATCH (:User {id: $u})-[k:KNOWS]->(:Concept {id: $c}) RETURN count(k) AS n",
        {"u": user, "c": f"{ns}-softmax"},
    )
    assert records[0]["n"] == 1


async def test_users(neo, ns):
    user = f"{ns}-reader"
    assert await neo.get_user(user) is None
    first = await neo.upsert_user(
        UserProfile(
            user_id=user, display_name="Ada", level="beginner", created_at=datetime(2026, 1, 1, tzinfo=UTC)
        )
    )
    assert (first.display_name, first.level, first.onboarded) == ("Ada", "beginner", False)
    second = await neo.upsert_user(
        UserProfile(
            user_id=user, display_name=None, level="expert", onboarded=True, created_at=datetime.now(UTC)
        )
    )
    assert (second.display_name, second.level, second.onboarded) == (None, "expert", True)
    assert second.created_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert await neo.get_user(user) == second


async def test_search(neo, ns):
    found = [c.concept_id for c in await neo.search(f"{ns} batchnorm", 3)]
    assert found[0] == f"{ns}-batch-normalization"
