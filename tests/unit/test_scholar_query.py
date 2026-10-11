import pytest

from papermind.scholar.query import PaperRef, classify, normalise_title, search_query, title_similarity


@pytest.mark.parametrize(
    ("query", "kind", "value", "api_id"),
    [
        ("1706.03762", "arxiv", "1706.03762", "ARXIV:1706.03762"),
        ("1706.03762v7", "arxiv", "1706.03762", "ARXIV:1706.03762"),
        ("arXiv:1409.0473", "arxiv", "1409.0473", "ARXIV:1409.0473"),
        ("ARXIV: 2106.15928v1", "arxiv", "2106.15928", "ARXIV:2106.15928"),
        ("https://arxiv.org/abs/1706.03762v5", "arxiv", "1706.03762", "ARXIV:1706.03762"),
        ("arxiv.org/pdf/1706.03762.pdf", "arxiv", "1706.03762", "ARXIV:1706.03762"),
        ("https://arxiv.org/html/2310.06825v1", "arxiv", "2310.06825", "ARXIV:2310.06825"),
        ("hep-th/9901001", "arxiv", "hep-th/9901001", "ARXIV:hep-th/9901001"),
        ("math.GT/0309136v2", "arxiv", "math.GT/0309136", "ARXIV:math.GT/0309136"),
        ("10.18653/v1/N18-3011", "doi", "10.18653/v1/N18-3011", "DOI:10.18653/v1/N18-3011"),
        ("doi: 10.1145/3292500.3330701", "doi", "10.1145/3292500.3330701", "DOI:10.1145/3292500.3330701"),
        ("https://doi.org/10.1038/nature14539.", "doi", "10.1038/nature14539", "DOI:10.1038/nature14539"),
        # arXiv's own DataCite DOIs resolve through the arXiv id.
        ("10.48550/arXiv.1706.03762", "arxiv", "1706.03762", "ARXIV:1706.03762"),
        (
            "204E3073870FAE3D05BCBC2F6A8E263D9B72E776",
            "s2",
            "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        ),
        (
            "https://www.semanticscholar.org/paper/Attention-is-All-you-Need-Vaswani/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "s2",
            "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        ),
        ("CorpusId:13756489", "corpus", "13756489", "CorpusId:13756489"),
        (
            "https://dl.acm.org/doi/10.1145/3292500.3330701",
            "url",
            "https://dl.acm.org/doi/10.1145/3292500.3330701",
            "URL:https://dl.acm.org/doi/10.1145/3292500.3330701",
        ),
        ("up_0123456789ab", "upload", "up_0123456789ab", None),
        ("Attention Is All You Need", "title", "Attention Is All You Need", None),
        ('  "Attention   Is All You Need"  ', "title", "Attention Is All You Need", None),
        ("BERT: Pre-training of Deep Bidirectional Transformers", "title", None, None),
        # 2007-2014 ids had four digits after the dot.
        ("0704.0001", "arxiv", "0704.0001", "ARXIV:0704.0001"),
        # Looks numeric but is not an arXiv id.
        ("1706.037", "title", "1706.037", None),
    ],
)
def test_classify(query: str, kind: str, value: str | None, api_id: str | None) -> None:
    ref = classify(query)
    assert ref.kind == kind
    if value is not None:
        assert ref.value == value
    assert ref.api_id == api_id


def test_title_ref_has_no_api_id() -> None:
    assert PaperRef("title", "anything").api_id is None


def test_search_query_drops_hyphens() -> None:
    # /paper/search finds nothing for hyphenated terms.
    assert (
        search_query("BERT: Pre-training of  Deep Bi-directional")
        == "BERT: Pre training of Deep Bi directional"
    )


def test_title_similarity_ignores_case_punctuation_and_accents() -> None:
    assert title_similarity("Attention Is All You Need", "Attention is All you Need") == 1.0
    assert title_similarity("Schrödinger's Cat!", "schrodinger s cat") == 1.0
    assert normalise_title("  Deep-Learning,  2015 ") == "deep learning 2015"


def test_title_similarity_is_low_for_a_vague_query() -> None:
    assert title_similarity("bert", "BERT: Pre-training of Deep Bidirectional Transformers") < 0.5
    assert title_similarity("", "anything") == 0.0
