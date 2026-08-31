"""MCP server: hybrid retrieval over the active paper.

Tools (roughly): search_sections, get_section, list_sections.

Runs as a separate stdio process rather than as in-process functions so the
tool boundary is real -- the agent can only reach retrieval through a declared,
schema-checked interface, and the server can be exercised on its own with the
MCP inspector.
"""
