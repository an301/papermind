"""Agent-facing models: tool calls, observations, trace events, expertise level.

Expertise level is a three-valued enum (beginner / intermediate / expert), not a
free string -- it selects prompt variants and eval slices, so a typo must fail
loudly at the boundary.
"""
