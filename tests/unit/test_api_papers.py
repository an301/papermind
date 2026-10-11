from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from papermind.api.deps import AsyncPapers
from papermind.api.ingest_jobs import IngestJobs
from papermind.ingest.arxiv import ArxivUnavailable
from papermind.ingest.extract import NoTextPDF
from papermind.memory.ingest_status import MemoryIngestStatusStore
from papermind.schemas.paper import IngestStage
from tests.unit.test_api_support import (
    MINIMAL_PDF,
    PAPER_ID,
    FakeIngest,
    FakeLLM,
    make_harness,
    make_record,
    make_settings,
)


async def _wait_for_status(
    client, paper_id: str, status: str, *, stage: str | None = None, within_s: float = 2.0
) -> dict:
    deadline = asyncio.get_running_loop().time() + within_s
    while True:
        rows = (await client.get("/api/papers")).json()
        row = next((r for r in rows if r["paper_id"] == paper_id), None)
        if row and row["status"] == status and (stage is None or row["stage"] == stage):
            return row
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"{paper_id} never reached {status}/{stage}: {row}")
        await asyncio.sleep(0.01)


async def test_list_shows_ready_papers_and_in_flight_ingestions_newest_first(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.res.ingest_status.claim("2401.00001", source="arxiv", title="arXiv:2401.00001")
    async with h.client() as client:
        rows = (await client.get("/api/papers")).json()
    assert [(r["paper_id"], r["status"]) for r in rows] == [("2401.00001", "queued"), (PAPER_ID, "ready")]
    ready = rows[1]
    assert ready["title"] == "Attention Is All You Need"
    assert ready["progress"] == 1.0 and ready["stage"] == "done"


async def test_add_arxiv_paper_runs_ingestion_in_background(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    h.ingest.gate.clear()
    async with h.client() as client:
        response = await client.post("/api/papers", json={"arxiv": "https://arxiv.org/abs/1409.0473v7"})
        assert response.status_code == 202
        body = response.json()
        assert body["paper_id"] == "1409.0473"
        assert body["status"] == "queued"

        # The fake reports three stages, then parks on its gate in "chunking".
        processing = await _wait_for_status(client, "1409.0473", "processing", stage="chunking")
        assert 0 < processing["progress"] < 1

        h.ingest.gate.set()
        await _wait_for_status(client, "1409.0473", "ready")

        detail = (await client.get("/api/papers/1409.0473")).json()
    assert detail["summary"]["status"] == "ready"
    assert detail["meta"]["paper_id"] == "1409.0473"
    # The canonical id, not the pasted URL: the version the reader asked for, pinned.
    assert h.ingest.calls == [("arxiv", "1409.0473v7")]


async def test_adding_a_ready_paper_returns_it_without_reingesting(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.post("/api/papers", json={"arxiv": f"arXiv:{PAPER_ID}v5"})
    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert h.ingest.calls == []


async def test_adding_an_in_flight_paper_is_already_added_and_starts_no_second_job(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    h.ingest.gate.clear()
    async with h.client() as client:
        first = await client.post("/api/papers", json={"arxiv": "1409.0473"})
        second = await client.post("/api/papers", json={"arxiv": "1409.0473v2"})
        h.ingest.gate.set()
        await _wait_for_status(client, "1409.0473", "ready")
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["error"] == {
        "code": "already_added",
        "message": "arXiv:1409.0473 is already being added to your papers.",
        "paper_id": "1409.0473",
    }
    assert len(h.ingest.calls) == 1


async def test_uploading_a_pdf_that_is_still_being_added_is_already_added(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    h.ingest.gate.clear()
    files = {"file": ("attention.pdf", MINIMAL_PDF, "application/pdf")}
    async with h.client() as client:
        first = await client.post("/api/papers/upload", files=files)
        second = await client.post("/api/papers/upload", files=files)
        h.ingest.gate.set()
        await _wait_for_status(client, first.json()["paper_id"], "ready")
        third = await client.post("/api/papers/upload", files=files)
    assert first.status_code == 202
    assert second.status_code == 409
    error = second.json()["error"]
    assert error["code"] == "already_added" and error["paper_id"] == first.json()["paper_id"]
    assert "attention.pdf" in error["message"]
    assert third.status_code == 200 and third.json()["status"] == "ready", (
        "a ready paper comes back, no error"
    )
    assert len(h.ingest.calls) == 1


async def test_invalid_arxiv_id_is_a_readable_422(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.post("/api/papers", json={"arxiv": "not a paper id"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "invalid_input"
    assert "2401.12345" in error["message"]


async def test_failed_ingestion_shows_a_plain_message(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    h.ingest.fail_with = ArxivUnavailable("HTTP 503 from arxiv.org")
    async with h.client() as client:
        await client.post("/api/papers", json={"arxiv": "1409.0473"})
        row = await _wait_for_status(client, "1409.0473", "failed")
    assert row["error_code"] == "arxiv_unreachable"
    assert row["error"].startswith("Couldn’t reach arXiv.")
    assert "503" not in row["error"]


async def test_pdf_errors_are_shown_verbatim(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    h.ingest.fail_with = NoTextPDF("This PDF has no text layer (it is a scan).")
    async with h.client() as client:
        await client.post("/api/papers/upload", files={"file": ("scan.pdf", MINIMAL_PDF, "application/pdf")})
        rows = (await client.get("/api/papers")).json()
        while rows[0]["status"] != "failed":
            await asyncio.sleep(0.01)
            rows = (await client.get("/api/papers")).json()
    assert rows[0]["error"] == "This PDF has no text layer (it is a scan)."


async def test_upload_accepts_a_pdf_and_ingests_it(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    async with h.client() as client:
        response = await client.post(
            "/api/papers/upload", files={"file": ("../My Paper.pdf", MINIMAL_PDF, "application/octet-stream")}
        )
        assert response.status_code == 202
        body = response.json()
        assert body["paper_id"].startswith("up_") and len(body["paper_id"]) == 15
        assert body["title"] == "My Paper.pdf"
        await _wait_for_status(client, body["paper_id"], "ready")

        again = await client.post("/api/papers/upload", files={"file": ("copy.pdf", MINIMAL_PDF)})
    assert again.status_code == 200, "same bytes are the same paper"
    assert h.ingest.calls == [("upload", "My Paper.pdf")]


async def test_upload_rejects_files_that_are_not_pdfs(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.post(
            "/api/papers/upload", files={"file": ("paper.pdf", b"<html>not a pdf</html>", "application/pdf")}
        )
        empty = await client.post("/api/papers/upload", files={"file": ("empty.pdf", b"", "application/pdf")})
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "not_a_pdf"
    assert (
        response.json()["error"]["message"] == "That file isn’t a PDF. PaperMind reads PDFs and arXiv papers."
    )
    assert empty.status_code == 415


async def test_upload_size_limit_in_the_route(tmp_path: Path) -> None:
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    big = MINIMAL_PDF + b"x" * (1024 * 1024)
    async with h.client() as client:
        response = await client.post(
            "/api/papers/upload", files={"file": ("big.pdf", big, "application/pdf")}
        )
    assert response.status_code == 413
    assert "1 MB" in response.json()["error"]["message"]


async def test_upload_size_limit_from_content_length_before_reading(tmp_path: Path) -> None:
    h = make_harness(tmp_path, settings=make_settings(tmp_path, max_upload_mb=1))
    async with h.client() as client:
        response = await client.post(
            "/api/papers/upload",
            content=b"--x--",
            headers={
                "content-type": "multipart/form-data; boundary=x",
                "content-length": str(50 * 1024 * 1024),
            },
        )
    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "too_large",
        "message": "That PDF is 50 MB. The limit is 1 MB.",
    }


async def test_upload_without_a_file_is_a_validation_error(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.post("/api/papers/upload", data={"other": "x"})
    assert response.status_code == 422
    assert response.json()["error"] == {"code": "validation_error", "message": "file: is required"}


async def test_pdf_supports_range_requests(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        full = await client.get(f"/api/papers/{PAPER_ID}/pdf")
        partial = await client.get(f"/api/papers/{PAPER_ID}/pdf", headers={"Range": "bytes=0-99"})
    assert full.status_code == 200
    assert full.headers["content-type"] == "application/pdf"
    assert full.headers["accept-ranges"] == "bytes"
    assert full.headers["content-disposition"].startswith("inline")
    assert full.content == MINIMAL_PDF
    assert partial.status_code == 206
    assert partial.headers["content-range"] == f"bytes 0-99/{len(MINIMAL_PDF)}"
    assert partial.content == MINIMAL_PDF[:100]


async def test_pdf_of_a_paper_that_is_still_processing_is_409(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    await h.res.ingest_status.claim("1409.0473", source="arxiv", title="x")
    async with h.client() as client:
        response = await client.get("/api/papers/1409.0473/pdf")
        missing = await client.get("/api/papers/9999.99999/pdf")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "paper_not_ready"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "paper_not_found"


async def test_get_chunk(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    chunk_id = f"{PAPER_ID}:sec_3.2_scaled_dot_product_attention"
    async with h.client() as client:
        response = await client.get(f"/api/papers/{PAPER_ID}/chunks/{chunk_id}")
        missing = await client.get(f"/api/papers/{PAPER_ID}/chunks/{PAPER_ID}:sec_9_nope")
    assert response.status_code == 200
    body = response.json()
    assert body["chunk_id"] == chunk_id and body["section_number"] == "3.2"
    assert body["boxes"][0]["page"] == 3
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "chunk_not_found"


async def test_path_traversal_ids_never_reach_the_store(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.get("/api/papers/..%2F..%2Fetc/pdf")
        dotted = await client.get("/api/papers/.hidden")
    assert response.status_code in (404, 422)
    assert dotted.status_code == 422


async def test_suggestions_without_a_key_come_from_section_titles(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.get(f"/api/papers/{PAPER_ID}/suggestions", params={"level": "beginner"})
    assert response.status_code == 200
    body = response.json()
    assert body["level"] == "beginner"
    assert len(body["questions"]) == 4
    assert any("Model Architecture" in q for q in body["questions"])
    assert not any("Introduction" in q or "Abstract" in q for q in body["questions"])
    assert not (h.store.papers_dir / PAPER_ID / "suggestions.beginner.json").exists()


async def test_suggestions_are_generated_once_and_cached(tmp_path: Path) -> None:
    llm = FakeLLM()
    h = make_harness(tmp_path, llm=llm)
    async with h.client() as client:
        first, second = await asyncio.gather(
            client.get(f"/api/papers/{PAPER_ID}/suggestions?level=expert"),
            client.get(f"/api/papers/{PAPER_ID}/suggestions?level=expert"),
        )
        third = await client.get(f"/api/papers/{PAPER_ID}/suggestions?level=expert")
    assert (
        first.json()["questions"] == llm.questions == third.json()["questions"] == second.json()["questions"]
    )
    assert len(llm.calls) == 1
    cache = json.loads((h.store.papers_dir / PAPER_ID / "suggestions.expert.json").read_text())
    assert cache["questions"] == llm.questions
    prompt = llm.calls[0]["messages"][0]["content"]
    assert "Reader level: expert" in prompt and "3.2 Scaled Dot-Product Attention" in prompt
    assert llm.calls[0]["effort"] == "low"


async def test_suggestion_failure_falls_back_and_is_not_cached(tmp_path: Path) -> None:
    h = make_harness(tmp_path, llm=FakeLLM(fail=True))
    async with h.client() as client:
        response = await client.get(f"/api/papers/{PAPER_ID}/suggestions")
    assert response.status_code == 200
    assert len(response.json()["questions"]) == 4
    assert not (h.store.papers_dir / PAPER_ID / "suggestions.intermediate.json").exists()


async def test_short_model_answer_is_padded(tmp_path: Path) -> None:
    h = make_harness(tmp_path, llm=FakeLLM(["1. What is attention", "What is attention?"]))
    async with h.client() as client:
        questions = (await client.get(f"/api/papers/{PAPER_ID}/suggestions")).json()["questions"]
    assert questions[0] == "What is attention?"
    assert len(questions) == 4 and len(set(questions)) == 4


async def test_suggestions_reject_unknown_level(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.get(f"/api/papers/{PAPER_ID}/suggestions?level=wizard")
    assert response.status_code == 422
    assert response.json()["error"]["message"].startswith("level:")


async def test_delete_removes_paper_and_cancels_ingestion(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    h.ingest.gate.clear()
    async with h.client() as client:
        await client.post("/api/papers", json={"arxiv": "1409.0473"})
        await _wait_for_status(client, "1409.0473", "processing", stage="chunking")

        assert (await client.delete("/api/papers/1409.0473")).status_code == 204
        assert (await client.delete(f"/api/papers/{PAPER_ID}")).status_code == 204
        assert (await client.delete(f"/api/papers/{PAPER_ID}")).status_code == 404
        rows = (await client.get("/api/papers")).json()
    assert rows == []
    assert not h.res.jobs.running("1409.0473")
    assert not (h.store.papers_dir / PAPER_ID).exists()


@pytest.mark.parametrize("paper_id", ["up_0123456789ab", "hep-th_9901001"])
async def test_paper_ids_of_every_shape_route(tmp_path: Path, paper_id: str) -> None:
    h = make_harness(tmp_path, with_paper=False)
    h.store.save(make_record(paper_id), MINIMAL_PDF)
    async with h.client() as client:
        response = await client.get(f"/api/papers/{paper_id}")
    assert response.status_code == 200


class _SwallowingStatus(MemoryIngestStatusStore):
    """redis-py can absorb a CancelledError that lands mid-command and return normally."""

    async def update(self, paper_id: str, **changes: Any) -> bool:  # type: ignore[override]
        try:
            await asyncio.sleep(0.05)
        except asyncio.CancelledError:
            pass  # what the library does: the cancel request stays pending on the task
        return await super().update(paper_id, **changes)


def _jobs_reaching(status: MemoryIngestStatusStore, reached: list[str], discarded: list[str]) -> IngestJobs:
    async def ingest(raw: str, *, on_progress: Any = None) -> Any:
        await on_progress(IngestStage.DOWNLOADING, 0.5)
        reached.append("after first progress")
        await on_progress(IngestStage.EXTRACTING, 0.5)
        reached.append("after second progress")
        raise AssertionError("unreachable")

    async def discard(paper_id: str) -> None:
        discarded.append(paper_id)

    return IngestJobs(status, ingest_arxiv=ingest, ingest_upload=ingest, discard=discard)  # type: ignore[arg-type]


async def test_a_shutdown_cancel_swallowed_by_a_status_write_still_stops_the_job() -> None:
    reached: list[str] = []
    discarded: list[str] = []
    status = _SwallowingStatus()
    jobs = _jobs_reaching(status, reached, discarded)
    await status.claim("1409.0473", source="arxiv", title="x")
    task = jobs.start_arxiv("1409.0473", "1409.0473")
    await asyncio.sleep(0.01)  # inside the first status write

    await asyncio.wait_for(jobs.aclose(), 2)

    assert task.cancelled()
    assert reached == []
    assert discarded == [], "a shutdown keeps the entry and tells the reader why it stopped"
    final = await status.get("1409.0473")
    assert final is not None and final.status == "failed" and "interrupted" in (final.error or "")


async def test_a_removal_cancel_swallowed_by_a_status_write_still_stops_the_job() -> None:
    reached: list[str] = []
    discarded: list[str] = []
    status = _SwallowingStatus()
    jobs = _jobs_reaching(status, reached, discarded)
    await status.claim("1409.0473", source="arxiv", title="x")
    task = jobs.start_arxiv("1409.0473", "1409.0473")
    await asyncio.sleep(0.01)  # inside the first status write

    await asyncio.wait_for(jobs.cancel("1409.0473"), 2)

    assert task.cancelled()
    assert reached == []
    assert discarded == ["1409.0473"]


async def test_a_delete_served_by_another_worker_stops_the_ingestion_and_its_files(tmp_path: Path) -> None:
    """uvicorn --workers N: the DELETE lands on a worker that does not own the job."""
    h = make_harness(tmp_path, with_paper=False)
    worker_a_ingest = FakeIngest(h.store)
    worker_a_ingest.gate.clear()
    worker_a = IngestJobs(
        h.res.ingest_status,
        ingest_arxiv=worker_a_ingest.arxiv,
        ingest_upload=worker_a_ingest.upload,
        discard=h.res.papers.discard,
    )
    await h.res.ingest_status.claim("2401.00001", source="arxiv", title="arXiv:2401.00001")
    task = worker_a.start_arxiv("2401.00001", "2401.00001")

    async with h.client() as client:  # worker B
        await _wait_for_status(client, "2401.00001", "processing", stage="chunking")
        assert (await client.delete("/api/papers/2401.00001")).status_code == 204
        assert not h.res.jobs.running("2401.00001"), "worker B never had the job"

        worker_a_ingest.gate.set()  # worker A saves the paper, then reports progress
        await asyncio.wait_for(task, 2)
        rows = (await client.get("/api/papers")).json()

    assert rows == [], "the deleted paper must not come back as ready"
    assert not (h.store.papers_dir / "2401.00001").exists()


async def test_a_delete_after_the_last_progress_report_still_discards_the_saved_paper(
    tmp_path: Path,
) -> None:
    h = make_harness(tmp_path, with_paper=False)
    saved = asyncio.Event()
    publish = asyncio.Event()

    async def ingest(raw: str, *, on_progress: Any = None) -> Any:
        await on_progress(IngestStage.INDEXING, 1.0)
        record = make_record("2401.00001")
        h.store.save(record, MINIMAL_PDF)
        saved.set()
        await publish.wait()  # the DELETE (on another worker) lands here
        return record

    worker_a = IngestJobs(
        h.res.ingest_status, ingest_arxiv=ingest, ingest_upload=ingest, discard=h.res.papers.discard
    )  # type: ignore[arg-type]
    await h.res.ingest_status.claim("2401.00001", source="arxiv", title="arXiv:2401.00001")
    task = worker_a.start_arxiv("2401.00001", "2401.00001")
    await asyncio.wait_for(saved.wait(), 2)
    async with h.client() as client:
        assert (await client.delete("/api/papers/2401.00001")).status_code == 204
        h.store.save(make_record("2401.00001"), MINIMAL_PDF)  # a write still in flight on worker A
        publish.set()
        await asyncio.wait_for(task, 2)
        rows = (await client.get("/api/papers")).json()
    assert rows == []
    assert not (h.store.papers_dir / "2401.00001").exists()


async def test_an_ingestion_abandoned_by_a_dead_worker_shows_as_interrupted(tmp_path: Path) -> None:
    from datetime import UTC, datetime, timedelta

    from papermind.memory.ingest_status import STALE_AFTER_SECONDS, status_key

    h = make_harness(tmp_path, with_paper=False)
    await h.res.ingest_status.claim("1409.0473", source="arxiv", title="arXiv:1409.0473")
    long_ago = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS + 60)
    await h.redis.hset(
        status_key("1409.0473"), mapping={"status": "processing", "updated_at": long_ago.isoformat()}
    )
    async with h.client() as client:
        row = (await client.get("/api/papers")).json()[0]
        detail = (await client.get("/api/papers/1409.0473")).json()
        again = await client.post("/api/papers", json={"arxiv": "1409.0473"})
    assert row["status"] == "failed" and "interrupted" in row["error"]
    assert detail["summary"]["status"] == "failed"
    assert again.status_code == 202 and again.json()["status"] == "queued", "adding it again retries"


# ---------------------------------------------------------------- progress counts (A2)


async def test_stage_counts_reach_the_library_row_and_reset_with_the_stage(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    indexing = asyncio.Event()
    finish = asyncio.Event()

    async def ingest(raw: str, *, on_progress: Any = None) -> Any:
        await on_progress(IngestStage.EXTRACTING, 0.4, done=6, total=15)
        await on_progress(IngestStage.INDEXING, 0.5, done=106, total=212)
        indexing.set()
        await finish.wait()
        await on_progress(IngestStage.DONE, 1.0)
        record = make_record("2401.00001")
        h.store.save(record, MINIMAL_PDF)
        return record

    h.res.jobs = IngestJobs(
        h.res.ingest_status,
        ingest_arxiv=ingest,
        ingest_upload=ingest,  # type: ignore[arg-type]
        discard=h.res.papers.discard,
    )
    async with h.client() as client:
        await client.post("/api/papers", json={"arxiv": "2401.00001"})
        await asyncio.wait_for(indexing.wait(), 2)
        row = await _wait_for_status(client, "2401.00001", "processing", stage="indexing")
        assert row["stage_detail"] == {"done": 106, "total": 212}
        finish.set()
        ready = await _wait_for_status(client, "2401.00001", "ready")
    assert ready["stage_detail"] is None


async def test_without_counts_the_row_has_no_stage_detail(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    h.ingest.gate.clear()
    async with h.client() as client:
        await client.post("/api/papers", json={"arxiv": "1409.0473"})
        row = await _wait_for_status(client, "1409.0473", "processing", stage="chunking")
        h.ingest.gate.set()
        await _wait_for_status(client, "1409.0473", "ready")
    assert row["stage_detail"] is None and 0 < row["progress"] < 1


# ---------------------------------------------------------------- cancelling an ingestion (A10)


async def test_delete_mid_ingestion_removes_the_files_written_so_far(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)
    cache_root = tmp_path / "cache" / "embeddings"
    papers = AsyncPapers(h.store, derived_dirs=[cache_root])
    h.res.papers = papers
    partial_written = asyncio.Event()
    cancelled = asyncio.Event()

    async def ingest(raw: str, *, on_progress: Any = None) -> Any:
        await on_progress(IngestStage.DOWNLOADING, 1.0)
        (h.store.papers_dir / "2401.00001").mkdir(parents=True)
        (h.store.papers_dir / "2401.00001" / "source.pdf").write_bytes(MINIMAL_PDF)
        (cache_root / "2401.00001").mkdir(parents=True)
        (cache_root / "2401.00001" / "vectors.npy").write_bytes(b"partial")
        await on_progress(IngestStage.EXTRACTING, 0.0)
        partial_written.set()
        try:
            await asyncio.Event().wait()  # extracting, in no hurry
        except asyncio.CancelledError:
            cancelled.set()
            raise

    h.res.jobs = IngestJobs(
        h.res.ingest_status,
        ingest_arxiv=ingest,
        ingest_upload=ingest,  # type: ignore[arg-type]
        discard=papers.discard,
    )
    async with h.client() as client:
        assert (await client.post("/api/papers", json={"arxiv": "2401.00001"})).status_code == 202
        await asyncio.wait_for(partial_written.wait(), 2)

        assert (await client.delete("/api/papers/2401.00001")).status_code == 204
        await asyncio.wait_for(cancelled.wait(), 2)
        for _ in range(100):  # the job's own cleanup runs as it unwinds
            if not h.res.jobs.running("2401.00001"):
                break
            await asyncio.sleep(0.01)
        rows = (await client.get("/api/papers")).json()
        missing = await client.get("/api/papers/2401.00001")

    assert rows == [] and missing.status_code == 404
    assert not h.res.jobs.running("2401.00001")
    assert not (h.store.papers_dir / "2401.00001").exists()
    assert not (cache_root / "2401.00001").exists()
