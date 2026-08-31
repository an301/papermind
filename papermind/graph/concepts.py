"""Prerequisite-graph queries over the 350-concept ontology.

The core question the agent asks: "to understand concept X, which prerequisites
does this user not yet KNOW?" That is a traversal of :REQUIRES edges minus the
user's :KNOWS edges -- one Cypher query, not a Python graph walk. Keep it that
way; pulling the graph into Python to traverse it is the obvious wrong turn.

The graph is small (350 nodes). Assume it fits in memory and optimize for query
clarity over cleverness.
"""
