"""GET /api/health: what is up, what is degraded, and why."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

from fastapi import APIRouter

from papermind.api.deps import Resources, ResourcesDep
from papermind.schemas.api import HealthResponse, ServiceCheck

router = APIRouter(tags=["health"])

MCP_SERVERS = ("retriever", "prereq_graph", "semantic_scholar")
CHECK_TIMEOUT_S = 2.0


async def _bounded(check: Awaitable[ServiceCheck]) -> ServiceCheck:
    # One hung dependency must not hang the health endpoint itself.
    try:
        return await asyncio.wait_for(check, CHECK_TIMEOUT_S)
    except TimeoutError:
        return ServiceCheck(ok=False, detail=f"no answer within {CHECK_TIMEOUT_S:.0f}s")
    except Exception as exc:
        return ServiceCheck(ok=False, detail=f"{type(exc).__name__}: {exc}")


async def _redis(res: Resources) -> ServiceCheck:
    if res.redis is None:
        return ServiceCheck(
            ok=False, detail="Redis is not connected; sessions are kept in this process's memory"
        )
    await res.redis.ping()
    return ServiceCheck(ok=True)


async def _neo4j(res: Resources) -> ServiceCheck:
    driver = getattr(res.graph, "driver", None)
    if res.graph.backend != "neo4j" or driver is None:
        return ServiceCheck(
            ok=False,
            detail="Neo4j is not connected; reader knowledge is kept in this process's memory "
            "(prereq_graph runs in-process on the same store) and lost on restart",
        )
    await driver.verify_connectivity()
    return ServiceCheck(ok=True)


async def _mcp(res: Resources) -> dict[str, ServiceCheck]:
    names = [f"mcp:{s}" for s in MCP_SERVERS]
    if res.mcp is None:
        return {n: ServiceCheck(ok=False, detail="MCP servers were not started") for n in names}
    try:
        reported = await asyncio.wait_for(res.mcp.health(), CHECK_TIMEOUT_S + 2.0)
    except Exception as exc:
        detail = "no answer in time" if isinstance(exc, TimeoutError) else f"{type(exc).__name__}: {exc}"
        return {n: ServiceCheck(ok=False, detail=detail) for n in names}
    return {n: reported.get(n, ServiceCheck(ok=False, detail="not configured")) for n in names}


async def _anthropic_key(res: Resources) -> ServiceCheck:
    if res.settings.has_api_key:
        return ServiceCheck(ok=True)
    return ServiceCheck(ok=False, detail="ANTHROPIC_API_KEY is not set in .env; chat is disabled")


@router.get("/health", response_model=HealthResponse)
async def health(res: ResourcesDep) -> HealthResponse:
    redis, neo4j, mcp, key = await asyncio.gather(
        _bounded(_redis(res)), _bounded(_neo4j(res)), _mcp(res), _bounded(_anthropic_key(res))
    )
    checks = {"redis": redis, "neo4j": neo4j, **mcp, "anthropic_key": key}
    status = "ok" if all(c.ok for c in checks.values()) else "degraded"
    return HealthResponse(status=status, version=res.settings.app_version, checks=checks)
