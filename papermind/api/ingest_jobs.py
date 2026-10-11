"""Background ingestion jobs started by the papers routes.

A request that adds a paper returns 202 at once; the work (download, extract,
section, chunk, index) runs as an asyncio task in this worker and reports each
stage to the shared ingest status store, which is what the library polls.

Tasks are held in ``self._tasks`` until they finish. The event loop keeps only
a weak reference to a task, so a fire-and-forget ``create_task`` can be
garbage-collected mid-ingest and vanish without an error.

Cancelling (the reader removed a paper that is still being added) has one
trap. The pipeline runs its CPU work through ``asyncio.to_thread``, and
cancelling the task does not stop a thread that is already running. Most of
those threads only compute, so a plain cancel is fine and immediate. The
indexing stage is different: its threads write the embedding cache and then
``paper.json``. A cancel landing there would let a thread finish writing
*after* the files were removed, and the deleted paper would reappear in the
library as ready. So during indexing the job is stopped cooperatively instead:
its next progress report (the pipeline reports after every thread step, and
per embedding batch) raises, and the job removes whatever was written.

The DELETE may also land on another worker, which cannot reach this task at
all. What every worker shares is the status entry, so a job treats its entry
disappearing (``IngestStatusStore.update`` returning False) exactly like a
local removal: it stops at the next progress report, and a job that finishes
after its entry is gone removes the paper it just saved instead of publishing it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Protocol, get_args

from papermind.api.background import raise_if_cancel_swallowed
from papermind.ingest.arxiv import ArxivNotFound, ArxivUnavailable, InvalidArxivId
from papermind.ingest.extract import EmptyPDF, EncryptedPDF, NotAPDF, NoTextPDF, PDFError, PDFTooLarge
from papermind.memory.ingest_status import IngestStatusStore
from papermind.schemas.api import AddPaperError, AddPaperErrorCode
from papermind.schemas.paper import IngestStage, PaperRecord, PaperStatus, StageDetail

log = logging.getLogger(__name__)


class ProgressFn(Protocol):
    """The pipeline's ``on_progress(stage, fraction)``, plus optional counts within the stage."""

    def __call__(
        self, stage: IngestStage, fraction: float, *, done: int | None = None, total: int | None = None
    ) -> Awaitable[None]: ...


IngestRun = Callable[[ProgressFn], Awaitable[PaperRecord]]
# Removes everything stored for a paper (paper dir, embedding cache). Idempotent.
DiscardFn = Callable[[str], Awaitable[None]]


class IngestArxivFn(Protocol):
    """``papermind.ingest.pipeline.ingest_arxiv``."""

    def __call__(self, raw: str, *, on_progress: ProgressFn | None = None) -> Awaitable[PaperRecord]: ...


class IngestUploadFn(Protocol):
    """``papermind.ingest.pipeline.ingest_upload``."""

    def __call__(
        self, pdf: bytes, filename: str, *, on_progress: ProgressFn | None = None
    ) -> Awaitable[PaperRecord]: ...


# Where each stage sits on the overall progress bar. The pipeline reports how
# far it is through the current stage; the reader wants one bar that only
# moves forward. Indexing (embedding every chunk) dominates on CPU.
_STAGE_SPAN: dict[IngestStage, tuple[float, float]] = {
    IngestStage.DOWNLOADING: (0.0, 0.15),
    IngestStage.EXTRACTING: (0.15, 0.40),
    IngestStage.SECTIONING: (0.40, 0.50),
    IngestStage.CHUNKING: (0.50, 0.60),
    IngestStage.INDEXING: (0.60, 0.99),
    IngestStage.DONE: (1.0, 1.0),
}
# Stages whose worker threads write files (see the module docstring).
_WRITING_STAGES = frozenset({IngestStage.INDEXING, IngestStage.DONE})

GENERIC_FAILURE = "Couldn’t finish preparing this paper: processing failed. Try adding it again."
ARXIV_UNREACHABLE = "Couldn’t reach arXiv. Check your connection and try again, or upload the PDF instead."
INTERRUPTED = "Processing was interrupted because the server restarted. Add the paper again to retry."
INTERRUPT_WRITE_TIMEOUT_S = 2.0
CANCEL_WAIT_S = 5.0
_ADD_PAPER_CODES: frozenset[str] = frozenset(get_args(AddPaperErrorCode))


class _Removed(Exception):
    """Raised from a progress report once the paper was removed mid-ingest."""


def describe_ingest_error(exc: BaseException, paper_id: str) -> AddPaperError:
    """The ingest package's typed errors as the library shows them (code + reader-facing sentence)."""
    # An error that already speaks the API's vocabulary (e.g. a withdrawn or
    # superseded arXiv version, with ``latest_version``) passes straight through.
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code in _ADD_PAPER_CODES:
        latest = getattr(exc, "latest_version", None)
        return AddPaperError(
            code=code,  # checked against the Literal above
            message=str(exc),
            latest_version=latest if isinstance(latest, str) else None,
            paper_id=paper_id,
        )
    if isinstance(exc, InvalidArxivId):
        return AddPaperError(
            code="invalid_input",
            message="That doesn’t look like an arXiv ID. Try one like 2401.12345, or drop a PDF.",
        )
    if isinstance(exc, ArxivNotFound):
        # Old-style ids keep their slash in the message ('hep-th/9901001'),
        # not the paper id's underscore.
        return AddPaperError(code="not_found", message=arxiv_not_found_message(paper_id.replace("_", "/", 1)))
    if isinstance(exc, ArxivUnavailable):
        return AddPaperError(code="arxiv_unreachable", message=ARXIV_UNREACHABLE)
    if isinstance(exc, NotAPDF):
        return AddPaperError(
            code="not_a_pdf", message="That file isn’t a PDF. PaperMind reads PDFs and arXiv papers."
        )
    # The extraction errors' messages are written for the reader; only the
    # trailing full stop is missing.
    if isinstance(exc, PDFTooLarge):
        return AddPaperError(code="too_large", message=_sentence(exc))
    if isinstance(exc, EncryptedPDF):
        return AddPaperError(code="encrypted", message=_sentence(exc))
    if isinstance(exc, NoTextPDF | EmptyPDF):
        return AddPaperError(code="no_text", message=_sentence(exc))
    if isinstance(exc, PDFError):
        return AddPaperError(code="ingest_failed", message=_sentence(exc))
    return AddPaperError(code="ingest_failed", message=GENERIC_FAILURE)


def arxiv_not_found_message(arxiv_id: str) -> str:
    return f"arXiv has no paper {arxiv_id}. Check the ID, or upload the PDF instead."


def _sentence(exc: BaseException) -> str:
    text = str(exc).strip()
    return text if text.endswith((".", "?", "!")) else text + "."


def overall_progress(stage: IngestStage, fraction: float) -> float:
    start, end = _STAGE_SPAN[stage]
    return start + (end - start) * min(1.0, max(0.0, fraction))


async def _settle(tasks: list[asyncio.Task[None]]) -> None:
    # Bounded: a job notices a cancel at its next await, which for CPU work in
    # a thread can take a while. Nobody needs to wait for that.
    if tasks:
        await asyncio.wait(tasks, timeout=CANCEL_WAIT_S)


class _Job:
    task: asyncio.Task[None]

    def __init__(self) -> None:
        # The stage last reported, i.e. the one the pipeline is in now.
        self.stage: IngestStage | None = None
        self.removed = False


class IngestJobs:
    def __init__(
        self,
        status: IngestStatusStore,
        *,
        ingest_arxiv: IngestArxivFn,
        ingest_upload: IngestUploadFn,
        discard: DiscardFn,
    ) -> None:
        self._status = status
        self._ingest_arxiv = ingest_arxiv
        self._ingest_upload = ingest_upload
        self._discard = discard
        self._jobs: dict[str, _Job] = {}

    def running(self, paper_id: str) -> bool:
        job = self._jobs.get(paper_id)
        return job is not None and not job.task.done()

    def start(self, paper_id: str, run: IngestRun) -> asyncio.Task[None]:
        """Start ``run`` for an already-claimed ``paper_id`` (see ``IngestStatusStore.claim``)."""
        previous = self._jobs.get(paper_id)
        if previous is not None and not previous.task.done() and not previous.removed:
            return previous.task
        # A removed job may still be winding down (cleaning up its files); the
        # re-added paper must not start writing until that is over.
        wait_for = previous.task if previous is not None and not previous.task.done() else None
        job = _Job()
        job.task = asyncio.create_task(self._run(paper_id, job, run, wait_for), name=f"ingest:{paper_id}")
        job.task.add_done_callback(lambda t: self._forget(paper_id, t))
        self._jobs[paper_id] = job
        return job.task

    def start_arxiv(self, paper_id: str, raw: str) -> asyncio.Task[None]:
        return self.start(paper_id, lambda on_progress: self._ingest_arxiv(raw, on_progress=on_progress))

    def start_upload(self, paper_id: str, pdf: bytes, filename: str) -> asyncio.Task[None]:
        return self.start(
            paper_id, lambda on_progress: self._ingest_upload(pdf, filename, on_progress=on_progress)
        )

    def cancel_removed(self, paper_id: str) -> bool:
        """Stop the job for a paper the reader removed. Returns whether one was running.

        Does not wait: the library entry is already gone, and the job removes
        anything it wrote once it has actually stopped.
        """
        job = self._jobs.get(paper_id)
        if job is None or job.task.done():
            return False
        job.removed = True
        if job.stage not in _WRITING_STAGES:
            job.task.cancel()
        return True

    async def cancel(self, paper_id: str) -> None:
        """Remove-and-wait, for callers that need the job gone before they continue."""
        if self.cancel_removed(paper_id):
            await _settle([self._jobs[paper_id].task])

    async def aclose(self) -> None:
        tasks = [job.task for job in self._jobs.values() if not job.task.done()]
        for task in tasks:
            task.cancel()
        await _settle(tasks)

    def _forget(self, paper_id: str, task: asyncio.Task[None]) -> None:
        job = self._jobs.get(paper_id)
        if job is not None and job.task is task:
            del self._jobs[paper_id]

    async def _run(
        self, paper_id: str, job: _Job, run: IngestRun, wait_for: asyncio.Task[None] | None
    ) -> None:
        if wait_for is not None:
            await asyncio.wait([wait_for])
        last = 0.0

        async def on_progress(
            stage: IngestStage, fraction: float, *, done: int | None = None, total: int | None = None
        ) -> None:
            nonlocal last
            if job.removed:
                raise _Removed
            # Recorded before awaiting, so cancel_removed() sees the stage the
            # pipeline is about to enter (and its threads) as soon as it is reported.
            job.stage = stage
            last = max(last, overall_progress(stage, fraction))
            detail = StageDetail(done=done, total=total) if done is not None and total is not None else None
            listed = await self._status.update(
                paper_id, status=PaperStatus.PROCESSING, stage=stage, progress=last, stage_detail=detail
            )
            # A DELETE or shutdown that cancels us during this write must still
            # stop the job, or a deleted paper would reappear when it finishes.
            raise_if_cancel_swallowed()
            if not listed:
                job.removed = True
                raise _Removed

        try:
            if not await self._status.update(paper_id, status=PaperStatus.PROCESSING, progress=0.0):
                return  # removed before it started: nothing written yet
            raise_if_cancel_swallowed()
            record = await run(on_progress)
            if job.removed:
                raise _Removed
        except _Removed:
            await self._remove_files(paper_id)
            return
        except asyncio.CancelledError:
            if job.removed:
                await self._remove_files(paper_id)
                raise
            # Shutdown: the reader should see why it stopped. Bounded, because
            # the cancel may have landed mid-command on the Redis connection
            # and shutdown must not wait on a connection in that state.
            with contextlib.suppress(Exception):
                async with asyncio.timeout(INTERRUPT_WRITE_TIMEOUT_S):
                    await asyncio.shield(
                        self._status.update(
                            paper_id, status=PaperStatus.FAILED, error=INTERRUPTED, error_code="ingest_failed"
                        )
                    )
            raise
        except Exception as exc:
            failure = describe_ingest_error(exc, paper_id)
            if failure.message == GENERIC_FAILURE:
                log.exception("ingestion of %s failed", paper_id)
            else:
                log.warning("ingestion of %s failed (%s): %s", paper_id, failure.code, exc)
            await self._status.update(
                paper_id, status=PaperStatus.FAILED, error=failure.message, error_code=failure.code
            )
            return
        if record.meta.paper_id != paper_id:
            log.warning("ingestion of %s produced paper_id %s", paper_id, record.meta.paper_id)
        published = await self._status.update(
            paper_id,
            status=PaperStatus.READY,
            stage=IngestStage.DONE,
            progress=1.0,
            title=record.meta.title,
        )
        if not published:
            # Removed after the last progress report, so the paper just saved
            # was written after the DELETE cleared the directory.
            await self._remove_files(paper_id)
            return
        log.info("ingested %s: %s (%d chunks)", paper_id, record.meta.title, len(record.chunks))

    async def _remove_files(self, paper_id: str) -> None:
        log.info("ingestion of %s stopped: the paper was removed", paper_id)
        # Shielded: this runs while the task is being cancelled, and leaving
        # half the files behind is exactly what it exists to prevent.
        try:
            await asyncio.shield(self._discard(paper_id))
        except Exception:
            log.warning("could not remove the files of %s", paper_id, exc_info=True)
