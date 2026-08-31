"""Structured trace of one agent turn: thoughts, tool calls, observations, timings.

Serves three consumers, which is why it is a first-class object rather than log
lines: the SSE progress stream, the pytest assertions ("did it check
prerequisites before explaining?"), and the W&B eval run.
"""
