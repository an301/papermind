"""The library: list, add (arXiv or upload), read, and delete papers.

Add-paper failures use the ``AddPaperError`` codes from ``papermind.schemas.api``.
What the request and one arXiv lookup can tell (not an arXiv id, no such
paper, withdrawn, a newer version exists, arXiv unreachable, not a PDF, too
large) is an HTTP error at once, for the hint line under the add field; what
needs the PDF parser happens in the background and shows up on the library
row as ``PaperSummary.error_code``.

Adding a paper that is already in the library and ready is not an error: the
existing row comes back with 200 so the UI can open it. One that is still
being added is a 409 ``already_added`` naming the ``paper_id``.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from typing import Annotated

from fastapi import APIRouter, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from papermind.api.arxiv_check import check_arxiv
from papermind.api.deps import Resources, ResourcesDep
from papermind.api.errors import AddPaperErrorBody, APIError, add_paper_error, error_responses, not_found
from papermind.api.guards import limit_papers
from papermind.api.lookups import (
    PaperIdPath,
    paper_not_found,
    paper_summary,
    require_ready_paper,
    status_summary,
    summary_from_meta,
)
from papermind.api.middleware import too_large_message
from papermind.api.suggestions import fallback_questions
from papermind.ingest.arxiv import InvalidArxivId, looks_like_pdf, parse_arxiv_id
from papermind.ingest.pipeline import arxiv_paper_id, upload_paper_id
from papermind.memory.ingest_status import IngestStatus
from papermind.schemas.api import (
    AddArxivPaperRequest,
    AddPaperError,
    ChunkDetail,
    PaperDetail,
    SuggestionsResponse,
)
from papermind.schemas.paper import PaperStatus, PaperSummary
from papermind.schemas.tools import Level

router = APIRouter(prefix="/papers", tags=["papers"])

_READ_CHUNK = 1024 * 1024

NOT_AN_ARXIV_ID = "That doesn’t look like an arXiv ID. Try one like 2401.12345, or drop a PDF."
NOT_A_PDF = "That file isn’t a PDF. PaperMind reads PDFs and arXiv papers."
EMPTY_FILE = "That file is empty. Choose the PDF of the paper."


def clean_filename(name: str | None) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFKC", base)
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()
    return base[:200] or "upload.pdf"


@router.get("", response_model=list[PaperSummary])
async def list_papers(res: ResourcesDep) -> list[PaperSummary]:
    """Ready papers plus in-flight and failed ingestions, newest first."""
    metas, statuses = await asyncio.gather(res.papers.list(), res.ingest_status.list())
    ready = {m.paper_id: summary_from_meta(m) for m in metas}
    pending = [
        status_summary(s)
        for s in statuses
        # A "ready" status whose paper is not in the store was deleted since.
        if s.paper_id not in ready and s.status is not PaperStatus.READY
    ]
    return sorted([*ready.values(), *pending], key=lambda p: p.added_at, reverse=True)


def _already_adding(status: IngestStatus) -> APIError:
    return add_paper_error(
        409,
        AddPaperError(
            code="already_added",
            message=f"{status.title} is already being added to your papers.",
            paper_id=status.paper_id,
        ),
    )


async def _in_flight(res: Resources, paper_id: str) -> IngestStatus | None:
    status = await res.ingest_status.get(paper_id)
    # A stale entry (its worker died) does not count: adding again replaces it.
    return status if status is not None and status.blocks_new_claim() else None


@router.post(
    "",
    status_code=202,
    response_model=PaperSummary,
    responses={
        200: {"model": PaperSummary, "description": "already in the library"},
        **error_responses(409, 422, 429, 503, model=AddPaperErrorBody),
    },
)
async def add_arxiv_paper(
    body: AddArxivPaperRequest,
    res: ResourcesDep,
    request: Request,
    response: Response,
    keep_version: Annotated[
        bool,
        Query(description="add the version pasted even when arXiv has a newer one (the 'Add v3' choice)"),
    ] = False,
) -> PaperSummary:
    try:
        ref = parse_arxiv_id(body.arxiv.strip())
    except InvalidArxivId:
        raise add_paper_error(422, AddPaperError(code="invalid_input", message=NOT_AN_ARXIV_ID)) from None
    # The pipeline's own id rule, so the status entry claimed here is the one
    # the finished paper replaces.
    paper_id = arxiv_paper_id(ref.arxiv_id)
    meta = await res.papers.meta(paper_id)
    if meta is not None:
        response.status_code = 200
        return summary_from_meta(meta)
    # Checked before the arXiv lookup, which would only cost the reader a wait.
    if (in_flight := await _in_flight(res, paper_id)) is not None:
        raise _already_adding(in_flight)
    versioned_id, title = ref.versioned_id, f"arXiv:{ref.versioned_id}"
    if res.arxiv is not None:
        choice = await check_arxiv(res.arxiv, ref, paper_id=paper_id, keep_version=keep_version)
        versioned_id, title = choice.versioned_id, choice.title
    # Counted only once a paper will actually be fetched and embedded:
    # opening one that is already in the library is free.
    await limit_papers(request, res)
    status, claimed = await res.ingest_status.claim(paper_id, source="arxiv", title=title)
    if not claimed:  # another request claimed it during the lookup
        raise _already_adding(status)
    # Pinned to the version checked above, so the paper ingested is the one
    # the reader was told about even if arXiv publishes another meanwhile.
    res.jobs.start_arxiv(paper_id, versioned_id)
    return status.to_summary()


def _too_large(size: int | None, max_mb: int) -> APIError:
    return add_paper_error(413, AddPaperError(code="too_large", message=too_large_message(size, max_mb)))


async def _read_upload(file: UploadFile, max_mb: int) -> bytes:
    limit = max_mb * 1024 * 1024
    if file.size is not None and file.size > limit:
        raise _too_large(file.size, max_mb)
    parts: list[bytes] = []
    total = 0
    while chunk := await file.read(_READ_CHUNK):
        total += len(chunk)
        if total > limit:
            # Stop reading: the exact size is not worth receiving the rest for.
            raise _too_large(None, max_mb)
        parts.append(chunk)
    return b"".join(parts)


@router.post(
    "/upload",
    status_code=202,
    response_model=PaperSummary,
    responses={
        200: {"model": PaperSummary, "description": "already in the library"},
        **error_responses(409, 413, 415, 429, model=AddPaperErrorBody),
    },
)
async def upload_paper(
    file: Annotated[UploadFile, File(description="the paper as a PDF")],
    res: ResourcesDep,
    request: Request,
    response: Response,
) -> PaperSummary:
    pdf = await _read_upload(file, res.settings.max_upload_mb)
    if not pdf:
        raise add_paper_error(415, AddPaperError(code="not_a_pdf", message=EMPTY_FILE))
    # Trust the bytes, not the filename or the browser's content type.
    if not looks_like_pdf(pdf):
        raise add_paper_error(415, AddPaperError(code="not_a_pdf", message=NOT_A_PDF))
    paper_id = upload_paper_id(pdf)
    filename = clean_filename(file.filename)
    meta = await res.papers.meta(paper_id)
    if meta is not None:
        response.status_code = 200
        return summary_from_meta(meta)
    await limit_papers(request, res)
    status, claimed = await res.ingest_status.claim(paper_id, source="upload", title=filename)
    if not claimed:
        raise _already_adding(status)
    res.jobs.start_upload(paper_id, pdf, filename)
    return status.to_summary()


@router.get("/{paper_id}", response_model=PaperDetail, responses=error_responses(404))
async def get_paper(paper_id: PaperIdPath, res: ResourcesDep) -> PaperDetail:
    summary, meta = await paper_summary(res, paper_id)
    return PaperDetail(summary=summary, meta=meta)


@router.get(
    "/{paper_id}/pdf",
    response_class=FileResponse,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "the PDF; honours Range requests"},
        **error_responses(404, 409),
    },
)
async def get_pdf(paper_id: PaperIdPath, res: ResourcesDep) -> FileResponse:
    meta = await res.papers.meta(paper_id)
    if meta is None and not await _in_flight(res, paper_id):
        meta = await require_ready_paper(res, paper_id)  # raises the right 404 / 409
    # A paper still being added is served too once its PDF is on disk ("Open now").
    path = res.papers.pdf_path(paper_id)
    if not await asyncio.to_thread(path.is_file):
        if meta is None:  # still downloading: nothing to show yet
            await require_ready_paper(res, paper_id)
        raise not_found(
            "pdf_not_found",
            "The PDF for this paper is missing on the server. Delete the paper and add it again.",
        )
    # FileResponse answers Range requests with 206 and Accept-Ranges, which is
    # what lets pdf.js fetch only the pages on screen of a large paper.
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=(meta.filename if meta else None) or f"{paper_id}.pdf",
        content_disposition_type="inline",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.get("/{paper_id}/chunks/{chunk_id}", response_model=ChunkDetail, responses=error_responses(404, 409))
async def get_chunk(paper_id: PaperIdPath, chunk_id: str, res: ResourcesDep) -> ChunkDetail:
    await require_ready_paper(res, paper_id)
    chunk = await res.papers.chunk(paper_id, chunk_id)
    if chunk is None:
        raise not_found("chunk_not_found", f"Passage {chunk_id} is not part of this paper.")
    return ChunkDetail(
        chunk_id=chunk.chunk_id,
        paper_id=chunk.paper_id,
        section_id=chunk.section_id,
        section_number=chunk.section_number,
        section_title=chunk.section_title,
        text=chunk.text,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        boxes=chunk.boxes,
    )


@router.get(
    "/{paper_id}/suggestions", response_model=SuggestionsResponse, responses=error_responses(404, 409)
)
async def get_suggestions(
    paper_id: PaperIdPath,
    res: ResourcesDep,
    level: Annotated[Level, Query(description="reader level the questions are written for")] = "intermediate",
) -> SuggestionsResponse:
    meta = await require_ready_paper(res, paper_id)
    if await res.spend.over_budget():
        # Out of model budget for today: the section-title questions cost nothing.
        return SuggestionsResponse(paper_id=paper_id, level=level, questions=fallback_questions(meta, level))
    questions = await res.suggestions.get(meta, level, res.papers.paper_dir(paper_id))
    return SuggestionsResponse(paper_id=paper_id, level=level, questions=questions)


@router.delete("/{paper_id}", status_code=204, responses=error_responses(403, 404))
async def delete_paper(paper_id: PaperIdPath, res: ResourcesDep) -> Response:
    """Remove a paper, or stop adding one: an in-flight ingestion is cancelled and its files removed."""
    meta, status = await asyncio.gather(res.papers.meta(paper_id), res.ingest_status.get(paper_id))
    if meta is None and status is None and not res.jobs.running(paper_id):
        raise paper_not_found(paper_id)
    # Only a finished paper is protected: clearing a failed or stuck addition
    # takes nothing away from anyone.
    if meta is not None and not res.settings.allow_paper_delete:
        raise APIError(
            403,
            "delete_disabled",
            "Removing papers is turned off on this server, because everyone who uses it shares one library.",
        )
    # Not awaited: the job stops at its next safe point and removes what it
    # wrote by then (see papermind.api.ingest_jobs); the row disappears now.
    res.jobs.cancel_removed(paper_id)
    # The addition may have finished between the check above and the cancel;
    # on a server where papers can't be removed, a finished one stays.
    if meta is None and not res.settings.allow_paper_delete and await res.papers.meta(paper_id) is not None:
        raise APIError(
            403,
            "delete_disabled",
            "Removing papers is turned off on this server, because everyone who uses it shares one library.",
        )
    await res.ingest_status.delete(paper_id)
    await res.papers.discard(paper_id)
    return Response(status_code=204)
