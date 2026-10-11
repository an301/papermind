"""The writer: turns the agent's plan and the labelled passages into the answer.

The agent decides what context is needed; the writer decides how to say it.
They are separate so the writer can be swapped -- today Claude with the level
rubric in ``prompts/writer.md``, later a fine-tuned Mistral 7B adapter -- without
touching the loop. A writer only yields text; the loop owns events, citations
and persistence.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from papermind.agent.cache_policy import load_prompt, writer_request
from papermind.agent.labels import LabelledExternal, LabelledSource, strip_citations
from papermind.config import Settings
from papermind.llm.client import CallResult, LLMClient, PaperMindLLMError
from papermind.schemas.paper import PaperMeta
from papermind.schemas.session import Quote
from papermind.schemas.tools import Altitude, Level

# The rubric the writer is held to and the eval judge grades against. The
# judge must use these same strings, and prompts/writer.md states each verbatim
# (tests/unit/test_agent_writer.py checks), so "beginner" cannot mean one thing
# to the writer and another to the grader.
LEVEL_DEFINITIONS: dict[Level, str] = {
    "beginner": "plain language and analogies; no unexplained jargon or notation",
    "intermediate": "standard notation; assumes linear algebra and basic machine learning",
    "expert": "precise and technical; assumes familiarity with transformers",
}

HISTORY_MESSAGES = 4  # the last two exchanges
HISTORY_CHARS = 1200
EXTERNAL_SUMMARY_CHARS = 400
EXTERNAL_AUTHORS = 3


@dataclass
class WriterInput:
    question: str
    level: Level
    paper: PaperMeta
    plan: str
    altitude: Altitude = "technical"
    sources: list[LabelledSource] = field(default_factory=list)
    concepts_to_scaffold: list[str] = field(default_factory=list)
    # Set when the agent called get_learning_path: prerequisites first, the target last.
    learning_path: list[str] = field(default_factory=list)
    # Looked-up papers the writer may cite as [P1], [P2] ...
    external_papers: list[LabelledExternal] = field(default_factory=list)
    quote: Quote | None = None
    # (role, text) pairs, oldest first; role is "user" or "assistant".
    history: list[tuple[str, str]] = field(default_factory=list)
    # Called once per model call the writer makes, for usage accounting. A
    # callback rather than writer state so one writer can serve concurrent turns.
    on_call: Callable[[CallResult], None] | None = None


class Writer(Protocol):
    name: str

    def stream(self, inp: WriterInput) -> AsyncIterator[str]: ...


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_writer_message(inp: WriterInput) -> str:
    """The writer's one user message, in tagged sections (see prompts/writer.md)."""
    out: list[str] = [
        "<reader>",
        f"level: {inp.level} ({LEVEL_DEFINITIONS[inp.level]})",
        f"altitude: {inp.altitude}",
        "</reader>",
        "",
        "<plan>",
        inp.plan.strip(),
        "</plan>",
        "",
        "<concepts_to_scaffold>",
    ]
    out += [f"{i}. {c}" for i, c in enumerate(inp.concepts_to_scaffold, 1)] or ["none"]
    out += ["</concepts_to_scaffold>", ""]
    if inp.learning_path:
        out += [
            "<learning_path>",
            *(f"{i}. {c}" for i, c in enumerate(inp.learning_path, 1)),
            "</learning_path>",
            "",
        ]
    out += ["<sources>"]
    if inp.sources:
        for s in inp.sources:
            ref = s.ref
            section = " ".join(
                p for p in (f"§{ref.section_number}" if ref.section_number else None, ref.section_title) if p
            )
            pages = (
                f"{ref.page_start + 1}"
                if ref.page_end == ref.page_start
                else f"{ref.page_start + 1}-{ref.page_end + 1}"
            )
            out += [
                f'<source label="{ref.label}" section="{section}" pages="{pages}">',
                s.text.strip(),
                "</source>",
            ]
    else:
        out.append("none: no passages from the paper were retrieved for this message")
    out += ["</sources>", "", "<external_papers>"]
    for e in inp.external_papers:
        out += _external_block(e)
    if not inp.external_papers:
        out.append("none")
    out += ["</external_papers>"]
    if inp.history:
        out += ["", "<conversation>"]
        for role, text in inp.history[-HISTORY_MESSAGES:]:
            speaker = "Reader" if role == "user" else "PaperMind"
            out.append(f"{speaker}: {_clip(strip_citations(text), HISTORY_CHARS)}")
        out.append("</conversation>")
    if inp.quote is not None:
        page = f' page="{inp.quote.page + 1}"' if inp.quote.page is not None else ""
        out += ["", f"<selected_passage{page}>", inp.quote.text.strip(), "</selected_passage>"]
    out += ["", "<question>", inp.question.strip(), "</question>"]
    return "\n".join(out)


def _external_block(e: LabelledExternal) -> list[str]:
    p = e.paper
    attrs = f' label="{e.label}"' + (f' year="{p.year}"' if p.year else "")
    lines = [f"<paper{attrs}>", f"Title: {p.title}"]
    if p.authors:
        more = " et al." if len(p.authors) > EXTERNAL_AUTHORS else ""
        lines.append(f"Authors: {', '.join(p.authors[:EXTERNAL_AUTHORS])}{more}")
    if p.venue:
        lines.append(f"Venue: {p.venue}")
    if summary := (p.tldr or p.abstract):
        lines.append(f"Summary: {_clip(' '.join(summary.split()), EXTERNAL_SUMMARY_CHARS)}")
    return [*lines, "</paper>"]


class ClaudeWriter:
    """Hosted Claude with the level rubric; cites passages as [S1] and other papers as [P1]."""

    def __init__(self, llm: LLMClient, settings: Settings) -> None:
        self._llm = llm
        self._settings = settings
        self.name = f"claude:{llm.model}"

    async def stream(self, inp: WriterInput) -> AsyncIterator[str]:
        parts = writer_request(
            paper=inp.paper, user_content=render_writer_message(inp), caching=self._settings.prompt_caching
        )
        # The LLM client reports text through a callback; a queue turns that
        # into the iterator the Writer protocol promises.
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def on_text(text: str) -> None:
            queue.put_nowait(text)

        task = asyncio.create_task(
            self._llm.stream(
                system=parts.system,
                messages=parts.messages,
                effort=self._settings.writer_effort,
                max_tokens=self._settings.writer_max_tokens,
                cache=parts.cache,
                on_text=on_text,
            )
        )
        task.add_done_callback(lambda _task: queue.put_nowait(None))
        try:
            while (chunk := await queue.get()) is not None:
                yield chunk
            try:
                result = await task
            except PaperMindLLMError as exc:
                if exc.result is not None and inp.on_call is not None:
                    inp.on_call(exc.result)
                raise
            if inp.on_call is not None:
                inp.on_call(result)
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


def mistral_messages(inp: WriterInput) -> list[dict[str, str]]:
    """The fine-tuned writer's prompt: compact instructions, the paper's title, then
    the same tagged sections Claude gets (``render_writer_message``).

    The training data in training/ is built with this exact function, so the
    prompt the adapter learned on and the prompt it serves are the same bytes.
    It is one user turn because Mistral's chat templates differ on whether a
    separate system role exists; folding the instructions in works on all of them.
    """
    head = load_prompt("writer_mistral").strip()
    paper = f"<paper>\nTitle: {inp.paper.title}\n</paper>"
    return [{"role": "user", "content": f"{head}\n\n{paper}\n\n{render_writer_message(inp)}"}]


class MistralWriter:
    """The fine-tuned Mistral 7B writer, served by vLLM's OpenAI-compatible API.

    Streams ``/chat/completions`` and yields the text deltas. Citations,
    events and persistence stay with the loop, exactly as for ClaudeWriter.
    """

    def __init__(self, settings: Settings, *, client: httpx.AsyncClient | None = None) -> None:
        self._settings = settings
        self._client = client
        self.name = f"mistral:{settings.mistral_model}"

    async def stream(self, inp: WriterInput) -> AsyncIterator[str]:
        s = self._settings
        headers = {"Content-Type": "application/json"}
        if s.mistral_api_key is not None:
            headers["Authorization"] = f"Bearer {s.mistral_api_key.get_secret_value()}"
        body = {
            "model": s.mistral_model,
            "messages": mistral_messages(inp),
            "stream": True,
            "max_tokens": s.mistral_max_tokens,
            "temperature": s.mistral_temperature,
        }
        url = s.mistral_base_url.rstrip("/") + "/chat/completions"
        client = self._client or httpx.AsyncClient(timeout=s.mistral_timeout_s)
        try:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code >= 400:
                    detail = (await response.aread()).decode(errors="replace")[:300]
                    retryable = response.status_code in (408, 429) or response.status_code >= 500
                    raise PaperMindLLMError(
                        "api_error",
                        f"The Mistral writer at {s.mistral_base_url} answered {response.status_code}: {detail}",
                        retryable=retryable,
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    choices = json.loads(data).get("choices") or []
                    delta = (choices[0].get("delta") or {}).get("content") if choices else None
                    if delta:
                        yield delta
        except httpx.HTTPError as exc:
            raise PaperMindLLMError(
                "api_error",
                f"Couldn’t reach the Mistral writer at {s.mistral_base_url}. Is vLLM running? ({type(exc).__name__})",
                retryable=True,
            ) from exc
        finally:
            if self._client is None:
                await client.aclose()


def make_writer(settings: Settings, llm: LLMClient) -> Writer:
    if settings.writer_backend == "mistral":
        return MistralWriter(settings)
    return ClaudeWriter(llm, settings)
