"""One real turn: the hosted model, the three MCP servers over stdio, and the graph.

Spends money (about $0.10), so it is marked ``eval`` as well as
``integration``: ``pytest -m eval tests/integration/test_agent_live.py``.
Needs ANTHROPIC_API_KEY, the ingested 1706.03762 and network access to
Semantic Scholar. Model output varies, so the assertions are about the
contract (labels, attribution, events, caching), not the wording.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from papermind.agent.labels import citation_labels
from papermind.agent.loop import TurnContext, build_runner
from papermind.config import get_settings
from papermind.graph.store import get_graph_store
from papermind.llm.client import LLM
from papermind.mcp_client.manager import SERVER_NAMES, MCPManager
from papermind.schemas.paper import PaperRecord
from papermind.schemas.session import Session, UserProfile
from tests.unit.fakes import EventLog

pytestmark = [pytest.mark.integration, pytest.mark.eval]

PAPER_ID = "1706.03762"


@pytest.fixture
async def stack() -> AsyncIterator[tuple[object, PaperRecord, object]]:
    settings = get_settings().model_copy(update={"mcp_transport": "stdio"})
    if not settings.has_api_key:
        pytest.skip("no ANTHROPIC_API_KEY")
    path = settings.papers_dir / PAPER_ID / "paper.json"
    if not path.exists():
        pytest.skip(f"{PAPER_ID} is not ingested")
    record = PaperRecord.model_validate_json(path.read_bytes())
    llm = LLM(settings)
    graph = await get_graph_store(settings)
    mcp = MCPManager(settings)
    await mcp.start(required=SERVER_NAMES)
    try:
        yield build_runner(settings, llm=llm, mcp=mcp, knowledge_source=graph.known), record, graph
    finally:
        await mcp.aclose()
        await graph.aclose()
        await llm.aclose()


async def test_a_cited_metric_is_looked_up_cited_as_p1_and_attributed(stack) -> None:
    runner, record, graph = stack
    user_id = f"u_eval_{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    ctx = TurnContext(
        session=Session(
            session_id="s_eval",
            user_id=user_id,
            paper_id=PAPER_ID,
            level="intermediate",
            created_at=now,
            updated_at=now,
        ),
        user=UserProfile(user_id=user_id, level="intermediate", created_at=now),
        paper=record.meta,
        question="What is BLEU and why do they use it?",
        quote=None,
        level="intermediate",
        known_summary=[],
        knowledge={},
    )
    warmed = await runner.prewarm(record.meta)
    events = EventLog()
    try:
        result = await runner.run_turn(ctx, events)
    finally:
        for concept_id in await graph.known(user_id):
            await graph.forget(user_id, concept_id)

    assert result.error is None, result.error
    lookups = [e for e in events.of("tool.finished") if e.tool == "lookup_paper"]
    assert lookups and lookups[0].ok and lookups[0].detail["s2_ids"]

    [done] = events.of("answer.done")
    assert done.external, "the looked-up paper was not cited as [P#]"
    # external lists exactly the P labels left in the text, and the text keeps only S labels it was given.
    used = citation_labels(done.text)
    assert [e.label for e in done.external] == list(dict.fromkeys(x for x in used if x.startswith("P")))
    assert set(done.cited) == {x for x in used if x.startswith("S")} <= {s.label for s in done.sources}

    # Every cited passage is pinned to a sentence that is really in it.
    chunks = {c.chunk_id: " ".join(c.text.split()) for c in record.chunks}
    cited = [s for s in done.sources if s.label in done.cited]
    assert cited
    for source in cited:
        if source.cited_sentence is not None:
            assert source.cited_sentence in chunks[source.chunk_id]
    assert any(s.cited_sentence for s in cited)

    # After the prewarm, every agent and writer call reads the cached prefix.
    assert len(warmed) == 2
    for call in result.trace.model_calls:
        if call.purpose in ("agent", "writer"):
            assert call.cache_read_input_tokens > 0, call
    assert events.types[-2:] == ["usage", "done"]
