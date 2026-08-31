"""Async Neo4j driver lifecycle and query execution.

`AsyncGraphDatabase` from the official driver. One driver per process, shared;
sessions are cheap and per-request.
"""
