"""Token usage and what it costs.

Prices are USD per million tokens. Cache writes are the 5-minute TTL rate
(1.25x input); cache reads are listed per model because the discount differs
(Opus 5.5 reads at 0.05x input, most models at 0.1x).

A call is priced by the model that actually served it, not the one we asked
for: with server-side refusal fallbacks a declined request is re-run on another
model inside the same call, and ``usage.iterations`` then carries one entry per
model that ran.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MTOK = 1_000_000


@dataclass(frozen=True, slots=True)
class Price:
    input: float
    output: float
    cache_write: float
    cache_read: float


PRICES: dict[str, Price] = {
    "claude-opus-5-5": Price(input=4.0, output=20.0, cache_write=5.0, cache_read=0.20),
    "claude-opus-5": Price(input=5.0, output=25.0, cache_write=6.25, cache_read=0.50),
    "claude-opus-4-8": Price(input=5.0, output=25.0, cache_write=6.25, cache_read=0.50),
    "claude-sonnet-5-5": Price(input=2.0, output=10.0, cache_write=2.50, cache_read=0.20),
    "claude-haiku-4-5": Price(input=1.0, output=5.0, cache_write=1.25, cache_read=0.10),
}
DEFAULT_MODEL = "claude-opus-5-5"


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens + other.cache_creation_input_tokens,
        )

    @property
    def prompt_tokens(self) -> int:
        """The whole prompt: ``input_tokens`` alone is only the uncached remainder."""
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    @classmethod
    def from_api(cls, usage: Any) -> Usage:
        return cls(
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            cache_read_input_tokens=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            cache_creation_input_tokens=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        )


_warned: set[str] = set()


def price_for(model: str | None) -> Price:
    """Longest known prefix wins, so a dated or suffixed id still prices correctly."""
    name = model or DEFAULT_MODEL
    for known in sorted(PRICES, key=len, reverse=True):
        if name == known or name.startswith(known + "-"):
            return PRICES[known]
    if name not in _warned:
        _warned.add(name)
        log.warning("no price for model %r; costing it as %s", name, DEFAULT_MODEL)
    return PRICES[DEFAULT_MODEL]


def cost_of(usage: Usage, model: str | None) -> float:
    p = price_for(model)
    return (
        usage.input_tokens * p.input
        + usage.output_tokens * p.output
        + usage.cache_creation_input_tokens * p.cache_write
        + usage.cache_read_input_tokens * p.cache_read
    ) / MTOK


def call_cost(usage: Any, model: str | None) -> float:
    """Cost of one API call from its ``usage`` object.

    When the response lists sampling iterations (a fallback ran), each is
    priced at its own model; otherwise the top-level totals at ``model``.
    Compaction iterations are excluded because the API excludes them from the
    top-level totals too.
    """
    iterations: Iterable[Any] = getattr(usage, "iterations", None) or []
    sampled = [it for it in iterations if getattr(it, "type", None) in ("message", "fallback_message")]
    if len(sampled) > 1:
        return sum(cost_of(Usage.from_api(it), getattr(it, "model", None) or model) for it in sampled)
    return cost_of(Usage.from_api(usage), model)
