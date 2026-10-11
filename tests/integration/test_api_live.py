"""The real app against real services: Redis, Neo4j, and the three MCP servers over stdio.

No API key (nothing here spends money; see test_api_chat_live.py for that).
The app runs on a temporary data_dir holding one benchmark paper and the
concept seed, so uploads and deletes never touch data/. Every reader profile
and session created here is removed afterwards.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import httpx
import pytest
import pytest_asyncio

from papermind.api.deps import Resources
from papermind.config import REPO_ROOT, Settings
from papermind.main import create_app
from papermind.memory.ingest_status import status_key
from papermind.memory.session_store import session_key

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

PAPER_ID = "1706.03762"


@dataclass
class Live:
    client: httpx.AsyncClient
    res: Resources
    users: list[str] = field(default_factory=list)
    sessions: list[str] = field(default_factory=list)
    papers: list[str] = field(default_factory=list)


def _settings(root: Path) -> Settings:
    data = root / "data"
    shutil.copytree(REPO_ROOT / "data" / "papers" / PAPER_ID, data / "papers" / PAPER_ID)
    shutil.copytree(REPO_ROOT / "data" / "concepts", data / "concepts")
    # The embedding model is a 200 MB download; share the repo's copy.
    (data / "models").symlink_to(REPO_ROOT / "data" / "models")
    return Settings(  # type: ignore[call-arg]
        _env_file=None, data_dir=data, anthropic_api_key=None, environment="test", prewarm_cache=False
    )


@pytest_asyncio.fixture(loop_scope="module", scope="module")
async def live(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Live]:
    settings = _settings(tmp_path_factory.mktemp("api_live"))
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        res: Resources = app.state.resources
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
            state = Live(client=client, res=res)
            try:
                yield state
            finally:
                driver = res.graph.driver  # type: ignore[attr-defined]
                for user_id in state.users:
                    await driver.execute_query("MATCH (u:User {id: $id}) DETACH DELETE u", id=user_id)
                assert res.redis is not None
                for sid in state.sessions:
                    await res.redis.delete(session_key(sid))
                for pid in state.papers:
                    await res.ingest_status.delete(pid)


async def _user(live: Live) -> str:
    user_id = (await live.client.post("/api/users")).json()["user_id"]
    live.users.append(user_id)
    return user_id


async def test_health_sees_every_real_service(live: Live) -> None:
    body = (await live.client.get("/api/health")).json()
    checks = body["checks"]
    for name in ("redis", "neo4j", "mcp:retriever", "mcp:prereq_graph", "mcp:semantic_scholar"):
        assert checks[name]["ok"], f"{name}: {checks[name]}"
    assert checks["anthropic_key"]["ok"] is False
    assert body["status"] == "degraded"
    assert live.res.sessions.backend == "redis" and live.res.graph.backend == "neo4j"


async def test_library_pdf_ranges_and_chunks(live: Live) -> None:
    rows = (await live.client.get("/api/papers")).json()
    assert [(r["paper_id"], r["status"]) for r in rows] == [(PAPER_ID, "ready")]

    detail = (await live.client.get(f"/api/papers/{PAPER_ID}")).json()
    assert detail["meta"]["title"].startswith("Attention Is All You Need")
    first = await live.client.get(f"/api/papers/{PAPER_ID}/pdf", headers={"Range": "bytes=0-99"})
    assert first.status_code == 206
    assert first.content.startswith(b"%PDF")
    assert len(first.content) == 100
    assert first.headers["content-range"].startswith("bytes 0-99/")

    record = live.res.papers.store.load(PAPER_ID)
    assert record is not None
    chunk_id = record.chunks[0].chunk_id
    chunk = (await live.client.get(f"/api/papers/{PAPER_ID}/chunks/{chunk_id}")).json()
    assert chunk["chunk_id"] == chunk_id and chunk["text"]

    suggestions = (await live.client.get(f"/api/papers/{PAPER_ID}/suggestions?level=beginner")).json()
    assert len(suggestions["questions"]) == 4


async def test_reader_knowledge_round_trips_through_neo4j(live: Live) -> None:
    user_id = await _user(live)
    c = live.client
    patched = (await c.patch(f"/api/users/{user_id}", json={"level": "beginner", "onboarded": True})).json()
    assert patched["level"] == "beginner"

    added = await c.post(f"/api/users/{user_id}/knowledge", json={"concept": "softmax"})
    assert added.status_code == 200 and added.json()["source"] == "self_reported"
    onboarded = (
        await c.post(f"/api/users/{user_id}/knowledge/areas", json={"areas": ["linear-algebra"]})
    ).json()
    assert onboarded and {r["source"] for r in onboarded} <= {"onboarding", "self_reported"}

    rows = (await c.get(f"/api/users/{user_id}/knowledge")).json()
    names = [r["name"] for r in rows]
    assert names == sorted(names, key=str.casefold)
    softmax_id = added.json()["concept_id"]
    assert softmax_id in {r["concept_id"] for r in rows}

    assert (await c.delete(f"/api/users/{user_id}/knowledge/{softmax_id}")).status_code == 204
    after = {r["concept_id"] for r in (await c.get(f"/api/users/{user_id}/knowledge")).json()}
    assert softmax_id not in after

    detail = (await c.get(f"/api/concepts/{softmax_id}", params={"user_id": user_id})).json()
    assert detail["known"] is False and detail["learning_path"][-1]["concept_id"] == softmax_id
    assert (await c.get("/api/concepts", params={"q": "attention"})).json()


async def test_session_and_chat_without_a_key(live: Live) -> None:
    user_id = await _user(live)
    c = live.client
    session = (await c.post("/api/sessions", json={"user_id": user_id, "paper_id": PAPER_ID})).json()
    sid = session["session_id"]
    live.sessions.append(sid)
    assert live.res.redis is not None
    assert 0 < await live.res.redis.ttl(session_key(sid)) <= live.res.settings.session_ttl_seconds

    response = await c.post("/api/chat", json={"session_id": sid, "message": "What is attention?"})
    assert response.status_code == 200
    assert "event: error" in response.text and '"code":"no_api_key"' in response.text
    assert response.text.rstrip().endswith('data: {"type":"done"}')

    await c.patch(f"/api/sessions/{sid}", json={"scroll": {"page": 2, "offset": 0.5}})
    saved = (await c.get(f"/api/sessions/{sid}")).json()
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["scroll"]["page"] == 2


def _small_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    y = 72.0
    page.insert_text((72, y), "A Tiny Paper About Integration Tests", fontsize=18)
    for number, title in (("1", "Introduction"), ("2", "Method"), ("3", "Results")):
        y += 40
        page.insert_text((72, y), f"{number} {title}", fontsize=13)
        for _ in range(4):
            y += 16
            page.insert_text(
                (72, y),
                f"This section describes the {title.lower()} of a system that checks itself end to end.",
                fontsize=10,
            )
    data: bytes = doc.tobytes()
    doc.close()
    return data


async def test_upload_ingests_end_to_end_then_deletes(live: Live) -> None:
    c = live.client
    response = await c.post(
        "/api/papers/upload", files={"file": ("tiny.pdf", _small_pdf(), "application/pdf")}
    )
    assert response.status_code == 202
    paper_id = response.json()["paper_id"]
    live.papers.append(paper_id)

    deadline = asyncio.get_running_loop().time() + 120
    row: dict = {}
    while asyncio.get_running_loop().time() < deadline:
        rows = (await c.get("/api/papers")).json()
        row = next(r for r in rows if r["paper_id"] == paper_id)
        if row["status"] in ("ready", "failed"):
            break
        await asyncio.sleep(0.25)
    assert row["status"] == "ready", row
    meta = (await c.get(f"/api/papers/{paper_id}")).json()["meta"]
    assert meta["source"] == "upload" and meta["filename"] == "tiny.pdf" and meta["chunk_count"] >= 1

    assert (await c.delete(f"/api/papers/{paper_id}")).status_code == 204
    assert (await c.get(f"/api/papers/{paper_id}")).status_code == 404
    assert live.res.redis is not None and not await live.res.redis.exists(status_key(paper_id))
