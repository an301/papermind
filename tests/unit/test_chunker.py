"""Chunking: section-first, paragraph fallback, character backstop."""

from papermind.ingest.chunker import chunk_paper, split_section_text
from papermind.ingest.models import Line, Section


def make_section(paragraphs: list[str], *, number: str | None = "3.2", title: str = "Attention"):
    """Build a Section whose line geometry yields the given paragraphs.

    Paragraph grouping keys off vertical gaps, so consecutive lines sit 10pt
    apart and a paragraph break is 40pt.
    """
    section = Section(number=number, title=title, page_start=1)
    y = 0.0
    for index, paragraph in enumerate(paragraphs):
        if index:
            y += 40.0
        for sentence in paragraph.split(" | "):
            section.add(Line(text=sentence, size=10.0, bold=False, page=1, y=y, x0=100.0))
            y += 10.0
    return section


def test_section_that_fits_is_a_single_chunk() -> None:
    section = make_section(["Short body text about attention."])
    assert len(split_section_text(section, max_tokens=800)) == 1


def test_long_section_splits_on_paragraph_boundaries() -> None:
    paragraph = "word " * 200          # ~1000 chars, ~250 tokens
    section = make_section([paragraph.strip()] * 4)
    parts = split_section_text(section, max_tokens=300)
    assert len(parts) > 1
    # Paragraph packing must not cut inside a paragraph: every part is whole
    # 'word' tokens, never a fragment.
    assert all(not part.startswith("ord") for part in parts)


def test_oversized_single_paragraph_falls_back_to_character_splitting() -> None:
    # One paragraph, no internal breaks, far over budget -- the References case.
    giant = "Vaswani et al. Attention is all you need. NeurIPS 2017. " * 80
    section = make_section([giant.strip()], number=None, title="References")
    parts = split_section_text(section, max_tokens=100)
    assert len(parts) > 1
    assert "".join(p.replace(" ", "") for p in parts) == giant.strip().replace(" ", "")


def test_no_chunk_exceeds_the_budget() -> None:
    from papermind.ingest.tokens import estimate_tokens

    section = make_section(["sentence text here " * 40] * 10)
    for part in split_section_text(section, max_tokens=250):
        assert estimate_tokens(part) <= 250


def test_furniture_is_appended_after_prose_not_interleaved() -> None:
    """A caption must not land in the middle of the sentence it interrupted."""
    section = Section(number="3.2", title="Attention", page_start=1)
    section.add(Line(text="The output is computed as a weighted sum", size=10, bold=False,
                     page=1, y=0, x0=100))
    section.add(Line(text="Figure 2: Scaled Dot-Product Attention.", size=9, bold=False,
                     page=1, y=10, x0=100, furniture=True))
    section.add(Line(text="of the values, weighted by compatibility.", size=10, bold=False,
                     page=1, y=20, x0=100))

    text = split_section_text(section, max_tokens=800)[0]
    assert "weighted sum of the values" in text
    assert text.index("Figure 2") > text.index("of the values")


def test_chunk_ids_are_stable_across_runs() -> None:
    """The guarantee the eval labels rest on."""
    sections = [
        make_section(["Body of attention section."], number="3.2", title="Attention"),
        make_section(["Body of conclusion."], number="7", title="Conclusion"),
    ]
    first = [c.id for c in chunk_paper("1706.03762", sections, 800)]
    second = [c.id for c in chunk_paper("1706.03762", sections, 800)]
    assert first == second
    assert first[0] == "1706.03762:sec_3.2_attention"


def test_inserting_a_section_does_not_move_other_ids() -> None:
    attention = make_section(["Body."], number="3.2", title="Attention")
    conclusion = make_section(["Body."], number="7", title="Conclusion")
    intro = make_section(["Body."], number="1", title="Introduction")

    without = {c.id for c in chunk_paper("p", [attention, conclusion], 800)}
    with_intro = {c.id for c in chunk_paper("p", [intro, attention, conclusion], 800)}
    assert without <= with_intro


def test_chunk_records_carry_the_documented_fields() -> None:
    section = make_section(["Body text."])
    chunk = chunk_paper("1706.03762", [section], 800)[0]
    assert chunk.paper_id == "1706.03762"
    assert chunk.section_title == "Attention"
    assert chunk.section_number == "3.2"
    assert chunk.token_count > 0
    assert chunk.char_count == len(chunk.text)
