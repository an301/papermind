"""Per-user :KNOWS edges -- reads and writes of what the user understands.

Writes are the sensitive part: mastery is inferred, not declared, so an edge
carries a confidence and a provenance (which turn, which signal) rather than
being a bare boolean. Deleting or downgrading an edge must be possible; users
forget, and a graph that only ever accumulates knowledge will confidently
under-explain.
"""
