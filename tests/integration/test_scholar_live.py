"""Live Semantic Scholar API: endpoint shapes, resolution, and caching.

Few requests on purpose (the keyless pool is shared and throttles hard). A 429
that outlasts our retries is S2's pool being busy, not a bug here, so it skips.
"""

from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import TypeVar

import pytest

from papermind.config import get_settings
from papermind.scholar.cache import FileCache
from papermind.scholar.client import ScholarClient
from papermind.scholar.errors import ScholarRateLimited

pytestmark = pytest.mark.integration

VASWANI = "204e3073870fae3d05bcbc2f6a8e263d9b72e776"
T = TypeVar("T")


async def live(call: Awaitable[T]) -> T:
    try:
        return await call
    except ScholarRateLimited:
        pytest.skip("Semantic Scholar's shared pool is rate-limiting; set S2_API_KEY for reliable runs")


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[ScholarClient]:
    # A private file cache: these tests must reach the live API, then prove the cache works.
    async with ScholarClient(settings=get_settings(), cache=FileCache(tmp_path / "s2")) as c:
        yield c


async def test_title_lookup_then_cache_hit(client: ScholarClient) -> None:
    matches = await live(client.lookup("Attention Is All You Need"))
    assert matches, "the Transformer paper should match by exact title"
    top = matches[0]
    assert top.s2_id == VASWANI
    assert top.arxiv_id == "1706.03762"
    assert top.year == 2017
    assert top.authors[0] == "Ashish Vaswani"
    assert len(top.authors) <= 6
    assert (top.citation_count or 0) > 100_000

    requests = client.stats.http_requests
    again = await client.lookup("Attention Is All You Need")
    assert again == matches
    assert client.stats.http_requests == requests  # served from the cache


async def test_references_of_an_arxiv_paper(client: ScholarClient) -> None:
    result = await live(client.references("arXiv:1706.03762", limit=5))
    assert 1 <= len(result.papers) <= 5
    if result.resolved is None:
        # The keyless pool throttled the /paper/{id} header lookup; the list still came back.
        assert result.total is None
    else:
        assert result.resolved.s2_id == VASWANI
        assert result.total is not None and result.total >= len(result.papers)
    counts = [p.citation_count or 0 for p in result.papers]
    assert counts == sorted(counts, reverse=True)
    assert all(p.tldr is None for p in result.papers)  # not offered on this endpoint


async def test_unknown_arxiv_id_is_empty(client: ScholarClient) -> None:
    assert await live(client.lookup("arXiv:2999.99999")) == []
