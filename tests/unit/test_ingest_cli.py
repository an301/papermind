"""python -m papermind.ingest: id lists, uploads, failures, exit codes."""

from __future__ import annotations

import pytest

from papermind.ingest.__main__ import main, read_id_list, run
from papermind.ingest.pipeline import Ingestor
from papermind.ingest.store import PaperStore
from papermind.retrieval.dense import EmbeddingCache
from tests.unit.conftest import HashEmbedder


def test_read_id_list_skips_comments_and_blank_lines(tmp_path) -> None:
    listing = tmp_path / "papers.txt"
    listing.write_text(
        "# pinned versions\n\n1706.03762v7   # Attention Is All You Need\n  https://arxiv.org/abs/1810.04805v2\n"
    )
    assert read_id_list(listing) == ["1706.03762v7", "https://arxiv.org/abs/1810.04805v2"]


@pytest.fixture
def ingestor(tmp_path) -> Ingestor:
    return Ingestor(
        PaperStore(tmp_path / "papers"), embedder=HashEmbedder(), cache=EmbeddingCache(tmp_path / "emb")
    )


async def test_run_ingests_files_and_prints_a_summary(tmp_path, ingestor, two_column_pdf, capsys) -> None:
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(two_column_pdf)
    assert await run([], [pdf], ingestor=ingestor) == 0
    out = capsys.readouterr()
    line = out.out.strip()
    assert line.startswith("up_") and "2 pages" in line and "Synthetic Layouts for Testing Extraction" in line
    assert "extracting" in out.err and "done" in out.err  # progress goes to stderr


async def test_run_reports_failures_and_carries_on(tmp_path, ingestor, two_column_pdf, capsys) -> None:
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"<html>nope</html>")
    good = tmp_path / "good.pdf"
    good.write_bytes(two_column_pdf)
    missing = tmp_path / "missing.pdf"
    assert await run(["not an arxiv id"], [bad, missing, good], ingestor=ingestor) == 1
    out = capsys.readouterr()
    assert out.err.count("FAILED") == 3
    assert out.out.count("\n") == 1  # the good one still got ingested


def test_main_requires_something_to_ingest(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2
    assert "nothing to ingest" in capsys.readouterr().err
