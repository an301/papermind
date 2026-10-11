"""Prerequisite analysis for one reader: resolve, fetch the closure, walk it.

Shared by the prereq_graph MCP server (the agent's tools) and anything else
that needs the same answer, such as a concept page showing a reader's learning
path. Store round trips per call: the subgraph and the reader's KNOWS edges
(concurrently), then one batch lookup for names and descriptions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from papermind.graph.gaps import adjacency_lookup, altitude_for, teaching_order, walk_prerequisites
from papermind.graph.store import GraphStore
from papermind.schemas.session import ConceptInfo
from papermind.schemas.tools import (
    Altitude,
    CheckPrerequisitesResult,
    ConceptRef,
    Gap,
    LearningPathResult,
)


@dataclass
class PrerequisiteAnalysis:
    query: str
    matched: ConceptRef | None
    match_score: float | None
    suggestions: list[ConceptRef] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    known_stops: list[ConceptRef] = field(default_factory=list)
    teaching_order: list[Gap] = field(default_factory=list)

    @property
    def altitude(self) -> Altitude:
        return altitude_for(len(self.gaps))


async def analyze(
    store: GraphStore, user_id: str, concept: str, *, known_threshold: float
) -> PrerequisiteAnalysis:
    """Gaps for ``concept`` given what ``user_id`` knows at ``confidence >= known_threshold``."""
    matched, score, suggestions = await store.resolve(concept)
    if matched is None:
        return PrerequisiteAnalysis(
            query=concept, matched=None, match_score=score or None, suggestions=list(suggestions)
        )

    adjacency, known_edges = await asyncio.gather(store.subgraph(matched.concept_id), store.known(user_id))
    known = {cid for cid, edge in known_edges.items() if edge.confidence >= known_threshold}
    prerequisites_of = adjacency_lookup(adjacency)
    walk = walk_prerequisites(matched.concept_id, known, prerequisites_of)

    gap_ids = [cid for cid, _ in walk.gaps]
    info = await store.concepts([*gap_ids, *walk.known_stops])
    depth = dict(walk.gaps)

    def name_of(cid: str) -> str:
        return info[cid].name if cid in info else cid

    def gap(cid: str) -> Gap:
        found: ConceptInfo | None = info.get(cid)
        return Gap(
            concept_id=cid,
            name=found.name if found else cid,
            depth=depth[cid],
            description=found.description if found else None,
        )

    order = teaching_order(gap_ids, prerequisites_of, name_of=name_of)
    return PrerequisiteAnalysis(
        query=concept,
        matched=matched,
        match_score=score,
        gaps=[gap(cid) for cid in gap_ids],
        known_stops=[ConceptRef(concept_id=cid, name=name_of(cid)) for cid in walk.known_stops],
        teaching_order=[gap(cid) for cid in order],
    )


async def check_prerequisites(
    store: GraphStore, user_id: str, concept: str, *, known_threshold: float
) -> CheckPrerequisitesResult:
    analysis = await analyze(store, user_id, concept, known_threshold=known_threshold)
    return CheckPrerequisitesResult(
        query=analysis.query,
        matched=analysis.matched,
        match_score=analysis.match_score,
        suggestions=analysis.suggestions,
        gaps=analysis.gaps,
        known_stops=analysis.known_stops,
        teaching_order=[ConceptRef(concept_id=g.concept_id, name=g.name) for g in analysis.teaching_order],
        # An unresolved concept has no gaps, so this is "technical": the agent
        # explains at the reader's level rather than inventing a scaffold.
        altitude=analysis.altitude,
    )


async def learning_path(
    store: GraphStore, user_id: str, concept: str, *, known_threshold: float
) -> LearningPathResult:
    analysis = await analyze(store, user_id, concept, known_threshold=known_threshold)
    # Topological order already ends with the target when it is unknown: every
    # other gap is one of its transitive prerequisites.
    return LearningPathResult(query=analysis.query, matched=analysis.matched, steps=analysis.teaching_order)
