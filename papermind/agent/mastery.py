"""After the answer: what did the reader's message reveal, and what might they ask next?

One low-effort structured-output call reviews the finished exchange and returns
three lists: concepts the reader demonstrated, concepts they revealed they do
not know, and up to two follow-up questions grounded in the answer. It runs
after ``answer.done`` because the follow-ups are about the answer; knowledge
evidence still comes only from the reader's own message, and an item whose
quoted evidence is not in that message is discarded here, whatever the model
said.

The update rules are asymmetric on purpose. Agreement is a guess -- "ok,
makes sense" is what people say whether or not it does -- while a question is
ground truth: nobody asks "what's softmax?" about something they know. So
negative evidence moves confidence further than positive evidence, inference
alone never reaches certainty, and an explicit "what is X?" removes the edge
outright.

    no KNOWS edge         counts as 0.3
    weak positive         +0.2        strong positive   +0.5
    inferred confidence   capped at 0.9 (only the reader can claim certainty)
    weak negative         -0.4        strong negative   forget_concept

What the reader told PaperMind themselves (``self_reported`` or ``onboarding``)
is theirs: it is never lowered or removed. Negative evidence about such a
concept is reported in ``KnowledgeUpdated.ask`` and the UI asks the reader.

A concept is known at ``confidence >= settings.known_threshold`` (0.6). Writes
go through the prereq_graph MCP tools with source "inferred" and the reader's
own words as evidence; the UI shows every crossing and offers undo. Nothing
here may fail the turn: the answer has already been delivered.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from papermind.agent.cache_policy import mastery_system
from papermind.agent.labels import strip_citations
from papermind.config import Settings
from papermind.graph.normalize import normalize_concept
from papermind.llm.client import CallResult, LLMClient, PaperMindLLMError
from papermind.mcp_client.manager import MCPCaller
from papermind.schemas.events import KnowledgeUpdated
from papermind.schemas.session import KnownConcept
from papermind.schemas.tools import (
    CheckPrerequisitesResult,
    ConceptRef,
    ForgetConceptServerArgs,
    Level,
    MarkKnownResult,
    MarkKnownServerArgs,
)

log = logging.getLogger(__name__)

NO_EDGE_CONFIDENCE = 0.3
WEAK_POSITIVE = 0.2
STRONG_POSITIVE = 0.5
INFERRED_CAP = 0.9
WEAK_NEGATIVE = 0.4
MAX_ITEMS = 5
MAX_CANDIDATES = 60
MAX_FOLLOWUPS = 2
FOLLOWUP_CHARS = 160
CLASSIFIER_MAX_TOKENS = 4000
PREVIOUS_ANSWER_CHARS = 1500
ANSWER_CHARS = 3000
QUOTE_CHARS = 1000
# The reader's own claims about what they know; the mastery pass never overrides them.
READER_OWNED = frozenset({"self_reported", "onboarding"})

Strength = Literal["weak", "strong"]
Evidence = Literal["demonstrated", "unfamiliar"]
Change = Literal["added", "removed", "ask"]


class MasteryItem(BaseModel):
    concept: str = Field(description="canonical concept name, e.g. 'softmax'")
    evidence: str = Field(description="short verbatim quote copied from the reader's latest message")
    strength: Strength


def _all_required(schema: dict[str, Any]) -> None:
    # Python callers may leave followups out, but the model must always
    # decide on it: an optional field is one it can silently skip.
    schema["required"] = list(schema.get("properties", {}))


class MasteryJudgement(BaseModel):
    """The review call's output: knowledge evidence from the reader's message, plus follow-ups."""

    model_config = ConfigDict(json_schema_extra=_all_required)

    demonstrated: list[MasteryItem] = Field(description="concepts the reader showed they understand")
    unfamiliar: list[MasteryItem] = Field(description="concepts the reader showed they do not know")
    # No max_length: the SDK enforces array limits client-side, and one extra
    # question must not throw away the knowledge evidence with it.
    followups: list[str] = Field(
        default_factory=list,
        description="0 to 2 short, specific next questions in the reader's voice, grounded in the answer",
    )


@dataclass(frozen=True)
class Decision:
    action: Literal["mark", "forget", "none", "ask"]
    confidence: float | None = None


def decide(
    current: float | None, kind: Evidence, strength: Strength, *, reader_owned: bool = False
) -> Decision:
    """The confidence policy as a pure function. ``current`` is None when there is no edge."""
    if reader_owned:
        # The reader said they know it. Contrary evidence is a reason to ask, not to overrule them.
        return Decision("ask") if kind == "unfamiliar" else Decision("none")
    if kind == "demonstrated":
        base = NO_EDGE_CONFIDENCE if current is None else current
        target = min(base + (STRONG_POSITIVE if strength == "strong" else WEAK_POSITIVE), INFERRED_CAP)
        # Never lower an edge with positive evidence.
        if current is not None and target <= current:
            return Decision("none")
        return Decision("mark", round(target, 3))
    if current is None:
        # No edge already reads as "not known"; there is nothing to lower.
        return Decision("none")
    if strength == "strong":
        return Decision("forget")
    target = current - WEAK_NEGATIVE
    if target <= 0:
        return Decision("forget")
    return Decision("mark", round(target, 3))


def _crossing(
    before: float | None, after: float | None, threshold: float
) -> Literal["added", "removed"] | None:
    was = before is not None and before >= threshold
    now = after is not None and after >= threshold
    if now and not was:
        return "added"
    if was and not now:
        return "removed"
    return None


def _clip(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_classifier_message(
    message: str,
    previous_answer: str | None,
    candidates: Iterable[str],
    *,
    answer: str | None = None,
    not_yet_met: Iterable[str] = (),
    level: Level | None = None,
    quote: str | None = None,
) -> str:
    """The review call's user message, in the tagged sections prompts/mastery.md describes.

    The reader's message sits between the context before it and the answer
    after it, in the order the exchange happened.
    """
    names = list(dict.fromkeys(c for c in candidates if c))[:MAX_CANDIDATES]
    parts: list[str] = []
    if level is not None:
        parts += ["<reader>", f"level: {level}", "</reader>", ""]
    parts += ["<concepts_in_play>", ", ".join(names) or "none", "</concepts_in_play>", ""]
    if previous_answer:
        context = _clip(strip_citations(previous_answer), PREVIOUS_ANSWER_CHARS)
        parts += ["<previous_answer>", "(context only; never evidence)", context, "</previous_answer>", ""]
    if quote:
        parts += [
            "<selected_passage>",
            "(the paper's words, not the reader's)",
            _clip(quote, QUOTE_CHARS),
            "</selected_passage>",
            "",
        ]
    parts += ["<reader_message>", message.strip(), "</reader_message>"]
    if answer:
        parts += [
            "",
            "<answer>",
            "(for follow-ups only; never evidence)",
            _clip(strip_citations(answer), ANSWER_CHARS),
            "</answer>",
        ]
        unmet = list(dict.fromkeys(c for c in not_yet_met if c))
        parts += ["", "<not_yet_met>", ", ".join(unmet) or "none", "</not_yet_met>"]
    return "\n".join(parts)


def _words(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def quoted_from(evidence: str, message: str) -> bool:
    """Whether ``evidence`` is a quote of ``message``, ignoring case, punctuation and spacing."""
    quote = _words(evidence)
    return bool(quote) and f" {quote} " in f" {_words(message)} "


def own_words_only(judgement: MasteryJudgement, message: str) -> MasteryJudgement:
    """Drop items whose evidence is not a quote of the reader's message.

    The model sees the answer too (for the follow-ups), and an item quoting
    the answer is exactly the mistake rule 1 of the prompt forbids: crediting
    the reader with what PaperMind explained.
    """

    def keep(items: list[MasteryItem]) -> list[MasteryItem]:
        kept = [i for i in items if quoted_from(i.evidence, message)]
        for dropped in (i for i in items if i not in kept):
            log.info(
                "mastery: dropped %r, evidence %r is not the reader's", dropped.concept, dropped.evidence
            )
        return kept

    return judgement.model_copy(
        update={"demonstrated": keep(judgement.demonstrated), "unfamiliar": keep(judgement.unfamiliar)}
    )


def clean_followups(questions: Iterable[str], asked: str) -> list[str]:
    """At most two distinct, non-empty questions that are not the one just asked."""
    seen = {_words(asked)}
    out: list[str] = []
    for q in questions:
        q = " ".join(q.split()).strip(" -•*\"'“”")
        key = _words(q)
        if not key or key in seen or len(q) > FOLLOWUP_CHARS:
            continue
        seen.add(key)
        out.append(q)
        if len(out) == MAX_FOLLOWUPS:
            break
    return out


@dataclass(frozen=True)
class _Verdict:
    concept: ConceptRef
    kind: Evidence
    item: MasteryItem

    @property
    def weight(self) -> tuple[bool, bool]:
        # Negative before positive, strong before weak: the order in which
        # evidence about one concept should win (see the module docstring).
        return (self.kind == "unfamiliar", self.item.strength == "strong")


class MasteryPass:
    """Review the exchange, then write what the reader's message revealed.

    The two halves are separate calls so the loop decides when writes may
    happen: a write is a change the reader must be shown, so it cannot happen
    in a turn that fails or is abandoned before ``knowledge.updated`` goes out.
    """

    def __init__(self, *, llm: LLMClient, mcp: MCPCaller, settings: Settings) -> None:
        self._llm = llm
        self._mcp = mcp
        self._settings = settings

    async def run(
        self,
        *,
        user_id: str,
        message: str,
        previous_answer: str | None,
        knowledge: dict[str, KnownConcept],
        candidates: Iterable[str] = (),
        on_call: Callable[[CallResult], None] | None = None,
    ) -> KnowledgeUpdated:
        """``classify`` then ``apply``. Returns the known/unknown crossings and the concepts to ask about."""
        judgement = await self.classify(
            message=message, previous_answer=previous_answer, candidates=candidates, on_call=on_call
        )
        if judgement is None:
            return KnowledgeUpdated()
        return await self.apply(user_id, judgement, knowledge)

    async def classify(
        self,
        *,
        message: str,
        previous_answer: str | None,
        candidates: Iterable[str] = (),
        answer: str | None = None,
        not_yet_met: Iterable[str] = (),
        level: Level | None = None,
        quote: str | None = None,
        on_call: Callable[[CallResult], None] | None = None,
    ) -> MasteryJudgement | None:
        """The review of one exchange; None if the call failed. Writes nothing.

        Knowledge items are already limited to evidence quoted from ``message``
        and the follow-ups are cleaned (see ``clean_followups``).
        """
        content = render_classifier_message(
            message,
            previous_answer,
            candidates,
            answer=answer,
            not_yet_met=not_yet_met,
            level=level,
            quote=quote,
        )
        try:
            judgement = await self._classify(content, on_call)
        except Exception:
            log.warning("turn review failed; knowledge unchanged and no follow-ups", exc_info=True)
            return None
        judgement = own_words_only(judgement, message)
        return judgement.model_copy(update={"followups": clean_followups(judgement.followups, message)})

    async def apply(
        self, user_id: str, judgement: MasteryJudgement, knowledge: dict[str, KnownConcept]
    ) -> KnowledgeUpdated:
        """Apply the confidence policy and write the edges. Never raises."""
        try:
            return await self._apply(user_id, judgement, knowledge)
        except Exception:
            log.warning("mastery writes failed; knowledge may be partly updated", exc_info=True)
            return KnowledgeUpdated()

    async def _classify(self, content: str, on_call: Callable[[CallResult], None] | None) -> MasteryJudgement:
        try:
            result = await self._llm.parse(
                system=mastery_system(caching=self._settings.prompt_caching),
                messages=[{"role": "user", "content": content}],
                schema=MasteryJudgement,
                effort=self._settings.classifier_effort,
                max_tokens=CLASSIFIER_MAX_TOKENS,
            )
        except PaperMindLLMError as exc:
            if exc.result is not None and on_call is not None:
                on_call(exc.result)
            raise
        if on_call is not None:
            on_call(result)
        assert isinstance(result.parsed, MasteryJudgement)
        return result.parsed

    async def _apply(
        self, user_id: str, judgement: MasteryJudgement, knowledge: dict[str, KnownConcept]
    ) -> KnowledgeUpdated:
        items: list[tuple[Evidence, MasteryItem]] = [
            *(("unfamiliar", i) for i in judgement.unfamiliar[:MAX_ITEMS]),
            *(("demonstrated", i) for i in judgement.demonstrated[:MAX_ITEMS]),
        ]
        if not items:
            return KnowledgeUpdated()

        by_id = {edge.concept_id: edge for edge in knowledge.values()}
        local: dict[str, ConceptRef] = {}
        for edge in by_id.values():
            ref = ConceptRef(concept_id=edge.concept_id, name=edge.name)
            local.setdefault(normalize_concept(edge.name), ref)
            local.setdefault(normalize_concept(edge.concept_id), ref)

        # Resolve every name before deciding anything. The classifier names
        # concepts in free text, and two names for one concept ("softmax",
        # "softmax function") must become one decision and one write: decided
        # separately they race, and the event can contradict the graph.
        names: dict[str, str] = {}  # lookup key -> the name as the classifier wrote it
        for _, item in items:
            names.setdefault(normalize_concept(item.concept), item.concept)
        refs = await asyncio.gather(*(self._concept(user_id, k, name, local) for k, name in names.items()))
        resolved = dict(zip(names, refs, strict=True))
        verdicts: dict[str, _Verdict] = {}
        for kind, item in items:
            ref = resolved[normalize_concept(item.concept)]
            if ref is None:
                continue
            verdict = _Verdict(ref, kind, item)
            held = verdicts.get(ref.concept_id)
            if held is None or verdict.weight > held.weight:
                verdicts[ref.concept_id] = verdict

        updates = await asyncio.gather(
            *(self._write(user_id, v, by_id.get(cid)) for cid, v in verdicts.items())
        )
        return KnowledgeUpdated(
            added=[ref for change, ref in updates if change == "added" and ref is not None],
            removed=[ref for change, ref in updates if change == "removed" and ref is not None],
            ask=[ref for change, ref in updates if change == "ask" and ref is not None],
        )

    async def _concept(
        self, user_id: str, key: str, name: str, local: dict[str, ConceptRef]
    ) -> ConceptRef | None:
        """The graph concept a classifier name refers to: an existing edge, else a graph lookup.

        The lookup matters for aliases of a known concept, which would
        otherwise be scored from the no-edge baseline and overwrite it.
        """
        if (ref := local.get(key)) is not None:
            return ref
        try:
            raw = await self._mcp.call(
                "prereq_graph", "check_prerequisites", {"concept": name, "user_id": user_id}
            )
            return CheckPrerequisitesResult.model_validate(raw).matched
        except Exception:
            log.warning("mastery: could not resolve %r", name, exc_info=True)
            return None

    async def _write(
        self, user_id: str, verdict: _Verdict, edge: KnownConcept | None
    ) -> tuple[Change | None, ConceptRef | None]:
        ref, item = verdict.concept, verdict.item
        before = edge.confidence if edge is not None else None
        owned = edge is not None and edge.source in READER_OWNED
        decision = decide(before, verdict.kind, item.strength, reader_owned=owned)
        if decision.action == "ask":
            # Only worth asking "still comfortable with it?" about something they still count as known.
            known = before is not None and before >= self._settings.known_threshold
            return ("ask" if known else None), ref
        if decision.action == "none":
            return None, ref
        try:
            if decision.action == "forget":
                args = ForgetConceptServerArgs(user_id=user_id, concept=ref.concept_id)
                raw = await self._mcp.call("prereq_graph", "forget_concept", args.model_dump(mode="json"))
                after = None
            else:
                mark = MarkKnownServerArgs(
                    user_id=user_id,
                    concept=ref.concept_id,
                    confidence=decision.confidence or 0.0,
                    source="inferred",
                    evidence=item.evidence.strip()[:500],
                )
                raw = await self._mcp.call("prereq_graph", "mark_known", mark.model_dump(mode="json"))
                after = decision.confidence
            written = MarkKnownResult.model_validate(raw)
        except Exception:
            log.warning("mastery update for %s failed", ref.concept_id, exc_info=True)
            return None, None
        if written.status == "not_found":
            return None, ref
        log.info(
            "mastery: %s %s (%s %s) %s -> %s",
            user_id,
            ref.concept_id,
            verdict.kind,
            item.strength,
            before,
            after,
        )
        return _crossing(before, after, self._settings.known_threshold), written.concept or ref
