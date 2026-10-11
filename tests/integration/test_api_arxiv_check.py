"""The add-paper arXiv check against the real arXiv API (network; three polite requests)."""

from __future__ import annotations

import pytest

from papermind.api.arxiv_check import ArxivLookup, check_arxiv
from papermind.api.errors import APIError
from papermind.ingest.arxiv import ArxivRef

pytestmark = pytest.mark.integration


async def test_an_unversioned_id_resolves_to_its_latest_version() -> None:
    choice = await check_arxiv(ArxivLookup(), ArxivRef("1706.03762"), paper_id="1706.03762")
    assert choice.title == "Attention Is All You Need"
    # v7 at the time of writing; a newer one would still be pinned explicitly.
    assert choice.versioned_id.startswith("1706.03762v") and int(choice.versioned_id.rsplit("v", 1)[1]) >= 7


async def test_a_paper_whose_latest_version_is_a_withdrawal_offers_the_one_before() -> None:
    # Withdrawn in 2008 with a notice in place of the abstract; v1 is the paper.
    with pytest.raises(APIError) as refused:
        await check_arxiv(ArxivLookup(), ArxivRef("0812.4560"), paper_id="0812.4560")
    assert refused.value.code == "withdrawn"
    assert refused.value.extra == {"latest_version": "v1", "paper_id": "0812.4560"}


async def test_an_id_arxiv_does_not_have_is_not_found() -> None:
    with pytest.raises(APIError) as refused:
        await check_arxiv(ArxivLookup(), ArxivRef("2401.99999"), paper_id="2401.99999")
    assert refused.value.status_code == 422 and refused.value.code == "not_found"
