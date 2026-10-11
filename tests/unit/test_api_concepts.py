from __future__ import annotations

from pathlib import Path

from papermind.schemas.tools import MarkKnownServerArgs
from tests.unit.test_api_support import make_harness


async def test_search_by_name_alias_and_limit(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        by_alias = (await client.get("/api/concepts", params={"q": "inner product"})).json()
        limited = (await client.get("/api/concepts", params={"q": "attention", "limit": 1})).json()
        missing_q = await client.get("/api/concepts")
    assert by_alias[0]["concept_id"] == "dot-product"
    assert by_alias[0]["requires"] == ["linear-algebra"]
    assert len(limited) == 1
    assert missing_q.status_code == 422 and missing_q.json()["error"]["message"] == "q: is required"


async def test_areas_with_counts(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        areas = (await client.get("/api/concepts/areas")).json()
    assert {a["area_id"]: a["concept_count"] for a in areas} == {"math": 5, "ml": 3, "dl": 4}
    assert all(a["name"] and a["description"] for a in areas)


async def test_detail_with_prerequisites(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        detail = (await client.get("/api/concepts/attention")).json()
    assert detail["concept"]["name"] == "Attention"
    assert detail["prerequisites"] == [
        {"concept_id": "softmax", "name": "Softmax"},
        {"concept_id": "dot-product", "name": "Dot Product"},
    ]
    assert detail["known"] is False and detail["learning_path"] == []


async def test_detail_for_a_reader_has_known_flag_and_learning_path(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    for concept, confidence in (("dot-product", 0.9), ("exponential-function", 0.9), ("transformer", 0.3)):
        await h.graph.mark(
            MarkKnownServerArgs(user_id="u_reader", concept=concept, confidence=confidence, source="inferred")
        )
    async with h.client() as client:
        attention = (await client.get("/api/concepts/attention", params={"user_id": "u_reader"})).json()
        dot = (await client.get("/api/concepts/dot-product", params={"user_id": "u_reader"})).json()
        transformer = (await client.get("/api/concepts/transformer", params={"user_id": "u_reader"})).json()
    # Prerequisites first, the concept itself last; known concepts stop the walk.
    assert [s["concept_id"] for s in attention["learning_path"]] == ["softmax", "attention"]
    assert attention["known"] is False
    assert dot["known"] is True
    assert transformer["known"] is False, "an edge below the threshold is not 'known'"


async def test_unknown_concept_is_a_404(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        response = await client.get("/api/concepts/flux-capacitor")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "concept_not_found"
