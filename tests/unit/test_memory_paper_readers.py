from __future__ import annotations

from pathlib import Path

import pytest

from papermind.memory.paper_readers import PaperReaders


async def test_arxiv_papers_are_everyones_and_uploads_only_their_readers(tmp_path: Path) -> None:
    readers = PaperReaders(tmp_path)
    await readers.add("up_0123456789ab", "u_alice")
    assert await readers.can_read("1706.03762", None)
    assert await readers.can_read("up_0123456789ab", "u_alice")
    assert not await readers.can_read("up_0123456789ab", "u_bob")
    assert not await readers.can_read("up_0123456789ab", None)


async def test_remove_counts_the_readers_left(tmp_path: Path) -> None:
    readers = PaperReaders(tmp_path)
    await readers.add("up_0123456789ab", "u_alice")
    await readers.add("up_0123456789ab", "u_bob")
    assert await readers.remove("up_0123456789ab", "u_alice") == 1
    assert await readers.remove("up_0123456789ab", "u_alice") == 1, "idempotent"
    assert await readers.remove("up_0123456789ab", "u_bob") == 0


@pytest.mark.parametrize("reader_id", ["../u_alice", "u_a/b", "alice", "u_", ""])
async def test_a_malformed_reader_id_never_becomes_a_path(tmp_path: Path, reader_id: str) -> None:
    readers = PaperReaders(tmp_path / "readers")
    assert not await readers.can_read("up_0123456789ab", reader_id)
    with pytest.raises(ValueError):
        await readers.add("up_0123456789ab", reader_id)
    assert not (tmp_path / "readers").exists()
