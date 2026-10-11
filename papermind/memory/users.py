"""Reader profiles and self-reported knowledge, on top of the graph store.

Profiles and KNOWS edges live in the graph (they outlive a session); this
module holds the policy the API applies when writing them, so the routes stay
thin and the rules are testable without HTTP.
"""

from __future__ import annotations

import asyncio
import secrets
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from papermind.graph.seed import Catalog, default_areas_path, load_catalog
from papermind.graph.store import GraphStore, InMemoryGraphStore
from papermind.schemas.session import KnownConcept, UserProfile
from papermind.schemas.tools import Level, MarkKnownResult, MarkKnownServerArgs

# Self-reports and onboarding sit well above the known threshold (0.6) but
# below certainty, so a later question that reveals a gap ("wait, what is
# softmax?") can still pull the concept back under the line.
SELF_REPORTED_CONFIDENCE = 0.9
ONBOARDING_CONFIDENCE = 0.9

# Saying "I'm comfortable with deep learning" covers the area's foundations and
# its standard toolkit, not every advanced method in it. Marking the advanced
# tier as well would hide exactly the gaps the agent should scaffold, while
# leaving it unmarked costs at most a short recap the reader can skip.
ONBOARDING_TIERS = frozenset({"foundation", "core"})


def new_user_id() -> str:
    return "u_" + secrets.token_urlsafe(9)


async def create_anonymous_user(
    graph: GraphStore, *, display_name: str | None = None, level: Level = "intermediate"
) -> UserProfile:
    profile = UserProfile(
        user_id=new_user_id(), display_name=display_name, level=level, created_at=datetime.now(UTC)
    )
    return await graph.upsert_user(profile)


async def ensure_user(graph: GraphStore, user_id: str, *, level: Level = "intermediate") -> UserProfile:
    """The profile, recreated with defaults if the graph lost it.

    Only the in-memory graph store loses users (on restart), while sessions in
    Redis survive; a reader mid-session should not be locked out by that.
    """
    found = await graph.get_user(user_id)
    if found is not None:
        return found
    return await graph.upsert_user(UserProfile(user_id=user_id, level=level, created_at=datetime.now(UTC)))


def sorted_knowledge(edges: Iterable[KnownConcept]) -> list[KnownConcept]:
    return sorted(edges, key=lambda k: (k.name.casefold(), k.concept_id))


def visible_knowledge(
    edges: Iterable[KnownConcept], *, threshold: float, include_not_yet: bool = False
) -> list[KnownConcept]:
    """What the profile lists, sorted by name.

    An edge below the known threshold is evidence the reader has *met* a
    concept, not that they know it (the mastery pass writes inferred edges at
    lower confidences, and pulls self-reports back under the line). Listing it
    as known by default would contradict what the agent assumes, so the
    "not yet" rows are opt-in.
    """
    return sorted_knowledge(e for e in edges if include_not_yet or e.confidence >= threshold)


async def mark_self_reported(graph: GraphStore, user_id: str, concept: str) -> MarkKnownResult:
    return await graph.mark(
        MarkKnownServerArgs(
            user_id=user_id,
            concept=concept,
            confidence=SELF_REPORTED_CONFIDENCE,
            source="self_reported",
            evidence="The reader said they know this.",
        )
    )


class CatalogSource:
    """Concept tiers for onboarding.

    Tiers are seed metadata the GraphStore protocol does not expose, so they
    come from the seed file the graph was built from (or straight from the
    in-memory store's catalog). Loaded once, off the event loop.
    """

    def __init__(self, concepts_path: Path) -> None:
        self._path = concepts_path
        self._catalog: Catalog | None = None
        self._lock = asyncio.Lock()

    async def get(self, graph: GraphStore) -> Catalog:
        if isinstance(graph, InMemoryGraphStore):
            return graph.catalog
        async with self._lock:
            if self._catalog is None:
                self._catalog = await asyncio.to_thread(
                    load_catalog, self._path, default_areas_path(self._path)
                )
            return self._catalog


class UnknownAreas(ValueError):
    def __init__(self, area_ids: list[str], valid: list[str]) -> None:
        super().__init__(f"unknown areas: {', '.join(area_ids)}")
        self.area_ids = area_ids
        self.valid = valid


def onboarding_concepts(catalog: Catalog, area_ids: Iterable[str]) -> list[str]:
    """Foundation and core concepts of the given areas. Raises ``UnknownAreas``."""
    wanted = list(dict.fromkeys(area_ids))
    valid = [a.area_id for a in catalog.areas]
    unknown = [a for a in wanted if a not in valid]
    if unknown:
        raise UnknownAreas(unknown, valid)
    chosen = set(wanted)
    return sorted(
        cid for cid, spec in catalog.concepts.items() if spec.area in chosen and spec.tier in ONBOARDING_TIERS
    )


async def mark_onboarding_areas(
    graph: GraphStore, user_id: str, catalog: Catalog, area_ids: Iterable[str]
) -> list[KnownConcept]:
    """Mark the areas' foundation and core concepts known (source ``onboarding``).

    Returns the reader's edges for those concepts, sorted by name. An edge the
    reader reported themselves, or one already at least as confident, is left
    alone: onboarding is a coarse default and must not overwrite more specific
    evidence.
    """
    concept_ids = onboarding_concepts(catalog, area_ids)
    area_name = {a.area_id: a.name for a in catalog.areas}
    existing = await graph.known(user_id)
    to_mark = [
        cid
        for cid in concept_ids
        if cid not in existing
        or (existing[cid].source != "self_reported" and existing[cid].confidence < ONBOARDING_CONFIDENCE)
    ]
    # Bounded fan-out: each mark is one Neo4j write transaction, and an area
    # can hold a hundred concepts.
    semaphore = asyncio.Semaphore(8)

    async def mark(cid: str) -> None:
        area = area_name.get(catalog.concepts[cid].area, catalog.concepts[cid].area)
        async with semaphore:
            await graph.mark(
                MarkKnownServerArgs(
                    user_id=user_id,
                    concept=cid,
                    confidence=ONBOARDING_CONFIDENCE,
                    source="onboarding",
                    evidence=f"Onboarding: comfortable with {area}.",
                )
            )

    await asyncio.gather(*(mark(cid) for cid in to_mark))
    after = await graph.known(user_id)
    return sorted_knowledge(after[cid] for cid in concept_ids if cid in after)
