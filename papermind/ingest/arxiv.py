"""arXiv access: identifier normalisation, metadata lookup, PDF download.

Two things here are load-bearing for reproducible eval labels:

* **Version pinning.** A bare id resolves to whatever version is latest *today*.
  If a v8 retitles a section, chunk ids move and the benchmark drifts silently.
  The metadata lookup therefore resolves a concrete version and the PDF is
  always fetched from the versioned URL; the version is recorded in PaperMeta.
* **Politeness.** arXiv asks automated clients for a descriptive User-Agent and
  at most one request every three seconds. Being throttled or blocked mid-batch
  is far worse than an eight-paper ingest taking half a minute, so every request
  this module makes reserves a slot on one process-wide schedule.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Self
from xml.etree import ElementTree

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from papermind.config import get_settings

API_URL = "https://export.arxiv.org/api/query"
PDF_URL = "https://arxiv.org/pdf/{versioned_id}"

# arXiv's documented courtesy interval for programmatic access.
MIN_REQUEST_INTERVAL_S = 3.0
# A paper PDF over this size is almost certainly not a paper (or a scan); refuse
# it rather than buffering an unbounded body into memory.
MAX_PDF_BYTES = 100 * 1024 * 1024

_ATOM = "{http://www.w3.org/2005/Atom}"
_OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"

# One pattern for everything a user might paste. Anchored at both ends so junk
# around an id ("see 1706.03762 for details") is rejected rather than guessed at.
#
#   1706.03762 | 1706.03762v7 | arXiv:1706.03762 | 1412.6980.pdf
#   https://arxiv.org/abs/1706.03762v7 | arxiv.org/pdf/1706.03762v7 | .../pdf/1706.03762.pdf
#   hep-th/9901001 | math.GT/0309136v2 | https://arxiv.org/abs/cond-mat/0102536
#   arXiv:1706.03762 [cs.CL]  (the "Cite as" line)  |  10.48550/arXiv.1706.03762 (the DOI)
#
# New-style ids are YYMM.NNNN (2007-2014) or YYMM.NNNNN (2015+). Old-style ids are
# archive[.SUBJECT]/YYMMNNN; the subject class is not part of the identifier
# (arXiv's own docs), so it is matched but dropped from the canonical form.
ARXIV_ID_RE = re.compile(
    r"""
    ^\s*
    (?:(?:https?://)?(?:doi\.org/)?10\.48550/arxiv\.)?
    (?:arxiv:\s*)?
    (?:(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/)?
    (?:
        (?P<new>\d{2}(?:0[1-9]|1[0-2])\.\d{4,5})
      | (?P<archive>[a-z]+(?:-[a-z]+)?)(?:\.[a-z]{2,}(?:-[a-z]{2,})?)?/(?P<old>\d{2}(?:0[1-9]|1[0-2])\d{3})
    )
    (?P<version>v[1-9]\d*)?
    (?:\.pdf)?
    /?
    (?:[?\#].*)?
    (?:\s*\[[\w.\-]+\])?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


class ArxivError(Exception):
    """Base class for arXiv failures."""


class InvalidArxivId(ArxivError, ValueError):
    """The input is not an arXiv identifier or arXiv URL."""


class ArxivNotFound(ArxivError):
    """arXiv has no paper (or no such version) for this identifier."""


class ArxivUnavailable(ArxivError):
    """arXiv could not be reached, kept failing, or served something other than a PDF."""


@dataclass(frozen=True, slots=True)
class ArxivRef:
    arxiv_id: str  # canonical, unversioned: '1706.03762', 'hep-th/9901001'
    version: str | None = None  # 'v7' when pinned

    @property
    def versioned_id(self) -> str:
        return f"{self.arxiv_id}{self.version or ''}"


@dataclass(frozen=True, slots=True)
class ArxivMetadata:
    arxiv_id: str
    version: str  # always resolved, e.g. 'v7'
    title: str
    authors: list[str]
    abstract: str
    published: date | None

    @property
    def versioned_id(self) -> str:
        return f"{self.arxiv_id}{self.version}"


def parse_arxiv_id(raw: str) -> ArxivRef:
    """Normalise anything a user might paste into a canonical (id, version)."""
    match = ARXIV_ID_RE.match(raw or "")
    if match is None:
        raise InvalidArxivId(f"Not an arXiv id or arXiv URL: {raw!r}")
    if match.group("new"):
        arxiv_id = match.group("new")
    else:
        arxiv_id = f"{match.group('archive').lower()}/{match.group('old')}"
    version = match.group("version")
    return ArxivRef(arxiv_id=arxiv_id, version=version.lower() if version else None)


class PoliteSchedule:
    """Process-wide spacing between requests to one host.

    Slots are reserved under a *threading* lock and slept on outside it. An
    asyncio.Lock would bind to the first event loop that used it, and this
    object lives at module scope where several loops (tests, a CLI run inside a
    server process) can touch it. Reserving slots also keeps concurrent callers
    in FIFO order instead of all waking at once after the same sleep.
    """

    def __init__(self, interval_s: float) -> None:
        self.interval_s = interval_s
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def reserve(self) -> float:
        """Claim the next slot; return how long the caller must wait for it."""
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self.interval_s
            return slot - now

    async def wait(self) -> None:
        delay = self.reserve()
        if delay > 0:
            await asyncio.sleep(delay)


ARXIV_SCHEDULE = PoliteSchedule(MIN_REQUEST_INTERVAL_S)


class _Transient(Exception):
    """A failure worth retrying (timeouts, 429, 5xx)."""


def _text(node: ElementTree.Element | None) -> str:
    return " ".join(node.text.split()) if node is not None and node.text else ""


def parse_atom(feed_xml: str | bytes, ref: ArxivRef) -> ArxivMetadata:
    """Parse an arXiv API Atom feed for a single id_list query."""
    try:
        root = ElementTree.fromstring(feed_xml)
    except ElementTree.ParseError as exc:
        raise ArxivUnavailable(f"arXiv returned an unreadable response for {ref.versioned_id}") from exc

    # Unknown ids come back as HTTP 200 with zero results (well-formed id) or
    # with a single entry titled "Error" (malformed id); neither is an HTTP error.
    total = _text(root.find(f"{_OPENSEARCH}totalResults"))
    entry = root.find(f"{_ATOM}entry")
    if entry is None or total == "0" or _text(entry.find(f"{_ATOM}title")).lower() == "error":
        raise ArxivNotFound(f"arXiv has no paper {ref.versioned_id}")

    # <id> is the canonical versioned abs URL, e.g. http://arxiv.org/abs/1706.03762v7.
    # It carries the version that was actually resolved (latest when unpinned).
    entry_url = _text(entry.find(f"{_ATOM}id"))
    version_match = re.search(r"(v\d+)$", entry_url)
    version = version_match.group(1) if version_match else (ref.version or "v1")

    published: date | None = None
    published_raw = _text(entry.find(f"{_ATOM}published"))
    if published_raw:
        try:
            published = datetime.fromisoformat(published_raw.replace("Z", "+00:00")).date()
        except ValueError:
            published = None

    return ArxivMetadata(
        arxiv_id=ref.arxiv_id,
        version=version,
        title=_text(entry.find(f"{_ATOM}title")),
        authors=[_text(a.find(f"{_ATOM}name")) for a in entry.findall(f"{_ATOM}author")],
        abstract=_text(entry.find(f"{_ATOM}summary")),
        published=published,
    )


def looks_like_pdf(body: bytes) -> bool:
    # The header may legally sit within the first 1024 bytes, after junk.
    return b"%PDF-" in body[:1024]


class ArxivClient:
    """Async arXiv client. Use as an async context manager, or pass your own httpx client."""

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        schedule: PoliteSchedule = ARXIV_SCHEDULE,
        attempts: int = 3,
        timeout_s: float = 60.0,
        backoff_s: float = 2.0,
    ) -> None:
        settings = get_settings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_s, connect=15.0),
            headers={"User-Agent": settings.arxiv_user_agent},
            follow_redirects=True,
        )
        self._schedule = schedule
        self._attempts = attempts
        self._backoff_s = backoff_s

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, url: str, *, params: dict[str, str] | None = None) -> httpx.Response:
        """GET with the polite schedule, retrying only transient failures."""

        async def once() -> httpx.Response:
            await self._schedule.wait()
            try:
                response = await self._client.get(url, params=params, follow_redirects=True)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise _Transient(f"{type(exc).__name__} fetching {url}") from exc
            if response.status_code == 429 or response.status_code >= 500:
                raise _Transient(f"HTTP {response.status_code} from {url}")
            return response

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._attempts),
                wait=wait_exponential_jitter(multiplier=self._backoff_s, max=20, jitter=self._backoff_s),
                retry=retry_if_exception_type(_Transient),
                reraise=True,
            ):
                with attempt:
                    return await once()
        except _Transient as exc:
            raise ArxivUnavailable(f"arXiv is not responding ({exc}); try again shortly") from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def metadata(self, ref: ArxivRef) -> ArxivMetadata:
        response = await self._get(API_URL, params={"id_list": ref.versioned_id, "max_results": "1"})
        if response.status_code == 400:
            raise ArxivNotFound(f"arXiv rejected the id {ref.versioned_id}")
        if response.status_code != 200:
            raise ArxivUnavailable(f"arXiv API returned HTTP {response.status_code} for {ref.versioned_id}")
        return parse_atom(response.content, ref)

    async def download_pdf(self, arxiv_id: str, version: str) -> bytes:
        """Fetch the PDF of one pinned version."""
        url = PDF_URL.format(versioned_id=f"{arxiv_id}{version}")
        response = await self._get(url)
        if response.status_code in (403, 404, 410):
            raise ArxivNotFound(f"arXiv has no PDF for {arxiv_id}{version}")
        if response.status_code != 200:
            raise ArxivUnavailable(f"arXiv returned HTTP {response.status_code} for {url}")
        body = response.content
        if len(body) > MAX_PDF_BYTES:
            raise ArxivUnavailable(f"{url} is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB")
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        # arXiv serves an HTML "PDF is being generated" interstitial with a 200 for
        # freshly-submitted papers; trusting the status alone would ingest that page.
        if not looks_like_pdf(body) or (
            content_type and "pdf" not in content_type and "octet" not in content_type
        ):
            raise ArxivUnavailable(
                f"{url} did not return a PDF (content-type {content_type or 'unknown'}); "
                "arXiv may still be generating it"
            )
        return body
