"""MCP server: prerequisite graph and user knowledge state.

Tools (roughly): concept_lookup, missing_prerequisites, mark_known.

`mark_known` is the only tool in the system that mutates persistent user state.
Keep it explicit, logged, and reversible.
"""
