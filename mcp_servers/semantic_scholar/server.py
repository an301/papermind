"""MCP server: Semantic Scholar -- citations, references, related work.

The only server that talks to a third party, so it owns the failure handling:
rate limits, timeouts, and a cache. External lookups are best-effort; the agent
must degrade to explaining from the paper alone when this server is down.
"""
