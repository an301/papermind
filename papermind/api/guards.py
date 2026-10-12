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
import ipaddress
import math

from fastapi import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from papermind.api.deps import Resources, ResourcesDep
from papermind.api.errors import APIError, error_response
from papermind.memory.usage import DAY_S

ACCESS_HEADER = "X-Access-Code"
HOUR_S = 60 * 60
# Wrong codes allowed per visitor per hour: enough for typos, too few to guess a code.
ACCESS_FAILURES_PER_HOUR = 20


def visitor_key(host: str | None) -> str:
    """Who to count a limit against: the address, or its /64 for IPv6, since
    one IPv6 connection usually controls a whole /64 and could rotate through it."""
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False).network_address) + "/64"
    return host


def client_ip(request: Request) -> str:
    """The visitor's limit key. Behind the proxy, uvicorn's --proxy-headers has
    already replaced the proxy's address with the one it forwarded."""
    return visitor_key(request.client.host if request.client else None)


def _wait(seconds: float) -> str:
    minutes = max(1, math.ceil(seconds / 60))
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours = math.ceil(minutes / 60)
    return f"{hours} hours"


# ---------------------------------------------------------------- access code


async def access_error(res: Resources, given: str, visitor: str) -> APIError | None:
    """Why this request may not proceed, or None. A visitor locked out for wrong
    guesses is refused before the code is even compared, so a right guess made
    during the lockout is not distinguishable from a wrong one."""
    settings = res.settings
    if not settings.access_code_required:
        return None
    assert settings.access_code is not None
    locked = await res.limiter.blocked(
        "access-fail", visitor, limit=ACCESS_FAILURES_PER_HOUR, window_s=HOUR_S
    )
    if locked is not None:
        return APIError(
            429,
            "rate_limited",
            f"Too many wrong access codes from this network. Try again in {_wait(locked)}.",
            headers={"Retry-After": str(math.ceil(locked))},
        )
    given = given.strip()
    if not given:
        return APIError(401, "access_code_required", "Enter the access code to use PaperMind.")
    expected = settings.access_code.get_secret_value().strip()
    if hmac.compare_digest(given.encode(), expected.encode()):
        return None
    await res.limiter.hit("access-fail", visitor, limit=ACCESS_FAILURES_PER_HOUR, window_s=HOUR_S)
    return APIError(401, "access_code_invalid", "That access code is not right. Check it and try again.")


class AccessGate:
    """ASGI middleware: every /api route but /health needs the access code.

    Middleware rather than a dependency because FastAPI reads (and spools) a
    multipart upload before it runs dependencies; here a request without the
    code is refused before a byte of its body is read.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or not path.startswith("/api/")
            or path == "/api/health"
            or scope["method"] == "OPTIONS"
        ):
            await self.app(scope, receive, send)
            return
        res: Resources | None = getattr(scope["app"].state, "resources", None)
        if res is None or not res.settings.access_code_required:
            await self.app(scope, receive, send)
            return
        given = next(
            (v.decode("latin-1") for k, v in scope["headers"] if k.lower() == ACCESS_HEADER.lower().encode()),
            "",
        )
        client = scope.get("client")
        error = await access_error(res, given, visitor_key(client[0] if client else None))
        if error is None:
            await self.app(scope, receive, send)
            return
        # The body is never read; closing tells the client not to send the rest.
        headers = {**(error.headers or {}), "Connection": "close"}
        await error_response(error.status_code, error.code, error.message, headers)(scope, receive, send)


# ---------------------------------------------------------------- dependencies


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
