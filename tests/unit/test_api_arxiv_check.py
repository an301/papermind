from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from papermind.api.arxiv_check import ArxivChoice, ArxivLookup, check_arxiv, choose_version, is_withdrawn
from papermind.api.errors import APIError
from papermind.ingest.arxiv import ArxivMetadata, ArxivNotFound, ArxivRef, ArxivUnavailable
from tests.unit.test_api_support import FakeArxivLookup, arxiv_meta, make_harness

WITHDRAWN_ABSTRACT = (
    "This paper has been withdrawn by the author due to a crucial error in the proof of Lemma 2.2."
)


@dataclass(frozen=True, slots=True)
class _MetaWithComment(ArxivMetadata):
    """ArxivMetadata as it will look once the ingest package parses <arxiv:comment>."""

    comment: str | None = None


def _error(exc: pytest.ExceptionInfo[APIError]) -> dict[str, Any]:
    return {
        "status": exc.value.status_code,
        "code": exc.value.code,
        "message": exc.value.message,
        **exc.value.extra,
    }


# ---------------------------------------------------------------- withdrawal detection


@pytest.mark.parametrize(
    "text",
    [
        "This paper has been withdrawn",
        "This paper has been withdrawn by the author due to a crucial sign error in equation 1.",
        "This version is being withdrawn because errors were identified in the analysis.",
        "Withdrawn by the authors because this version was submitted before both authors approved it",
        "withdrawn",
        "Paper withdrawn by the authors.",
        "  This submission has been withdrawn by arXiv administrators",
    ],
)
def test_withdrawal_notices_are_recognised(text: str) -> None:
    assert is_withdrawn(_MetaWithComment(**_fields(arxiv_meta()), comment=text))
    assert is_withdrawn(arxiv_meta(abstract=text)), "older withdrawals replaced the abstract instead"


@pytest.mark.parametrize(
    "text",
    [
        "15 pages, 3 figures, accepted at ICLR 2015",
        "v2: withdrawn claim in Section 3 corrected",
        "We study why papers are withdrawn from preprint servers.",
        "",
    ],
)
def test_text_that_only_mentions_withdrawal_is_not_a_withdrawal(text: str) -> None:
    assert not is_withdrawn(_MetaWithComment(**_fields(arxiv_meta()), comment=text))
    assert not is_withdrawn(arxiv_meta(abstract=text))


def _fields(meta: ArxivMetadata) -> dict[str, Any]:
    return {name: getattr(meta, name) for name in ArxivMetadata.__dataclass_fields__}


# ---------------------------------------------------------------- choosing the version


def test_unversioned_input_pins_the_latest_version() -> None:
    choice = choose_version(
        ArxivRef("1409.0473"), arxiv_meta(version="v7"), paper_id="1409.0473", keep_version=False
    )
    assert choice == ArxivChoice(
        versioned_id="1409.0473v7",
        title="Neural Machine Translation by Jointly Learning to Align and Translate",
    )


def test_the_latest_version_pasted_explicitly_is_accepted() -> None:
    choice = choose_version(
        ArxivRef("1409.0473", "v7"), arxiv_meta(version="v7"), paper_id="1409.0473", keep_version=False
    )
    assert choice.versioned_id == "1409.0473v7"


def test_an_older_version_is_refused_with_the_latest_unless_the_reader_insists() -> None:
    ref = ArxivRef("1706.03762", "v3")
    latest = arxiv_meta("1706.03762", "v5")
    with pytest.raises(APIError) as refused:
        choose_version(ref, latest, paper_id="1706.03762", keep_version=False)
    assert _error(refused) == {
        "status": 422,
        "code": "newer_version",
        "message": "You pasted v3. The latest version is v5.",
        "latest_version": "v5",
        "paper_id": "1706.03762",
    }
    kept = choose_version(ref, latest, paper_id="1706.03762", keep_version=True)
    assert kept.versioned_id == "1706.03762v3"


def test_a_version_arxiv_does_not_have_is_not_found_with_the_latest() -> None:
    with pytest.raises(APIError) as refused:
        choose_version(
            ArxivRef("1706.03762", "v9"),
            arxiv_meta("1706.03762", "v7"),
            paper_id="1706.03762",
            keep_version=True,
        )
    assert _error(refused) == {
        "status": 422,
        "code": "not_found",
        "message": "arXiv has no version v9 of 1706.03762. The latest is v7.",
        "latest_version": "v7",
        "paper_id": "1706.03762",
    }


@pytest.mark.parametrize("pasted", [None, "v3"])
def test_a_withdrawn_paper_offers_the_version_before_the_withdrawal(pasted: str | None) -> None:
    latest = arxiv_meta("0812.4560", "v3", abstract=WITHDRAWN_ABSTRACT)
    with pytest.raises(APIError) as refused:
        choose_version(ArxivRef("0812.4560", pasted), latest, paper_id="0812.4560", keep_version=False)
    assert _error(refused) == {
        "status": 422,
        "code": "withdrawn",
        "message": "arXiv lists 0812.4560 as withdrawn. Version v2 is still available.",
        "latest_version": "v2",
        "paper_id": "0812.4560",
    }


def test_the_version_before_a_withdrawal_can_be_added() -> None:
    latest = arxiv_meta("0812.4560", "v3", abstract=WITHDRAWN_ABSTRACT)
    choice = choose_version(ArxivRef("0812.4560", "v2"), latest, paper_id="0812.4560", keep_version=False)
    assert choice.versioned_id == "0812.4560v2"
    # v1 is older than the readable v2: the usual newer-version question applies.
    with pytest.raises(APIError) as refused:
        choose_version(ArxivRef("0812.4560", "v1"), latest, paper_id="0812.4560", keep_version=False)
    assert refused.value.code == "newer_version" and refused.value.extra["latest_version"] == "v2"


def test_a_withdrawn_paper_without_an_earlier_version() -> None:
    latest = arxiv_meta("2401.00001", "v1", abstract="This paper has been withdrawn.")
    with pytest.raises(APIError) as refused:
        choose_version(ArxivRef("2401.00001"), latest, paper_id="2401.00001", keep_version=False)
    error = _error(refused)
    assert error["code"] == "withdrawn" and "latest_version" not in error
    assert error["message"] == "arXiv lists 2401.00001 as withdrawn, and it has no earlier version to read."


def test_old_style_ids_keep_their_slash_in_messages() -> None:
    with pytest.raises(APIError) as refused:
        choose_version(
            ArxivRef("hep-th/9901001", "v1"),
            arxiv_meta("hep-th/9901001", "v2"),
            paper_id="hep-th_9901001",
            keep_version=False,
        )
    assert refused.value.message == "You pasted v1. The latest version is v2."
    assert refused.value.extra["paper_id"] == "hep-th_9901001"


# ---------------------------------------------------------------- the lookup


async def test_lookup_failures_become_add_paper_errors() -> None:
    lookup = FakeArxivLookup({"2401.00002": ArxivUnavailable("HTTP 503"), "2401.00003": TimeoutError()})

    with pytest.raises(APIError) as missing:
        await check_arxiv(lookup, ArxivRef("2401.99999"), paper_id="2401.99999")
    assert _error(missing) == {
        "status": 422,
        "code": "not_found",
        "message": "arXiv has no paper 2401.99999. Check the ID, or upload the PDF instead.",
        "paper_id": "2401.99999",
    }
    for arxiv_id in ("2401.00002", "2401.00003"):
        with pytest.raises(APIError) as down:
            await check_arxiv(lookup, ArxivRef(arxiv_id), paper_id=arxiv_id)
        assert down.value.status_code == 503 and down.value.code == "arxiv_unreachable"
        assert down.value.message.startswith("Couldn’t reach arXiv.")
        assert "503" not in down.value.message


class _FakeClient:
    def __init__(self, behaviour: Any) -> None:
        self.behaviour = behaviour
        self.refs: list[ArxivRef] = []
        self.closed = False

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.closed = True

    async def metadata(self, ref: ArxivRef) -> ArxivMetadata:
        self.refs.append(ref)
        return await self.behaviour(ref)


async def test_lookup_asks_for_the_unversioned_id_and_closes_its_client() -> None:
    async def found(ref: ArxivRef) -> ArxivMetadata:
        return arxiv_meta(ref.arxiv_id, "v7")

    client = _FakeClient(found)
    meta = await ArxivLookup(lambda: client).latest("1409.0473")  # type: ignore[arg-type,return-value]
    assert meta.version == "v7"
    assert client.refs == [ArxivRef("1409.0473")] and client.closed


async def test_lookup_is_bounded_in_time() -> None:
    async def hangs(ref: ArxivRef) -> ArxivMetadata:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    client = _FakeClient(hangs)
    with pytest.raises(TimeoutError):
        await ArxivLookup(lambda: client, timeout_s=0.05).latest("1409.0473")  # type: ignore[arg-type,return-value]
    assert client.closed


# ---------------------------------------------------------------- through the route


def _harness(tmp_path: Path, papers: dict[str, Any]) -> Any:
    h = make_harness(tmp_path, with_paper=False)
    h.res.arxiv = FakeArxivLookup(papers)
    return h


async def _settled(h: Any, paper_id: str) -> None:
    """Wait for the background job, so none outlives the test's event loop."""
    for _ in range(500):
        if not h.res.jobs.running(paper_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"the ingestion of {paper_id} never finished")


async def test_add_pins_the_resolved_version_and_shows_the_arxiv_title_at_once(tmp_path: Path) -> None:
    h = _harness(tmp_path, {"1409.0473": arxiv_meta("1409.0473", "v7")})
    h.ingest.gate.clear()
    async with h.client() as client:
        response = await client.post("/api/papers", json={"arxiv": "https://arxiv.org/abs/1409.0473"})
        rows = (await client.get("/api/papers")).json()
    h.ingest.gate.set()
    await _settled(h, "1409.0473")
    assert response.status_code == 202
    assert response.json()["title"] == "Neural Machine Translation by Jointly Learning to Align and Translate"
    assert rows[0]["title"] == response.json()["title"]
    assert h.ingest.calls == [("arxiv", "1409.0473v7")]


async def test_newer_version_then_keep_version(tmp_path: Path) -> None:
    h = _harness(tmp_path, {"1706.03762": arxiv_meta("1706.03762", "v7", title="Attention Is All You Need")})
    async with h.client() as client:
        refused = await client.post("/api/papers", json={"arxiv": "arXiv:1706.03762v5"})
        rows_after_refusal = (await client.get("/api/papers")).json()
        kept = await client.post("/api/papers?keep_version=true", json={"arxiv": "arXiv:1706.03762v5"})
    assert refused.status_code == 422
    assert refused.json() == {
        "error": {
            "code": "newer_version",
            "message": "You pasted v5. The latest version is v7.",
            "latest_version": "v7",
            "paper_id": "1706.03762",
        }
    }
    assert rows_after_refusal == [], "a refused paper leaves no row behind"
    assert kept.status_code == 202
    await _settled(h, "1706.03762")
    assert h.ingest.calls == [("arxiv", "1706.03762v5")]


async def test_withdrawn_and_missing_papers_are_refused_before_any_job(tmp_path: Path) -> None:
    h = _harness(tmp_path, {"0812.4560": arxiv_meta("0812.4560", "v2", abstract=WITHDRAWN_ABSTRACT)})
    async with h.client() as client:
        withdrawn = await client.post("/api/papers", json={"arxiv": "0812.4560"})
        missing = await client.post("/api/papers", json={"arxiv": "2401.99999"})
        earlier = await client.post("/api/papers", json={"arxiv": "0812.4560v1"})
    assert withdrawn.status_code == 422
    assert withdrawn.json()["error"] == {
        "code": "withdrawn",
        "message": "arXiv lists 0812.4560 as withdrawn. Version v1 is still available.",
        "latest_version": "v1",
        "paper_id": "0812.4560",
    }
    assert missing.status_code == 422 and missing.json()["error"]["code"] == "not_found"
    assert earlier.status_code == 202, "the version before the withdrawal can be added"
    await _settled(h, "0812.4560")
    assert h.ingest.calls == [("arxiv", "0812.4560v1")]


async def test_arxiv_unreachable_is_a_503_the_reader_can_retry(tmp_path: Path) -> None:
    h = _harness(tmp_path, {"1409.0473": ArxivUnavailable("arXiv is not responding (ConnectError)")})
    async with h.client() as client:
        response = await client.post("/api/papers", json={"arxiv": "1409.0473"})
        rows = (await client.get("/api/papers")).json()
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "arxiv_unreachable" and error["paper_id"] == "1409.0473"
    assert rows == [] and h.ingest.calls == []


async def test_papers_already_known_skip_the_lookup(tmp_path: Path) -> None:
    h = _harness(tmp_path, {"1409.0473": arxiv_meta()})
    h.ingest.gate.clear()
    async with h.client() as client:
        await client.post("/api/papers", json={"arxiv": "1409.0473"})
        again = await client.post("/api/papers", json={"arxiv": "1409.0473"})
        h.ingest.gate.set()
        for _ in range(200):
            if (await client.get("/api/papers/1409.0473")).json()["summary"]["status"] == "ready":
                break
            await asyncio.sleep(0.01)
        ready = await client.post("/api/papers", json={"arxiv": "1409.0473v1"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_added"
    assert ready.status_code == 200 and ready.json()["status"] == "ready"
    assert h.res.arxiv.calls == ["1409.0473"], "one lookup: in-flight and ready papers need none"


async def test_a_claim_lost_during_the_lookup_is_already_added(tmp_path: Path) -> None:
    h = make_harness(tmp_path, with_paper=False)

    class RacingLookup(FakeArxivLookup):
        async def latest(self, arxiv_id: str) -> ArxivMetadata:
            # Another request (or worker) claims the paper while we wait on arXiv.
            await h.res.ingest_status.claim("1409.0473", source="arxiv", title="arXiv:1409.0473")
            return await super().latest(arxiv_id)

    h.res.arxiv = RacingLookup({"1409.0473": arxiv_meta()})
    async with h.client() as client:
        response = await client.post("/api/papers", json={"arxiv": "1409.0473"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "already_added"
    assert h.ingest.calls == []


async def test_a_paper_not_on_arxiv_failing_in_the_background_names_its_arxiv_id(tmp_path: Path) -> None:
    """Without the up-front lookup the job still reports not_found, with the id the reader typed."""
    h = make_harness(tmp_path, with_paper=False)
    h.ingest.fail_with = ArxivNotFound("arXiv has no paper hep-th/9901001")
    async with h.client() as client:
        await client.post("/api/papers", json={"arxiv": "hep-th/9901001"})
        for _ in range(200):
            row = (await client.get("/api/papers")).json()[0]
            if row["status"] == "failed":
                break
            await asyncio.sleep(0.01)
    assert row["error_code"] == "not_found"
    assert row["error"] == "arXiv has no paper hep-th/9901001. Check the ID, or upload the PDF instead."
