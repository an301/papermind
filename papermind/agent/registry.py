"""The agent-visible tools, defined in code.

Each ``ToolSpec`` ties a model-facing argument model (``papermind/schemas/tools.py``)
to the MCP tool it routes to, the session context the dispatcher injects, and
the two sentences the reader sees in the activity trail (``label`` while it
runs, ``summarize`` when it is done).

The tool list sent to the model is rendered once, sorted by name, and kept as
frozen JSON. Tools are the first bytes of every prompt, so a list whose order
or content wobbled between requests would silently miss the prompt cache on
every call.

``mark_known`` and ``forget_concept`` are deliberately absent: reader mastery is
written by the mastery pass, where every update is reported and can be undone.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel

from papermind.mcp_client.manager import MCPCaller
from papermind.schemas.tools import (
    CheckPrerequisitesArgs,
    CheckPrerequisitesResult,
    CheckPrerequisitesServerArgs,
    ComposeAnswerArgs,
    ConceptRef,
    LearningPathArgs,
    LearningPathResult,
    LearningPathServerArgs,
    LookupPaperArgs,
    LookupPaperResult,
    PaperGraphArgs,
    PaperGraphResult,
    PaperGraphServerArgs,
    RetrievedChunk,
    ScholarPaper,
    SearchPaperArgs,
    SearchPaperResult,
    SearchPaperServerArgs,
)

log = logging.getLogger(__name__)

COMPOSE_ANSWER = "compose_answer"


class RegistryMismatch(RuntimeError):
    """A routed MCP tool is missing or does not accept the arguments we send."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class ToolSpec:
    name: str
    args_model: type[BaseModel]
    guidance: str
    label: Callable[[Any], str]
    summarize: Callable[[Any], str] = field(default=lambda _result: "Done")
    failure: str = "The tool failed"
    server: str | None = None
    mcp_tool: str | None = None
    server_args_model: type[BaseModel] | None = None
    result_model: type[BaseModel] | None = None
    inject: tuple[str, ...] = ()
    # What goes back to the model as the tool_result text; defaults to the full result.
    for_model: Callable[[Any], dict[str, Any]] = field(default=lambda result: result.model_dump(mode="json"))
    # Small structured payload for the UI's activity trail (ToolFinished.detail).
    detail: Callable[[Any], dict[str, Any] | None] = field(default=lambda _result: None)

    @property
    def local(self) -> bool:
        return self.server is None

    @property
    def description(self) -> str:
        doc = inspect.cleandoc(self.args_model.__doc__ or "")
        return f"{doc}\n\n{self.guidance}".strip()

    def model_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
            # Our pydantic layer validates every call, so the API need not
            # buffer and validate the input before streaming it.
            "eager_input_streaming": True,
        }


class Registry:
    """The frozen, name-sorted tool table."""

    def __init__(self, specs: Iterable[ToolSpec]) -> None:
        ordered = tuple(sorted(specs, key=lambda s: s.name))
        names = [s.name for s in ordered]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate tool names: {names}")
        self.specs: tuple[ToolSpec, ...] = ordered
        self._by_name = MappingProxyType({s.name: s for s in ordered})
        self._tools_json = json.dumps([s.model_tool() for s in ordered], ensure_ascii=False)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._by_name)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self._tools_json.encode()).hexdigest()[:16]

    def get(self, name: str) -> ToolSpec | None:
        return self._by_name.get(name)

    def anthropic_tools(self) -> list[dict[str, Any]]:
        """A fresh copy of the frozen tool list (callers cannot mutate the original)."""
        return json.loads(self._tools_json)

    def verify_against(self, mcp: MCPCaller) -> list[str]:
        """Check every routed MCP tool exists and accepts the arguments we will send.

        Servers with no tools listed (not running) are skipped and returned as
        warnings, so a down Semantic Scholar does not stop the app. Any real
        mismatch raises ``RegistryMismatch``.
        """
        problems: list[str] = []
        skipped: list[str] = []
        for spec in self.specs:
            if spec.server is None or spec.mcp_tool is None:
                continue
            schemas = mcp.tool_schemas(spec.server)
            if not schemas:
                skipped.append(f"{spec.server} is not running; cannot verify {spec.mcp_tool}")
                continue
            schema = schemas.get(spec.mcp_tool)
            if schema is None:
                problems.append(f"{spec.server} has no tool {spec.mcp_tool!r} (has {sorted(schemas)})")
                continue
            sent = set(spec.args_model.model_fields) | set(spec.inject)
            accepted = set(schema.get("properties", {}))
            if missing := sorted(sent - accepted):
                problems.append(f"{spec.server}.{spec.mcp_tool} does not accept {missing}")
            if unsent := sorted(set(schema.get("required", [])) - sent):
                problems.append(f"{spec.server}.{spec.mcp_tool} requires {unsent}, which we never send")
        for warning in skipped:
            log.warning("tool registry: %s", warning)
        if problems:
            raise RegistryMismatch(problems)
        return skipped


# ---------------------------------------------------------------- activity-trail sentences
#
# The copy is specified in docs/design/DESIGN.md §5.11 and must match it
# exactly: single curly quotes, a closing "…" on every live line, no trailing
# full stop. ``detail`` carries the counts the collapsed trail summary needs
# ("Read 4 passages and checked 2 ideas") so the UI never parses a sentence.

QUOTE_CHARS = 60
TITLE_CHARS = 48


def _clip(text: str, limit: int) -> str:
    """Whitespace-collapsed, cut at a word boundary with "…" when longer than ``limit``."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.") + "…"


def _quoted(text: str, limit: int = QUOTE_CHARS) -> str:
    return f"‘{_clip(text, limit)}’"


def _capitalized(text: str) -> str:
    """First letter up for the start of a sentence; 'LoRA' and 'BLEU' are unchanged."""
    return text[:1].upper() + text[1:]


def _join(items: list[str], limit: int = 3) -> str:
    if len(items) > limit:
        return ", ".join(items[:limit]) + f" and {len(items) - limit} more"
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + f" and {items[-1]}"


def display_name(name: str) -> str:
    """'Dot Product' -> 'dot product', but 'BLEU' and 'LoRA' keep their case."""

    def word(w: str) -> str:
        parts = w.split("-")
        return "-".join(p.lower() if p[:1].isupper() and p[1:] == p[1:].lower() else p for p in parts)

    return " ".join(word(w) for w in name.split())


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def section_ref(chunk: RetrievedChunk) -> str:
    return f"§{chunk.section_number}" if chunk.section_number else chunk.section_title


def _search_label(args: SearchPaperArgs) -> str:
    where = f"§{args.section_filter}" if args.section_filter else "the paper"
    return f"Searching {where} for {_quoted(args.query)}…"


def _search_summary(result: SearchPaperResult) -> str:
    if not result.chunks:
        return f"No passages matched {_quoted(result.query)}"
    sections = list(dict.fromkeys(section_ref(c) for c in result.chunks))
    return f"Found {_plural(len(result.chunks), 'passage', 'passages')} in {_join(sections)}"


def _search_for_model(result: SearchPaperResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "chunks": [
            {
                "chunk_id": c.chunk_id,
                "section": " ".join(p for p in (c.section_number, c.section_title) if p),
                "pages": f"{c.page_start + 1}"
                if c.page_end == c.page_start
                else f"{c.page_start + 1}-{c.page_end + 1}",
                "text": c.text,
            }
            for c in result.chunks
        ],
    }


def _search_detail(result: SearchPaperResult) -> dict[str, Any]:
    return {
        "passages": len(result.chunks),
        "chunk_ids": [c.chunk_id for c in result.chunks],
        "sections": list(dict.fromkeys(section_ref(c) for c in result.chunks)),
    }


def _not_in_map(query: str) -> str:
    return f"{_quoted(_capitalized(query.strip()))} isn’t in the concept map yet"


def _prereq_label(args: CheckPrerequisitesArgs) -> str:
    return f"Checking what {_quoted(args.concept)} builds on…"


def _prereq_summary(result: CheckPrerequisitesResult) -> str:
    if result.matched is None:
        return _not_in_map(result.query)
    target = display_name(result.matched.name)
    missing = [display_name(g.name) for g in result.gaps if g.depth > 0]
    if not missing:
        return f"You’ve met everything {_quoted(target)} builds on"
    shown = ", ".join(missing[:4]) + (f" and {len(missing) - 4} more" if len(missing) > 4 else "")
    ideas = _plural(len(missing), "idea that hasn’t", "ideas that haven’t")
    return f"{_quoted(_capitalized(target))} builds on {ideas} come up in your reading yet: {shown}"


def _ref(ref: ConceptRef | None) -> dict[str, str] | None:
    return ref.model_dump(mode="json") if ref is not None else None


def _prereq_detail(result: CheckPrerequisitesResult) -> dict[str, Any]:
    return {
        "matched": _ref(result.matched),
        "gaps": sum(1 for g in result.gaps if g.depth > 0),
        "altitude": result.altitude,
    }


def _prereq_for_model(result: CheckPrerequisitesResult) -> dict[str, Any]:
    if result.matched is None:
        return {
            "query": result.query,
            "matched": None,
            "suggestions": [s.name for s in result.suggestions],
            "altitude": result.altitude,
        }
    return {
        "query": result.query,
        "matched": result.matched.name,
        "gaps": [{"name": g.name, "depth": g.depth} for g in result.gaps],
        "teaching_order": [c.name for c in result.teaching_order],
        "known_foundations": [c.name for c in result.known_stops],
        "altitude": result.altitude,
    }


def _path_label(args: LearningPathArgs) -> str:
    return f"Working out an order for learning {_quoted(args.concept)}…"


def _path_summary(result: LearningPathResult) -> str:
    if result.matched is None:
        return _not_in_map(result.query)
    if not result.steps:
        return f"You’ve met everything {_quoted(display_name(result.matched.name))} builds on"
    names = [display_name(s.name) for s in result.steps]
    shown = names if len(names) <= 6 else [*names[:4], f"{len(names) - 5} more", names[-1]]
    return "Suggested order: " + ", ".join(shown)


def _path_detail(result: LearningPathResult) -> dict[str, Any]:
    return {"matched": _ref(result.matched), "steps": [s.name for s in result.steps]}


def _path_for_model(result: LearningPathResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "matched": result.matched.name if result.matched else None,
        "steps": [s.name for s in result.steps],
    }


def _paper_for_model(p: ScholarPaper, *, abstract: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "s2_id": p.s2_id,
        "title": p.title,
        "year": p.year,
        "authors": p.authors[:3],
        "citation_count": p.citation_count,
    }
    if p.arxiv_id:
        out["arxiv_id"] = p.arxiv_id
    if abstract:
        summary = p.tldr or p.abstract
        if summary:
            out["summary"] = _clip(summary, 600)
        if p.venue:
            out["venue"] = p.venue
    return out


def _lookup_label(args: LookupPaperArgs) -> str:
    return f"Looking up {_quoted(args.query)} on Semantic Scholar…"


def _lookup_summary(result: LookupPaperResult) -> str:
    if not result.matches:
        return f"Nothing on Semantic Scholar for {_quoted(result.query)}"
    first = result.matches[0]
    return f"Found {_quoted(first.title, TITLE_CHARS)}" + (f" ({first.year})" if first.year else "")


def _lookup_for_model(result: LookupPaperResult) -> dict[str, Any]:
    return {"query": result.query, "matches": [_paper_for_model(p, abstract=True) for p in result.matches]}


def _papers_detail(papers: list[ScholarPaper], total: int | None = None) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "s2_ids": list(dict.fromkeys(p.s2_id for p in papers)),
        "papers": [{"s2_id": p.s2_id, "title": p.title, "year": p.year, "url": p.url} for p in papers],
    }
    if total is not None:
        detail["total"] = total
    return detail


def _graph_for_model(result: PaperGraphResult) -> dict[str, Any]:
    return {
        "paper": result.resolved.title if result.resolved else result.paper,
        "total": result.total,
        "papers": [_paper_for_model(p, abstract=False) for p in result.papers],
    }


def _references_label(args: PaperGraphArgs) -> str:
    return f"Finding what {_quoted(args.paper)} cites…" if args.paper else "Finding what this paper cites…"


def _citations_label(args: PaperGraphArgs) -> str:
    return (
        f"Finding later work that cites {_quoted(args.paper)}…"
        if args.paper
        else "Finding later work that cites this paper…"
    )


def _references_summary(result: PaperGraphResult) -> str:
    return f"Found {_plural(len(result.papers), 'paper', 'papers')} it cites"


def _citations_summary(result: PaperGraphResult) -> str:
    n = len(result.papers)
    return f"Found {_plural(n, 'paper', 'papers')} that {'cites' if n == 1 else 'cite'} it"


# ---------------------------------------------------------------- the table

DEFAULT_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_paper",
        args_model=SearchPaperArgs,
        guidance=(
            "Call this first for any question about the paper's content, and search again with "
            "different terms if the results miss. Results carry chunk_ids; only these can be cited."
        ),
        label=_search_label,
        summarize=_search_summary,
        failure="Couldn’t search the paper",
        server="retriever",
        mcp_tool="search_paper",
        server_args_model=SearchPaperServerArgs,
        result_model=SearchPaperResult,
        inject=("paper_id", "mode"),
        for_model=_search_for_model,
        detail=_search_detail,
    ),
    ToolSpec(
        name="check_prerequisites",
        args_model=CheckPrerequisitesArgs,
        guidance=(
            "Call this in the same step as your first search when the question is about understanding "
            "a concept. Returns the reader's gaps, a teaching order and the altitude for the answer."
        ),
        label=_prereq_label,
        summarize=_prereq_summary,
        failure="Couldn’t check what this builds on",
        server="prereq_graph",
        mcp_tool="check_prerequisites",
        server_args_model=CheckPrerequisitesServerArgs,
        result_model=CheckPrerequisitesResult,
        inject=("user_id",),
        for_model=_prereq_for_model,
        detail=_prereq_detail,
    ),
    ToolSpec(
        name="get_learning_path",
        args_model=LearningPathArgs,
        guidance=(
            "Only when the reader asks what to learn first or how to build up to a concept. "
            "Never together with check_prerequisites for the same concept."
        ),
        label=_path_label,
        summarize=_path_summary,
        failure="Couldn’t work out a learning path",
        server="prereq_graph",
        mcp_tool="get_learning_path",
        server_args_model=LearningPathServerArgs,
        result_model=LearningPathResult,
        inject=("user_id",),
        for_model=_path_for_model,
        detail=_path_detail,
    ),
    ToolSpec(
        name="lookup_paper",
        args_model=LookupPaperArgs,
        guidance=(
            "Use when the question is about something the paper cites rather than explains, such as "
            "a metric, dataset or earlier method. Search the open paper in the same step."
        ),
        label=_lookup_label,
        summarize=_lookup_summary,
        failure="Couldn’t reach Semantic Scholar",
        server="semantic_scholar",
        mcp_tool="lookup_paper",
        result_model=LookupPaperResult,
        for_model=_lookup_for_model,
        detail=lambda result: _papers_detail(result.matches),
    ),
    ToolSpec(
        name="get_references",
        args_model=PaperGraphArgs,
        guidance="Use for 'what does this build on?' or 'where did this idea come from?'.",
        label=_references_label,
        summarize=_references_summary,
        failure="Couldn’t reach Semantic Scholar",
        server="semantic_scholar",
        mcp_tool="get_references",
        server_args_model=PaperGraphServerArgs,
        result_model=PaperGraphResult,
        inject=("default_paper",),
        for_model=_graph_for_model,
        detail=lambda result: _papers_detail(result.papers, result.total),
    ),
    ToolSpec(
        name="get_citations",
        args_model=PaperGraphArgs,
        guidance="Use for 'what came after this?' or 'who builds on this now?'.",
        label=_citations_label,
        summarize=_citations_summary,
        failure="Couldn’t reach Semantic Scholar",
        server="semantic_scholar",
        mcp_tool="get_citations",
        server_args_model=PaperGraphServerArgs,
        result_model=PaperGraphResult,
        inject=("default_paper",),
        for_model=_graph_for_model,
        detail=lambda result: _papers_detail(result.papers, result.total),
    ),
    ToolSpec(
        name=COMPOSE_ANSWER,
        args_model=ComposeAnswerArgs,
        guidance=(
            "Call this as soon as you have enough, usually after one or two rounds of tools; it is the "
            "only way to finish. For small talk call it immediately with no sources."
        ),
        # Never emitted as a tool event: the trail moves straight to "Writing the answer…".
        label=lambda _args: "Writing the answer…",
        summarize=lambda _result: "Wrote the answer",
    ),
)


def build_registry(specs: Iterable[ToolSpec] = DEFAULT_SPECS) -> Registry:
    return Registry(specs)
