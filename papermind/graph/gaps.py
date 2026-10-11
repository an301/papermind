"""Prerequisite gap analysis: pure functions over an adjacency callback.

The graph store fetches the target's whole prerequisite closure in one round
trip; everything here runs on that in-memory snapshot, so it is fast, has no
I/O, and is tested without a database.

Edges point from a concept to what it requires: ``prerequisites_of("attention")``
returns ``["softmax", "dot-product"]``.
"""

from __future__ import annotations

import heapq
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from papermind.schemas.tools import Altitude

PrerequisitesOf = Callable[[str], Iterable[str]]


@dataclass(frozen=True)
class GapWalk:
    gaps: list[tuple[str, int]]
    """Unknown concepts as ``(concept_id, depth)`` in BFS order; the target first if unknown."""
    known_stops: list[str]
    """Known concepts the walk reached and did not descend into, in BFS order."""


def walk_prerequisites(target: str, known: set[str], prerequisites_of: PrerequisitesOf) -> GapWalk:
    """Breadth-first walk from ``target`` along REQUIRES edges.

    One rule does all the work: a known concept stops the walk (if the reader
    knows attention, they know what attention is built on, so we do not ask);
    an unknown concept is a gap and its prerequisites are enqueued. The visited
    set makes the walk terminate even on cyclic data and reports a concept
    reachable along several paths once, at its shortest distance.
    """
    gaps: list[tuple[str, int]] = []
    stops: list[str] = []
    visited = {target}
    queue: deque[tuple[str, int]] = deque([(target, 0)])
    while queue:
        concept, depth = queue.popleft()
        if concept in known:
            stops.append(concept)
            continue
        gaps.append((concept, depth))
        for prerequisite in prerequisites_of(concept):
            if prerequisite not in visited:
                visited.add(prerequisite)
                queue.append((prerequisite, depth + 1))
    return GapWalk(gaps=gaps, known_stops=stops)


def find_gaps(target: str, known: set[str], prerequisites_of: PrerequisitesOf) -> list[tuple[str, int]]:
    """Unknown concepts needed for ``target`` as ``(concept_id, depth)``, nearest first."""
    return walk_prerequisites(target, known, prerequisites_of).gaps


def known_stops(target: str, known: set[str], prerequisites_of: PrerequisitesOf) -> list[str]:
    """Known concepts where the walk from ``target`` stopped (the reader's covered foundations)."""
    return walk_prerequisites(target, known, prerequisites_of).known_stops


def _depths_within(nodes: set[str], prerequisites_of: PrerequisitesOf) -> dict[str, int]:
    """BFS depth of each node inside the subgraph induced by ``nodes``.

    Starts from every node no other node in the set requires. For a gap set
    produced by ``find_gaps`` that is exactly the target, and because the walk
    only ever passes through gaps, these depths equal the walk's own.
    """
    required = {p for n in nodes for p in prerequisites_of(n) if p in nodes}
    roots = sorted(nodes - required)
    depth = dict.fromkeys(roots, 0)
    queue = deque(roots)
    while queue:
        node = queue.popleft()
        for prerequisite in prerequisites_of(node):
            if prerequisite in nodes and prerequisite not in depth:
                depth[prerequisite] = depth[node] + 1
                queue.append(prerequisite)
    # A gap set that is one big cycle has no roots; give leftovers a depth so
    # the ordering below is still total.
    for node in nodes:
        depth.setdefault(node, 0)
    return depth


def teaching_order(
    gaps: Iterable[str],
    prerequisites_of: PrerequisitesOf,
    *,
    name_of: Callable[[str], str] | None = None,
) -> list[str]:
    """Order ``gaps`` so every concept comes after the gaps it requires.

    Kahn's algorithm over the subgraph the gaps induce. Among concepts that are
    ready at the same time, the one deepest in the walk goes first (it is the
    most foundational), then alphabetical by name for a stable result.

    Reversed BFS order looks like it should work and does not: BFS depth is the
    *shortest* distance from the target, but a concept can be a direct
    prerequisite of the target and also a prerequisite of something deeper.
    With T -> {X, Y}, X -> Z, Z -> Y the depths are X=1, Y=1, Z=2, so reversed
    BFS teaches Z before Y even though Z requires Y. Only a topological sort
    respects every edge.

    Edges to concepts outside ``gaps`` (known ones) are ignored. If the data
    contains a cycle, the concepts on it can never become ready; they are
    appended at the end in the same tie-break order rather than dropped, since
    validation, not this function, is the place to reject cycles.
    """
    nodes = list(dict.fromkeys(gaps))
    node_set = set(nodes)
    label = name_of or (lambda concept_id: concept_id)
    depth = _depths_within(node_set, prerequisites_of)

    def priority(concept_id: str) -> tuple[int, str, str]:
        return (-depth[concept_id], label(concept_id).casefold(), concept_id)

    unmet: dict[str, int] = {}
    dependents: dict[str, list[str]] = {n: [] for n in nodes}
    for node in nodes:
        within = {p for p in prerequisites_of(node) if p in node_set and p != node}
        unmet[node] = len(within)
        for prerequisite in within:
            dependents[prerequisite].append(node)

    ready = [priority(n) for n in nodes if unmet[n] == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        *_, concept = heapq.heappop(ready)
        order.append(concept)
        for dependent in dependents[concept]:
            unmet[dependent] -= 1
            if unmet[dependent] == 0:
                heapq.heappush(ready, priority(dependent))

    if len(order) < len(nodes):
        placed = set(order)
        order.extend(sorted((n for n in nodes if n not in placed), key=priority))
    return order


def altitude_for(gap_count: int) -> Altitude:
    """Explanation altitude for a number of gaps (the target counts if it is unknown).

    0-2 technical: explain at the requested level, fill small gaps inline.
    3-10 scaffolded: teach the prerequisites in order, then the concept.
    >10 intuition: an intuition-level answer plus a suggested learning path.
    """
    if gap_count <= 2:
        return "technical"
    if gap_count <= 10:
        return "scaffolded"
    return "intuition"


def adjacency_lookup(adjacency: Mapping[str, Iterable[str]]) -> PrerequisitesOf:
    """``prerequisites_of`` over a fetched subgraph; concepts outside it have none."""

    def prerequisites_of(concept_id: str) -> Iterable[str]:
        return adjacency.get(concept_id, ())

    return prerequisites_of
