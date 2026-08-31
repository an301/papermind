"""Apply infra/neo4j/schema.cypher and load the 350-concept prerequisite graph.

Idempotent: safe to re-run against an existing database. Uses MERGE, not CREATE.
"""
