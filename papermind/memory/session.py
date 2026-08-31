"""Session document in Redis: paper_id, scroll position, message history. 24h TTL.

The boundary rule for this project: if losing it after 24 hours is acceptable,
it goes in Redis; if it is something the user learned, it goes in Neo4j. Scroll
position is Redis. "User understands backpropagation" is Neo4j. Getting this
wrong in either direction is the expensive mistake -- knowledge that evaporates
overnight, or a graph full of dead scroll offsets.

Refresh the TTL on write so an active session never expires underneath the user.
"""
