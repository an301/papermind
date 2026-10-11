"""Browse the prerequisite concept graph."""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Query

from papermind.api.deps import GraphDep, SettingsDep
from papermind.api.errors import error_responses, not_found
from papermind.graph import service
from papermind.schemas.api import ConceptArea, ConceptDetail
from papermind.schemas.session import ConceptInfo
from papermind.schemas.tools import ConceptRef, Gap

router = APIRouter(prefix="/concepts", tags=["concepts"])


@router.get("", response_model=list[ConceptInfo])
async def search_concepts(
    graph: GraphDep,
    q: Annotated[str, Query(min_length=1, max_length=120, description="name, alias or id")],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ConceptInfo]:
    return await graph.search(q.strip(), limit)


# Declared before /{concept_id} so "areas" is not taken for a concept id.
@router.get("/areas", response_model=list[ConceptArea])
async def list_areas(graph: GraphDep) -> list[ConceptArea]:
    return await graph.areas()


@router.get("/{concept_id}", response_model=ConceptDetail, responses=error_responses(404))
async def get_concept(
    concept_id: str,
    graph: GraphDep,
    settings: SettingsDep,
    user_id: Annotated[
        str | None, Query(description="include whether this reader knows it, and their path")
    ] = None,
) -> ConceptDetail:
    concept = await graph.concept(concept_id)
    if concept is None:
        raise not_found("concept_not_found", f"“{concept_id}” is not in the concept graph.")
    prerequisites = await graph.concepts(concept.requires)
    refs = [
        ConceptRef(concept_id=cid, name=prerequisites[cid].name)
        for cid in concept.requires
        if cid in prerequisites
    ]
    if user_id is None:
        return ConceptDetail(concept=concept, prerequisites=refs)

    known, path = await asyncio.gather(
        graph.known(user_id),
        service.learning_path(graph, user_id, concept.concept_id, known_threshold=settings.known_threshold),
    )
    edge = known.get(concept.concept_id)
    steps: list[Gap] = path.steps
    return ConceptDetail(
        concept=concept,
        prerequisites=refs,
        known=edge is not None and edge.confidence >= settings.known_threshold,
        learning_path=steps,
    )
