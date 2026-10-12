"""On-disk paper store: data/papers/{paper_id}/paper.json + source.pdf.

paper.json is the commit point. Ingestion writes the PDF first and paper.json
last, each through a temp file and ``os.replace`` (atomic on POSIX), so a crash
or a concurrent reader never sees a half-written record: a directory either
holds a complete paper or no paper.json at all.

Every method is synchronous file I/O. The API calls them via
``asyncio.to_thread`` (see ``papermind.api.deps.AsyncPapers``); the retriever
does the same.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from pydantic import ValidationError

from papermind.schemas.paper import Chunk, PaperMeta, PaperRecord

RECORD_FILE = "paper.json"
PDF_FILE = "source.pdf"

# Paper ids become directory names and URL path segments. Anything else (a
# '..', a slash) is refused before it gets near the filesystem.
_PAPER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidPaperId(ValueError):
    pass


def validate_paper_id(paper_id: str) -> str:
    if not _PAPER_ID.match(paper_id) or ".." in paper_id:
        raise InvalidPaperId(f"invalid paper id: {paper_id!r}")
    return paper_id


def write_atomic(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so readers see the old file or the new one, never a mix."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


class PaperStore:
    """Ingested papers on disk.

    ``meta`` and ``list`` serve the library page, which is polled. paper.json
    for a long paper is a few hundred KB, so parsed metadata is memoised per
    file, keyed by (mtime_ns, size): a rewrite by another process (the CLI
    re-ingesting while the API runs) is picked up on the next call.
    """

    def __init__(self, papers_dir: Path) -> None:
        self.papers_dir = papers_dir
        self._meta_cache: dict[str, tuple[tuple[int, int], PaperMeta]] = {}
        self._lock = threading.Lock()

    def _dir(self, paper_id: str) -> Path:
        return self.papers_dir / validate_paper_id(paper_id)

    def record_path(self, paper_id: str) -> Path:
        return self._dir(paper_id) / RECORD_FILE

    def pdf_path(self, paper_id: str) -> Path:
        return self._dir(paper_id) / PDF_FILE

    def save_pdf(self, paper_id: str, pdf: bytes) -> None:
        """Put the PDF in place before processing, so a reader can open the paper
        early ("Open now") while it is still being indexed. The paper only joins
        the library when ``save`` writes its record."""
        directory = self._dir(paper_id)
        directory.mkdir(parents=True, exist_ok=True)
        write_atomic(directory / PDF_FILE, pdf)

    def save(self, record: PaperRecord, pdf: bytes) -> None:
        paper_id = record.meta.paper_id
        directory = self._dir(paper_id)
        directory.mkdir(parents=True, exist_ok=True)
        write_atomic(directory / PDF_FILE, pdf)
        write_atomic(directory / RECORD_FILE, record.model_dump_json(indent=1).encode("utf-8"))
        with self._lock:
            self._meta_cache.pop(paper_id, None)

    def load(self, paper_id: str) -> PaperRecord | None:
        try:
            path = self.record_path(paper_id)
        except InvalidPaperId:
            return None
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        return PaperRecord.model_validate_json(raw)

    def meta(self, paper_id: str) -> PaperMeta | None:
        try:
            path = self.record_path(paper_id)
            stat = path.stat()
        except (InvalidPaperId, FileNotFoundError):
            return None
        key = (stat.st_mtime_ns, stat.st_size)
        with self._lock:
            cached = self._meta_cache.get(paper_id)
        if cached is not None and cached[0] == key:
            return cached[1]
        record = self.load(paper_id)
        if record is None:
            return None
        with self._lock:
            self._meta_cache[paper_id] = (key, record.meta)
        return record.meta

    def list(self) -> list[PaperMeta]:
        """Every complete paper, most recently ingested first.

        A directory without a readable paper.json (an ingestion that died
        before its commit, a record from an incompatible schema) is skipped
        rather than failing the whole library.
        """
        if not self.papers_dir.is_dir():
            return []
        metas: list[PaperMeta] = []
        for entry in sorted(self.papers_dir.iterdir()):
            if not entry.is_dir() or not _PAPER_ID.match(entry.name):
                continue
            try:
                meta = self.meta(entry.name)
            except (ValidationError, ValueError, OSError):
                continue
            if meta is not None:
                metas.append(meta)
        return sorted(metas, key=lambda m: (m.ingested_at, m.paper_id), reverse=True)

    def delete(self, paper_id: str) -> None:
        try:
            directory = self._dir(paper_id)
        except InvalidPaperId:
            return
        shutil.rmtree(directory, ignore_errors=True)
        with self._lock:
            self._meta_cache.pop(paper_id, None)

    def chunk(self, paper_id: str, chunk_id: str) -> Chunk | None:
        record = self.load(paper_id)
        if record is None:
            return None
        return next((c for c in record.chunks if c.chunk_id == chunk_id), None)
