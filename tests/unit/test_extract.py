"""Line reassembly: hyphen repair and whitespace."""

from papermind.ingest.extract import join_lines
from papermind.ingest.models import Line


def lines(*texts: str) -> list[Line]:
    return [Line(text=t, size=10.0, bold=False, page=1, y=float(i * 10), x0=100.0)
            for i, t in enumerate(texts)]


def test_hyphenated_line_break_is_rejoined() -> None:
    assert join_lines(lines("the atten-", "tion mechanism")) == "the attention mechanism"


def test_genuine_compound_across_a_line_break_keeps_its_hyphen() -> None:
    """'Multi-' + 'Head' is a real hyphen; the next line starts uppercase."""
    assert join_lines(lines("Multi-", "Head Attention")) == "Multi- Head Attention"


def test_lines_are_joined_with_single_spaces() -> None:
    assert join_lines(lines("one   two", "three")) == "one two three"


def test_empty_input() -> None:
    assert join_lines([]) == ""
