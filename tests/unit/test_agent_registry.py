from __future__ import annotations

import json

import pytest

from papermind.agent.registry import COMPOSE_ANSWER, DEFAULT_SPECS, RegistryMismatch, build_registry
from papermind.schemas.tools import (
    CheckPrerequisitesArgs,
    CheckPrerequisitesResult,
    ConceptRef,
    Gap,
    LearningPathResult,
    LookupPaperArgs,
    LookupPaperResult,
    PaperGraphArgs,
    PaperGraphResult,
    SearchPaperArgs,
    SearchPaperResult,
)
from tests.unit.fakes import BLEU_PAPER, DEFAULT_CHUNKS, FakeMCP

EXPECTED = {
    "search_paper": ("retriever", "search_paper", ("paper_id", "mode")),
    "check_prerequisites": ("prereq_graph", "check_prerequisites", ("user_id",)),
    "get_learning_path": ("prereq_graph", "get_learning_path", ("user_id",)),
    "lookup_paper": ("semantic_scholar", "lookup_paper", ()),
    "get_references": ("semantic_scholar", "get_references", ("default_paper",)),
    "get_citations": ("semantic_scholar", "get_citations", ("default_paper",)),
    "compose_answer": (None, None, ()),
}


def test_table_matches_the_architecture() -> None:
    registry = build_registry()
    assert set(registry.names) == set(EXPECTED)
    for name, (server, tool, inject) in EXPECTED.items():
        spec = registry.get(name)
        assert spec is not None
        assert (spec.server, spec.mcp_tool, spec.inject) == (server, tool, inject)
    # Mastery is written by the mastery pass, never by the agent.
    assert registry.get("mark_known") is None and registry.get("forget_concept") is None


def test_tools_are_sorted_and_frozen() -> None:
    registry = build_registry()
    names = [t["name"] for t in registry.anthropic_tools()]
    assert names == sorted(names)
    assert isinstance(registry.specs, tuple)
    # Callers get a copy; mutating it cannot change the next request's prefix.
    tools = registry.anthropic_tools()
    tools[0]["description"] = "changed"
    tools.pop()
    assert registry.anthropic_tools() != tools
    with pytest.raises(TypeError):
        registry._by_name["x"] = None  # type: ignore[index]


def test_order_does_not_depend_on_definition_order() -> None:
    forward = build_registry(DEFAULT_SPECS)
    backward = build_registry(tuple(reversed(DEFAULT_SPECS)))
    assert json.dumps(forward.anthropic_tools()) == json.dumps(backward.anthropic_tools())
    assert forward.fingerprint == backward.fingerprint


def test_schema_is_the_args_model_schema() -> None:
    registry = build_registry()
    for tool in registry.anthropic_tools():
        spec = registry.get(tool["name"])
        assert spec is not None
        assert tool["input_schema"] == spec.args_model.model_json_schema()
        assert tool["eager_input_streaming"] is True
        doc = (spec.args_model.__doc__ or "").strip().splitlines()[0]
        assert tool["description"].startswith(doc)
        # The model-facing schema never exposes injected context.
        assert not set(spec.inject) & set(tool["input_schema"]["properties"])


def test_duplicate_names_are_rejected() -> None:
    with pytest.raises(ValueError):
        build_registry((*DEFAULT_SPECS, DEFAULT_SPECS[0]))


def test_verify_against_matching_servers() -> None:
    assert build_registry().verify_against(FakeMCP()) == []


def test_verify_reports_a_missing_tool_and_an_unaccepted_argument() -> None:
    mcp = FakeMCP()
    schemas = {
        server: mcp.tool_schemas(server) for server in ("retriever", "prereq_graph", "semantic_scholar")
    }
    del schemas["prereq_graph"]["get_learning_path"]
    del schemas["retriever"]["search_paper"]["properties"]["mode"]

    class Mismatched(FakeMCP):
        def tool_schemas(self, server: str) -> dict:
            return schemas[server]

    with pytest.raises(RegistryMismatch) as info:
        build_registry().verify_against(Mismatched())
    problems = " | ".join(info.value.problems)
    assert "get_learning_path" in problems
    assert "search_paper does not accept ['mode']" in problems


def test_verify_skips_servers_that_are_not_running() -> None:
    class ScholarDown(FakeMCP):
        def tool_schemas(self, server: str) -> dict:
            return {} if server == "semantic_scholar" else super().tool_schemas(server)

    skipped = build_registry().verify_against(ScholarDown())
    assert len(skipped) == 3 and all("semantic_scholar" in s for s in skipped)


# ---------------------------------------------------------------- activity-trail sentences


def spec(name: str):
    found = build_registry().get(name)
    assert found is not None
    return found


def test_search_sentences() -> None:
    s = spec("search_paper")
    assert s.label(SearchPaperArgs(query="label smoothing")) == "Searching the paper for ‘label smoothing’…"
    assert s.label(SearchPaperArgs(query="dropout", section_filter="5.4")) == "Searching §5.4 for ‘dropout’…"
    result = SearchPaperResult(paper_id="p", query="q", mode="hybrid", chunks=list(DEFAULT_CHUNKS[:2]))
    assert s.summarize(result) == "Found 2 passages in §3.2.1 and §3.2.2"
    assert s.summarize(SearchPaperResult(paper_id="p", query="q", mode="hybrid", chunks=[])) == (
        "No passages matched ‘q’"
    )


def test_prerequisite_sentences() -> None:
    s = spec("check_prerequisites")
    assert s.label(CheckPrerequisitesArgs(concept="attention")) == "Checking what ‘attention’ builds on…"
    target = ConceptRef(concept_id="attention", name="Attention")
    result = CheckPrerequisitesResult(
        query="attention",
        matched=target,
        gaps=[
            Gap(concept_id="attention", name="Attention", depth=0),
            Gap(concept_id="softmax", name="Softmax", depth=1),
            Gap(concept_id="dot-product", name="Dot Product", depth=1),
        ],
        altitude="scaffolded",
    )
    assert s.summarize(result) == (
        "‘Attention’ builds on 2 ideas that haven’t come up in your reading yet: softmax, dot product"
    )
    covered = CheckPrerequisitesResult(query="attention", matched=target, altitude="technical")
    assert s.summarize(covered) == "You’ve met everything ‘attention’ builds on"
    unmatched = CheckPrerequisitesResult(query="flux capacitor", altitude="technical")
    assert s.summarize(unmatched) == "‘Flux capacitor’ isn’t in the concept map yet"


def test_learning_path_sentence_keeps_acronyms() -> None:
    result = LearningPathResult(
        query="BLEU",
        matched=ConceptRef(concept_id="bleu", name="BLEU"),
        steps=[
            Gap(concept_id="n-gram", name="N-Gram", depth=1),
            Gap(concept_id="bleu", name="BLEU", depth=0),
        ],
    )
    assert spec("get_learning_path").summarize(result) == "Suggested order: n-gram, BLEU"


def test_scholar_sentences() -> None:
    lookup = spec("lookup_paper")
    assert lookup.label(LookupPaperArgs(query="BLEU")) == "Looking up ‘BLEU’ on Semantic Scholar…"
    found = LookupPaperResult(query="BLEU", matches=[BLEU_PAPER])
    # Long titles are cut at a word boundary so the trail line stays on one line.
    assert lookup.summarize(found) == "Found ‘Bleu: a Method for Automatic Evaluation of…’ (2002)"
    assert (
        lookup.summarize(LookupPaperResult(query="zzz", matches=[]))
        == "Nothing on Semantic Scholar for ‘zzz’"
    )
    refs = spec("get_references")
    assert refs.label(PaperGraphArgs()) == "Finding what this paper cites…"
    assert spec("get_citations").label(PaperGraphArgs()) == "Finding later work that cites this paper…"
    one = PaperGraphResult(paper="p", papers=[BLEU_PAPER])
    assert spec("get_citations").summarize(one) == "Found 1 paper that cites it"
    assert refs.summarize(one) == "Found 1 paper it cites"


def test_no_emoji_in_any_sentence() -> None:
    registry = build_registry()
    text = " ".join(s.description + s.failure for s in registry.specs)
    assert all(ord(ch) < 0x2190 or ch in "‘’“”…→" for ch in text)


def test_compose_answer_is_local() -> None:
    s = spec(COMPOSE_ANSWER)
    assert s.local and s.server is None


def test_one_idea_reads_in_the_singular() -> None:
    result = CheckPrerequisitesResult(
        query="multi-head attention",
        matched=ConceptRef(concept_id="multi-head-attention", name="Multi-Head Attention"),
        gaps=[Gap(concept_id="softmax", name="Softmax", depth=1)],
        altitude="technical",
    )
    assert spec("check_prerequisites").summarize(result) == (
        "‘Multi-head attention’ builds on 1 idea that hasn’t come up in your reading yet: softmax"
    )


def test_failure_lines_match_the_design() -> None:
    # DESIGN.md §5.11, "Failed" column.
    assert {s.name: s.failure for s in build_registry().specs if not s.local} == {
        "search_paper": "Couldn’t search the paper",
        "check_prerequisites": "Couldn’t check what this builds on",
        "get_learning_path": "Couldn’t work out a learning path",
        "lookup_paper": "Couldn’t reach Semantic Scholar",
        "get_references": "Couldn’t reach Semantic Scholar",
        "get_citations": "Couldn’t reach Semantic Scholar",
    }


def test_details_carry_what_the_trail_counts() -> None:
    # "ideas" counts check_prerequisites calls that matched; "papers" counts distinct s2 ids.
    matched = ConceptRef(concept_id="attention", name="Attention")
    checked = CheckPrerequisitesResult(query="attention", matched=matched, altitude="technical")
    assert spec("check_prerequisites").detail(checked) == {
        "matched": {"concept_id": "attention", "name": "Attention"},
        "gaps": 0,
        "altitude": "technical",
    }
    unmatched = CheckPrerequisitesResult(query="flux capacitor", altitude="technical")
    assert spec("check_prerequisites").detail(unmatched)["matched"] is None
    path = LearningPathResult(
        query="attention", matched=matched, steps=[Gap(concept_id="softmax", name="Softmax", depth=1)]
    )
    assert spec("get_learning_path").detail(path) == {"matched": matched.model_dump(), "steps": ["Softmax"]}
    found = LookupPaperResult(query="BLEU", matches=[BLEU_PAPER, BLEU_PAPER])
    assert spec("lookup_paper").detail(found)["s2_ids"] == [BLEU_PAPER.s2_id]
    refs = PaperGraphResult(paper="p", papers=[BLEU_PAPER], total=41)
    assert spec("get_references").detail(refs)["s2_ids"] == [BLEU_PAPER.s2_id]
    assert spec("get_citations").detail(refs)["total"] == 41
    search = SearchPaperResult(paper_id="p", query="q", mode="hybrid", chunks=list(DEFAULT_CHUNKS[:2]))
    assert spec("search_paper").detail(search)["passages"] == 2
