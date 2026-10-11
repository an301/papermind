"""The ReAct loop: one reader message in, a stream of ChatEvents out.

Hand-written on purpose (no agent framework, no SDK tool runner) so the
control flow can be read top to bottom and stepped through in a debugger:

    1. messages = condensed history + the question + a mid-conversation
       system message with the reader's volatile state
    2. up to max_agent_steps: stream one call, append the assistant content
       unchanged, hand off on compose_answer, else run the tools and append
       their results as one user message
    3. resolve what the writer may cite: passages as S1..Sn, looked-up papers as P1..Pn
    4. stream the writer, then pin each cited passage to the sentence it used
    5. review the exchange (knowledge + follow-ups), persist, usage, done

Events follow the order documented in ``papermind/schemas/events.py``. A
failure becomes an ``error`` event; ``done`` always ends the stream unless the
client went away (cancellation propagates).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from papermind.agent.attribution import with_cited_sentences
from papermind.agent.cache_policy import agent_request, writer_request
from papermind.agent.dispatch import DispatchContext, Dispatcher, Emit, Gathered
from papermind.agent.labels import (
    LabelledExternal,
    LabelledSource,
    finalize_citations,
    label_external,
    label_sources,
    resolve_external,
    resolve_sources,
    strip_citations,
)
from papermind.agent.mastery import MasteryJudgement, MasteryPass
from papermind.agent.registry import Registry, build_registry
from papermind.agent.trace import Trace
from papermind.agent.writer import ClaudeWriter, Writer, WriterInput, make_writer
from papermind.config import Settings
from papermind.llm.client import CallResult, LLMClient, PaperMindLLMError, ToolInputParseError
from papermind.mcp_client.manager import MCPCaller
from papermind.schemas.events import (
    AnswerDelta,
    AnswerDone,
    Done,
    ErrorEvent,
    ExternalRef,
    Followups,
    GapsFound,
    KnowledgeUpdated,
    SourceRef,
    SourcesReady,
    TurnStarted,
    UsageReport,
)
from papermind.schemas.paper import PaperMeta
from papermind.schemas.session import ActivityItem, ChatMessage, KnownConcept, Quote, Session, UserProfile
from papermind.schemas.tools import Altitude, ComposeAnswerArgs, Level

log = logging.getLogger(__name__)

# Unparseable eager-streamed tool JSON is re-issued at most this many times per step.
MAX_JSON_RETRIES = 2
MAX_KNOWN_IN_STATE = 80
DEFAULT_PLAN = (
    "Answer the reader's message from the passages provided, at their level. "
    "If the passages do not answer it, say so plainly."
)
INTERNAL_ERROR = "Something went wrong on our side while answering. Try again in a moment."
# Stands in for an answer that was refused, failed or interrupted: its partial
# text is not an answer and must not be fed back as one, but dropping the turn
# would hide that the question before it went unanswered.
UNFINISHED_ANSWER = "(This answer was not completed.)"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class TurnContext:
    session: Session
    user: UserProfile
    paper: PaperMeta
    question: str
    quote: Quote | None
    level: Level
    known_summary: list[str]  # names of concepts the reader knows (for the agent)
    # The reader's KNOWS edges by concept id, with confidences. The mastery
    # pass needs the current values to apply its policy; None means "not
    # loaded", and the runner then asks its ``knowledge_source``.
    knowledge: dict[str, KnownConcept] | None = None
    turn_id: str = field(default_factory=lambda: new_id("t"))
    user_message_id: str = field(default_factory=lambda: new_id("m"))


@dataclass
class TurnResult:
    """Everything about one turn, for the API (persist) and the evals (trace).

    ``assistant_message`` is the answer as the transcript stores it: final
    sources (with ``cited_sentence``), external papers, gaps, the activity
    trail, follow-ups, latency and any error with its code.
    """

    turn_id: str
    question: str
    level: Level
    answer: str
    cited: list[str]  # S labels used in the answer, in order of first use
    sources: list[SourceRef]  # every labelled source; the cited ones carry cited_sentence
    external: list[ExternalRef]  # looked-up papers the answer cites as [P#], in order of first use
    followups: list[str]
    plan: str
    altitude: Altitude
    gaps: GapsFound | None
    knowledge: KnowledgeUpdated | None
    usage: UsageReport
    error: ErrorEvent | None
    trace: Trace
    user_message: ChatMessage
    assistant_message: ChatMessage

    @property
    def retrieved(self) -> list[list[str]]:
        """Chunk ids per search_paper call, in call order and rank order."""
        return self.trace.retrieved

    @property
    def activity(self) -> list[ActivityItem]:
        return self.assistant_message.activity

    @property
    def latency_ms(self) -> int:
        """Time to the finished answer (or to the failure); see ``AgentRunner._result``."""
        return self.usage.latency_ms

    @property
    def ttft_ms(self) -> int | None:
        return self.usage.ttft_ms


@dataclass
class Handoff:
    """What the agent hands the writer."""

    plan: str
    # None: the agent never chose (no valid compose_answer); use the top retrieved chunks.
    source_ids: list[str] | None
    concepts_to_scaffold: list[str] = field(default_factory=list)
    # None: the agent never chose; the best match of each lookup stands in (see resolve_external).
    external_papers: list[str] | None = None
    reason: Literal[
        "compose_answer", "end_turn", "max_tokens", "out_of_steps", "compose_failed", "json_errors"
    ] = "compose_answer"

    @classmethod
    def from_compose(cls, args: ComposeAnswerArgs) -> Handoff:
        # A left-out list is "not chosen", unlike an explicit []: both fields
        # default to [], so only model_fields_set tells them apart. Read as
        # [], a forgotten source_chunk_ids would leave the writer with no
        # passages after a search that found them.
        chosen = args.model_fields_set
        return cls(
            plan=args.plan,
            source_ids=args.source_chunk_ids if "source_chunk_ids" in chosen else None,
            concepts_to_scaffold=args.concepts_to_scaffold,
            external_papers=args.external_papers if "external_papers" in chosen else None,
        )

    @classmethod
    def fallback(cls, reason: Any, text: str = "") -> Handoff:
        return cls(plan=text.strip() or DEFAULT_PLAN, source_ids=None, reason=reason)


PersistFn = Callable[[TurnContext, TurnResult], Awaitable[None]]
# ``GraphStore.known``: a reader's KNOWS edges by concept id.
KnowledgeSource = Callable[[str], Awaitable[dict[str, KnownConcept]]]


# ---------------------------------------------------------------- messages


def user_turn_text(question: str, quote: Quote | None) -> str:
    if quote is None:
        return question.strip()
    where = f" (page {quote.page + 1})" if quote.page is not None else ""
    return f'The reader selected this passage in the paper{where}:\n"""\n{quote.text.strip()}\n"""\n\n{question.strip()}'


def reader_state(level: Level, known: list[str]) -> str:
    """The volatile reader state, as a mid-conversation system message (never the system prompt)."""
    names = sorted(dict.fromkeys(n.strip() for n in known if n.strip()), key=str.casefold)
    if names:
        shown = ", ".join(names[:MAX_KNOWN_IN_STATE])
        if len(names) > MAX_KNOWN_IN_STATE:
            shown += f", and {len(names) - MAX_KNOWN_IN_STATE} more"
        known_line = f"- Concepts the reader already knows ({len(names)}): {shown}"
    else:
        known_line = "- Concepts the reader already knows: none recorded yet"
    return f"Reader state for this turn:\n- Level: {level}\n{known_line}"


def _history_text(m: ChatMessage) -> str:
    if m.role == "user":
        return user_turn_text(m.text, m.quote)
    if m.error:
        return UNFINISHED_ANSWER
    return strip_citations(m.text).strip()


def condensed_history(ctx: TurnContext, turns: int) -> list[tuple[str, str]]:
    """Prior (role, text) pairs, oldest first: reader messages with their quotes,
    answers without citation markers, no tool chatter. An answer that ended in
    an error is replaced by a marker rather than replayed as if complete."""
    prior = [
        m
        for m in ctx.session.messages
        if m.message_id != ctx.user_message_id and (m.text.strip() or (m.role == "assistant" and m.error))
    ]
    recent = prior[-2 * turns :] if turns > 0 else []
    while recent and recent[0].role != "user":
        recent = recent[1:]
    return [(m.role, _history_text(m)) for m in recent]


def previous_answer(ctx: TurnContext) -> str | None:
    """The answer just before this message, if it was completed (context for the mastery classifier)."""
    last = next(
        (
            m
            for m in reversed(ctx.session.messages)
            if m.role == "assistant" and m.message_id != ctx.user_message_id
        ),
        None,
    )
    return last.text if last is not None and last.text.strip() and not last.error else None


def build_messages(ctx: TurnContext, settings: Settings) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": role, "content": text} for role, text in condensed_history(ctx, settings.history_turns)
    ]
    messages.append({"role": "user", "content": user_turn_text(ctx.question, ctx.quote)})
    # Placement rules: a system message must follow a user message and be last
    # or followed by an assistant turn -- the first agent call is appended next.
    messages.append({"role": "system", "content": reader_state(ctx.level, ctx.known_summary)})
    return messages


# ---------------------------------------------------------------- the runner


@dataclass
class _Turn:
    """Mutable state of one turn while it runs."""

    ctx: TurnContext
    started: float
    trace: Trace
    gathered: Gathered = field(default_factory=Gathered)
    handoff: Handoff | None = None
    sources: list[LabelledSource] = field(default_factory=list)
    external: list[LabelledExternal] = field(default_factory=list)
    text: str = ""
    cited: list[str] = field(default_factory=list)
    cited_external: list[ExternalRef] = field(default_factory=list)
    # The sources as answer.done sends them, with cited_sentence; None until the answer is final.
    final_sources: list[SourceRef] | None = None
    first_token_at: float | None = None
    answered_at: float | None = None
    knowledge: KnowledgeUpdated | None = None
    followups: list[str] = field(default_factory=list)
    error: ErrorEvent | None = None
    answer_id: str = field(default_factory=lambda: new_id("m"))

    @property
    def altitude(self) -> Altitude:
        prereq = self.gathered.prerequisites
        return prereq.altitude if prereq is not None and prereq.matched is not None else "technical"


class AgentRunner:
    def __init__(
        self,
        *,
        llm: LLMClient,
        mcp: MCPCaller,
        registry: Registry,
        writer: Writer,
        settings: Settings,
        mastery: MasteryPass | None = None,
        knowledge_source: KnowledgeSource | None = None,
        persist: PersistFn | None = None,
    ) -> None:
        self._llm = llm
        self._mcp = mcp
        self._registry = registry
        self._writer = writer
        self._settings = settings
        self._mastery = mastery
        self._knowledge_source = knowledge_source
        self._persist_fn = persist
        # Rendered once: the same bytes go out at prefix position 0 on every call.
        self._tools = registry.anthropic_tools()
        # Shielded mastery writes outliving a cancelled turn; held so they are not collected mid-way.
        self._background: set[asyncio.Task[Any]] = set()

    async def run_turn(self, ctx: TurnContext, emit: Emit) -> TurnResult:
        turn = _Turn(ctx=ctx, started=time.perf_counter(), trace=Trace(turn_id=ctx.turn_id))
        await emit(
            TurnStarted(
                turn_id=ctx.turn_id,
                session_id=ctx.session.session_id,
                level=ctx.level,
                user_message_id=ctx.user_message_id,
            )
        )
        try:
            await self._answer(turn, emit)
        except PaperMindLLMError as exc:
            turn.error = ErrorEvent(code=exc.code, message=exc.message, retryable=exc.retryable)
        except NotImplementedError as exc:
            turn.error = ErrorEvent(code="internal", message=str(exc))
        except Exception:
            log.exception("turn %s failed", ctx.turn_id)
            turn.error = ErrorEvent(code="internal", message=INTERNAL_ERROR, retryable=True)
        if turn.error is not None:
            turn.trace.error(f"{turn.error.code}: {turn.error.message}")

        result = self._result(turn)
        await self._persist(ctx, result)
        await emit(turn.error if turn.error is not None else result.usage)
        await emit(Done())
        return result

    async def _answer(self, turn: _Turn, emit: Emit) -> None:
        # 1-2. Research: the agent gathers passages, gaps and papers, then hands off.
        turn.handoff = handoff = await self._research(turn, emit)

        # 3. What the writer may cite: passages as S1..Sn, looked-up papers as P1..Pn.
        g = turn.gathered
        chunks, dropped = resolve_sources(handoff.source_ids, g.chunks, fallback_k=self._settings.retrieval_k)
        if dropped:
            turn.trace.error(f"compose_answer named chunks not retrieved this turn: {dropped}")
        turn.sources = label_sources(chunks)
        turn.external = label_external(resolve_external(handoff.external_papers, g.papers, g.lookups))
        await emit(SourcesReady(sources=[s.ref for s in turn.sources]))

        # 4. The writer.
        async for chunk in self._writer.stream(self._writer_input(turn, handoff)):
            turn.first_token_at = turn.first_token_at or time.perf_counter()
            turn.text += chunk
            await emit(AnswerDelta(text=chunk))
        self._finalize(turn)
        turn.final_sources = with_cited_sentences(turn.text, turn.sources)
        turn.answered_at = time.perf_counter()
        await emit(
            AnswerDone(
                message_id=turn.answer_id,
                text=turn.text,
                cited=turn.cited,
                external=turn.cited_external,
                sources=turn.final_sources,
                writer=self._writer.name,
            )
        )

        # 5. What the reader's message revealed, and what they might ask next.
        await self._review(turn, emit)

    async def _research(self, turn: _Turn, emit: Emit) -> Handoff:
        settings = self._settings
        dispatcher = Dispatcher(
            registry=self._registry,
            mcp=self._mcp,
            settings=settings,
            context=self._dispatch_context(turn.ctx),
            emit=emit,
            trace=turn.trace,
            gathered=turn.gathered,
        )
        messages = build_messages(turn.ctx, settings)

        for step in range(1, settings.max_agent_steps + 1):
            turn.trace.steps = step
            response = await self._agent_call(turn, messages, step)
            if response is None:
                return Handoff.fallback("json_errors")
            # Unchanged, thinking blocks and signatures included: the next call
            # must see exactly the bytes this one produced.
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "max_tokens":
                # A truncated tool input still parses; running it would act on half an argument.
                turn.trace.error(f"agent step {step} hit max_tokens")
                return Handoff.fallback("max_tokens", response.text)
            if not response.tool_uses:
                return Handoff.fallback("end_turn", response.text)

            outcome = await dispatcher.run_step(step, response.tool_uses)
            if outcome.compose is not None:
                return Handoff.from_compose(outcome.compose)
            if outcome.compose_exhausted:
                return Handoff.fallback("compose_failed")
            messages.append({"role": "user", "content": outcome.tool_results})

        turn.trace.error(f"agent ran out of steps ({settings.max_agent_steps})")
        return Handoff.fallback("out_of_steps")

    # ------------------------------------------------------------ helpers

    async def _agent_call(self, turn: _Turn, messages: list[dict[str, Any]], step: int) -> CallResult | None:
        parts = agent_request(
            tools=self._tools, paper=turn.ctx.paper, messages=messages, caching=self._settings.prompt_caching
        )
        for _attempt in range(MAX_JSON_RETRIES + 1):
            try:
                response = await self._llm.stream(
                    system=parts.system,
                    messages=parts.messages,
                    tools=parts.tools,
                    effort=self._settings.agent_effort,
                    max_tokens=self._settings.agent_max_tokens,
                    cache=parts.cache,
                )
            except ToolInputParseError as exc:
                if exc.result is not None:
                    turn.trace.record_call("agent", exc.result, step)
                turn.trace.json_retries += 1
                turn.trace.error(f"agent step {step}: unparseable tool input, re-issuing ({exc})")
                continue
            except PaperMindLLMError as exc:
                if exc.result is not None:
                    turn.trace.record_call("agent", exc.result, step)
                raise
            turn.trace.record_call("agent", response, step)
            return response
        return None

    def _dispatch_context(self, ctx: TurnContext) -> DispatchContext:
        paper = ctx.paper
        # Semantic Scholar resolves an arXiv id exactly; an upload only has its title.
        default_paper = paper.arxiv_id if paper.source == "arxiv" and paper.arxiv_id else paper.title
        return DispatchContext(
            paper_id=paper.paper_id,
            user_id=ctx.user.user_id,
            default_paper=default_paper,
            mode=self._settings.retrieval_mode,
            level=ctx.level,
        )

    def _writer_input(self, turn: _Turn, handoff: Handoff) -> WriterInput:
        g = turn.gathered
        scaffold = handoff.concepts_to_scaffold
        prereq = g.prerequisites
        if (
            not scaffold
            and prereq is not None
            and prereq.matched is not None
            and turn.altitude != "technical"
        ):
            scaffold = [c.name for c in prereq.teaching_order if c.concept_id != prereq.matched.concept_id]
        return WriterInput(
            question=turn.ctx.question,
            level=turn.ctx.level,
            paper=turn.ctx.paper,
            plan=handoff.plan,
            altitude=turn.altitude,
            sources=turn.sources,
            concepts_to_scaffold=scaffold,
            learning_path=self._learning_path(g),
            external_papers=turn.external,
            quote=turn.ctx.quote,
            history=condensed_history(turn.ctx, self._settings.history_turns),
            on_call=lambda r: turn.trace.record_call("writer", r),
        )

    @staticmethod
    def _learning_path(g: Gathered) -> list[str]:
        """get_learning_path's steps, prerequisites first and the target last, for the writer."""
        path = g.learning_path
        if path is None or path.matched is None:
            return []
        return [step.name for step in path.steps]

    async def _review(self, turn: _Turn, emit: Emit) -> None:
        """One structured call after the answer: knowledge evidence, then follow-ups. Never fails the turn.

        Nothing here is written before the call returns: a turn abandoned
        while it runs changes nothing about what the reader is recorded as knowing.
        """
        if self._mastery is None:
            return
        ctx = turn.ctx
        judgement, knowledge = await asyncio.gather(
            self._mastery.classify(
                message=ctx.question,
                previous_answer=previous_answer(ctx),
                candidates=self._candidates(turn),
                answer=turn.text,
                not_yet_met=self._not_yet_met(turn),
                level=ctx.level,
                quote=ctx.quote.text if ctx.quote is not None else None,
                on_call=lambda r: turn.trace.record_call("mastery", r),
            ),
            self._current_knowledge(ctx),
        )
        if judgement is None:
            return
        if knowledge is None:
            # Without current confidences the policy cannot tell a new concept
            # from a self-reported one, and would overwrite the reader's own
            # claim with an inference. Better to learn nothing this turn.
            log.warning("mastery writes skipped: no knowledge for %s", ctx.user.user_id)
        else:
            turn.knowledge = update = await self._write_mastery(turn, judgement, knowledge)
            if update.added or update.removed or update.ask:
                await emit(update)
        turn.followups = judgement.followups
        if turn.followups:
            await emit(Followups(message_id=turn.answer_id, questions=turn.followups))

    async def _current_knowledge(self, ctx: TurnContext) -> dict[str, KnownConcept] | None:
        if ctx.knowledge is not None or self._knowledge_source is None:
            return ctx.knowledge
        try:
            return await self._knowledge_source(ctx.user.user_id)
        except Exception:
            log.warning("could not load %s's knowledge for the mastery pass", ctx.user.user_id, exc_info=True)
            return None

    def _candidates(self, turn: _Turn) -> list[str]:
        """Concept names the reader's message may say something about."""
        candidates: list[str] = []
        if (prereq := turn.gathered.prerequisites) is not None and prereq.matched is not None:
            candidates += [
                prereq.matched.name,
                *(g.name for g in prereq.gaps),
                *(k.name for k in prereq.known_stops),
            ]
        if turn.handoff is not None:
            candidates += turn.handoff.concepts_to_scaffold
        candidates += self._learning_path(turn.gathered)
        return candidates + turn.ctx.known_summary

    def _not_yet_met(self, turn: _Turn) -> list[str]:
        """Prerequisites the answer built on that the reader has not met, for the follow-ups.

        Empty for an expert: the rubric assumes their foundations, so a
        follow-up offering to explain the dot product would read as a slight.
        """
        if turn.ctx.level == "expert":
            return []
        prereq = turn.gathered.prerequisites
        if prereq is not None and prereq.matched is not None:
            return [c.name for c in prereq.teaching_order if c.concept_id != prereq.matched.concept_id]
        return self._learning_path(turn.gathered)[:-1]

    def _finalize(self, turn: _Turn) -> None:
        """Strip labels the writer was not given and record which S and P labels it used."""
        external = {e.label: e.ref for e in turn.external}
        valid = {s.label for s in turn.sources} | set(external)
        turn.text, cited = finalize_citations(turn.text, valid)
        turn.cited = [label for label in cited if label not in external]
        turn.cited_external = [external[label] for label in cited if label in external]

    async def _write_mastery(
        self, turn: _Turn, judgement: MasteryJudgement, knowledge: dict[str, KnownConcept]
    ) -> KnowledgeUpdated:
        assert self._mastery is not None
        writes = asyncio.create_task(self._mastery.apply(turn.ctx.user.user_id, judgement, knowledge))
        # Once the first mark_known/forget_concept is out, the rest must follow
        # or the graph is left half-updated: a disconnect now cancels the turn
        # but not the writes. The reader then misses only the event; the edges
        # are on their knowledge page, where each can be removed.
        self._background.add(writes)
        writes.add_done_callback(self._background.discard)
        return await asyncio.shield(writes)

    def _result(self, turn: _Turn) -> TurnResult:
        ctx, trace, now = turn.ctx, turn.trace, time.perf_counter()
        if turn.final_sources is None and turn.text:
            # A partial answer from a failed turn is stored as the reader saw it, minus invalid labels.
            self._finalize(turn)
        sources = turn.final_sources if turn.final_sources is not None else [s.ref for s in turn.sources]
        # The reader's wait: until answer.done, not until the review after it
        # finished. The trail summary shows this number beside the answer.
        ended = turn.answered_at or now
        u = trace.usage
        usage = UsageReport(
            model_calls=len(trace.model_calls),
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_input_tokens=u.cache_read_input_tokens,
            cache_creation_input_tokens=u.cache_creation_input_tokens,
            cost_usd=round(trace.cost_usd, 6),
            latency_ms=round((ended - turn.started) * 1000),
            ttft_ms=round((turn.first_token_at - turn.started) * 1000) if turn.first_token_at else None,
            tool_calls=trace.executed_tool_calls,
            tool_retries=trace.tool_retries,
        )
        created = _now()
        return TurnResult(
            turn_id=ctx.turn_id,
            question=ctx.question,
            level=ctx.level,
            answer=turn.text,
            cited=turn.cited,
            sources=sources,
            external=turn.cited_external,
            followups=turn.followups,
            plan=turn.handoff.plan if turn.handoff else "",
            altitude=turn.altitude,
            gaps=turn.gathered.gaps_event,
            knowledge=turn.knowledge,
            usage=usage,
            error=turn.error,
            trace=trace,
            user_message=ChatMessage(
                message_id=ctx.user_message_id,
                role="user",
                text=ctx.question,
                level=ctx.level,
                quote=ctx.quote,
                created_at=created,
            ),
            assistant_message=ChatMessage(
                message_id=turn.answer_id,
                role="assistant",
                text=turn.text,
                level=ctx.level,
                sources=sources,
                external=turn.cited_external,
                gaps=turn.gathered.gaps_event,
                activity=turn.gathered.activity,
                followups=turn.followups,
                latency_ms=usage.latency_ms,
                error=turn.error.message if turn.error else None,
                error_code=turn.error.code if turn.error else None,
                created_at=created,
            ),
        )

    async def _persist(self, ctx: TurnContext, result: TurnResult) -> None:
        if self._persist_fn is None:
            return
        try:
            await self._persist_fn(ctx, result)
        except Exception:
            # The reader already has the answer; losing it from the transcript
            # is worth a log line, not an error event.
            log.exception("persisting turn %s failed", ctx.turn_id)

    async def prewarm(self, paper: PaperMeta) -> list[CallResult]:
        """Write the agent's and the writer's tools+system cache entries for ``paper``.

        Worth it only when the first question is still seconds away (session
        open). Uses the same thinking/effort settings as real traffic, since
        both are part of the cache key. The review call is not prewarmed: its
        structured-output schema is rendered into the prefix ahead of the
        system breakpoint, and ``max_tokens: 0`` rejects a request carrying
        it, so a prewarm could only write an entry the review never reads.
        """
        if not (self._settings.prompt_caching and self._settings.prewarm_cache):
            return []
        settings = self._settings
        agent = agent_request(tools=self._tools, paper=paper, messages=[], caching=True)
        calls = [self._llm.prewarm(system=agent.system, tools=agent.tools, effort=settings.agent_effort)]
        if isinstance(self._writer, ClaudeWriter):
            writer = writer_request(paper=paper, user_content="", caching=True)
            calls.append(self._llm.prewarm(system=writer.system, tools=None, effort=settings.writer_effort))
        results = await asyncio.gather(*calls, return_exceptions=True)
        warmed: list[CallResult] = []
        for r in results:
            if isinstance(r, CallResult):
                warmed.append(r)
            else:
                log.warning("cache prewarm failed: %r", r)
        return warmed


def build_runner(
    settings: Settings,
    *,
    llm: LLMClient,
    mcp: MCPCaller,
    knowledge_source: KnowledgeSource | None = None,
    persist: PersistFn | None = None,
) -> AgentRunner:
    """The runner as the app and the scripts use it, after the MCP servers are up.

    Checks the tool registry against the servers' advertised schemas first, so
    a renamed MCP parameter fails at startup rather than on a reader's question.
    """
    registry = build_registry()
    registry.verify_against(mcp)
    return AgentRunner(
        llm=llm,
        mcp=mcp,
        registry=registry,
        writer=make_writer(settings, llm),
        settings=settings,
        mastery=MasteryPass(llm=llm, mcp=mcp, settings=settings),
        knowledge_source=knowledge_source,
        persist=persist,
    )
