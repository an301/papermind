"""Who may read an uploaded PDF.

    {root}/{paper_id}/{user_id}   -> empty marker file, one per reader

arXiv papers are public, so everyone who has the access code shares them. An
upload may be anything (a draft, a resume), so it is listed and served only to
the readers who uploaded it. Upload ids are content hashes, so a second reader
who uploads the same bytes gets the same paper; having the file is proof
enough, and they are added as a reader rather than told it exists.

On disk next to the papers, not in Redis: production Redis keeps no data across
restarts, and losing this would hide every upload from its owner. One empty
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

    def _marker(self, paper_id: str, reader_id: str) -> Path:
        validate_paper_id(paper_id)
        if valid_reader_id(reader_id) is None:
            raise ValueError(f"invalid reader id: {reader_id!r}")
        return self.root / paper_id / reader_id

    def _add(self, paper_id: str, reader_id: str) -> None:
        marker = self._marker(paper_id, reader_id)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()

    def _remove(self, paper_id: str, reader_id: str) -> int:
        marker = self._marker(paper_id, reader_id)
        marker.unlink(missing_ok=True)
        try:
            return sum(1 for _ in marker.parent.iterdir())
        except FileNotFoundError:
            return 0

    async def add(self, paper_id: str, reader_id: str) -> None:
        await asyncio.to_thread(self._add, paper_id, reader_id)

    async def remove(self, paper_id: str, reader_id: str) -> int:
        """Stop sharing the paper with this reader. Returns how many readers remain."""
        return await asyncio.to_thread(self._remove, paper_id, reader_id)

    async def can_read(self, paper_id: str, reader_id: str | None) -> bool:
        if not is_upload(paper_id):
            return True
        reader_id = valid_reader_id(reader_id)
        if reader_id is None:
            return False
        return await asyncio.to_thread(self._marker(paper_id, reader_id).is_file)
