"""Semantic Scholar Academic Graph client: lookup, references, citations.

The HTTP calls are trivial; the engineering is around them:

* **Resolution.** Ids (arXiv, DOI, S2, CorpusId, URL) go to ``/paper/{id}``;
  titles go to ``/paper/search/match`` and fall back to ``/paper/search``.
* **Caching.** Raw response bodies, keyed by path + params, TTL from settings;
  404s are cached briefly. Concurrent identical requests share one fetch.
* **Rate limiting.** Every request takes a token from the process-wide bucket.
* **Partial answers.** References/citations by id fetch the list and the
  paper's own record together; the record (only the header) gets a short grace
  period after the list arrives instead of holding the call through retries.
* **Retries.** 429, 5xx, timeouts, and connection errors are retried with
  exponential backoff and full jitter (Retry-After wins when S2 sends one), at
  most ``MAX_ATTEMPTS`` tries, all inside one deadline per public call that ends
  before the MCP client would give up on the tool call.
"""

from __future__ import annotations

import asyncio
import email.utils
import hashlib
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC
from functools import partial
from typing import Any, Literal, Protocol
from urllib.parse import quote, urlencode

import httpx

from papermind.config import Settings, get_settings
from papermind.schemas.tools import PaperGraphResult, ScholarPaper
from papermind.scholar.cache import Cache, NullCache, open_cache
from papermind.scholar.errors import ScholarError, ScholarNotFound, ScholarRateLimited, ScholarUnavailable
from papermind.scholar.query import classify, search_query, title_similarity
from papermind.scholar.ratelimit import TokenBucket, shared_bucket
from papermind.scholar.reshape import most_cited_first, papers_from_graph_page, to_scholar_paper

logger = logging.getLogger(__name__)

# referenceCount is not part of ScholarPaper, but it is the only source for
# PaperGraphResult.total on references: the references endpoint pages carry no total.
PAPER_FIELDS = "paperId,title,year,authors,venue,abstract,tldr,citationCount,url,externalIds,referenceCount"
# The references/citations endpoints reject tldr with a 400
# ("Unrecognized or unsupported fields: [tldr]").
GRAPH_FIELDS = "paperId,title,year,authors,venue,abstract,citationCount,url,externalIds"

# S2 cannot sort references or citations (citations arrive newest-first). One
# large page lets us return the most-cited entries instead of an arbitrary
# slice, and most papers have fewer than 100 references, so for references this
# is usually the whole bibliography. The page size is fixed so every `limit`
# shares one cache entry.
GRAPH_PAGE_SIZE = 100
GRAPH_MAX_PAGE = 1000  # documented maximum for references/citations
SEARCH_MAX_LIMIT = 100  # documented maximum for /paper/search

# search/match always returns the closest title in the corpus. For an exact
# title that is the paper; for a vague query ("bahdanau attention") it is a
# confident wrong answer. Below this similarity, relevance search leads.
MATCH_CONFIDENCE = 0.8

# Short, because a 404 for a new arXiv paper usually turns into a hit once S2
# ingests it, and a typo'd title should not stay "missing" for a week.
NEGATIVE_TTL_SECONDS = 60 * 60

MAX_ATTEMPTS = 4
REQUEST_TIMEOUT_S = 10.0
CONNECT_TIMEOUT_S = 5.0
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 8.0
# Full jitter can draw ~0 s. After a 429 an immediate retry is certain to be
# throttled again and only burns an attempt, so rate limits wait at least this.
RATE_LIMIT_MIN_DELAY_S = 1.0

# S2 throttles per endpoint: keyless, /paper/{id} and the search endpoints 429
# for long stretches while /references and /citations still answer (measured
# 2026-10-09). For references/citations by id, the paper's own record is only
# the header (resolved + total), so once the list is in hand it gets this long
# to finish, rather than holding the whole tool call through its retries. It
# keeps running in the background and lands in the cache for the next call.
HEADER_GRACE_S = 1.5

_NOT_FOUND: dict[str, Any] = {"__s2_not_found__": True}

GraphKind = Literal["references", "citations"]
Sleep = Callable[[float], Awaitable[None]]


class ScholarAPI(Protocol):
    """What the MCP server needs; ScholarClient implements it, tests fake it."""

    async def lookup(self, query: str, limit: int = 3) -> list[ScholarPaper]: ...

    async def references(self, paper: str, limit: int = 10) -> PaperGraphResult: ...

    async def citations(self, paper: str, limit: int = 10) -> PaperGraphResult: ...

    async def aclose(self) -> None: ...


@dataclass(slots=True)
class ScholarStats:
    http_requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    retries: int = 0


@dataclass(slots=True)
class _Deadline:
    at: float
    clock: Callable[[], float] = field(repr=False)

    def remaining(self) -> float:
        return max(0.0, self.at - self.clock())


def cache_key(path: str, params: Mapping[str, Any]) -> str:
    """Readable path, hashed params (titles and field lists make keys long)."""
    canonical = urlencode(sorted((k, str(v)) for k, v in params.items()))
    return f"{path}:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


def parse_retry_after(value: str | None, *, now: Callable[[], float] = time.time) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, when.timestamp() - now())


def _error_text(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text.strip()[:200] or f"HTTP {resp.status_code}"
    if isinstance(body, Mapping):
        return str(body.get("error") or body.get("message") or f"HTTP {resp.status_code}")
    return f"HTTP {resp.status_code}"


def _first_match(body: Mapping[str, Any] | None) -> dict[str, Any] | None:
    data = (body or {}).get("data") or []
    first = data[0] if data else None
    return first if isinstance(first, dict) and first.get("paperId") else None


def _dedupe(raws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for raw in raws:
        pid = raw.get("paperId") if isinstance(raw, dict) else None
        if pid and pid not in seen:
            seen.add(pid)
            out.append(raw)
    return out


def _path_id(api_id: str) -> str:
    # DOIs may contain characters that are not path-safe; ':' and '/' are part of S2's id syntax.
    return quote(api_id, safe=":/")


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


class ScholarClient:
    """Async Semantic Scholar client. Use ``await ScholarClient.create()`` in production.

    The constructor takes every collaborator explicitly so tests can swap the
    cache, transport, limiter, clock, sleep, and jitter source.
    """

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        cache: Cache | None = None,
        http: httpx.AsyncClient | None = None,
        limiter: TokenBucket | None = None,
        max_attempts: int = MAX_ATTEMPTS,
        request_timeout_s: float = REQUEST_TIMEOUT_S,
        budget_s: float | None = None,
        header_grace_s: float = HEADER_GRACE_S,
        sleep: Sleep = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: random.Random | None = None,
    ) -> None:
        settings = settings or get_settings()
        api_key = settings.s2_api_key.get_secret_value().strip() if settings.s2_api_key else ""
        self.authenticated = bool(api_key)
        self._base_url = settings.s2_base_url.rstrip("/")
        self._headers = {"User-Agent": f"PaperMind/{settings.app_version}", "Accept": "application/json"}
        if api_key:
            self._headers["x-api-key"] = api_key
        self._ttl = settings.s2_cache_ttl_seconds
        self._cache: Cache = cache if cache is not None else NullCache()
        self._owns_cache = False
        self._owns_http = http is None
        self._http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(request_timeout_s, connect=CONNECT_TIMEOUT_S),
            follow_redirects=True,
        )
        self._limiter = limiter or shared_bucket(authenticated=self.authenticated)
        self._max_attempts = max(1, max_attempts)
        self._timeout_s = request_timeout_s
        # The MCP client abandons a tool call after mcp_call_timeout_s. Failing
        # cleanly a few seconds earlier gives the agent an actionable error
        # instead of a dead call.
        self._budget_s = budget_s if budget_s is not None else max(5.0, settings.mcp_call_timeout_s - 5.0)
        self._header_grace_s = header_grace_s
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._inflight: dict[str, asyncio.Future[dict[str, Any] | None]] = {}
        self.stats = ScholarStats()

    @classmethod
    async def create(cls, settings: Settings | None = None, **kwargs: Any) -> ScholarClient:
        """A client with the shared cache (Redis, or files if Redis is down); it owns and closes the cache."""
        cache = await open_cache(settings)
        client = cls(settings=settings, cache=cache, **kwargs)
        client._owns_cache = True
        return client

    async def __aenter__(self) -> ScholarClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        # Fetches outliving their callers (a header past its grace period) must
        # not run on against a closed HTTP pool or cache.
        pending = list(self._inflight.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if self._owns_http:
            await self._http.aclose()
        if self._owns_cache:
            await self._cache.aclose()

    # ------------------------------------------------------------------ public API

    async def lookup(self, query: str, limit: int = 3) -> list[ScholarPaper]:
        """Candidate papers for a title, arXiv id, DOI, S2 id, or URL. Empty when nothing matches.

        A confident title match returns just that paper; ``limit`` bounds the
        candidates only when the lookup falls back to relevance search.
        """
        limit = _clamp(limit, 1, SEARCH_MAX_LIMIT)
        deadline = self._deadline()
        ref = classify(query)
        if ref.kind == "upload":
            return []
        if ref.api_id is not None:
            raws = [raw] if (raw := await self._paper(ref.api_id, deadline)) else []
        else:
            raws = await self._title_candidates(ref.value, limit, deadline)
        return [paper for raw in raws if (paper := to_scholar_paper(raw)) is not None]

    async def resolve(self, paper: str) -> ScholarPaper:
        """The single paper a reference resolves to; raises ScholarNotFound."""
        resolved = to_scholar_paper(await self._resolve_raw(paper, self._deadline()))
        if resolved is None:
            raise ScholarNotFound(f"Semantic Scholar has no paper matching {paper!r}")
        return resolved

    async def references(self, paper: str, limit: int = 10) -> PaperGraphResult:
        """Papers this paper cites, most-cited first."""
        return await self._graph(paper, limit, "references")

    async def citations(self, paper: str, limit: int = 10) -> PaperGraphResult:
        """Papers citing this one: the most-cited of the newest ``GRAPH_PAGE_SIZE``."""
        return await self._graph(paper, limit, "citations")

    # ------------------------------------------------------------------ resolution

    def _deadline(self) -> _Deadline:
        return _Deadline(self._clock() + self._budget_s, self._clock)

    async def _paper(self, api_id: str, deadline: _Deadline) -> dict[str, Any] | None:
        return await self._get_json(f"paper/{_path_id(api_id)}", {"fields": PAPER_FIELDS}, deadline)

    async def _title_candidates(self, title: str, limit: int, deadline: _Deadline) -> list[dict[str, Any]]:
        best = _first_match(
            await self._get_json("paper/search/match", {"query": title, "fields": PAPER_FIELDS}, deadline)
        )
        if best is not None and title_similarity(title, best.get("title") or "") >= MATCH_CONFIDENCE:
            return [best]
        try:
            found = await self._get_json(
                "paper/search",
                {"query": search_query(title), "fields": PAPER_FIELDS, "limit": limit, "offset": 0},
                deadline,
            )
        except ScholarError:
            # A weak match beats an error: the agent sees the title and can judge it.
            if best is not None:
                return [best]
            raise
        candidates = [raw for raw in (found or {}).get("data") or [] if isinstance(raw, dict)]
        if best is not None:
            candidates.append(best)
        return _dedupe(candidates)[:limit]

    async def _resolve_raw(self, paper: str, deadline: _Deadline) -> dict[str, Any]:
        ref = classify(paper)
        if ref.kind == "upload":
            raise ScholarNotFound(f"{paper!r} is an uploaded PDF, which Semantic Scholar has no record of")
        if ref.api_id is not None:
            raw = await self._paper(ref.api_id, deadline)
            candidates = [raw] if raw else []
        else:
            candidates = await self._title_candidates(ref.value, 1, deadline)
        for raw in candidates:
            if raw.get("paperId"):
                return raw
        raise ScholarNotFound(f"Semantic Scholar has no paper matching {paper!r}")

    async def _graph(self, paper: str, limit: int, kind: GraphKind) -> PaperGraphResult:
        limit = _clamp(limit, 1, GRAPH_MAX_PAGE)
        deadline = self._deadline()
        params = {
            "fields": GRAPH_FIELDS,
            "offset": 0,
            "limit": min(GRAPH_MAX_PAGE, max(limit, GRAPH_PAGE_SIZE)),
        }
        ref = classify(paper)

        raw: dict[str, Any] | None
        page: dict[str, Any] | None
        if ref.api_id is None:
            raw = await self._resolve_raw(paper, deadline)
            page = await self._get_json(f"paper/{raw['paperId']}/{kind}", params, deadline)
        else:
            # The graph endpoints take the same id forms, so the list need not
            # wait for resolution: both requests go out together, and the
            # paper's own record is only allowed to delay the answer briefly.
            detail = asyncio.ensure_future(self._paper(ref.api_id, deadline))
            try:
                page = await self._get_json(f"paper/{_path_id(ref.api_id)}/{kind}", params, deadline)
                raw = await self._header(detail, paper, kind, deadline)
            except BaseException:
                detail.cancel()
                raise

        if page is None:
            raise ScholarNotFound(f"Semantic Scholar has no paper matching {paper!r}")
        wrapper = "citedPaper" if kind == "references" else "citingPaper"
        papers = most_cited_first(papers_from_graph_page(page, wrapper))[:limit]
        total = (raw or {}).get("referenceCount" if kind == "references" else "citationCount")
        return PaperGraphResult(
            paper=paper,
            resolved=to_scholar_paper(raw),
            papers=papers,
            total=total if isinstance(total, int) else None,
        )

    async def _header(
        self, detail: asyncio.Future[dict[str, Any] | None], paper: str, kind: GraphKind, deadline: _Deadline
    ) -> dict[str, Any] | None:
        """The paper's own record if it arrives within the grace period, else None (logged).

        On timeout ``wait_for`` cancels only this caller's wait: the fetch
        itself is shielded in ``_get_json``, so it runs on and caches the record.
        """
        grace = min(self._header_grace_s, deadline.remaining())
        try:
            return await asyncio.wait_for(detail, grace)
        except TimeoutError:
            reason = f"not back within {grace:.1f}s"
        except ScholarError as exc:
            reason = str(exc)
        logger.info("s2 %s of %r returned without its header (%s)", kind, paper, reason)
        return None

    # ------------------------------------------------------------------ cached transport

    async def _get_json(
        self, path: str, params: dict[str, Any], deadline: _Deadline
    ) -> dict[str, Any] | None:
        """The response body, served from cache when possible; None means S2 answered 404."""
        key = cache_key(path, params)
        cached = await self._cache.get(key)
        if cached is not None:
            self.stats.cache_hits += 1
            return None if cached == _NOT_FOUND else cached
        self.stats.cache_misses += 1

        # Single flight: get_references and get_citations for the same paper
        # usually arrive together and both resolve it first; one request serves both.
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._fetch_and_store(key, path, params, deadline))
            self._inflight[key] = task
            task.add_done_callback(partial(self._inflight_done, key))
        # Shielded so one cancelled caller does not cancel the fetch the others await.
        return await asyncio.shield(task)

    def _inflight_done(self, key: str, task: asyncio.Future[Any]) -> None:
        self._inflight.pop(key, None)
        if not task.cancelled():
            # Marks the exception retrieved; every live waiter still receives it.
            task.exception()

    async def _fetch_and_store(
        self, key: str, path: str, params: dict[str, Any], deadline: _Deadline
    ) -> dict[str, Any] | None:
        try:
            body = await self._request(path, params, deadline)
        except ScholarNotFound:
            await self._cache.set(key, _NOT_FOUND, min(NEGATIVE_TTL_SECONDS, self._ttl))
            return None
        await self._cache.set(key, body, self._ttl)
        return body

    async def _request(self, path: str, params: dict[str, Any], deadline: _Deadline) -> dict[str, Any]:
        url = f"{self._base_url}/{path}"
        failure: ScholarError = ScholarUnavailable("ran out of time before Semantic Scholar answered")
        for attempt in range(1, self._max_attempts + 1):
            wait = self._limiter.reserve(max_wait=deadline.remaining())
            if wait is None:
                raise ScholarRateLimited("too many Semantic Scholar requests are queued in this process")
            if wait > 0:
                await self._sleep(wait)
            timeout = min(self._timeout_s, deadline.remaining())
            if timeout <= 0:
                raise failure

            # A bare float here would replace the client's whole Timeout and
            # silently lift the connect bound to the full request timeout.
            per_request = httpx.Timeout(timeout, connect=min(CONNECT_TIMEOUT_S, timeout))
            started = self._clock()
            self.stats.http_requests += 1
            try:
                resp = await self._http.get(url, params=params, headers=self._headers, timeout=per_request)
            except httpx.TimeoutException:
                failure = ScholarUnavailable("Semantic Scholar timed out")
            except httpx.TransportError as exc:
                failure = ScholarUnavailable(f"could not reach Semantic Scholar ({type(exc).__name__})")
            else:
                status = resp.status_code
                logger.debug("s2 GET %s -> %d in %.0fms", path, status, (self._clock() - started) * 1000)
                if status == 200:
                    return self._body(resp)
                if status == 404:
                    raise ScholarNotFound(_error_text(resp))
                if status == 429:
                    failure = ScholarRateLimited(
                        "Semantic Scholar is rate-limiting requests",
                        retry_after=parse_retry_after(resp.headers.get("retry-after")),
                    )
                elif status >= 500:
                    failure = ScholarUnavailable(f"Semantic Scholar returned HTTP {status}")
                elif status in (401, 403) and self.authenticated:
                    raise ScholarError(
                        f"Semantic Scholar rejected the API key (HTTP {status}); check S2_API_KEY"
                    )
                else:
                    raise ScholarError(
                        f"Semantic Scholar rejected the request (HTTP {status}): {_error_text(resp)}"
                    )

            if attempt == self._max_attempts:
                break
            delay = self._retry_delay(attempt, failure)
            if delay > deadline.remaining():
                break
            self.stats.retries += 1
            logger.info(
                "s2 GET %s failed (%s); retry %d of %d in %.1fs",
                path,
                failure,
                attempt,
                self._max_attempts - 1,
                delay,
            )
            await self._sleep(delay)
        raise failure

    def _retry_delay(self, attempt: int, failure: ScholarError) -> float:
        if isinstance(failure, ScholarRateLimited) and failure.retry_after is not None:
            return failure.retry_after
        # Full jitter: concurrent clients that failed together spread out instead of retrying in lockstep.
        delay = self._rng.uniform(0.0, min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2**attempt))
        if isinstance(failure, ScholarRateLimited):
            delay = max(delay, RATE_LIMIT_MIN_DELAY_S)
        return delay

    @staticmethod
    def _body(resp: httpx.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError as exc:
            raise ScholarUnavailable("Semantic Scholar returned a malformed response") from exc
        if not isinstance(body, dict):
            raise ScholarUnavailable("Semantic Scholar returned an unexpected response shape")
        return body
