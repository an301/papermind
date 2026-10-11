from papermind.schemas.tools import ScholarPaper
from papermind.scholar.reshape import (
    ABSTRACT_CHARS,
    MAX_AUTHORS,
    most_cited_first,
    papers_from_graph_page,
    to_scholar_paper,
    trim_text,
)

FULL = {
    "paperId": "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "externalIds": {"MAG": "2626778328", "ArXiv": "1706.03762", "CorpusId": 13756489},
    "url": "https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
    "title": "Attention is All you Need",
    "venue": "Neural Information Processing Systems",
    "year": 2017,
    "citationCount": 196005,
    "referenceCount": 41,
    "abstract": "The dominant sequence transduction models are based on complex recurrent networks.",
    "tldr": {"model": "tldr@v2.0.0", "text": "A new simple network architecture, the Transformer."},
    "authors": [{"authorId": str(i), "name": f"Author {i}"} for i in range(8)],
}


def test_full_record() -> None:
    paper = to_scholar_paper(FULL)
    assert paper == ScholarPaper(
        s2_id="204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        title="Attention is All you Need",
        year=2017,
        authors=[f"Author {i}" for i in range(MAX_AUTHORS)],
        venue="Neural Information Processing Systems",
        abstract="The dominant sequence transduction models are based on complex recurrent networks.",
        tldr="A new simple network architecture, the Transformer.",
        citation_count=196005,
        url="https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        arxiv_id="1706.03762",
    )


def test_sparse_record_uses_nones_not_empty_strings() -> None:
    paper = to_scholar_paper(
        {
            "paperId": "abc",
            "title": "  ",
            "venue": "",
            "abstract": None,
            "tldr": None,
            "externalIds": None,
            "authors": [{"authorId": None, "name": ""}, {"name": "Real Name"}, "junk"],
            "year": None,
            "citationCount": True,  # not a count
        }
    )
    assert paper is not None
    assert paper.title == "(untitled)"
    assert paper.venue is None
    assert paper.abstract is None
    assert paper.tldr is None
    assert paper.arxiv_id is None
    assert paper.authors == ["Real Name"]
    assert paper.citation_count is None


def test_records_without_an_s2_id_are_dropped() -> None:
    assert to_scholar_paper(None) is None
    assert to_scholar_paper({}) is None
    assert to_scholar_paper({"paperId": None, "title": "Unresolved reference"}) is None


def test_trim_text_cuts_at_a_word_boundary() -> None:
    long = ("word " * 400).strip()
    trimmed = trim_text(long)
    assert trimmed is not None
    assert len(trimmed) <= ABSTRACT_CHARS + 1
    assert trimmed.endswith("word…")
    assert trim_text("short  text\n here") == "short text here"
    assert trim_text("   ") is None
    assert trim_text(None) is None


def test_trim_text_hard_cuts_one_giant_token() -> None:
    trimmed = trim_text("x" * 5000, limit=100)
    assert trimmed == "x" * 100 + "…"


def test_graph_page_unwraps_skips_unresolved_and_dedupes() -> None:
    page = {
        "offset": 0,
        "next": 4,
        "data": [
            {"citedPaper": {"paperId": "a", "title": "A", "citationCount": 5}},
            {"citedPaper": {"paperId": None, "title": "Not in the corpus"}},
            {"citedPaper": None},
            {"citedPaper": {"paperId": "a", "title": "A again"}},
            {"citedPaper": {"paperId": "b", "title": "B", "citationCount": 50}},
        ],
    }
    papers = papers_from_graph_page(page, "citedPaper")
    assert [p.s2_id for p in papers] == ["a", "b"]
    assert papers_from_graph_page(page, "citingPaper") == []
    assert papers_from_graph_page({"data": None}, "citedPaper") == []
    assert papers_from_graph_page(None, "citedPaper") == []


def test_most_cited_first_is_stable_and_puts_unknown_counts_last() -> None:
    papers = [
        ScholarPaper(s2_id="none", title="n", citation_count=None),
        ScholarPaper(s2_id="low", title="l", citation_count=1),
        ScholarPaper(s2_id="high", title="h", citation_count=100),
        ScholarPaper(s2_id="low2", title="l2", citation_count=1),
    ]
    assert [p.s2_id for p in most_cited_first(papers)] == ["high", "low", "low2", "none"]
