"""Typed failures of the Semantic Scholar client.

The MCP server maps each class to a message the agent can act on, so the split
is by what the agent should do next, not by HTTP status: a rate limit or an
outage means "answer from the paper alone", a miss means "try another title".
"""

from __future__ import annotations


class ScholarError(Exception):
    """Anything that stopped a Semantic Scholar call from producing an answer."""


class ScholarNotFound(ScholarError):
    """The paper (or title match) does not exist in Semantic Scholar."""


class ScholarRateLimited(ScholarError):
    """Still throttled (HTTP 429) after the retry budget, or our own queue is too long."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ScholarUnavailable(ScholarError):
    """5xx responses, timeouts, or connection failures that outlasted the retries."""
