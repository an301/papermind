"""Fetch-or-fail helpers shared by the routes, with the reader-facing 404/409 messages."""

from __future__ import annotations

from typing import Annotated

from fastapi import Path

from papermind.api.deps import Resources
from papermind.api.errors import SESSION_NOT_FOUND_MESSAGE, APIError, conflict, not_found
from papermind.api.ingest_jobs import INTERRUPTED
from papermind.graph.store import GraphStore
from papermind.memory.ingest_status import IngestStatus
from papermind.schemas.paper import IngestStage, PaperMeta, PaperStatus, PaperSummary
from papermind.schemas.session import Session, UserProfile

# arXiv ids ('1706.03762') and upload ids ('up_<sha12>'). Starting with an
# alphanumeric and excluding '/' keeps a crafted id from ever naming a path
# outside data/papers before it reaches the store.
PAPER_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,79}$"
PaperIdPath = Annotated[str, Path(pattern=PAPER_ID_PATTERN, description="arXiv id or up_<sha12>")]


def summary_from_meta(meta: PaperMeta) -> PaperSummary:
    return PaperSummary(
        paper_id=meta.paper_id,
        source=meta.source,
        title=meta.title,
        authors=meta.authors,
        published=meta.published,
        num_pages=meta.num_pages,
        status=PaperStatus.READY,
        stage=IngestStage.DONE,
        progress=1.0,
        added_at=meta.ingested_at,
    )


def effective_status(status: IngestStatus) -> IngestStatus:
    """The status as the reader should see it.

    An in-flight entry that has not moved for ``STALE_AFTER_SECONDS`` belongs
    to a worker that died mid-ingest; showing it as still processing would
    leave a progress bar that never moves. Adding the paper again replaces it.
    """
    if status.in_flight and not status.blocks_new_claim():
        return status.model_copy(
            update={"status": PaperStatus.FAILED, "error": INTERRUPTED, "error_code": "ingest_failed"}
        )
    return status


def status_summary(status: IngestStatus) -> PaperSummary:
    return effective_status(status).to_summary()


def paper_not_found(paper_id: str) -> APIError:
    return not_found("paper_not_found", f"No paper with id {paper_id} is in the library. Add it first.")


def not_ready_error(status: IngestStatus) -> APIError:
    if status.status is PaperStatus.FAILED:
        reason = status.error or "processing failed"
        return conflict("paper_not_ready", f"This paper could not be processed: {reason}")
    stage = f" ({status.stage.value})" if status.stage else ""
    return conflict(
        "paper_not_ready",
        f"This paper is still being processed{stage}. It will be ready to read in a moment.",
    )


async def require_readable(res: Resources, paper_id: str, reader_id: str | None) -> None:
    """Someone else's upload is "not found", never "forbidden": its existence is private too."""
    if not await res.readers.can_read(paper_id, reader_id):
        raise paper_not_found(paper_id)


async def paper_summary(
    res: Resources, paper_id: str, *, reader_id: str | None
) -> tuple[PaperSummary, PaperMeta | None]:
    await require_readable(res, paper_id, reader_id)
    meta = await res.papers.meta(paper_id)
    if meta is not None:
        return summary_from_meta(meta), meta
    status = await res.ingest_status.get(paper_id)
    if status is None:
        raise paper_not_found(paper_id)
    return status_summary(status), None


async def require_ready_paper(res: Resources, paper_id: str, *, reader_id: str | None) -> PaperMeta:
    await require_readable(res, paper_id, reader_id)
    meta = await res.papers.meta(paper_id)
    if meta is not None:
        return meta
    status = await res.ingest_status.get(paper_id)
    # A "ready" status without a stored paper means it was deleted since.
    if status is None or status.status is PaperStatus.READY:
        raise paper_not_found(paper_id)
    raise not_ready_error(effective_status(status))


async def require_session(res: Resources, session_id: str) -> Session:
    session = await res.sessions.get(session_id)
    if session is None:
        raise not_found("session_not_found", SESSION_NOT_FOUND_MESSAGE)
    return session


async def require_user(graph: GraphStore, user_id: str) -> UserProfile:
    user = await graph.get_user(user_id)
    if user is None:
        raise not_found("user_not_found", f"No reader profile with id {user_id}. Create one first.")
    return user
