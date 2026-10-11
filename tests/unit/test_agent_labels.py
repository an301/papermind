from __future__ import annotations

from papermind.agent.labels import (
    MAX_EXTERNAL,
    MAX_SOURCES,
    citation_labels,
    finalize_citations,
    label_external,
    label_sources,
    resolve_external,
    resolve_sources,
    strip_citations,
    top_retrieved,
)
from papermind.schemas.tools import ScholarPaper
from tests.unit.fakes import BLEU_PAPER, make_chunk


def seen(*chunks):
    return {c.chunk_id: c for c in chunks}


A = make_chunk("a", "alpha " * 100, number="1", rank=2)
B = make_chunk("b", "beta", number="2", rank=1)
C = make_chunk("c", "gamma", number="3", rank=1)


def test_compose_ids_are_used_in_the_order_given() -> None:
    chunks, dropped = resolve_sources([C.chunk_id, A.chunk_id], seen(A, B, C), fallback_k=5)
    assert [c.chunk_id for c in chunks] == [C.chunk_id, A.chunk_id]
    assert dropped == []


def test_ids_not_retrieved_this_turn_are_dropped() -> None:
    chunks, dropped = resolve_sources(["made_up", B.chunk_id, B.chunk_id], seen(A, B), fallback_k=5)
    assert [c.chunk_id for c in chunks] == [B.chunk_id]
    assert dropped == ["made_up"]


def test_all_invented_ids_fall_back_to_the_top_retrieved() -> None:
    chunks, dropped = resolve_sources(["x", "y"], seen(A, B, C), fallback_k=2)
    # Rank-1 hits first, in first-seen order; then rank 2.
    assert [c.chunk_id for c in chunks] == [B.chunk_id, C.chunk_id]
    assert dropped == ["x", "y"]


def test_no_compose_answer_falls_back_too() -> None:
    chunks, _ = resolve_sources(None, seen(A, B, C), fallback_k=5)
    assert [c.chunk_id for c in chunks] == [B.chunk_id, C.chunk_id, A.chunk_id]


def test_an_explicitly_empty_list_means_no_sources() -> None:
    assert resolve_sources([], seen(A, B), fallback_k=5) == ([], [])


def test_sources_are_capped() -> None:
    many = [make_chunk(f"c{i}", "t", number=str(i)) for i in range(12)]
    chunks, _ = resolve_sources([c.chunk_id for c in many], seen(*many), fallback_k=5)
    assert len(chunks) == MAX_SOURCES
    assert len(top_retrieved(seen(*many), 3)) == 3


def test_labels_and_snippets() -> None:
    labelled = label_sources([B, A])
    assert [s.label for s in labelled] == ["S1", "S2"]
    ref = labelled[1].ref
    assert ref.chunk_id == A.chunk_id and ref.section_number == "1"
    assert len(ref.snippet) <= 241 and ref.snippet.endswith("…")
    assert labelled[1].text == A.text


def test_unknown_labels_are_stripped_and_cited_labels_listed() -> None:
    text, cited = finalize_citations("Scaled [S2]. Softmax [S9]. Both [S1, S2]. Again [S2].", {"S1", "S2"})
    assert text == "Scaled [S2]. Softmax. Both [S1][S2]. Again [S2]."
    assert cited == ["S2", "S1"]


def test_partly_valid_groups_keep_the_valid_labels() -> None:
    text, cited = finalize_citations("Claim [S1; S7].", {"S1"})
    assert (text, cited) == ("Claim [S1].", ["S1"])


def test_no_sources_strips_every_label() -> None:
    assert finalize_citations("Hello [S1].", set()) == ("Hello.", [])


def test_text_without_citations_is_untouched() -> None:
    body = "Use $\\sqrt{d_k}$ and [this link](https://x) and a list [1]."
    assert finalize_citations(body, {"S1"}) == (body, [])


def test_strip_citations_for_history() -> None:
    assert strip_citations("It scales [S1] by root d_k [S2, S3].") == "It scales by root d_k."
    assert strip_citations("BLEU counts n-grams [P1][S2].") == "BLEU counts n-grams."


# ---------------------------------------------------------------- other papers, cited as [P#]


def paper(n: int, **fields) -> ScholarPaper:
    return ScholarPaper(s2_id=f"s2_{n}", title=f"Paper {n}", **fields)


def test_p_labels_are_kept_split_and_listed_with_s_labels() -> None:
    text, cited = finalize_citations(
        "BLEU [P1]. Scores [S1, P1]. Made up [P4]. Both [P2;S9].", {"S1", "P1", "P2"}
    )
    assert text == "BLEU [P1]. Scores [S1][P1]. Made up. Both [P2]."
    assert cited == ["P1", "S1", "P2"]
    assert citation_labels("a [S2] b [P1, S2]") == ["S2", "P1", "S2"]


def test_external_labels_carry_the_card_fields() -> None:
    [labelled] = label_external([BLEU_PAPER.model_copy(update={"tldr": "An n-gram metric.", "venue": "ACL"})])
    ref = labelled.ref
    assert (ref.label, ref.s2_id, ref.year, ref.venue, ref.tldr) == (
        "P1",
        BLEU_PAPER.s2_id,
        2002,
        "ACL",
        "An n-gram metric.",
    )
    assert ref.authors == BLEU_PAPER.authors and labelled.paper.abstract == BLEU_PAPER.abstract


def test_named_papers_are_used_in_the_order_given() -> None:
    seen = {p.s2_id: p for p in (paper(1), paper(2), paper(3))}
    picked = resolve_external(["s2_3", "made_up", "s2_1", "s2_3"], seen, lookups=[paper(2)])
    assert [p.s2_id for p in picked] == ["s2_3", "s2_1"]


def test_unchosen_papers_fall_back_to_each_lookups_best_match() -> None:
    seen = {p.s2_id: p for p in (paper(1), paper(2), paper(3))}
    lookups = [paper(2), paper(1), paper(2)]
    assert [p.s2_id for p in resolve_external(None, seen, lookups)] == ["s2_2", "s2_1"]
    # Every id invented: the same fallback as for passages.
    assert [p.s2_id for p in resolve_external(["nope"], seen, lookups)] == ["s2_2", "s2_1"]
    # An explicit empty list means the agent decided no other paper belongs in the answer.
    assert resolve_external([], seen, lookups) == []


def test_external_papers_are_capped() -> None:
    many = [paper(i) for i in range(9)]
    seen = {p.s2_id: p for p in many}
    assert len(resolve_external([p.s2_id for p in many], seen, [])) == MAX_EXTERNAL
    assert len(resolve_external(None, seen, many)) == MAX_EXTERNAL
