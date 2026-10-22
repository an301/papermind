"""Reading sessions: one reader, one paper, a transcript and a scroll position."""

from __future__ import annotations

import logging

from fastapi import APIRouter

from papermind.api.deps import ReaderDep, Resources, ResourcesDep
from papermind.api.errors import error_responses
from papermind.api.lookups import require_ready_paper, require_session, require_user
from papermind.schemas.api import CreateSessionRequest, UpdateSessionRequest
from papermind.schemas.paper import PaperMeta
from papermind.schemas.session import Session

log = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _maybe_prewarm(res: Resources, paper: PaperMeta) -> None:
    """Warm the prompt cache for this paper so the first question skips the cold prefix.

    Strictly best effort and never awaited: the session is returned at once
    and a failed pre-warm only means the first question pays full price.
    """
    if not res.settings.prewarm_cache or res.prewarm is None or not res.settings.has_api_key:
        return
    if await res.spend.over_budget():
        return
    res.background.spawn(res.prewarm(paper), name=f"prewarm:{paper.paper_id}")


@router.post("", response_model=Session, responses=error_responses(404, 409))
async def create_session(body: CreateSessionRequest, res: ResourcesDep, reader_id: ReaderDep) -> Session:
    user = await require_user(res.graph, body.user_id)
    paper = await require_ready_paper(res, body.paper_id, reader_id=reader_id)
    session = await res.sessions.create(user.user_id, paper.paper_id, body.level or user.level)
    await _maybe_prewarm(res, paper)
    return session


@router.get("/{session_id}", response_model=Session, responses=error_responses(404))
async def get_session(session_id: str, res: ResourcesDep) -> Session:
    return await require_session(res, session_id)


@router.patch("/{session_id}", response_model=Session, responses=error_responses(404, 409))
async def update_session(session_id: str, body: UpdateSessionRequest, res: ResourcesDep) -> Session:
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if not changes:
        return await require_session(res, session_id)
    update = {k: getattr(body, k) for k in changes}
    # SessionNotFound / SessionBusy become 404 / 409 in papermind.api.errors.
    return await res.sessions.update(session_id, lambda s: s.model_copy(update=update))
