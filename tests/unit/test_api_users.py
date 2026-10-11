from __future__ import annotations

from pathlib import Path

from papermind.schemas.tools import MarkKnownServerArgs
from tests.unit.test_api_support import make_harness


async def test_create_get_and_patch_a_profile(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        created = await client.post("/api/users")
        user_id = created.json()["user_id"]
        fetched = await client.get(f"/api/users/{user_id}")
        patched = await client.patch(
            f"/api/users/{user_id}", json={"display_name": "  Ada  ", "level": "expert", "onboarded": True}
        )
        cleared = await client.patch(f"/api/users/{user_id}", json={"display_name": "   "})
        level_only = await client.patch(f"/api/users/{user_id}", json={"level": "beginner"})
    assert created.status_code == 200
    assert user_id.startswith("u_") and len(user_id) == 14
    assert created.json()["level"] == "intermediate" and created.json()["onboarded"] is False
    assert fetched.json() == created.json()
    assert patched.json()["display_name"] == "Ada"
    assert (patched.json()["level"], patched.json()["onboarded"]) == ("expert", True)
    assert cleared.json()["display_name"] is None
    assert level_only.json()["onboarded"] is True, "PATCH leaves unspecified fields alone"
    assert (await h.graph.get_user(user_id)).level == "beginner"  # type: ignore[union-attr]


async def test_every_user_route_404s_for_an_unknown_user(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    async with h.client() as client:
        responses = [
            await client.get("/api/users/u_nobody"),
            await client.patch("/api/users/u_nobody", json={"level": "expert"}),
            await client.get("/api/users/u_nobody/knowledge"),
            await client.post("/api/users/u_nobody/knowledge", json={"concept": "softmax"}),
            await client.post("/api/users/u_nobody/knowledge/areas", json={"areas": ["math"]}),
            await client.delete("/api/users/u_nobody/knowledge/softmax"),
        ]
    for response in responses:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "user_not_found"


async def test_knowledge_lists_what_the_reader_knows_and_not_yet_on_request(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    await h.graph.mark(
        MarkKnownServerArgs(user_id="u_reader", concept="softmax", confidence=0.9, source="self_reported")
    )
    await h.graph.mark(
        MarkKnownServerArgs(
            user_id="u_reader", concept="attention", confidence=0.4, source="inferred", evidence="asked well"
        )
    )
    # Exactly at the threshold counts as known, as it does for the agent.
    await h.graph.mark(
        MarkKnownServerArgs(user_id="u_reader", concept="dot-product", confidence=0.6, source="inferred")
    )
    async with h.client() as client:
        known = (await client.get("/api/users/u_reader/knowledge")).json()
        everything = (await client.get("/api/users/u_reader/knowledge?include=not_yet")).json()
        bad = await client.get("/api/users/u_reader/knowledge?include=everything")
    assert [(r["name"], r["confidence"]) for r in known] == [("Dot Product", 0.6), ("Softmax", 0.9)]
    assert [(r["name"], r["confidence"], r["source"]) for r in everything] == [
        ("Attention", 0.4, "inferred"),
        ("Dot Product", 0.6, "inferred"),
        ("Softmax", 0.9, "self_reported"),
    ]
    assert everything[0]["evidence"] == "asked well"
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "validation_error"


async def test_adding_knowledge_by_name_or_alias(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    async with h.client() as client:
        by_alias = await client.post("/api/users/u_reader/knowledge", json={"concept": "inner product"})
        by_id = await client.post("/api/users/u_reader/knowledge", json={"concept": "softmax"})
    assert by_alias.status_code == 200
    body = by_alias.json()
    assert (body["concept_id"], body["name"], body["source"], body["confidence"]) == (
        "dot-product",
        "Dot Product",
        "self_reported",
        0.9,
    )
    assert by_id.json()["concept_id"] == "softmax"
    assert set(await h.graph.known("u_reader")) == {"dot-product", "softmax"}


async def test_unknown_concept_suggests_near_matches(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    async with h.client() as client:
        response = await client.post("/api/users/u_reader/knowledge", json={"concept": "softmax regression"})
        nothing_close = await client.post(
            "/api/users/u_reader/knowledge", json={"concept": "quantum chromodynamics"}
        )
        empty = await client.post("/api/users/u_reader/knowledge", json={"concept": ""})
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "concept_not_found"
    assert error["message"] == "“softmax regression” is not in the concept graph. Did you mean Softmax?"
    assert nothing_close.json()["error"]["message"] == "“quantum chromodynamics” is not in the concept graph."
    assert await h.graph.known("u_reader") == {}
    assert empty.status_code == 422


async def test_onboarding_marks_foundation_and_core_concepts_only(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    async with h.client() as client:
        response = await client.post("/api/users/u_reader/knowledge/areas", json={"areas": ["dl"]})
    assert response.status_code == 200
    rows = response.json()
    # Transformer and self-attention are the area's advanced tier: left for the agent to scaffold.
    assert [r["concept_id"] for r in rows] == ["attention", "batch-normalization"]
    assert {(r["source"], r["confidence"]) for r in rows} == {("onboarding", 0.9)}


async def test_onboarding_never_overrides_what_the_reader_said(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    await h.graph.mark(
        MarkKnownServerArgs(
            user_id="u_reader", concept="linear-algebra", confidence=0.95, source="self_reported"
        )
    )
    async with h.client() as client:
        rows = (await client.post("/api/users/u_reader/knowledge/areas", json={"areas": ["math"]})).json()
    by_id = {r["concept_id"]: r for r in rows}
    assert set(by_id) == {
        "derivative",
        "dot-product",
        "exponential-function",
        "linear-algebra",
        "mean-and-variance",
    }
    assert (by_id["linear-algebra"]["source"], by_id["linear-algebra"]["confidence"]) == (
        "self_reported",
        0.95,
    )


async def test_onboarding_rejects_unknown_areas_with_the_valid_ones(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    async with h.client() as client:
        response = await client.post(
            "/api/users/u_reader/knowledge/areas", json={"areas": ["math", "cooking"]}
        )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "unknown_area"
    assert "cooking" in error["message"] and "math, ml, dl" in error["message"]
    assert await h.graph.known("u_reader") == {}, "nothing is marked when any area is unknown"


async def test_forgetting_is_idempotent(tmp_path: Path) -> None:
    h = make_harness(tmp_path)
    await h.add_user()
    await h.graph.mark(
        MarkKnownServerArgs(user_id="u_reader", concept="softmax", confidence=0.9, source="self_reported")
    )
    async with h.client() as client:
        first = await client.delete("/api/users/u_reader/knowledge/softmax")
        again = await client.delete("/api/users/u_reader/knowledge/softmax")
        unknown = await client.delete("/api/users/u_reader/knowledge/not-a-concept")
    assert first.status_code == again.status_code == 204
    assert await h.graph.known("u_reader") == {}
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "concept_not_found"
