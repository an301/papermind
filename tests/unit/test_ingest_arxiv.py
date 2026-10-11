"""arXiv id normalisation, Atom parsing, politeness and the download checks."""

from __future__ import annotations

import asyncio
import time
from datetime import date
from itertools import pairwise

import httpx
import pytest
import respx

from papermind.ingest.arxiv import (
    API_URL,
    ArxivClient,
    ArxivNotFound,
    ArxivRef,
    ArxivUnavailable,
    InvalidArxivId,
    PoliteSchedule,
    looks_like_pdf,
    parse_arxiv_id,
    parse_atom,
)


@pytest.mark.parametrize(
    ("raw", "arxiv_id", "version"),
    [
        ("1706.03762", "1706.03762", None),
        ("1706.03762v7", "1706.03762", "v7"),
        ("  1706.03762V7 ", "1706.03762", "v7"),
        ("arXiv:1706.03762", "1706.03762", None),
        ("arxiv: 1706.03762v2", "1706.03762", "v2"),
        ("1412.6980.pdf", "1412.6980", None),
        ("0704.0001", "0704.0001", None),
        ("https://arxiv.org/abs/1706.03762", "1706.03762", None),
        ("https://arxiv.org/abs/1706.03762v7", "1706.03762", "v7"),
        ("http://www.arxiv.org/abs/1706.03762v7/", "1706.03762", "v7"),
        ("arxiv.org/pdf/1706.03762v7", "1706.03762", "v7"),
        ("https://arxiv.org/pdf/1706.03762.pdf", "1706.03762", None),
        ("https://arxiv.org/pdf/1706.03762v5.pdf", "1706.03762", "v5"),
        ("https://export.arxiv.org/abs/2305.18290v3", "2305.18290", "v3"),
        ("https://arxiv.org/html/2305.18290v3", "2305.18290", "v3"),
        ("https://arxiv.org/abs/1706.03762?context=cs", "1706.03762", None),
        ("https://arxiv.org/abs/1706.03762#section", "1706.03762", None),
        ("hep-th/9901001", "hep-th/9901001", None),
        ("hep-th/9901001v2", "hep-th/9901001", "v2"),
        ("math.GT/0309136", "math/0309136", None),
        ("https://arxiv.org/abs/cond-mat/0102536v1", "cond-mat/0102536", "v1"),
        ("arxiv.org/pdf/quant-ph/0201082.pdf", "quant-ph/0201082", None),
    ],
)
def test_parse_arxiv_id_accepts_every_form(raw: str, arxiv_id: str, version: str | None) -> None:
    assert parse_arxiv_id(raw) == ArxivRef(arxiv_id=arxiv_id, version=version)


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "attention is all you need",
        "see 1706.03762 for details",
        "1706.037",  # too short
        "1713.03762",  # month 13
        "1706.03762v0",
        "https://example.com/abs/1706.03762",
        "https://arxiv.org/list/cs.LG/recent",
        "10.48550/arXiv.1706.03762",
    ],
)
def test_parse_arxiv_id_rejects_junk(raw: str) -> None:
    with pytest.raises(InvalidArxivId):
        parse_arxiv_id(raw)


def test_versioned_id() -> None:
    assert ArxivRef("1706.03762", "v7").versioned_id == "1706.03762v7"
    assert ArxivRef("hep-th/9901001").versioned_id == "hep-th/9901001"


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry>
    <id>http://arxiv.org/abs/1706.03762v7</id>
    <published>2017-06-12T17:57:34Z</published>
    <title>Attention Is All
      You Need</title>
    <summary>  The dominant sequence transduction models
      are based on complex recurrent networks. </summary>
    <author><name>Ashish Vaswani</name></author>
    <author><name>Noam Shazeer</name></author>
  </entry>
</feed>"""

EMPTY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>0</opensearch:totalResults>
</feed>"""

ERROR_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">
  <opensearch:totalResults>1</opensearch:totalResults>
  <entry><id>http://arxiv.org/api/errors#incorrect_id_format</id><title>Error</title>
  <summary>incorrect id format</summary></entry>
</feed>"""


def test_parse_atom_reads_metadata_and_resolved_version() -> None:
    meta = parse_atom(FEED, ArxivRef("1706.03762"))
    assert meta.version == "v7"
    assert meta.title == "Attention Is All You Need"
    assert meta.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert (
        meta.abstract == "The dominant sequence transduction models are based on complex recurrent networks."
    )
    assert meta.published == date(2017, 6, 12)
    assert meta.versioned_id == "1706.03762v7"


@pytest.mark.parametrize("feed", [EMPTY_FEED, ERROR_FEED])
def test_parse_atom_unknown_ids(feed: str) -> None:
    with pytest.raises(ArxivNotFound):
        parse_atom(feed, ArxivRef("1706.99999"))


def test_parse_atom_garbage_is_unavailable() -> None:
    with pytest.raises(ArxivUnavailable):
        parse_atom("<html>busy</html", ArxivRef("1706.03762"))


def test_looks_like_pdf() -> None:
    assert looks_like_pdf(b"%PDF-1.5\n...")
    assert looks_like_pdf(b"\r\n" * 10 + b"%PDF-1.4")
    assert not looks_like_pdf(b"<!DOCTYPE html><html>")


def test_polite_schedule_reserves_spaced_slots() -> None:
    schedule = PoliteSchedule(3.0)
    delays = [schedule.reserve() for _ in range(3)]
    assert delays[0] == pytest.approx(0.0, abs=0.05)
    assert delays[1] == pytest.approx(3.0, abs=0.05)
    assert delays[2] == pytest.approx(6.0, abs=0.05)


async def test_polite_schedule_spaces_concurrent_callers() -> None:
    schedule = PoliteSchedule(0.05)
    stamps: list[float] = []

    async def call() -> None:
        await schedule.wait()
        stamps.append(time.monotonic())

    await asyncio.gather(*(call() for _ in range(4)))
    gaps = [b - a for a, b in pairwise(stamps)]
    assert all(gap >= 0.04 for gap in gaps)


def _client(**kwargs: object) -> ArxivClient:
    # A fresh schedule with no interval and no backoff: tests must not sleep.
    return ArxivClient(schedule=PoliteSchedule(0.0), backoff_s=0.0, **kwargs)  # type: ignore[arg-type]


@respx.mock
async def test_metadata_sends_user_agent_and_versioned_id() -> None:
    route = respx.get(API_URL).mock(return_value=httpx.Response(200, text=FEED))
    async with _client() as client:
        meta = await client.metadata(ArxivRef("1706.03762", "v7"))
    assert meta.version == "v7"
    request = route.calls.last.request
    assert request.url.params["id_list"] == "1706.03762v7"
    assert "PaperMind" in request.headers["user-agent"]


@respx.mock
async def test_download_pdf_fetches_the_pinned_version() -> None:
    route = respx.get("https://arxiv.org/pdf/1706.03762v7").mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.5 body", headers={"content-type": "application/pdf"}
        )
    )
    async with _client() as client:
        assert await client.download_pdf("1706.03762", "v7") == b"%PDF-1.5 body"
    assert route.called


@respx.mock
async def test_download_rejects_html_interstitial() -> None:
    respx.get("https://arxiv.org/pdf/2401.00001v1").mock(
        return_value=httpx.Response(
            200, text="<html>PDF is being generated</html>", headers={"content-type": "text/html"}
        )
    )
    async with _client() as client:
        with pytest.raises(ArxivUnavailable, match="did not return a PDF"):
            await client.download_pdf("2401.00001", "v1")


@respx.mock
async def test_download_404_is_not_found() -> None:
    respx.get("https://arxiv.org/pdf/1706.03762v99").mock(return_value=httpx.Response(404))
    async with _client() as client:
        with pytest.raises(ArxivNotFound):
            await client.download_pdf("1706.03762", "v99")


@respx.mock
async def test_transient_errors_are_retried_then_reported() -> None:
    route = respx.get(API_URL).mock(
        side_effect=[httpx.Response(503), httpx.ConnectTimeout("slow"), httpx.Response(200, text=FEED)]
    )
    async with _client(attempts=3) as client:
        meta = await client.metadata(ArxivRef("1706.03762"))
    assert meta.version == "v7" and route.call_count == 3

    route.side_effect = [httpx.Response(503)] * 3
    async with _client(attempts=3) as client:
        with pytest.raises(ArxivUnavailable, match="not responding"):
            await client.metadata(ArxivRef("1706.03762"))


@respx.mock
async def test_bad_request_is_not_found() -> None:
    respx.get(API_URL).mock(return_value=httpx.Response(400, text="bad id"))
    async with _client() as client:
        with pytest.raises(ArxivNotFound):
            await client.metadata(ArxivRef("1706.03762"))
