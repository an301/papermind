"""Builds the frozen, deterministically ordered Anthropic tool list from MCP servers.

Connects to the three MCP servers, collects their advertised tools, namespaces
them (`retriever.search`, `graph.prerequisites_of`, ...) to avoid collisions,
sorts by name, and freezes. Called once at startup -- see the caching rule in
`app/agent/loop.py`.
"""
