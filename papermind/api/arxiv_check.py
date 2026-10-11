"""What the reader pasted, checked against arXiv before an ingestion starts.

Adding by arXiv id answers in the hint line under the field (DESIGN.md §5.4,
§5.25), so the failures a reader can act on have to come back on the POST
itself, with what they need to act: "You pasted v3. The latest version is v5."
with an "Add v5" button needs ``latest_version``; a background failure on a
library row could not carry it. So the route looks up the paper's latest
version first (one arXiv API request, on the ingest package's polite client
and schedule) and only then starts the ingestion, pinned to the version it
resolved here.

Withdrawals: arXiv withdraws a paper by publishing a new version whose PDF is
gone (the PDF URL answers 404) and whose comment, or for older withdrawals the
abstract, says so. The version before it is still readable, which is what the
"Add v2" action offers.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from papermind.api.errors import APIError, add_paper_error
from papermind.api.ingest_jobs import ARXIV_UNREACHABLE, arxiv_not_found_message
from papermind.ingest.arxiv import ArxivClient, ArxivError, ArxivMetadata, ArxivNotFound, ArxivRef
from papermind.schemas.api import AddPaperError, AddPaperErrorCode

log = logging.getLogger(__name__)

# The reader is watching "Looking up 1706.03762 on arXiv…". Fewer and shorter
# attempts than an ingestion makes, and one overall bound, because the
# client's own retries and the shared 3 s request spacing add up.
LOOKUP_TIMEOUT_S = 20.0
LOOKUP_ATTEMPTS = 2
LOOKUP_REQUEST_TIMEOUT_S = 8.0

# Anchored at the start: a comment such as "v2: withdrawn claim in §3
# corrected" or an abstract that discusses withdrawals is not a withdrawal.
_WITHDRAWAL = re.compile(
    r"^\W*(?:(?:this|the)\s+)?(?:paper|article|manuscript|submission|version|work|preprint)?\s*"
    r"(?:(?:has\s+been|is\s+being|was|is|been)\s+)?withdrawn\b",
    re.IGNORECASE,
)


class ArxivLookupAPI(Protocol):
    async def latest(self, arxiv_id: str) -> ArxivMetadata:
        """Metadata of the paper's latest version. Raises ``ArxivNotFound`` / ``ArxivUnavailable`` / ``TimeoutError``."""
        ...


def _lookup_client() -> ArxivClient:
    return ArxivClient(attempts=LOOKUP_ATTEMPTS, timeout_s=LOOKUP_REQUEST_TIMEOUT_S, backoff_s=1.0)


class ArxivLookup:
    def __init__(
        self,
        client_factory: Callable[[], ArxivClient] = _lookup_client,
        *,
        timeout_s: float = LOOKUP_TIMEOUT_S,
    ) -> None:
        self._client_factory = client_factory
        self._timeout_s = timeout_s

    async def latest(self, arxiv_id: str) -> ArxivMetadata:
        async with asyncio.timeout(self._timeout_s):
            async with self._client_factory() as client:
                # Unversioned on purpose: arXiv resolves it to the latest version.
                return await client.metadata(ArxivRef(arxiv_id=arxiv_id))


@dataclass(frozen=True, slots=True)
class ArxivChoice:
    """The version to ingest, and the title to show on the row until ingestion finishes."""

    versioned_id: str
    title: str


def is_withdrawn(meta: ArxivMetadata) -> bool:
    # arXiv puts the notice in <arxiv:comment>, which ArxivMetadata does not
    # carry yet (requested from the ingest package); until it does, only the
    # withdrawals that also replaced the abstract are recognised.
    comment = getattr(meta, "comment", None)
    if isinstance(comment, str) and _WITHDRAWAL.match(comment):
        return True
    return bool(_WITHDRAWAL.match(meta.abstract or ""))


def _number(version: str) -> int:
    return int(version.lstrip("vV"))


def _rejected(
    code: AddPaperErrorCode, message: str, *, paper_id: str, latest_version: str | None = None
) -> APIError:
    return add_paper_error(
        422, AddPaperError(code=code, message=message, latest_version=latest_version, paper_id=paper_id)
    )


def choose_version(ref: ArxivRef, latest: ArxivMetadata, *, paper_id: str, keep_version: bool) -> ArxivChoice:
    """The version to ingest for what the reader pasted, or the ``APIError`` that explains why not.

    ``keep_version`` is the reader insisting on an older version they pasted
    ("Add v3" after being told v5 exists).
    """
    latest_n = _number(latest.version)
    withdrawn = is_withdrawn(latest)
    readable_n = latest_n - 1 if withdrawn else latest_n
    readable = f"v{readable_n}" if readable_n >= 1 else None
    asked_n = _number(ref.version) if ref.version else None
    arxiv_id = ref.arxiv_id

    if withdrawn and asked_n in (None, latest_n):
        message = (
            f"arXiv lists {arxiv_id} as withdrawn. Version {readable} is still available."
            if readable
            else f"arXiv lists {arxiv_id} as withdrawn, and it has no earlier version to read."
        )
        raise _rejected("withdrawn", message, paper_id=paper_id, latest_version=readable)
    if asked_n is not None and asked_n > latest_n:
        raise _rejected(
            "not_found",
            f"arXiv has no version v{asked_n} of {arxiv_id}. The latest is v{latest_n}.",
            paper_id=paper_id,
            latest_version=readable,
        )
    if asked_n is not None and asked_n < readable_n and not keep_version:
        raise _rejected(
            "newer_version",
            f"You pasted v{asked_n}. The latest version is {readable}.",
            paper_id=paper_id,
            latest_version=readable,
        )
    version = f"v{asked_n}" if asked_n is not None else latest.version
    return ArxivChoice(
        versioned_id=f"{arxiv_id}{version}", title=latest.title or f"arXiv:{arxiv_id}{version}"
    )


async def check_arxiv(
    lookup: ArxivLookupAPI, ref: ArxivRef, *, paper_id: str, keep_version: bool = False
) -> ArxivChoice:
    """Look the paper up on arXiv and choose the version to ingest. Raises ``APIError``."""
    try:
        latest = await lookup.latest(ref.arxiv_id)
    except ArxivNotFound:
        raise _rejected("not_found", arxiv_not_found_message(ref.arxiv_id), paper_id=paper_id) from None
    except (ArxivError, TimeoutError) as exc:
        log.warning("arXiv lookup for %s failed: %s", ref.arxiv_id, exc or type(exc).__name__)
        raise add_paper_error(
            503, AddPaperError(code="arxiv_unreachable", message=ARXIV_UNREACHABLE, paper_id=paper_id)
        ) from None
    return choose_version(ref, latest, paper_id=paper_id, keep_version=keep_version)
