"""What stands between a public URL and the operator's Anthropic bill.

Every question runs several model calls on the server's key, so a public
deployment needs three things a laptop does not:

* an access code, so only invited readers can use it at all;
* per-visitor rate limits on the two expensive actions (asking, adding papers);
* a daily spend ceiling across everyone, fed by the cost of every model call.

All three are off unless set in ``.env`` (see ``Settings``), so local
development behaves exactly as before. The counters are in
``papermind.memory.usage``.
"""

from __future__ import annotations

import hmac
import math

from fastapi import Request

from papermind.api.deps import ResourcesDep
from papermind.api.errors import APIError
from papermind.memory.usage import DAY_S

ACCESS_HEADER = "X-Access-Code"
HOUR_S = 60 * 60
# Wrong codes allowed per visitor per hour: enough for typos, too few to guess a code.
ACCESS_FAILURES_PER_HOUR = 20


def client_ip(request: Request) -> str:
    """The visitor's address. Behind the proxy, uvicorn's --proxy-headers has
    already replaced the proxy's address with the one it forwarded."""
    return request.client.host if request.client else "unknown"


def _wait(seconds: float) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = math.ceil(minutes / 60)
    return f"{hours} hours"


# ---------------------------------------------------------------- dependencies


async def require_access(request: Request, res: ResourcesDep) -> None:
    """Every route but /health: the visitor must send the server's access code."""
    settings = res.settings
    if not settings.access_code_required:
        return
    assert settings.access_code is not None
    given = request.headers.get(ACCESS_HEADER, "").strip()
    if not given:
        raise APIError(401, "access_code_required", "Enter the access code to use PaperMind.")
    expected = settings.access_code.get_secret_value().strip()
    if hmac.compare_digest(given.encode(), expected.encode()):
        return
    retry = await res.limiter.hit(
        "access-fail", client_ip(request), limit=ACCESS_FAILURES_PER_HOUR, window_s=HOUR_S
    )
    if retry is not None:
        raise APIError(
            429,
            "rate_limited",
            f"Too many wrong access codes from this network. Try again in {_wait(retry)}.",
            headers={"Retry-After": str(math.ceil(retry))},
        )
    raise APIError(401, "access_code_invalid", "That access code is not right. Check it and try again.")


async def within_budget(res: ResourcesDep) -> None:
    if await res.spend.over_budget():
        raise APIError(
            503,
            "daily_limit_reached",
            "PaperMind has used up today's question budget. It resets at midnight UTC; try again then.",
        )


async def limit_questions(request: Request, res: ResourcesDep) -> None:
    limit = res.settings.questions_per_hour
    if limit <= 0:
        return
    retry = await res.limiter.hit("questions", client_ip(request), limit=limit, window_s=HOUR_S)
    if retry is not None:
        raise APIError(
            429,
            "rate_limited",
            f"You have asked {limit} questions this hour, the most this server allows. "
            f"Try again in {_wait(retry)}.",
            headers={"Retry-After": str(math.ceil(retry))},
        )


async def limit_papers(request: Request, res: ResourcesDep) -> None:
    limit = res.settings.papers_per_day
    if limit <= 0:
        return
    retry = await res.limiter.hit("papers", client_ip(request), limit=limit, window_s=DAY_S)
    if retry is not None:
        raise APIError(
            429,
            "rate_limited",
            f"You have added {limit} papers today, the most this server allows. Try again in {_wait(retry)}.",
            headers={"Retry-After": str(math.ceil(retry))},
        )
