"""PaperStore: atomic writes, lookups, listing, and refusing unsafe ids."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from papermind.ingest.store import (
    PDF_FILE,
    RECORD_FILE,
    InvalidPaperId,
    PaperStore,
    validate_paper_id,
    write_atomic,
)
from papermind.schemas.paper import PaperRecord
from tests.unit.conftest import TRANSFORMER_LIKE, make_record

PDF = b"%PDF-1.7 not really a pdf, but the store does not care"


@pytest.fixture
def store(tmp_path) -> PaperStore:
    return PaperStore(tmp_path / "papers")


def test_save_writes_record_and_pdf_and_leaves_no_temp_files(store: PaperStore) -> None:
    record = make_record("1706.03762", TRANSFORMER_LIKE)
    store.save(record, PDF)
    directory = store.papers_dir / "1706.03762"
    assert sorted(p.name for p in directory.iterdir()) == sorted([PDF_FILE, RECORD_FILE])
    assert store.pdf_path("1706.03762").read_bytes() == PDF
    assert store.load("1706.03762") == record


def test_meta_list_and_chunk_lookup(store: PaperStore) -> None:
    older = make_record("up_aaaaaaaaaaaa", TRANSFORMER_LIKE[:3], title="Older")
    newer = make_record("1706.03762", TRANSFORMER_LIKE, title="Newer")
    newer.meta.ingested_at = datetime(2026, 6, 1, tzinfo=UTC)
    store.save(older, PDF)
    store.save(newer, PDF)

    meta = store.meta("1706.03762")
    assert meta is not None and meta.title == "Newer" and meta.chunk_count == len(TRANSFORMER_LIKE)
    assert [m.paper_id for m in store.list()] == ["1706.03762", "up_aaaaaaaaaaaa"]  # newest first

    chunk_id = "1706.03762:sec_3.2.1_scaled_dot_product_attention"
    chunk = store.chunk("1706.03762", chunk_id)
    assert chunk is not None and chunk.section_number == "3.2.1"
    assert store.chunk("1706.03762", "1706.03762:sec_99_nope") is None
    assert store.chunk("missing", chunk_id) is None


def test_meta_cache_sees_a_rewrite(store: PaperStore) -> None:
    store.save(make_record("p1", TRANSFORMER_LIKE, title="First"), PDF)
    assert store.meta("p1").title == "First"  # type: ignore[union-attr]
    # Another process (the CLI) re-ingests behind this store's back.
    path = store.record_path("p1")
    rewritten = make_record("p1", TRANSFORMER_LIKE, title="Second")
    write_atomic(path, rewritten.model_dump_json().encode())
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    assert store.meta("p1").title == "Second"  # type: ignore[union-attr]


def test_missing_and_broken_papers(store: PaperStore) -> None:
    assert store.load("nope") is None and store.meta("nope") is None
    assert store.list() == []
    store.save(make_record("good", TRANSFORMER_LIKE[:2]), PDF)
    # An ingestion that died before its commit point: a PDF but no paper.json.
    (store.papers_dir / "half").mkdir()
    (store.papers_dir / "half" / PDF_FILE).write_bytes(PDF)
    # A record from an incompatible schema.
    (store.papers_dir / "old").mkdir()
    (store.papers_dir / "old" / RECORD_FILE).write_text('{"meta": {"paper_id": "old"}}')
    assert [m.paper_id for m in store.list()] == ["good"]


def test_delete_removes_the_directory(store: PaperStore) -> None:
    store.save(make_record("p1", TRANSFORMER_LIKE[:2]), PDF)
    store.delete("p1")
    assert not (store.papers_dir / "p1").exists()
    assert store.meta("p1") is None
    store.delete("p1")  # idempotent
    store.delete("../etc")  # refused quietly


@pytest.mark.parametrize("bad", ["", "..", "../x", "a/b", ".hidden", "x" * 200, "sp ace"])
def test_unsafe_ids_never_reach_the_filesystem(store: PaperStore, bad: str) -> None:
    with pytest.raises(InvalidPaperId):
        validate_paper_id(bad)
    with pytest.raises(InvalidPaperId):
        store.pdf_path(bad)
    assert store.load(bad) is None
    assert store.meta(bad) is None


@pytest.mark.parametrize("good", ["1706.03762", "up_0123456789ab", "hep-th_9901001"])
def test_real_ids_are_valid(good: str) -> None:
    assert validate_paper_id(good) == good


def test_write_atomic_keeps_the_old_file_when_the_write_fails(tmp_path, monkeypatch) -> None:
    target = tmp_path / "paper.json"
    target.write_bytes(b"old")

    def broken_replace(src, dst):  # the rename is the commit point
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_atomic(target, b"new")
    assert target.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir()] == ["paper.json"]  # temp file cleaned up


def test_record_round_trips_through_json(store: PaperStore) -> None:
    record = make_record("1706.03762", TRANSFORMER_LIKE)
    store.save(record, PDF)
    raw = store.record_path("1706.03762").read_bytes()
    assert PaperRecord.model_validate_json(raw) == record
