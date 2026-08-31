"""arXiv access: id normalisation, metadata lookup, and cached PDF download.

Two things here are load-bearing for reproducible eval labels:

1. **Version pinning.** A bare id like `1706.03762` resolves to whatever the
   latest version is *today*. If arXiv publishes a v8 that retitles a section,
   every chunk ID under it changes and the benchmark silently drifts. So the
   metadata lookup resolves the concrete version, and the PDF is fetched from
   the versioned URL. The resolved `versioned_id` is stored in the output --
   pass it back (`--arxiv-id 1706.03762v7`) to reproduce a run exactly.
2. **PDF caching.** Downloaded PDFs are kept and re-used, with a sha256 in the
   output. Re-running ingestion does not re-download and therefore cannot
   silently pick up new bytes; `--force` is the explicit opt-in.

arXiv asks automated clients for a descriptive User-Agent and roughly one
request every three seconds. Both are honoured here; being rate-limited
mid-batch is a much worse outcome than an eight-paper ingest taking a minute.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from pathlib import Path
from xml.etree import ElementTree

import httpx

from papermind.ingest.models import ArxivEntry, PaperMeta

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_API_URL = "https://export.arxiv.org/api/query"
_PDF_URL = "https://arxiv.org/pdf/{versioned_id}"

# arXiv's stated courtesy limit for programmatic access.
_MIN_REQUEST_INTERVAL = 3.0

USER_AGENT = "PaperMind/0.1 (research reading agent; +https://github.com/papermind)"

# Modern (0704.0001) and legacy (math.GT/0309136) arXiv identifiers, each with
# an optional version suffix, optionally wrapped in a URL or an "arXiv:" prefix.
_ID_PATTERN = re.compile(
    r"(?:arxiv[:/]|abs/|pdf/)?"
    r"(?P<id>\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})"
    r"(?:v(?P<version>\d+))?",
    re.IGNORECASE,
)


class ArxivError(RuntimeError):
    """Raised when arXiv cannot be reached or the id does not resolve."""


def normalize_arxiv_id(raw: str) -> tuple[str, int | None]:
    """Accept anything a user might paste; return (canonical_id, version|None).

    Handles '1706.03762', 'arXiv:1706.03762v5', a full abs/ or pdf/ URL, and
    legacy ids like 'cs.CL/0501001'.
    """
    match = _ID_PATTERN.search(raw.strip())
    if not match:
        raise ArxivError(f"not a recognisable arXiv id: {raw!r}")
    version = match.group("version")
    return match.group("id"), int(version) if version else None


class _RateLimiter:
    """Process-wide spacing between arXiv requests."""

    def __init__(self, min_interval: float) -> None:
        self._min_interval = min_interval
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            elapsed = time.monotonic() - self._last
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last = time.monotonic()


_limiter = _RateLimiter(_MIN_REQUEST_INTERVAL)


def _text(node: ElementTree.Element | None) -> str:
    return " ".join(node.text.split()) if node is not None and node.text else ""


async def fetch_metadata(client: httpx.AsyncClient, arxiv_id: str) -> ArxivEntry:
    """Look up an entry and resolve its concrete version.

    The PDF-derived fields (sha256, page count) are filled in by `build_meta`
    after download; they cannot be known here.
    """
    await _limiter.wait()
    response = await client.get(_API_URL, params={"id_list": arxiv_id, "max_results": 1})
    response.raise_for_status()

    feed = ElementTree.fromstring(response.text)
    entry = feed.find(f"{_ATOM}entry")
    # arXiv returns a 200 with a placeholder entry for unknown ids; the marker
    # is a missing/error title rather than an HTTP status.
    if entry is None or _text(entry.find(f"{_ATOM}title")).lower() == "error":
        raise ArxivError(f"arXiv has no entry for {arxiv_id!r}")

    # <id> is the canonical versioned URL: http://arxiv.org/abs/1706.03762v7
    entry_id = _text(entry.find(f"{_ATOM}id"))
    version_match = re.search(r"v(\d+)$", entry_id)
    version = int(version_match.group(1)) if version_match else 1

    primary = entry.find(f"{_ARXIV_NS}primary_category")

    return ArxivEntry(
        version=version,
        title=_text(entry.find(f"{_ATOM}title")),
        abstract=_text(entry.find(f"{_ATOM}summary")),
        authors=[_text(a.find(f"{_ATOM}name")) for a in entry.findall(f"{_ATOM}author")],
        categories=[
            c.attrib["term"] for c in entry.findall(f"{_ATOM}category") if "term" in c.attrib
        ],
        published=_text(entry.find(f"{_ATOM}published")) or None,
        updated=_text(entry.find(f"{_ATOM}updated")) or None,
        primary_category=primary.attrib.get("term") if primary is not None else None,
    )


def _read_cached(dest: Path) -> str | None:
    """Return the sha256 of an already-downloaded PDF, or None. Blocking."""
    if not dest.exists():
        return None
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def _write_pdf(dest: Path, body: bytes) -> str:
    """Write via temp file + rename, and return the sha256. Blocking.

    Atomic because an interrupted download that left a truncated PDF in the
    cache would parse into garbage chunks on the next run, silently.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".pdf.part")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return hashlib.sha256(body).hexdigest()


async def download_pdf(
    client: httpx.AsyncClient,
    versioned_id: str,
    dest: Path,
    *,
    force: bool = False,
) -> tuple[Path, str]:
    """Fetch the versioned PDF, or reuse the cache. Returns (path, sha256).

    Filesystem work goes through `asyncio.to_thread`: hashing a cached PDF
    means reading megabytes, and this is called from the request path when a
    user opens a paper the service has not seen.
    """
    if not force:
        cached = await asyncio.to_thread(_read_cached, dest)
        if cached is not None:
            return dest, cached

    url = _PDF_URL.format(versioned_id=versioned_id)
    await _limiter.wait()
    response = await client.get(url, follow_redirects=True)
    response.raise_for_status()

    body = response.content
    if not body.startswith(b"%PDF"):
        # arXiv serves an HTML interstitial while a paper is being rendered.
        raise ArxivError(f"{url} did not return a PDF (got {len(body)} bytes of non-PDF data)")

    return dest, await asyncio.to_thread(_write_pdf, dest, body)


def make_client(timeout: float = 30.0) -> httpx.AsyncClient:
    """An httpx client configured the way arXiv asks clients to behave."""
    return httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def build_meta(
    *,
    paper_id: str,
    version: int,
    entry: ArxivEntry,
    pdf_sha256: str,
    n_pages: int,
) -> PaperMeta:
    versioned_id = f"{paper_id}v{version}"
    return PaperMeta(
        paper_id=paper_id,
        versioned_id=versioned_id,
        version=version,
        title=entry.title,
        authors=entry.authors,
        abstract=entry.abstract,
        categories=entry.categories,
        published=entry.published,
        updated=entry.updated,
        pdf_sha256=pdf_sha256,
        pdf_url=_PDF_URL.format(versioned_id=versioned_id),
        n_pages=n_pages,
    )
