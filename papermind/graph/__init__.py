"""Prerequisite concept graph.

normalize  concept-name keys and free-text -> concept resolution (ConceptIndex)
gaps       pure BFS gap detection, teaching order, altitude
seed       seed-file models, validation, the idempotent Neo4j load
store      GraphStore protocol: Neo4jGraphStore, InMemoryGraphStore, get_graph_store()
service    per-reader analysis shared by the MCP server and the API
testing    a small valid catalog for tests and fakes
"""
