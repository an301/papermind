"""Readiness endpoint: pings Redis, Neo4j, and each MCP server.

Kept separate from the liveness check in `main.py` -- liveness must never
depend on a downstream service, or a Redis blip restarts the container.
"""
