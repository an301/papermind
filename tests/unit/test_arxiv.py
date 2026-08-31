"""arXiv id parsing. Users paste all of these shapes."""

import pytest
from papermind.ingest.arxiv import ArxivError, normalize_arxiv_id


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1706.03762", ("1706.03762", None)),
        ("1706.03762v7", ("1706.03762", 7)),
        ("arXiv:1706.03762", ("1706.03762", None)),
        ("arXiv:1706.03762v5", ("1706.03762", 5)),
        ("https://arxiv.org/abs/1706.03762", ("1706.03762", None)),
        ("https://arxiv.org/pdf/1706.03762v7", ("1706.03762", 7)),
        ("  1706.03762  ", ("1706.03762", None)),
        ("cs.CL/0501001", ("cs.CL/0501001", None)),
    ],
)
def test_normalize(raw: str, expected: tuple[str, int | None]) -> None:
    assert normalize_arxiv_id(raw) == expected


def test_rejects_nonsense() -> None:
    with pytest.raises(ArxivError):
        normalize_arxiv_id("not-a-paper")
