"""One agent step's tool_use blocks in, one user message of tool_results out.

The model does not call functions; it emits JSON shaped like a call, and that
JSON is sometimes wrong. Every input is validated with the tool's argument
model before anything runs. A bad call becomes an ``is_error`` tool_result
whose text says exactly what to fix, so the model corrects itself on the next
step; a tool that keeps failing is switched off for the rest of the turn so the
loop cannot spin on it.

Context the model must not choose (which paper, which reader, which retrieval
mode) is injected here from the session, after validation, so a model that
tries to pass ``paper_id`` gets a validation error instead of another paper.

Every tool_use gets exactly one tool_result, in tool_use order, and the whole
list goes back as a single user message: the API pairs results by id, and one
message per step keeps the prompt-cache prefix append-only.

Parallel calls finish in whatever order the servers answer, so only the live
events (tool.started / tool.finished / gaps) follow completion order. What a
step leaves behind -- the results, failure counts, the trace, the gathered
chunks and papers, the activity trail -- is folded in afterwards in tool_use
order, so the turn is the same with ``parallel_tools`` on or off: the
fallback sources, the [P#] numbering and ``Trace.retrieved[0]`` ("the first
search") do not depend on which server was quicker.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from papermind.agent.registry import Registry, ToolSpec
from papermind.agent.trace import ToolCallRecord, Trace
from papermind.config import Settings
from papermind.mcp_client.manager import MCPCaller, ToolExecutionError
from papermind.schemas.events import ChatEvent, GapsFound, ToolFinished, ToolRetry, ToolStarted
from papermind.schemas.session import ActivityItem
from papermind.schemas.tools import (
    CheckPrerequisitesResult,
    ComposeAnswerArgs,
    LearningPathResult,
    Level,
    LookupPaperResult,
    PaperGraphResult,
    RetrievalMode,
    RetrievedChunk,
    ScholarPaper,
    SearchPaperResult,
)

log = logging.getLogger(__name__)

# Enough of a broken argument string for the model to see what went wrong.
MAX_RAW_JSON_CHARS = 2000

Emit = Callable[[ChatEvent], Awaitable[None]]


@dataclass(frozen=True)
class DispatchContext:
    """The session as the tools see it.

    ``paper_id``, ``user_id``, ``default_paper`` and ``mode`` are injected into
    MCP calls (field names match the ``*ServerArgs`` keys); ``level`` adjusts
    what the prerequisite tools report (see ``Dispatcher._calibrate``).
    """

    paper_id: str
    user_id: str
    default_paper: str | None
    mode: RetrievalMode
    level: Level = "intermediate"


@dataclass
class Gathered:
    """What the tools found during this turn."""

    chunks: dict[str, RetrievedChunk] = field(default_factory=dict)  # first-seen order
    prerequisites: CheckPrerequisitesResult | None = None
    gaps_event: GapsFound | None = None
    learning_path: LearningPathResult | None = None
    papers: dict[str, ScholarPaper] = field(default_factory=dict)  # every paper seen, by s2_id
    # The best match of each lookup_paper call, in call order: what the writer
    # may cite when the agent did not name the papers itself.
    lookups: list[ScholarPaper] = field(default_factory=list)
    activity: list[ActivityItem] = field(default_factory=list)


@dataclass
class StepResult:
    tool_results: list[dict[str, Any]]
    compose: ComposeAnswerArgs | None = None
    # compose_answer kept failing validation: the loop hands off without it.
    compose_exhausted: bool = False


@dataclass
class _Ran:
    """A routed call that has run, before it is folded into the turn."""

    block: dict[str, Any]
    spec: ToolSpec
    label: str
    args: dict[str, Any]
    duration_ms: int
    summary: str
    result: BaseModel | None
    error: str | None = None


def tool_result(call_id: str, content: str, *, is_error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": call_id, "content": content}
    if is_error:
        block["is_error"] = True
    return block


def _short_repr(value: Any, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def format_validation_error(exc: ValidationError, injected: tuple[str, ...] = ()) -> str:
    """'ValidationError: k - Input should be a valid integer (got 'five')', one clause per problem."""
    parts: list[str] = []
    for err in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in err["loc"]) or "arguments"
        msg = err["msg"]
        if err["type"] == "extra_forbidden" and loc in injected:
            msg += "; it is filled in automatically, so leave it out"
        given = err.get("input")
        got = (
            ""
            if err["type"] == "missing" or isinstance(given, dict | list)
            else f" (got {_short_repr(given)})"
        )
        parts.append(f"{loc} - {msg}{got}")
    return "ValidationError: " + "; ".join(parts)


class Dispatcher:
    """Runs tool calls for one turn. Create one per turn: failure counts are per turn."""

    def __init__(
        self,
        *,
        registry: Registry,
        mcp: MCPCaller,
        settings: Settings,
        context: DispatchContext,
        emit: Emit,
        trace: Trace,
        gathered: Gathered,
    ) -> None:
        self._registry = registry
        self._mcp = mcp
        self._settings = settings
        self._ctx = context
        self._emit = emit
        self._trace = trace
        self._gathered = gathered
        self._failures: Counter[str] = Counter()

    async def run_step(
        self,
        step: int,
        tool_uses: list[dict[str, Any]],
        malformed: Mapping[str, str] | None = None,
    ) -> StepResult:
        """Answer every tool_use of one agent step.

        ``malformed`` maps tool_use ids whose streamed input was not valid JSON
        to that raw text (``CallResult.malformed``). Their parsed ``input`` may
        be a silently truncated object, so they are never run, compose_answer
        included: each is answered with the raw text as an error to fix.
        """
        results: list[dict[str, Any] | None] = [None] * len(tool_uses)
        runnable: list[tuple[int, dict[str, Any], ToolSpec, BaseModel]] = []
        out = StepResult(tool_results=[])
        malformed = malformed or {}

        for i, block in enumerate(tool_uses):
            call_id, name = block["id"], block["name"]
            spec = self._registry.get(name)
            if spec is None:
                results[i] = await self._unknown_tool(step, block)
                continue
            if self._exhausted(name):
                results[i] = self._stopped(step, block)
                out.compose_exhausted |= name == "compose_answer"
                continue
            if call_id in malformed:
                results[i] = await self._invalid_json(step, block, spec, malformed[call_id])
                out.compose_exhausted |= spec.local and self._exhausted(name)
                continue
            try:
                args = spec.args_model.model_validate(block.get("input"))
            except ValidationError as exc:
                results[i] = await self._invalid(step, block, spec, exc)
                out.compose_exhausted |= spec.local and self._exhausted(name)
                continue
            if spec.local:
                assert isinstance(args, ComposeAnswerArgs)
                out.compose = args
                self._trace.tool_calls.append(
                    ToolCallRecord(
                        step=step, call_id=call_id, tool=name, args=args.model_dump(mode="json"), ok=True
                    )
                )
                results[i] = tool_result(call_id, "Handed to the writer.")
                continue
            runnable.append((i, block, spec, args))

        if self._settings.parallel_tools:
            ran = await asyncio.gather(*(self._execute(b, s, a) for _, b, s, a in runnable))
        else:
            ran = [await self._execute(b, s, a) for _, b, s, a in runnable]
        # Folded in tool_use order, not completion order: see the module docstring.
        for (i, *_), call in zip(runnable, ran, strict=True):
            results[i] = self._settle(step, call)

        out.tool_results = [r for r in results if r is not None]
        return out

    # ------------------------------------------------------------ rejected calls

    def _exhausted(self, name: str) -> bool:
        return self._failures[name] > self._settings.max_tool_retries

    def _stop_hint(self, name: str) -> str:
        if not self._exhausted(name):
            return f" Fix the arguments and call {name} again."
        return (
            f" {name} has failed {self._failures[name]} times in a row this turn: "
            f"stop calling {name} and continue without it."
        )

    async def _unknown_tool(self, step: int, block: dict[str, Any]) -> dict[str, Any]:
        name = block["name"]
        self._failures[name] += 1
        error = f"Unknown tool {name!r}. Valid tools: {', '.join(self._registry.names)}."
        self._trace.tool_calls.append(
            ToolCallRecord(
                step=step,
                call_id=block["id"],
                tool=name,
                args={},
                ok=False,
                error=error,
                error_kind="unknown_tool",
            )
        )
        await self._emit(ToolRetry(call_id=block["id"], tool=name, error=error, attempt=self._failures[name]))
        return tool_result(block["id"], error, is_error=True)

    async def _invalid(
        self, step: int, block: dict[str, Any], spec: ToolSpec, exc: ValidationError
    ) -> dict[str, Any]:
        self._failures[spec.name] += 1
        error = format_validation_error(exc, spec.inject)
        raw = block.get("input")
        self._trace.tool_calls.append(
            ToolCallRecord(
                step=step,
                call_id=block["id"],
                tool=spec.name,
                args=raw if isinstance(raw, dict) else {"_raw": raw},
                ok=False,
                error=error,
                error_kind="validation",
            )
        )
        await self._emit(
            ToolRetry(call_id=block["id"], tool=spec.name, error=error, attempt=self._failures[spec.name])
        )
        return tool_result(block["id"], error + self._stop_hint(spec.name), is_error=True)

    async def _invalid_json(
        self, step: int, block: dict[str, Any], spec: ToolSpec, raw: str
    ) -> dict[str, Any]:
        """Arguments that were not valid JSON: hand the raw text back so the model can rewrite it.

        Answering the block (rather than re-issuing the request) keeps the
        valid calls beside it and costs one tool_result instead of a whole
        repeated agent call. The wrapper is built with json.dumps so quotes in
        the broken text are escaped.
        """
        self._failures[spec.name] += 1
        shown = raw if len(raw) <= MAX_RAW_JSON_CHARS else raw[: MAX_RAW_JSON_CHARS - 1] + "…"
        error = "ValidationError: arguments - not valid JSON"
        self._trace.tool_calls.append(
            ToolCallRecord(
                step=step,
                call_id=block["id"],
                tool=spec.name,
                args={"_raw": shown},
                ok=False,
                error=error,
                error_kind="validation",
            )
        )
        await self._emit(
            ToolRetry(call_id=block["id"], tool=spec.name, error=error, attempt=self._failures[spec.name])
        )
        content = json.dumps({"INVALID_JSON": shown}, ensure_ascii=False)
        hint = "\nThese arguments are not valid JSON." + self._stop_hint(spec.name)
        return tool_result(block["id"], content + hint, is_error=True)

    def _stopped(self, step: int, block: dict[str, Any]) -> dict[str, Any]:
        name = block["name"]
        error = (
            f"Not run: {name} failed {self._failures[name]} times in a row this turn. "
            f"Stop calling {name} and continue without it."
        )
        self._trace.tool_calls.append(
            ToolCallRecord(
                step=step,
                call_id=block["id"],
                tool=name,
                args={},
                ok=False,
                error=error,
                error_kind="stopped",
            )
        )
        return tool_result(block["id"], error, is_error=True)

    # ------------------------------------------------------------ execution

    def _server_args(self, spec: ToolSpec, args: BaseModel) -> dict[str, Any]:
        injected = {key: getattr(self._ctx, key) for key in spec.inject}
        model = spec.server_args_model or spec.args_model
        full = model.model_validate({**args.model_dump(), **injected})
        return full.model_dump(mode="json", exclude_none=True)

    async def _execute(self, block: dict[str, Any], spec: ToolSpec, args: BaseModel) -> _Ran:
        """Run one routed call and emit its live events; ``_settle`` folds it into the turn."""
        assert spec.server is not None and spec.mcp_tool is not None and spec.result_model is not None
        call_id = block["id"]
        label = spec.label(args)
        model_args = args.model_dump(mode="json")
        await self._emit(ToolStarted(call_id=call_id, tool=spec.name, label=label, args=model_args))

        started = time.perf_counter()
        result: BaseModel | None = None
        error: str | None = None
        # The trail's failure line is fixed copy; why it failed goes in the detail.
        failure_kind = "tool_error"
        try:
            raw = await self._mcp.call(spec.server, spec.mcp_tool, self._server_args(spec, args))
            result = self._calibrate(spec.result_model.model_validate(raw))
        except ToolExecutionError as exc:
            error, failure_kind = exc.message, exc.kind
            log.warning("tool %s failed (%s): %s", spec.name, exc.kind, exc.message)
        except ValidationError as exc:
            error, failure_kind = "the tool returned a result in an unexpected shape", "bad_result"
            log.error("tool %s returned an invalid %s: %s", spec.name, spec.result_model.__name__, exc)
        except Exception:
            # A bug on our side of the call must not end the turn; the model is
            # told the tool failed and carries on without it.
            error, failure_kind = "the tool failed unexpectedly", "internal"
            log.exception("tool %s raised", spec.name)
        duration_ms = round((time.perf_counter() - started) * 1000)

        summary = spec.summarize(result) if result is not None else spec.failure
        await self._emit(
            ToolFinished(
                call_id=call_id,
                tool=spec.name,
                ok=result is not None,
                duration_ms=duration_ms,
                summary=summary,
                detail=spec.detail(result) if result is not None else {"error": failure_kind},
            )
        )
        if (gaps := _gaps_event(result)) is not None:
            await self._emit(gaps)
        return _Ran(
            block=block,
            spec=spec,
            label=label,
            args=model_args,
            duration_ms=duration_ms,
            summary=summary,
            result=result,
            error=error,
        )

    def _settle(self, step: int, ran: _Ran) -> dict[str, Any]:
        """Fold a finished call into the failure counts, the trace and what was gathered."""
        spec, call_id, summary = ran.spec, ran.block["id"], ran.summary
        ok = ran.result is not None
        self._trace.tool_calls.append(
            ToolCallRecord(
                step=step,
                call_id=call_id,
                tool=spec.name,
                args=ran.args,
                ok=ok,
                duration_ms=ran.duration_ms,
                error=ran.error,
                error_kind=None if ok else "execution",
                summary=summary,
            )
        )
        self._gathered.activity.append(
            ActivityItem(tool=spec.name, label=ran.label, summary=summary, ok=ok, duration_ms=ran.duration_ms)
        )
        if ran.result is not None:
            self._failures[spec.name] = 0
            self._absorb(ran.result)
            content = json.dumps(spec.for_model(ran.result), ensure_ascii=False, separators=(",", ":"))
            return tool_result(call_id, content)
        self._failures[spec.name] += 1
        hint = self._stop_hint(spec.name) if self._exhausted(spec.name) else ""
        return tool_result(call_id, f"Error: {ran.error}.{hint}", is_error=True)

    def _calibrate(self, result: BaseModel) -> BaseModel:
        """Hold an expert's answer at the technical altitude, whatever the gap count.

        The rubric defines an expert as familiar with transformers, so for a
        reader who chose expert the foundations the graph lists as gaps are
        unrecorded rather than unknown (most often: they never onboarded).
        Left alone, 20-odd such "gaps" select the intuition altitude and a
        learning path through dot products. Adjusting the result here means
        the model, the gaps event and the writer all see the same altitude.
        """
        if (
            self._ctx.level == "expert"
            and isinstance(result, CheckPrerequisitesResult)
            and result.altitude != "technical"
        ):
            return result.model_copy(update={"altitude": "technical"})
        return result

    def _absorb(self, result: BaseModel) -> None:
        g = self._gathered
        if isinstance(result, SearchPaperResult):
            for chunk in result.chunks:
                g.chunks.setdefault(chunk.chunk_id, chunk)
            self._trace.retrieved.append([c.chunk_id for c in result.chunks])
        elif isinstance(result, CheckPrerequisitesResult):
            g.prerequisites = result
            if (gaps := _gaps_event(result)) is not None:
                g.gaps_event = gaps
        elif isinstance(result, LearningPathResult):
            g.learning_path = result
        elif isinstance(result, LookupPaperResult):
            for paper in result.matches:
                g.papers.setdefault(paper.s2_id, paper)
            if result.matches:
                g.lookups.append(g.papers[result.matches[0].s2_id])
        elif isinstance(result, PaperGraphResult):
            for paper in [*([result.resolved] if result.resolved else []), *result.papers]:
                g.papers.setdefault(paper.s2_id, paper)


def _gaps_event(result: BaseModel | None) -> GapsFound | None:
    if not isinstance(result, CheckPrerequisitesResult) or result.matched is None:
        return None
    return GapsFound(
        concept=result.matched,
        gaps=result.gaps,
        teaching_order=result.teaching_order,
        altitude=result.altitude,
    )
