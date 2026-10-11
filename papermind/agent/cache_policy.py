"""How every model request is laid out so that its prefix is cacheable.

Prompt caching is a prefix match over the rendered request, in the order
tools -> system -> messages, and it stops at the first byte that differs. So
the request is ordered by how often each part changes:

    breakpoint 1   tools (sorted, frozen JSON) + system block 1: prompts/system.md
                   identical for every reader and every paper; never changes
    breakpoint 2   system block 2: the paper card (title, authors, abstract, outline)
                   changes per paper; shared by every reader of that paper
    automatic      top-level cache_control on the message tail; the API moves it
                   to the last cacheable block, so each agent step reads
                   everything the previous step wrote and adds only its delta

Nothing volatile goes above the messages: the reader's level and known concepts
travel in a mid-conversation ``{"role": "system"}`` message after the question
(see ``loop.py``), so a reader learning a concept never invalidates the
tools+system prefix.

The writer uses the same two system breakpoints (writer.md, then the paper
card) but no automatic tail breakpoint: its single user message is new every
turn, so caching it would pay the write premium on bytes nothing ever reads.

With ``prompt_caching`` off no ``cache_control`` is sent anywhere -- that is
the ablation baseline the latency evals compare against.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from papermind.schemas.paper import PaperMeta

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
EPHEMERAL = {"type": "ephemeral"}
MAX_AUTHORS = 8


@cache
def load_prompt(name: str) -> str:
    """A prompt file, read once per process so its bytes cannot drift between requests."""
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


@dataclass(frozen=True)
class RequestParts:
    system: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    cache: bool  # top-level automatic caching of the message tail


def paper_card(paper: PaperMeta) -> str:
    """The paper as the models see it. Deterministic for a given PaperMeta.

    Deliberately excludes anything that changes without the paper changing
    (ingestion time, chunk counts, page geometry): this block is a cache key.
    """
    lines = ["# The paper the reader has open", "", f"Title: {paper.title}"]
    if paper.authors:
        authors = ", ".join(paper.authors[:MAX_AUTHORS])
        if len(paper.authors) > MAX_AUTHORS:
            authors += f", and {len(paper.authors) - MAX_AUTHORS} more"
        lines.append(f"Authors: {authors}")
    if paper.arxiv_id:
        lines.append(f"arXiv: {paper.arxiv_id}")
    if paper.published:
        lines.append(f"Published: {paper.published.year}")
    lines.append(f"Pages: {paper.num_pages}")
    if paper.abstract:
        lines += ["", "## Abstract", "", " ".join(paper.abstract.split())]
    if paper.sections:
        lines += ["", "## Outline", ""]
        for s in paper.sections:
            indent = "  " * (max(s.level, 1) - 1)
            number = f"§{s.number} " if s.number else ""
            lines.append(f"{indent}- {number}{s.title} (page {s.page + 1}, {s.section_id})")
    return "\n".join(lines) + "\n"


def system_blocks(prompt: str, card: str, *, caching: bool) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "text", "text": prompt}, {"type": "text", "text": card}]
    if caching:
        for block in blocks:
            block["cache_control"] = dict(EPHEMERAL)
    return blocks


def agent_request(
    *, tools: list[dict[str, Any]], paper: PaperMeta, messages: list[dict[str, Any]], caching: bool
) -> RequestParts:
    return RequestParts(
        system=system_blocks(load_prompt("system"), paper_card(paper), caching=caching),
        messages=messages,
        tools=tools,
        cache=caching,
    )


def writer_request(*, paper: PaperMeta, user_content: str, caching: bool) -> RequestParts:
    return RequestParts(
        system=system_blocks(load_prompt("writer"), paper_card(paper), caching=caching),
        messages=[{"role": "user", "content": user_content}],
        tools=None,
        cache=False,
    )


def mastery_system(*, caching: bool) -> list[dict[str, Any]]:
    block: dict[str, Any] = {"type": "text", "text": load_prompt("mastery")}
    if caching:
        block["cache_control"] = dict(EPHEMERAL)
    return [block]
