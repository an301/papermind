"""Orchestration: arXiv id in, chunks on disk out.

Async because the network stages benefit and the FastAPI service will call this
directly when a user opens a paper it has not seen. PyMuPDF is synchronous and
CPU-bound, so parsing goes through `asyncio.to_thread` -- the same rule the
project applies to local model inference: blocking work never runs inline on
the event loop.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import httpx

from papermind.ingest.arxiv import (
    ArxivError,
    build_meta,
    download_pdf,
    fetch_metadata,
    normalize_arxiv_id,
)
from papermind.ingest.chunker import chunk_paper
from papermind.ingest.extract import extract_lines
from papermind.ingest.ids import CHUNKER_VERSION
from papermind.ingest.models import Chunk, IngestResult
from papermind.ingest.sections import split_into_sections

DEFAULT_MAX_CHUNK_TOKENS = 800


def _parse_pdf(pdf_path: Path, paper_id: str, max_chunk_tokens: int) -> tuple[list[Chunk], int]:
    """Blocking parse+chunk. Runs in a worker thread."""
    lines, n_pages = extract_lines(pdf_path)
    sections = split_into_sections(lines)
    return chunk_paper(paper_id, sections, max_chunk_tokens), n_pages


async def ingest_paper(
    raw_id: str,
    *,
    client: httpx.AsyncClient,
    pdf_dir: Path,
    max_chunk_tokens: int = DEFAULT_MAX_CHUNK_TOKENS,
    force: bool = False,
) -> IngestResult:
    """Download, parse, and chunk one arXiv paper.

    If `raw_id` pins a version ('1706.03762v5') that version is fetched;
    otherwise the current version is resolved and recorded, so the run can be
    reproduced later by passing the versioned id back.
    """
    paper_id, pinned_version = normalize_arxiv_id(raw_id)

    entry = await fetch_metadata(client, paper_id)
    version = pinned_version or entry.version
    versioned_id = f"{paper_id}v{version}"

    pdf_path = pdf_dir / f"{versioned_id.replace('/', '_')}.pdf"
    pdf_path, sha256 = await download_pdf(client, versioned_id, pdf_path, force=force)

    chunks, n_pages = await asyncio.to_thread(_parse_pdf, pdf_path, paper_id, max_chunk_tokens)
    if not chunks:
        raise ArxivError(f"{versioned_id}: extracted no text -- is the PDF scanned images?")

    meta = build_meta(
        paper_id=paper_id,
        version=version,
        entry=entry,
        pdf_sha256=sha256,
        n_pages=n_pages,
    )

    return IngestResult(
        chunker_version=CHUNKER_VERSION,
        ingested_at=datetime.now(UTC).isoformat(timespec="seconds"),
        meta=meta,
        max_chunk_tokens=max_chunk_tokens,
        chunks=chunks,
    )


def apply_exact_token_counts(result: IngestResult, model: str) -> IngestResult:
    """Replace estimated token counts with exact ones from the Messages API.

    One network call per chunk, so this is opt-in (`--exact-tokens`). It only
    rewrites `token_count` and `token_counter`; chunk boundaries and IDs were
    already fixed by the estimate and are deliberately not recomputed -- doing
    so would make IDs depend on whether the flag was passed.
    """
    from papermind.ingest.tokens import AnthropicTokenCounter

    counter = AnthropicTokenCounter(model=model)
    for chunk in result.chunks:
        chunk.token_count = counter.count(chunk.text)
        chunk.token_counter = "anthropic"
    return result
