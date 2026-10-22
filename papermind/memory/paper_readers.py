"""Whose library each paper is in.

    {root}/{paper_id}/{user_id}   -> empty marker file, one per reader

A paper added through the app is private to the readers who added it, arXiv
or upload alike. A paper with no readers was put there by the operator (the
setup script's starter papers, ``python -m papermind.ingest``) and is shared
with everyone, except an upload: with no readers it is nobody's, never
everyone's, so an upload can only fail closed.

Paper ids are deterministic (the arXiv id, or a hash of the PDF), so a second
reader who adds the same paper gets the same one; they are added as a reader
rather than told it exists.

On disk next to the papers, not in Redis: production Redis keeps no data across
restarts, and losing this would hide every paper from its owner. One empty
file per reader makes adding and removing a single create or unlink, so the
API workers need no lock between them.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from papermind.ingest.store import validate_paper_id

UPLOAD_PREFIX = "up_"
# papermind.memory.users.new_user_id: "u_" + token_urlsafe. The id becomes a
# file name, so anything else is refused before it reaches a path.
_READER_ID = re.compile(r"^u_[A-Za-z0-9_-]{1,64}$")


def is_upload(paper_id: str) -> bool:
    return paper_id.startswith(UPLOAD_PREFIX)


def valid_reader_id(reader_id: str | None) -> str | None:
    """The id if it is well formed, else None (treated like no reader at all)."""
    return reader_id if reader_id and _READER_ID.match(reader_id) else None


class PaperReaders:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _dir(self, paper_id: str) -> Path:
        return self.root / validate_paper_id(paper_id)

    def _marker(self, paper_id: str, reader_id: str) -> Path:
        if valid_reader_id(reader_id) is None:
            raise ValueError(f"invalid reader id: {reader_id!r}")
        return self._dir(paper_id) / reader_id

    def _add(self, paper_id: str, reader_id: str) -> None:
        marker = self._marker(paper_id, reader_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def _readers(self, paper_id: str) -> frozenset[str]:
        try:
            return frozenset(entry.name for entry in self._dir(paper_id).iterdir())
        except FileNotFoundError:
            return frozenset()

    async def add(self, paper_id: str, reader_id: str) -> None:
        await asyncio.to_thread(self._add, paper_id, reader_id)

    async def remove(self, paper_id: str, reader_id: str) -> None:
        await asyncio.to_thread(self._marker(paper_id, reader_id).unlink, missing_ok=True)

    async def readers(self, paper_id: str) -> frozenset[str]:
        """Who added the paper through the app; empty for a shared paper."""
        return await asyncio.to_thread(self._readers, paper_id)

    async def is_shared(self, paper_id: str) -> bool:
        return not is_upload(paper_id) and not await self.readers(paper_id)

    async def can_read(self, paper_id: str, reader_id: str | None) -> bool:
        readers = await self.readers(paper_id)
        if not readers:
            return not is_upload(paper_id)
        return valid_reader_id(reader_id) in readers
