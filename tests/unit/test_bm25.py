"""The lexical arm. rank_bm25 is pure Python, so these run offline."""

from papermind.ingest.models import Chunk
from papermind.retrieval.bm25 import Bm25Index
from papermind.retrieval.text import indexable_text, tokenize


def chunk(chunk_id: str, title: str, text: str) -> Chunk:
    return Chunk(
        id=chunk_id,
        paper_id="p",
        section_title=title,
        text=text,
        token_count=len(text) // 4 or 1,
        char_count=len(text),
        page_start=1,
        page_end=1,
    )


CHUNKS = [
    chunk("p:sec_3.2_attention", "Attention",
          "An attention function maps a query and key-value pairs to an output."),
    chunk("p:sec_3.5_positional_encoding", "Positional Encoding",
          "We add positional encodings using sine and cosine functions of different "
          "frequencies so the model can use the order of the sequence."),
    chunk("p:sec_5.2_hardware", "Hardware and Schedule",
          "We trained on one machine with eight NVIDIA P100 GPUs for twelve hours."),
]


def test_lexical_match_wins() -> None:
    index = Bm25Index(CHUNKS)
    assert index.rank("P100 GPUs", limit=3)[0] == "p:sec_5.2_hardware"


def test_zero_score_chunks_are_dropped() -> None:
    """A chunk with no query term must not get an arbitrary rank in the fusion."""
    index = Bm25Index(CHUNKS)
    assert index.rank("kangaroo marsupial", limit=10) == []


def test_limit_is_respected() -> None:
    index = Bm25Index(CHUNKS)
    assert len(index.rank("the model", limit=1)) <= 1


def test_empty_query_returns_nothing() -> None:
    assert Bm25Index(CHUNKS).rank("", limit=5) == []


def test_empty_corpus_is_safe() -> None:
    assert Bm25Index([]).rank("attention", limit=5) == []


def test_results_are_deterministic() -> None:
    index = Bm25Index(CHUNKS)
    assert index.rank("attention output", limit=3) == index.rank("attention output", limit=3)


def test_section_title_is_searchable() -> None:
    """The title is indexed, so a query matching only the title still hits."""
    index = Bm25Index(CHUNKS)
    assert "p:sec_5.2_hardware" in index.rank("schedule", limit=3)


def test_hyphenated_compounds_split_for_recall() -> None:
    assert tokenize("self-attention") == ["self", "attention"]


def test_single_character_tokens_survive() -> None:
    """Q, K and V are the names of the things being asked about."""
    assert tokenize("the matrices Q K and V") == ["the", "matrices", "q", "k", "and", "v"]


def test_indexable_text_prefixes_the_title() -> None:
    text = indexable_text(CHUNKS[0])
    assert text.startswith("Attention\n\n")
    assert CHUNKS[0].text in text
