from __future__ import annotations

from pathlib import Path

import pytest

from papermind.memory.paper_readers import PaperReaders


async def test_a_paper_nobody_added_is_shared_except_an_upload(tmp_path: Path) -> None:
    readers = PaperReaders(tmp_path)
    assert await readers.is_shared("1706.03762")
    assert await readers.can_read("1706.03762", None)
    assert not await readers.is_shared("up_0123456789ab")
    assert not await readers.can_read("up_0123456789ab", "u_alice"), "an upload fails closed"


@pytest.mark.parametrize("paper_id", ["2401.00001", "up_0123456789ab"])
async def test_a_paper_readers_added_is_theirs_alone(tmp_path: Path, paper_id: str) -> None:
    readers = PaperReaders(tmp_path)
    await readers.add(paper_id, "u_alice")
    assert not await readers.is_shared(paper_id)
    assert await readers.can_read(paper_id, "u_alice")
    assert not await readers.can_read(paper_id, "u_bob")
    assert not await readers.can_read(paper_id, None)


async def test_remove_takes_one_reader_and_is_idempotent(tmp_path: Path) -> None:
    readers = PaperReaders(tmp_path)
    await readers.add("up_0123456789ab", "u_alice")
    await readers.add("up_0123456789ab", "u_bob")
    await readers.remove("up_0123456789ab", "u_alice")
    await readers.remove("up_0123456789ab", "u_alice")
    assert await readers.readers("up_0123456789ab") == {"u_bob"}


@pytest.mark.parametrize("reader_id", ["../u_alice", "u_a/b", "alice", "u_", ""])
async def test_a_malformed_reader_id_never_becomes_a_path(tmp_path: Path, reader_id: str) -> None:
    readers = PaperReaders(tmp_path / "readers")
    assert not await readers.can_read("up_0123456789ab", reader_id)
    with pytest.raises(ValueError):
        await readers.add("up_0123456789ab", reader_id)
    assert not (tmp_path / "readers").exists()
