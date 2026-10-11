import asyncio
import random
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from papermind.config import Settings
from papermind.scholar.cache import Cache, FileCache
from papermind.scholar.client import (
    BACKOFF_CAP_S,
    CONNECT_TIMEOUT_S,
    GRAPH_FIELDS,
    GRAPH_PAGE_SIZE,
    MAX_ATTEMPTS,
    NEGATIVE_TTL_SECONDS,
    PAPER_FIELDS,
    RATE_LIMIT_MIN_DELAY_S,
    REQUEST_TIMEOUT_S,
    ScholarClient,
    cache_key,
    parse_retry_after,
)
from papermind.scholar.errors import ScholarError, ScholarNotFound, ScholarRateLimited, ScholarUnavailable
from papermind.scholar.ratelimit import TokenBucket

BASE = "https://api.semanticscholar.org/graph/v1"
VASWANI = "204e3073870fae3d05bcbc2f6a8e263d9b72e776"


def paper(pid: str, title: str, **extra: Any) -> dict[str, Any]:
    return {
        "paperId": pid,
        "title": title,
        "year": 2017,
        "authors": [{"authorId": "1", "name": "Ashish Vaswani"}],
        "venue": "NeurIPS",
        "abstract": "Abstract text.",
        "citationCount": 10,
        "url": f"https://www.semanticscholar.org/paper/{pid}",
        "externalIds": {},
        **extra,
    }


TRANSFORMER = paper(
    VASWANI,
    "Attention is All you Need",
    citationCount=196005,
    referenceCount=41,
    externalIds={"ArXiv": "1706.03762"},
    tldr={"model": "tldr@v2.0.0", "text": "The Transformer."},
)


class FakeTime:
    """Monotonic clock + sleep that advance together, so retries take no real time."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class RecordingCache:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self.ttls: dict[str, int] = {}

    async def get(self, key: str) -> Any | None:
        return self.data.get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int) -> None:
        self.data[key] = value
        self.ttls[key] = ttl_seconds

    async def aclose(self) -> None:
        return None


def settings(**overrides: Any) -> Settings:
    # No .env and no ambient key: unit tests must not depend on the developer's machine.
    return Settings(_env_file=None, **{"s2_api_key": None, **overrides})


MakeClient = Callable[..., ScholarClient]


@pytest.fixture
async def make_client(tmp_path: Path) -> AsyncIterator[MakeClient]:
    made: list[ScholarClient] = []

    def make(
        *,
        time: FakeTime | None = None,
        cache: Cache | None = None,
        limiter: TokenBucket | None = None,
        cfg: Settings | None = None,
        **kwargs: Any,
    ) -> ScholarClient:
        time = time or FakeTime()
        client = ScholarClient(
            settings=cfg or settings(),
            cache=cache if cache is not None else FileCache(tmp_path / "s2"),
            limiter=limiter or TokenBucket(1000.0, 1000.0, clock=time.clock),
            sleep=time.sleep,
            clock=time.clock,
            rng=kwargs.pop("rng", None) or random.Random(7),
            **kwargs,
        )
        made.append(client)
        return client

    yield make
    for client in made:
        await client.aclose()


@pytest.fixture
def s2() -> Iterator[respx.MockRouter]:
    with respx.mock(base_url=BASE, assert_all_called=False) as router:
        yield router


# ---------------------------------------------------------------- lookup


async def test_lookup_by_arxiv_id_fetches_the_paper_directly(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    matches = await make_client().lookup("https://arxiv.org/abs/1706.03762v7")
    assert route.call_count == 1
    assert route.calls.last.request.url.params["fields"] == PAPER_FIELDS
    assert [m.s2_id for m in matches] == [VASWANI]
    m = matches[0]
    assert (m.title, m.year, m.arxiv_id, m.tldr, m.citation_count) == (
        "Attention is All you Need",
        2017,
        "1706.03762",
        "The Transformer.",
        196005,
    )


async def test_lookup_confident_title_match_skips_search(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    match = s2.get("/paper/search/match").respond(200, json={"data": [{**TRANSFORMER, "matchScore": 220.4}]})
    search = s2.get("/paper/search").respond(200, json={"total": 0, "offset": 0, "data": []})
    matches = await make_client().lookup("Attention Is All You Need", limit=3)
    assert [m.s2_id for m in matches] == [VASWANI]
    assert match.calls.last.request.url.params["query"] == "Attention Is All You Need"
    assert search.call_count == 0


async def test_lookup_weak_match_falls_back_to_search(make_client: MakeClient, s2: respx.MockRouter) -> None:
    weak = paper("weak", "Pre-training Strategies for Sparse Graph Kernels")
    bert = paper("bert", "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding")
    roberta = paper("roberta", "RoBERTa: A Robustly Optimized BERT Pretraining Approach")
    s2.get("/paper/search/match").respond(200, json={"data": [weak]})
    search = s2.get("/paper/search").respond(
        200, json={"total": 3, "offset": 0, "next": 3, "data": [bert, roberta, weak]}
    )
    matches = await make_client().lookup("bert pre-training", limit=3)
    params = search.calls.last.request.url.params
    assert params["query"] == "bert pre training"  # hyphens break /paper/search
    assert params["limit"] == "3"
    # Relevance results lead; the weak title match is kept once, at the end.
    assert [m.s2_id for m in matches] == ["bert", "roberta", "weak"]


async def test_lookup_title_miss_then_empty_search(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/search/match").respond(404, json={"error": "Title match not found"})
    s2.get("/paper/search").respond(200, json={"total": 0, "offset": 0, "data": []})
    assert await make_client().lookup("zzqx totally unknown paper") == []


async def test_lookup_keeps_a_weak_match_when_search_is_rate_limited(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    s2.get("/paper/search/match").respond(200, json={"data": [paper("weak", "Something Else Entirely")]})
    s2.get("/paper/search").respond(429, json={"message": "Too Many Requests", "code": "429"})
    matches = await make_client(max_attempts=1).lookup("vague query")
    assert [m.s2_id for m in matches] == ["weak"]


async def test_lookup_unknown_id_is_empty_not_an_error(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/ARXIV:9999.99999").respond(404, json={"error": "Paper with id arXiv:9999.99999 not found"})
    assert await make_client().lookup("9999.99999") == []


async def test_lookup_doi_is_path_encoded(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get(url__regex=r".*/paper/DOI:.*").respond(200, json=paper("doi", "A DOI Paper"))
    await make_client().lookup("10.1002/(SICI)1097-4571(199009)41:6<391::AID-ASI1>3.0.CO;2-9")
    raw_path = route.calls.last.request.url.raw_path.decode()
    assert raw_path.startswith("/graph/v1/paper/DOI:10.1002/%28SICI%29")
    assert "<" not in raw_path and ";" not in raw_path.split("?")[0]


async def test_lookup_of_an_upload_id_makes_no_request(make_client: MakeClient, s2: respx.MockRouter) -> None:
    assert await make_client().lookup("up_0123456789ab") == []
    assert len(s2.calls) == 0


# ---------------------------------------------------------------- references / citations


def graph_page(wrapper: str, entries: list[dict[str, Any] | None]) -> dict[str, Any]:
    return {"offset": 0, "next": len(entries), "data": [{wrapper: e} for e in entries]}


async def test_references_resolve_rank_and_total(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    # For an id the page is fetched by that id, alongside (not after) the paper itself.
    refs = s2.get("/paper/ARXIV:1706.03762/references").respond(
        200,
        json=graph_page(
            "citedPaper",
            [
                paper("adam", "Adam", citationCount=150000),
                {"paperId": None, "title": "An unresolved citation"},
                paper("seq2seq", "Sequence to Sequence Learning", citationCount=20000),
                paper("lstm", "Long Short-Term Memory", citationCount=90000),
            ],
        ),
    )
    result = await make_client().references("arXiv:1706.03762", limit=2)
    params = refs.calls.last.request.url.params
    assert params["fields"] == GRAPH_FIELDS
    assert "tldr" not in params["fields"]  # rejected by the references endpoint
    assert (params["offset"], params["limit"]) == ("0", str(GRAPH_PAGE_SIZE))
    assert result.paper == "arXiv:1706.03762"
    assert result.resolved is not None and result.resolved.s2_id == VASWANI
    assert [p.s2_id for p in result.papers] == ["adam", "lstm"]
    assert result.total == 41  # referenceCount of the resolved paper


async def test_citations_use_citing_paper_and_citation_count(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    s2.get(f"/paper/{VASWANI}").respond(200, json=TRANSFORMER)
    s2.get(f"/paper/{VASWANI}/citations").respond(
        200,
        json=graph_page(
            "citingPaper",
            [paper("new", "Brand New", citationCount=0), paper("bert", "BERT", citationCount=100000)],
        ),
    )
    result = await make_client().citations(VASWANI, limit=5)
    assert [p.s2_id for p in result.papers] == ["bert", "new"]
    assert result.total == 196005


async def test_references_by_title_resolve_through_match(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    s2.get("/paper/search/match").respond(200, json={"data": [TRANSFORMER]})
    s2.get(f"/paper/{VASWANI}/references").respond(200, json=graph_page("citedPaper", [paper("a", "A")]))
    result = await make_client().references("Attention is all you need", limit=10)
    assert result.resolved is not None and result.resolved.arxiv_id == "1706.03762"
    assert [p.s2_id for p in result.papers] == ["a"]


async def test_unknown_paper_raises_not_found_and_is_negatively_cached(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    missing = {"error": "Paper with id not found"}
    detail = s2.get("/paper/ARXIV:2999.00001").respond(404, json=missing)
    refs = s2.get("/paper/ARXIV:2999.00001/references").respond(404, json=missing)
    s2.get("/paper/ARXIV:2999.00001/citations").respond(404, json=missing)
    cache = RecordingCache()
    client = make_client(cache=cache)
    with pytest.raises(ScholarNotFound):
        await client.references("2999.00001")
    with pytest.raises(ScholarNotFound):
        await client.citations("2999.00001")
    with pytest.raises(ScholarNotFound):
        await client.references("arXiv:2999.00001v2")
    # Repeat misses came from the cache.
    assert (detail.call_count, refs.call_count) == (1, 1)
    key = cache_key("paper/ARXIV:2999.00001", {"fields": PAPER_FIELDS})
    assert cache.ttls[key] == NEGATIVE_TTL_SECONDS


async def test_graph_page_survives_a_throttled_detail_lookup(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    s2.get("/paper/ARXIV:1706.03762").respond(429, json={"message": "Too Many Requests"})
    s2.get("/paper/ARXIV:1706.03762/citations").respond(
        200, json=graph_page("citingPaper", [paper("bert", "BERT", citationCount=100000)])
    )
    result = await make_client(max_attempts=1).citations("1706.03762")
    assert [p.s2_id for p in result.papers] == ["bert"]
    assert result.resolved is None
    assert result.total is None


async def test_a_slow_header_does_not_hold_the_list_and_lands_in_the_cache(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    gate = asyncio.Event()

    async def slow_detail(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=TRANSFORMER)

    detail = s2.get("/paper/ARXIV:1706.03762").mock(side_effect=slow_detail)
    s2.get("/paper/ARXIV:1706.03762/references").respond(
        200, json=graph_page("citedPaper", [paper("adam", "Adam")])
    )
    client = make_client(cache=RecordingCache(), header_grace_s=0.05)

    first = await client.references("1706.03762")
    assert [p.s2_id for p in first.papers] == ["adam"]
    assert (first.resolved, first.total) == (None, None)

    # The abandoned header fetch was not cancelled: it finishes and is cached.
    gate.set()
    for _ in range(50):
        if detail.call_count and not client._inflight:
            break
        await asyncio.sleep(0.01)
    second = await client.references("1706.03762")
    assert second.resolved is not None and second.resolved.s2_id == VASWANI
    assert second.total == 41
    assert detail.call_count == 1


async def test_aclose_cancels_fetches_that_outlived_their_caller(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    cancelled = asyncio.Event()

    async def stuck(request: httpx.Request) -> httpx.Response:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        raise AssertionError("unreachable")

    s2.get("/paper/ARXIV:1706.03762").mock(side_effect=stuck)
    s2.get("/paper/ARXIV:1706.03762/citations").respond(200, json=graph_page("citingPaper", []))
    client = make_client(cache=RecordingCache(), header_grace_s=0.01)
    await client.citations("1706.03762")
    await client.aclose()
    assert cancelled.is_set()
    assert not client._inflight


async def test_a_throttled_graph_page_is_an_error(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    s2.get("/paper/ARXIV:1706.03762/references").respond(429)
    with pytest.raises(ScholarRateLimited):
        await make_client(max_attempts=2).references("1706.03762")


async def test_upload_ids_are_not_found_with_a_hint(make_client: MakeClient, s2: respx.MockRouter) -> None:
    with pytest.raises(ScholarNotFound, match="uploaded PDF"):
        await make_client().references("up_0123456789ab")
    assert len(s2.calls) == 0


# ---------------------------------------------------------------- caching


async def test_second_call_is_served_from_cache(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/search/match").respond(200, json={"data": [TRANSFORMER]})
    cache = RecordingCache()
    client = make_client(cache=cache)
    first = await client.lookup("Attention Is All You Need")
    second = await client.lookup("Attention Is All You Need")
    assert first == second
    assert route.call_count == 1
    assert (client.stats.cache_misses, client.stats.cache_hits, client.stats.http_requests) == (1, 1, 1)
    (ttl,) = cache.ttls.values()
    assert ttl == settings().s2_cache_ttl_seconds


async def test_cache_survives_a_new_client(
    make_client: MakeClient, s2: respx.MockRouter, tmp_path: Path
) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    await make_client(cache=FileCache(tmp_path / "shared")).lookup("1706.03762")
    again = await make_client(cache=FileCache(tmp_path / "shared")).lookup("1706.03762")
    assert route.call_count == 1
    assert again[0].s2_id == VASWANI


async def test_concurrent_identical_requests_share_one_fetch(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return httpx.Response(200, json=TRANSFORMER)

    route = s2.get("/paper/ARXIV:1706.03762").mock(side_effect=slow)
    # An in-memory cache, so every caller misses before the shared fetch can finish.
    client = make_client(cache=RecordingCache())
    tasks = [asyncio.create_task(client.lookup("1706.03762")) for _ in range(3)]
    for _ in range(5):
        await asyncio.sleep(0)
    gate.set()
    results = await asyncio.gather(*tasks)
    assert route.call_count == 1
    assert all(r[0].s2_id == VASWANI for r in results)


# ---------------------------------------------------------------- retries


async def test_429_with_retry_after_then_success(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}, json={"message": "Too Many Requests"}),
            httpx.Response(200, json=TRANSFORMER),
        ]
    )
    time = FakeTime()
    client = make_client(time=time)
    matches = await client.lookup("1706.03762")
    assert [m.s2_id for m in matches] == [VASWANI]
    assert route.call_count == 2
    assert time.sleeps == [3.0]  # Retry-After wins over jittered backoff
    assert client.stats.retries == 1


async def test_429_without_retry_after_uses_capped_full_jitter(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(429),
            httpx.Response(200, json=TRANSFORMER),
        ]
    )
    time = FakeTime()
    await make_client(time=time).lookup("1706.03762")
    assert route.call_count == 4
    assert len(time.sleeps) == 3
    for attempt, delay in enumerate(time.sleeps, start=1):
        assert RATE_LIMIT_MIN_DELAY_S <= delay <= max(RATE_LIMIT_MIN_DELAY_S, min(BACKOFF_CAP_S, 2**attempt))


async def test_5xx_backoff_is_full_jitter(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/ARXIV:1706.03762").respond(502)

    class LowDraw(random.Random):
        def uniform(self, a: float, b: float) -> float:
            return a  # the bottom of every jitter window

    time = FakeTime()
    with pytest.raises(ScholarUnavailable):
        await make_client(time=time, rng=LowDraw()).lookup("1706.03762")
    # No floor for server errors: full jitter may retry immediately.
    assert time.sleeps == [0.0, 0.0, 0.0]


async def test_exhausted_429_retries_raise_rate_limited(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(
        429, json={"message": "Too Many Requests", "code": "429"}
    )
    with pytest.raises(ScholarRateLimited):
        await make_client().lookup("1706.03762")
    assert route.call_count == MAX_ATTEMPTS == 4


async def test_exhausted_5xx_retries_raise_unavailable(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(503)
    with pytest.raises(ScholarUnavailable):
        await make_client().lookup("1706.03762")
    assert route.call_count == 4


async def test_timeouts_are_retried(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").mock(
        side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("refused"),
            httpx.Response(200, json=TRANSFORMER),
        ]
    )
    matches = await make_client().lookup("1706.03762")
    assert route.call_count == 3
    assert matches[0].s2_id == VASWANI


async def test_per_request_timeout_keeps_a_short_connect_bound(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    await make_client().lookup("1706.03762")
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["connect"] == CONNECT_TIMEOUT_S
    assert timeout["read"] == REQUEST_TIMEOUT_S


async def test_timeout_shrinks_to_the_remaining_budget(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    await make_client(budget_s=3.0).lookup("1706.03762")
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["read"] == pytest.approx(3.0)
    assert timeout["connect"] == pytest.approx(3.0)


async def test_timeouts_exhausted_raise_unavailable(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/ARXIV:1706.03762").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(ScholarUnavailable, match="timed out"):
        await make_client().lookup("1706.03762")


async def test_client_errors_are_not_retried(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(
        400, json={"error": "Unrecognized or unsupported fields: [x]"}
    )
    with pytest.raises(ScholarError, match="unsupported fields"):
        await make_client().lookup("1706.03762")
    assert route.call_count == 1


async def test_failures_are_not_cached(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").mock(
        side_effect=[httpx.Response(503)] * 4 + [httpx.Response(200, json=TRANSFORMER)]
    )
    client = make_client()
    with pytest.raises(ScholarUnavailable):
        await client.lookup("1706.03762")
    assert (await client.lookup("1706.03762"))[0].s2_id == VASWANI
    assert route.call_count == 5


async def test_retry_after_beyond_the_budget_fails_fast(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(429, headers={"Retry-After": "120"})
    time = FakeTime()
    with pytest.raises(ScholarRateLimited) as info:
        await make_client(time=time, budget_s=20.0).lookup("1706.03762")
    assert route.call_count == 1
    assert time.sleeps == []
    assert info.value.retry_after == 120.0


async def test_a_long_local_queue_is_reported_as_rate_limited(
    make_client: MakeClient, s2: respx.MockRouter
) -> None:
    s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    time = FakeTime()
    bucket = TokenBucket(rate=0.01, capacity=1, clock=time.clock)  # next slot is 100 s away
    bucket.reserve()
    with pytest.raises(ScholarRateLimited, match="queued"):
        await make_client(time=time, limiter=bucket, budget_s=10.0).lookup("1706.03762")
    assert len(s2.calls) == 0


async def test_requests_wait_for_the_rate_limiter(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    s2.get("/paper/ARXIV:1706.03762/references").respond(200, json=graph_page("citedPaper", []))
    time = FakeTime()
    bucket = TokenBucket(rate=1.0, capacity=1, clock=time.clock)
    await make_client(time=time, limiter=bucket).references("1706.03762")
    # The two requests are issued together; the limiter still spaces them a second apart.
    assert time.sleeps == [pytest.approx(1.0)]


# ---------------------------------------------------------------- headers


async def test_api_key_header_only_when_configured(make_client: MakeClient, s2: respx.MockRouter) -> None:
    route = s2.get("/paper/ARXIV:1706.03762").respond(200, json=TRANSFORMER)
    await make_client(cache=RecordingCache()).lookup("1706.03762")
    assert "x-api-key" not in route.calls.last.request.headers

    keyed = make_client(cache=RecordingCache(), cfg=settings(s2_api_key=SecretStr("sekret")))
    assert keyed.authenticated
    await keyed.lookup("1706.03762")
    assert route.calls.last.request.headers["x-api-key"] == "sekret"


async def test_blank_api_key_counts_as_none(make_client: MakeClient) -> None:
    assert not make_client(cfg=settings(s2_api_key=SecretStr("   "))).authenticated


async def test_rejected_api_key_is_explicit(make_client: MakeClient, s2: respx.MockRouter) -> None:
    s2.get("/paper/ARXIV:1706.03762").respond(403, json={"message": "Forbidden"})
    client = make_client(cfg=settings(s2_api_key=SecretStr("bad")))
    with pytest.raises(ScholarError, match="API key"):
        await client.lookup("1706.03762")


# ---------------------------------------------------------------- helpers


def test_parse_retry_after() -> None:
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None
    assert parse_retry_after("7") == 7.0
    assert parse_retry_after("-3") == 0.0
    assert parse_retry_after("soon") is None
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=UTC)
    header = format_datetime(now + timedelta(seconds=30), usegmt=True)
    assert parse_retry_after(header, now=now.timestamp) == pytest.approx(30.0)


def test_cache_key_is_stable_and_param_order_free() -> None:
    a = cache_key("paper/search", {"query": "x", "fields": "a,b", "limit": 3})
    b = cache_key("paper/search", {"limit": 3, "fields": "a,b", "query": "x"})
    assert a == b
    assert a.startswith("paper/search:")
    assert a != cache_key("paper/search", {"query": "y", "fields": "a,b", "limit": 3})
