"""Graph store: concept lookup, prerequisite subgraphs, and readers' KNOWS edges.

Two implementations behind one protocol:

* ``Neo4jGraphStore`` -- production. Mastery (KNOWS) and user profiles persist.
* ``InMemoryGraphStore`` -- tests and the no-Neo4j fallback. Loads the same
  JSON seed files; users and KNOWS edges live only as long as the process.

Resolution of free text to a concept is in-process in both (``ConceptIndex``):
concepts only change when the seed is re-run, so there is no reason to pay a
round trip, and fuzzy matching is not something Cypher does well anyway.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from neo4j import AsyncDriver, AsyncGraphDatabase, NotificationDisabledClassification, RoutingControl
from neo4j.exceptions import ServiceUnavailable, SessionExpired

from papermind.config import Settings, get_settings
from papermind.graph.normalize import ConceptIndex, IndexEntry, Resolution
from papermind.graph.seed import Catalog, default_areas_path, ensure_schema, load_catalog
from papermind.schemas.api import ConceptArea
from papermind.schemas.session import ConceptInfo, KnownConcept, UserProfile
from papermind.schemas.tools import ConceptRef, MarkKnownResult, MarkKnownServerArgs

log = logging.getLogger(__name__)


@runtime_checkable
class GraphStore(Protocol):
    backend: str

    async def resolve(self, query: str) -> tuple[ConceptRef | None, float, list[ConceptRef]]:
        """Free text or an id -> ``(matched, score 0..1, suggestions when unmatched)``."""
        ...

    async def subgraph(self, concept_id: str) -> dict[str, list[str]]:
        """Every concept reachable from ``concept_id`` -> its direct prerequisite ids ({} if unknown)."""
        ...

    async def known(self, user_id: str) -> dict[str, KnownConcept]:
        """All KNOWS edges of a reader, whatever their confidence."""
        ...

    async def mark(self, args: MarkKnownServerArgs) -> MarkKnownResult:
        """Create or overwrite a KNOWS edge with exactly the given confidence/source/evidence."""
        ...

    async def forget(self, user_id: str, concept_id: str) -> MarkKnownResult:
        """Delete a KNOWS edge. ``concept_id`` may also be a name; it is resolved like ``mark``."""
        ...

    async def concept(self, concept_id: str) -> ConceptInfo | None: ...

    async def concepts(self, concept_ids: Iterable[str]) -> dict[str, ConceptInfo]:
        """Batch form of ``concept``; unknown ids are omitted."""
        ...

    async def search(self, q: str, limit: int = 20) -> list[ConceptInfo]: ...

    async def areas(self) -> list[ConceptArea]: ...

    async def get_user(self, user_id: str) -> UserProfile | None: ...

    async def upsert_user(self, profile: UserProfile) -> UserProfile:
        """Create or update; an existing user's ``created_at`` is kept."""
        ...

    async def aclose(self) -> None: ...


def _now() -> datetime:
    return datetime.now(UTC)


def _resolve(index: ConceptIndex, query: str) -> Resolution:
    # An exact id short-circuits: the API and the mastery pass send ids, and an
    # id like "adam" must not be fuzzy-matched against anything else.
    ref = index.ref(query)
    return Resolution(ref, 1.0, []) if ref else index.resolve(query)


def _lookup(index: ConceptIndex, concept: str) -> ConceptRef | None:
    return _resolve(index, concept).ref


# ---------------------------------------------------------------- in memory


class InMemoryGraphStore:
    """Concepts from the seed files; users and KNOWS edges in process memory.

    Every method body runs without an ``await`` between reading and writing
    shared state, so on one event loop each call is atomic without a lock.
    """

    backend = "memory"

    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._index = catalog.index
        self._users: dict[str, UserProfile] = {}
        self._knows: dict[str, dict[str, KnownConcept]] = {}

    @classmethod
    async def load(cls, concepts_path: Path, areas_path: Path | None = None) -> InMemoryGraphStore:
        # Parsing + validating a few hundred concepts is file I/O plus a few
        # ms of CPU; keep it off the event loop.
        catalog = await asyncio.to_thread(load_catalog, concepts_path, areas_path)
        return cls(catalog)

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    async def resolve(self, query: str) -> Resolution:
        return _resolve(self._index, query)

    async def subgraph(self, concept_id: str) -> dict[str, list[str]]:
        if concept_id not in self._catalog.concepts:
            return {}
        adjacency: dict[str, list[str]] = {}
        stack = [concept_id]
        while stack:
            node = stack.pop()
            if node in adjacency:
                continue
            adjacency[node] = self._catalog.prerequisites_of(node)
            stack.extend(adjacency[node])
        return adjacency

    async def known(self, user_id: str) -> dict[str, KnownConcept]:
        return {cid: k.model_copy() for cid, k in self._knows.get(user_id, {}).items()}

    async def mark(self, args: MarkKnownServerArgs) -> MarkKnownResult:
        ref = _lookup(self._index, args.concept)
        if ref is None:
            return MarkKnownResult(concept=None, status="not_found")
        self._ensure_user(args.user_id)
        edges = self._knows.setdefault(args.user_id, {})
        status = "updated" if ref.concept_id in edges else "added"
        spec = self._catalog.concepts[ref.concept_id]
        edges[ref.concept_id] = KnownConcept(
            concept_id=ref.concept_id,
            name=spec.name,
            area=spec.area,
            confidence=args.confidence,
            source=args.source,
            evidence=args.evidence,
            updated_at=_now(),
        )
        return MarkKnownResult(concept=ref, status=status, confidence=args.confidence)

    async def forget(self, user_id: str, concept_id: str) -> MarkKnownResult:
        ref = _lookup(self._index, concept_id)
        if ref is None:
            return MarkKnownResult(concept=None, status="not_found")
        removed = self._knows.get(user_id, {}).pop(ref.concept_id, None)
        if removed is None:
            return MarkKnownResult(concept=ref, status="not_found")
        return MarkKnownResult(concept=ref, status="removed", confidence=removed.confidence)

    async def concept(self, concept_id: str) -> ConceptInfo | None:
        return self._catalog.info(concept_id)

    async def concepts(self, concept_ids: Iterable[str]) -> dict[str, ConceptInfo]:
        found = {cid: self._catalog.info(cid) for cid in dict.fromkeys(concept_ids)}
        return {cid: info for cid, info in found.items() if info is not None}

    async def search(self, q: str, limit: int = 20) -> list[ConceptInfo]:
        infos = (self._catalog.info(ref.concept_id) for ref, _ in self._index.search(q, limit))
        return [info for info in infos if info is not None]

    async def areas(self) -> list[ConceptArea]:
        counts = Counter(spec.area for spec in self._catalog.concepts.values())
        return [
            ConceptArea(
                area_id=a.area_id,
                name=a.name,
                description=a.description,
                concept_count=counts[a.area_id],
            )
            for a in self._catalog.areas
        ]

    async def get_user(self, user_id: str) -> UserProfile | None:
        user = self._users.get(user_id)
        return user.model_copy() if user else None

    async def upsert_user(self, profile: UserProfile) -> UserProfile:
        existing = self._users.get(profile.user_id)
        stored = (
            profile.model_copy(update={"created_at": existing.created_at})
            if existing
            else profile.model_copy()
        )
        self._users[profile.user_id] = stored
        return stored.model_copy()

    async def aclose(self) -> None:
        return None

    def _ensure_user(self, user_id: str) -> None:
        if user_id not in self._users:
            self._users[user_id] = UserProfile(user_id=user_id, created_at=_now())


# ---------------------------------------------------------------- Neo4j


_LOAD_INDEX = """
MATCH (c:Concept)
WHERE NOT coalesce(c.retired, false)
RETURN c.id AS id, c.name AS name, coalesce(c.aliases, []) AS aliases
"""

# One round trip for the whole prerequisite closure. Collecting DISTINCT
# reachable nodes (rather than returning paths) lets the planner use a pruning
# BFS expand, so a diamond-heavy DAG costs O(nodes + edges), not one row per
# path. The second hop then lists each reached node's direct prerequisites.
_SUBGRAPH = """
MATCH (t:Concept {id: $id})
OPTIONAL MATCH (t)-[:REQUIRES*]->(r:Concept)
WITH t, collect(DISTINCT r) AS reached
UNWIND [t] + reached AS c
OPTIONAL MATCH (c)-[q:REQUIRES]->(p:Concept)
WITH c, p ORDER BY q.rank, p.id
RETURN c.id AS id, collect(p.id) AS requires
"""

_CONCEPTS = """
UNWIND $ids AS cid
MATCH (c:Concept {id: cid})
OPTIONAL MATCH (c)-[q:REQUIRES]->(p:Concept)
WITH c, p ORDER BY q.rank, p.id
RETURN c.id AS id, c.name AS name, c.area AS area, c.description AS description,
       coalesce(c.aliases, []) AS aliases, collect(p.id) AS requires
"""

_KNOWN = """
MATCH (:User {id: $user_id})-[k:KNOWS]->(c:Concept)
WHERE NOT coalesce(c.retired, false)
RETURN c.id AS id, c.name AS name, c.area AS area, k.confidence AS confidence,
       k.source AS source, coalesce(k.evidence, '') AS evidence, k.updated_at AS updated_at
"""

# Writing u.knows_updated_at takes the user node's write lock before the
# relationship MERGE. Two concurrent marks for one reader then serialise on
# it, so the second sees the first's edge instead of MERGE creating a twin.
_MARK = """
MERGE (u:User {id: $user_id})
ON CREATE SET u.created_at = $now
SET u.knows_updated_at = $now
WITH u
MATCH (c:Concept {id: $concept_id})
OPTIONAL MATCH (u)-[old:KNOWS]->(c)
WITH u, c, old IS NOT NULL AS existed
MERGE (u)-[k:KNOWS]->(c)
SET k.confidence = $confidence, k.source = $source, k.evidence = $evidence, k.updated_at = $now
RETURN existed
"""

_FORGET = """
MATCH (:User {id: $user_id})-[k:KNOWS]->(:Concept {id: $concept_id})
WITH k, k.confidence AS confidence
DELETE k
RETURN confidence
"""

_AREAS = """
MATCH (a:Area)
OPTIONAL MATCH (c:Concept {area: a.id})
WHERE NOT coalesce(c.retired, false)
RETURN a.id AS id, a.name AS name, coalesce(a.description, '') AS description, count(c) AS concept_count
ORDER BY name
"""

_GET_USER = """
MATCH (u:User {id: $user_id})
RETURN u.id AS id, u.display_name AS display_name, u.level AS level,
       u.onboarded AS onboarded, u.created_at AS created_at
"""

_UPSERT_USER = """
MERGE (u:User {id: $user_id})
ON CREATE SET u.created_at = $created_at
SET u.display_name = $display_name, u.level = $level, u.onboarded = $onboarded
RETURN u.id AS id, u.display_name AS display_name, u.level AS level,
       u.onboarded AS onboarded, u.created_at AS created_at
"""


def _native(value: Any) -> Any:
    # The driver returns neo4j.time.DateTime; pydantic wants datetime.
    return value.to_native() if hasattr(value, "to_native") else value


def _user_from_record(record: Any) -> UserProfile:
    return UserProfile(
        user_id=record["id"],
        display_name=record["display_name"],
        level=record["level"] or "intermediate",
        onboarded=bool(record["onboarded"]),
        created_at=_native(record["created_at"]) or _now(),
    )


def create_driver(settings: Settings, *, timeout_s: float = 3.0) -> AsyncDriver:
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
        connection_timeout=timeout_s,
        # The driver retries a failed transaction for 30 s by default. Every
        # caller here is on a reader's request path (a chat turn, the knowledge
        # page, an MCP tool call inside an agent step), where a dead database
        # should fail fast and degrade, not hold the request open.
        max_transaction_retry_time=timeout_s,
        # "Property/label does not exist" notices are expected before the
        # first reader or the first seed and would log a warning per query.
        # Deprecation and performance notices stay on.
        notifications_disabled_classifications=[NotificationDisabledClassification.UNRECOGNIZED],
    )


class Neo4jGraphStore:
    backend = "neo4j"

    def __init__(self, driver: AsyncDriver, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database
        self._index = ConceptIndex()

    @classmethod
    async def connect(cls, settings: Settings | None = None, *, timeout_s: float = 3.0) -> Neo4jGraphStore:
        """Open the driver, check connectivity, apply constraints, load the concept index."""
        driver = create_driver(settings or get_settings(), timeout_s=timeout_s)
        try:
            await driver.verify_connectivity()
            store = cls(driver)
            await ensure_schema(driver)
            count = await store.refresh_index()
        except BaseException:
            await driver.close()
            raise
        if count == 0:
            log.warning("Neo4j has no Concept nodes; run scripts/seed_graph.py to load data/concepts")
        return store

    @property
    def driver(self) -> AsyncDriver:
        return self._driver

    async def refresh_index(self) -> int:
        """Reload the in-process resolution index (call after re-seeding a running store)."""
        records = await self._read(_LOAD_INDEX)
        self._index = ConceptIndex(IndexEntry(r["id"], r["name"], tuple(r["aliases"])) for r in records)
        if self._index.collisions:
            log.warning(
                "concept index: %d name collisions, e.g. %s",
                len(self._index.collisions),
                self._index.collisions[0],
            )
        return len(self._index)

    async def _read(self, query: str, **params: Any) -> list[Any]:
        records, _, _ = await self._driver.execute_query(
            query, params, routing_=RoutingControl.READ, database_=self._database
        )
        return records

    async def _write(self, query: str, **params: Any) -> list[Any]:
        records, _, _ = await self._driver.execute_query(
            query, params, routing_=RoutingControl.WRITE, database_=self._database
        )
        return records

    async def resolve(self, query: str) -> Resolution:
        return _resolve(self._index, query)

    async def subgraph(self, concept_id: str) -> dict[str, list[str]]:
        records = await self._read(_SUBGRAPH, id=concept_id)
        return {r["id"]: list(r["requires"]) for r in records}

    async def known(self, user_id: str) -> dict[str, KnownConcept]:
        records = await self._read(_KNOWN, user_id=user_id)
        return {
            r["id"]: KnownConcept(
                concept_id=r["id"],
                name=r["name"],
                area=r["area"],
                confidence=r["confidence"],
                source=r["source"],
                evidence=r["evidence"],
                updated_at=_native(r["updated_at"]),
            )
            for r in records
        }

    async def mark(self, args: MarkKnownServerArgs) -> MarkKnownResult:
        ref = _lookup(self._index, args.concept)
        if ref is None:
            return MarkKnownResult(concept=None, status="not_found")
        records = await self._write(
            _MARK,
            user_id=args.user_id,
            concept_id=ref.concept_id,
            confidence=args.confidence,
            source=args.source,
            evidence=args.evidence,
            now=_now(),
        )
        if not records:  # in the index but gone from the database (re-seeded underneath us)
            return MarkKnownResult(concept=None, status="not_found")
        status = "updated" if records[0]["existed"] else "added"
        return MarkKnownResult(concept=ref, status=status, confidence=args.confidence)

    async def forget(self, user_id: str, concept_id: str) -> MarkKnownResult:
        ref = _lookup(self._index, concept_id)
        if ref is None:
            return MarkKnownResult(concept=None, status="not_found")
        records = await self._write(_FORGET, user_id=user_id, concept_id=ref.concept_id)
        if not records:
            return MarkKnownResult(concept=ref, status="not_found")
        return MarkKnownResult(concept=ref, status="removed", confidence=records[0]["confidence"])

    async def concept(self, concept_id: str) -> ConceptInfo | None:
        return (await self.concepts([concept_id])).get(concept_id)

    async def concepts(self, concept_ids: Iterable[str]) -> dict[str, ConceptInfo]:
        ids = list(dict.fromkeys(concept_ids))
        if not ids:
            return {}
        records = await self._read(_CONCEPTS, ids=ids)
        return {
            r["id"]: ConceptInfo(
                concept_id=r["id"],
                name=r["name"],
                area=r["area"],
                description=r["description"],
                aliases=list(r["aliases"]),
                requires=list(r["requires"]),
            )
            for r in records
        }

    async def search(self, q: str, limit: int = 20) -> list[ConceptInfo]:
        ranked = [ref.concept_id for ref, _ in self._index.search(q, limit)]
        found = await self.concepts(ranked)
        return [found[cid] for cid in ranked if cid in found]

    async def areas(self) -> list[ConceptArea]:
        records = await self._read(_AREAS)
        return [
            ConceptArea(
                area_id=r["id"],
                name=r["name"],
                description=r["description"],
                concept_count=r["concept_count"],
            )
            for r in records
        ]

    async def get_user(self, user_id: str) -> UserProfile | None:
        records = await self._read(_GET_USER, user_id=user_id)
        return _user_from_record(records[0]) if records else None

    async def upsert_user(self, profile: UserProfile) -> UserProfile:
        records = await self._write(
            _UPSERT_USER,
            user_id=profile.user_id,
            display_name=profile.display_name,
            level=profile.level,
            onboarded=profile.onboarded,
            created_at=profile.created_at,
        )
        return _user_from_record(records[0])

    async def aclose(self) -> None:
        await self._driver.close()


# ---------------------------------------------------------------- factory


async def get_graph_store(settings: Settings | None = None) -> GraphStore:
    """Neo4j when it is reachable, otherwise the in-memory store with a warning.

    Only "cannot reach the server" falls back. A wrong password or a bad URI
    scheme is a configuration bug, and silently swapping in a store that
    forgets every reader's progress on restart would hide it, so those raise.
    """
    settings = settings or get_settings()
    try:
        return await Neo4jGraphStore.connect(settings)
    except (ServiceUnavailable, SessionExpired, OSError) as exc:
        log.warning(
            "Neo4j at %s is unreachable (%s); using the in-memory graph store. "
            "Reader knowledge will not survive a restart.",
            settings.neo4j_uri,
            exc.__class__.__name__,
        )
    return await InMemoryGraphStore.load(settings.concepts_path, default_areas_path(settings.concepts_path))
