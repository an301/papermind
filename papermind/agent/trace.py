"""What happened during one turn, recorded for evals, logs and the usage event.

The eval harness reads a ``Trace`` rather than parsing events: which chunks each
search retrieved (recall@k needs the exact ids, in order), what every model call
cost and whether it hit the prompt cache, and every tool call, retry and error.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from papermind.agent.registry import COMPOSE_ANSWER
from papermind.llm.client import CallResult
from papermind.llm.pricing import Usage

log = logging.getLogger(__name__)

CallPurpose = Literal["agent", "writer", "mastery", "prewarm"]


@dataclass
class ModelCallRecord:
    purpose: CallPurpose
    step: int | None
    model: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float
    latency_ms: int
    ttft_ms: int | None
    request_id: str | None
    fell_back: bool = False


@dataclass
class ToolCallRecord:
    step: int
    call_id: str
    tool: str
    args: dict[str, Any]
    ok: bool
    duration_ms: int = 0
    error: str | None = None
    # "validation" errors were the model's fault and went back for a retry;
    # "execution" errors came from the tool or its server.
    error_kind: Literal["validation", "unknown_tool", "execution", "stopped"] | None = None
    summary: str | None = None


@dataclass
class Trace:
    turn_id: str
    model_calls: list[ModelCallRecord] = field(default_factory=list)
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    # One entry per successful search_paper call: the retrieved chunk ids in rank order.
    retrieved: list[list[str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    steps: int = 0
    json_retries: int = 0

    def record_call(self, purpose: CallPurpose, result: CallResult, step: int | None = None) -> None:
        u = result.usage
        self.model_calls.append(
            ModelCallRecord(
                purpose=purpose,
                step=step,
                model=result.model,
                stop_reason=result.stop_reason,
                input_tokens=u.input_tokens,
                output_tokens=u.output_tokens,
                cache_read_input_tokens=u.cache_read_input_tokens,
                cache_creation_input_tokens=u.cache_creation_input_tokens,
                cost_usd=result.cost_usd,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
                request_id=result.request_id,
                fell_back=result.fell_back,
            )
        )
        # Logged on every call: zero cache reads on a turn's second agent call
        # means something upstream of the breakpoint changed.
        log.info(
            "%s call step=%s stop=%s in=%d cache_read=%d cache_write=%d out=%d cost=$%.4f %dms request_id=%s",
            purpose,
            step,
            result.stop_reason,
            u.input_tokens,
            u.cache_read_input_tokens,
            u.cache_creation_input_tokens,
            u.output_tokens,
            result.cost_usd,
            result.latency_ms,
            result.request_id,
        )

    def error(self, message: str) -> None:
        self.errors.append(message)

    @property
    def usage(self) -> Usage:
        total = Usage()
        for c in self.model_calls:
            total = total + Usage(
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                cache_read_input_tokens=c.cache_read_input_tokens,
                cache_creation_input_tokens=c.cache_creation_input_tokens,
            )
        return total

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self.model_calls)

    @property
    def executed_tool_calls(self) -> int:
        """Calls that reached a tool server; the compose_answer hand-off is not one."""
        return sum(
            1
            for t in self.tool_calls
            if t.tool != COMPOSE_ANSWER and t.error_kind not in ("validation", "unknown_tool", "stopped")
        )

    @property
    def tool_retries(self) -> int:
        return sum(1 for t in self.tool_calls if t.error_kind in ("validation", "unknown_tool"))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
