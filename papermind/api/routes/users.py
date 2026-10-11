"""Anonymous reader profiles and what each reader knows."""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Response

from papermind.api.deps import GraphDep, ResourcesDep, SettingsDep
from papermind.api.errors import APIError, error_responses, not_found
from papermind.api.lookups import require_user
from papermind.memory.users import (
    UnknownAreas,
    create_anonymous_user,
    mark_onboarding_areas,
    mark_self_reported,
    visible_knowledge,
)
from papermind.schemas.api import AddKnowledgeRequest, OnboardingAreasRequest, UpdateUserRequest
from papermind.schemas.session import KnownConcept, UserProfile

log = logging.getLogger(__name__)

router = APIRouter(prefix="/users", tags=["users"])


# Status codes follow the endpoint map in papermind/schemas/api.py, which marks
# only the non-200 ones (202, 204).
@router.post("", response_model=UserProfile)
async def create_user(graph: GraphDep) -> UserProfile:
    return await create_anonymous_user(graph)


@router.get("/{user_id}", response_model=UserProfile, responses=error_responses(404))
async def get_user(user_id: str, graph: GraphDep) -> UserProfile:
    return await require_user(graph, user_id)


@router.patch("/{user_id}", response_model=UserProfile, responses=error_responses(404))
async def update_user(user_id: str, body: UpdateUserRequest, graph: GraphDep) -> UserProfile:
    user = await require_user(graph, user_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "display_name" in changes:
        changes["display_name"] = changes["display_name"].strip() or None
    return await graph.upsert_user(user.model_copy(update=changes))


@router.get("/{user_id}/knowledge", response_model=list[KnownConcept], responses=error_responses(404, 422))
async def list_knowledge(
    user_id: str,
    graph: GraphDep,
    settings: SettingsDep,
    include: Annotated[
        Literal["not_yet"] | None,
        Query(description="'not_yet' adds the concepts below the known threshold, with their confidence"),
    ] = None,
) -> list[KnownConcept]:
    """What the reader knows (confidence at or above the known threshold), sorted by name."""
    await require_user(graph, user_id)
    return visible_knowledge(
        (await graph.known(user_id)).values(),
        threshold=settings.known_threshold,
        include_not_yet=include == "not_yet",
    )


@router.post("/{user_id}/knowledge", response_model=KnownConcept, responses=error_responses(404))
async def add_knowledge(user_id: str, body: AddKnowledgeRequest, graph: GraphDep) -> KnownConcept:
    await require_user(graph, user_id)
    result = await mark_self_reported(graph, user_id, body.concept.strip())
    if result.concept is None:
        _, _, suggestions = await graph.resolve(body.concept)
        hint = f" Did you mean {', '.join(s.name for s in suggestions[:3])}?" if suggestions else ""
        raise not_found("concept_not_found", f"“{body.concept.strip()}” is not in the concept graph.{hint}")
    edge = (await graph.known(user_id)).get(result.concept.concept_id)
    if edge is None:  # written and immediately gone: only a concurrent forget can do that
        raise APIError(409, "conflict", "That change was undone by another request. Try again.")
    return edge


@router.post(
    "/{user_id}/knowledge/areas",
    response_model=list[KnownConcept],
    responses=error_responses(404, 422, 503),
)
async def add_onboarding_areas(
    user_id: str, body: OnboardingAreasRequest, res: ResourcesDep
) -> list[KnownConcept]:
    """Onboarding: mark the foundation and core concepts of each chosen area as known.

    Advanced concepts stay unmarked on purpose (see ``papermind.memory.users``).
    """
    await require_user(res.graph, user_id)
    try:
        catalog = await res.catalog.get(res.graph)
    except Exception:
        log.exception("concept catalog could not be loaded")
        raise APIError(
            503,
            "concepts_unavailable",
            "The concept list is not available right now. Skip this step for now.",
        ) from None
    try:
        return await mark_onboarding_areas(res.graph, user_id, catalog, body.areas)
    except UnknownAreas as exc:
        raise APIError(
            422,
            "unknown_area",
            f"Unknown area: {', '.join(exc.area_ids)}. Choose from: {', '.join(exc.valid)}.",
        ) from None


@router.delete("/{user_id}/knowledge/{concept_id}", status_code=204, responses=error_responses(404))
async def forget_knowledge(user_id: str, concept_id: str, graph: GraphDep) -> Response:
    """Idempotent: forgetting a concept the reader was not marked as knowing is not an error."""
    await require_user(graph, user_id)
    result = await graph.forget(user_id, concept_id)
    if result.concept is None:
        raise not_found("concept_not_found", f"“{concept_id}” is not in the concept graph.")
    return Response(status_code=204)
