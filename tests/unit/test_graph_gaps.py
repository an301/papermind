import random

import pytest

from papermind.graph.gaps import (
    adjacency_lookup,
    altitude_for,
    find_gaps,
    known_stops,
    teaching_order,
    walk_prerequisites,
)

WORKED_EXAMPLE = {
    "Transformer": ["Attention"],
    "Attention": ["Softmax", "DotProduct"],
    "Softmax": ["Exponentials"],
    "DotProduct": ["LinearAlgebra"],
    "Exponentials": [],
    "LinearAlgebra": [],
}


class RecordingLookup:
    """prerequisites_of that remembers which concepts the walk expanded."""

    def __init__(self, adjacency: dict[str, list[str]]) -> None:
        self.adjacency = adjacency
        self.expanded: list[str] = []

    def __call__(self, concept_id: str) -> list[str]:
        self.expanded.append(concept_id)
        return self.adjacency.get(concept_id, [])


def assert_topological(order: list[str], adjacency: dict[str, list[str]]) -> None:
    position = {c: i for i, c in enumerate(order)}
    for concept in order:
        for prerequisite in adjacency.get(concept, []):
            if prerequisite in position:
                assert position[prerequisite] < position[concept], f"{prerequisite} must precede {concept}"


def test_worked_example_gaps_in_bfs_order_and_known_concepts_stop_the_walk():
    lookup = RecordingLookup(WORKED_EXAMPLE)
    known = {"DotProduct", "Exponentials"}

    gaps = find_gaps("Transformer", known, lookup)

    assert gaps == [("Transformer", 0), ("Attention", 1), ("Softmax", 2)]
    # DotProduct is known, so the walk never descends to LinearAlgebra.
    assert "LinearAlgebra" not in lookup.expanded
    assert "DotProduct" not in lookup.expanded
    assert known_stops("Transformer", known, adjacency_lookup(WORKED_EXAMPLE)) == [
        "DotProduct",
        "Exponentials",
    ]


def test_worked_example_teaching_order():
    gaps = [
        cid
        for cid, _ in find_gaps(
            "Transformer", {"DotProduct", "Exponentials"}, adjacency_lookup(WORKED_EXAMPLE)
        )
    ]
    assert teaching_order(gaps, adjacency_lookup(WORKED_EXAMPLE)) == ["Softmax", "Attention", "Transformer"]


def test_known_target_has_no_gaps():
    walk = walk_prerequisites("Transformer", {"Transformer"}, adjacency_lookup(WORKED_EXAMPLE))
    assert walk.gaps == []
    assert walk.known_stops == ["Transformer"]


def test_nothing_known_walks_the_whole_closure():
    gaps = find_gaps("Transformer", set(), adjacency_lookup(WORKED_EXAMPLE))
    assert [g for g, _ in gaps] == [
        "Transformer",
        "Attention",
        "Softmax",
        "DotProduct",
        "Exponentials",
        "LinearAlgebra",
    ]
    assert dict(gaps) == {
        "Transformer": 0,
        "Attention": 1,
        "Softmax": 2,
        "DotProduct": 2,
        "Exponentials": 3,
        "LinearAlgebra": 3,
    }


def test_unknown_concept_without_prerequisites_is_a_single_gap():
    assert find_gaps("lonely", set(), adjacency_lookup({})) == [("lonely", 0)]


DIAMOND = {"T": ["A", "B"], "A": ["C"], "B": ["C"], "C": ["D"], "D": []}


def test_diamond_visits_shared_prerequisite_once_at_shortest_depth():
    lookup = RecordingLookup(DIAMOND)
    gaps = find_gaps("T", set(), lookup)
    assert gaps == [("T", 0), ("A", 1), ("B", 1), ("C", 2), ("D", 3)]
    assert lookup.expanded.count("C") == 1


def test_diamond_with_known_join_reports_the_stop_once():
    walk = walk_prerequisites("T", {"C"}, adjacency_lookup(DIAMOND))
    assert walk.gaps == [("T", 0), ("A", 1), ("B", 1)]
    assert walk.known_stops == ["C"]


def test_diamond_teaching_order():
    gaps = [g for g, _ in find_gaps("T", set(), adjacency_lookup(DIAMOND))]
    assert teaching_order(gaps, adjacency_lookup(DIAMOND)) == ["D", "C", "A", "B", "T"]


def test_teaching_order_is_not_reversed_bfs():
    # Y is a direct prerequisite of T (depth 1) and also of Z (depth 2):
    # reversed BFS would teach Z before Y.
    graph = {"T": ["X", "Y"], "X": ["Z"], "Z": ["Y"], "Y": []}
    gaps = find_gaps("T", set(), adjacency_lookup(graph))
    assert dict(gaps) == {"T": 0, "X": 1, "Y": 1, "Z": 2}
    reversed_bfs = [g for g, _ in reversed(gaps)]
    assert reversed_bfs.index("Z") < reversed_bfs.index("Y")

    order = teaching_order([g for g, _ in gaps], adjacency_lookup(graph))
    assert order == ["Y", "Z", "X", "T"]
    assert_topological(order, graph)


def test_ties_break_by_depth_then_name():
    graph = {"T": ["Q", "P"], "P": ["R"], "Q": [], "R": []}
    gaps = [g for g, _ in find_gaps("T", set(), adjacency_lookup(graph))]
    # R (depth 2) before the depth-1 leaves; P before Q alphabetically.
    assert teaching_order(gaps, adjacency_lookup(graph)) == ["R", "P", "Q", "T"]
    names = {"P": "zeta", "Q": "alpha", "R": "r", "T": "t"}
    assert teaching_order(gaps, adjacency_lookup(graph), name_of=names.__getitem__) == ["R", "Q", "P", "T"]


def test_teaching_order_ignores_edges_to_known_concepts():
    order = teaching_order(["Transformer", "Attention", "Softmax"], adjacency_lookup(WORKED_EXAMPLE))
    assert order == ["Softmax", "Attention", "Transformer"]


def test_cyclic_data_terminates_and_keeps_every_gap():
    graph = {"a": ["b"], "b": ["c"], "c": ["a"]}
    gaps = find_gaps("a", set(), adjacency_lookup(graph))
    assert gaps == [("a", 0), ("b", 1), ("c", 2)]
    order = teaching_order([g for g, _ in gaps], adjacency_lookup(graph))
    assert sorted(order) == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("count", "altitude"),
    [
        (0, "technical"),
        (1, "technical"),
        (2, "technical"),
        (3, "scaffolded"),
        (10, "scaffolded"),
        (11, "intuition"),
    ],
)
def test_altitude_boundaries(count, altitude):
    assert altitude_for(count) == altitude


def random_dag(rng: random.Random, n: int, p: float) -> dict[str, list[str]]:
    # Edges only from higher to lower index: acyclic by construction.
    nodes = [f"c{i:02d}" for i in range(n)]
    return {nodes[i]: [nodes[j] for j in range(i) if rng.random() < p] for i in range(n)}


@pytest.mark.parametrize("seed", range(25))
def test_random_dags_produce_valid_teaching_orders(seed):
    rng = random.Random(seed)
    graph = random_dag(rng, n=rng.randint(5, 30), p=rng.uniform(0.05, 0.4))
    target = max(graph)
    known = {c for c in graph if c != target and rng.random() < 0.3}
    gaps = find_gaps(target, known, adjacency_lookup(graph))
    gap_ids = [g for g, _ in gaps]

    assert len(gap_ids) == len(set(gap_ids))
    assert not set(gap_ids) & known
    assert [d for _, d in gaps] == sorted(d for _, d in gaps)  # BFS order is non-decreasing depth

    order = teaching_order(gap_ids, adjacency_lookup(graph))
    assert sorted(order) == sorted(gap_ids)
    assert_topological(order, graph)
    if gap_ids:
        assert order[-1] == target
