"""A small, valid concept catalog for tests and local fakes.

Unit tests across the codebase need a graph store without Neo4j and without
depending on ``data/concepts`` (which is generated and changes). This catalog
is hand-written, tiny, and stable. Its transformer subtree is the worked
example from the design notes:

    transformer -> attention -> {softmax, dot-product}
    softmax -> exponential-function      dot-product -> linear-algebra

``namespace`` prefixes every id, name and alias so an integration test can
seed it next to real data without colliding, and clean up by prefix.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from papermind.graph.seed import Catalog, CatalogError, Problem, parse_areas, parse_concepts

SAMPLE_AREAS: list[dict[str, str]] = [
    {"area_id": "math", "name": "Mathematics", "description": "Linear algebra, calculus and statistics."},
    {"area_id": "ml", "name": "Machine learning", "description": "Optimisation and classic models."},
    {"area_id": "dl", "name": "Deep learning", "description": "Neural architectures and training tricks."},
]

SAMPLE_CONCEPTS: list[dict[str, Any]] = [
    {
        "id": "linear-algebra",
        "name": "Linear Algebra",
        "aliases": [],
        "area": "math",
        "tier": "foundation",
        "description": "Vectors, matrices and the linear maps between them.",
        "requires": [],
    },
    {
        "id": "exponential-function",
        "name": "Exponential Function",
        "aliases": ["exponentials", "exp"],
        "area": "math",
        "tier": "foundation",
        "description": "The function e^x and its growth behaviour.",
        "requires": [],
    },
    {
        "id": "dot-product",
        "name": "Dot Product",
        "aliases": ["inner product", "scalar product"],
        "area": "math",
        "tier": "foundation",
        "description": "Sum of element-wise products of two vectors.",
        "requires": ["linear-algebra"],
    },
    {
        "id": "softmax",
        "name": "Softmax",
        "aliases": ["softmax function"],
        "area": "ml",
        "tier": "core",
        "description": "Turns a vector of scores into a probability distribution.",
        "requires": ["exponential-function"],
    },
    {
        "id": "attention",
        "name": "Attention",
        "aliases": ["attention mechanism"],
        "area": "dl",
        "tier": "core",
        "description": "Weighting values by the similarity of a query to keys.",
        "requires": ["softmax", "dot-product"],
    },
    {
        "id": "transformer",
        "name": "Transformer",
        "aliases": ["transformer architecture"],
        "area": "dl",
        "tier": "advanced",
        "description": "A sequence model built from stacked attention layers.",
        "requires": ["attention"],
    },
    {
        "id": "self-attention",
        "name": "Self-Attention",
        "aliases": ["intra-attention"],
        "area": "dl",
        "tier": "advanced",
        "description": "Attention where queries, keys and values come from one sequence.",
        "requires": ["attention"],
    },
    {
        "id": "derivative",
        "name": "Derivative",
        "aliases": [],
        "area": "math",
        "tier": "foundation",
        "description": "The instantaneous rate of change of a function.",
        "requires": [],
    },
    {
        "id": "gradient-descent",
        "name": "Gradient Descent",
        "aliases": ["GD"],
        "area": "ml",
        "tier": "core",
        "description": "Minimising a function by stepping against its gradient.",
        "requires": ["derivative"],
    },
    {
        "id": "mean-and-variance",
        "name": "Mean and Variance",
        "aliases": ["variance"],
        "area": "math",
        "tier": "foundation",
        "description": "The average of a distribution and its spread around it.",
        "requires": [],
    },
    {
        "id": "batch-normalization",
        "name": "Batch Normalization",
        "aliases": ["batchnorm", "BN"],
        "area": "dl",
        "tier": "core",
        "description": "Normalising activations with mini-batch statistics during training.",
        "requires": ["mean-and-variance", "gradient-descent"],
    },
    {
        "id": "neural-network",
        "name": "Neural Network",
        "aliases": ["neural net"],
        "area": "ml",
        "tier": "core",
        "description": "Layers of weighted sums and nonlinearities trained by gradient descent.",
        "requires": ["gradient-descent", "linear-algebra"],
    },
]


def sample_payloads(namespace: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
    """``(concepts.json, areas.json)`` payloads, optionally namespaced by ``namespace``."""

    def nid(value: str) -> str:
        return f"{namespace}-{value}" if namespace else value

    def nname(value: str) -> str:
        return f"{namespace} {value}" if namespace else value

    concepts = [
        {
            **c,
            "id": nid(c["id"]),
            "name": nname(c["name"]),
            "aliases": [nname(a) for a in c["aliases"]],
            "area": nid(c["area"]),
            "requires": [nid(r) for r in c["requires"]],
        }
        for c in SAMPLE_CONCEPTS
    ]
    areas = [{**a, "area_id": nid(a["area_id"]), "name": nname(a["name"])} for a in SAMPLE_AREAS]
    return {"version": 1, "concepts": concepts}, {"areas": areas}


def sample_catalog(namespace: str = "") -> Catalog:
    concepts_payload, areas_payload = sample_payloads(namespace)
    problems: list[Problem] = []
    areas = parse_areas(areas_payload, problems)
    concepts = parse_concepts(concepts_payload, problems)
    if problems:
        raise CatalogError(problems)
    return Catalog.build(areas, concepts)


def write_sample_files(directory: Path, namespace: str = "") -> tuple[Path, Path]:
    """Write ``concepts.json`` and ``areas.json`` into ``directory``; returns their paths."""
    concepts_payload, areas_payload = sample_payloads(namespace)
    directory.mkdir(parents=True, exist_ok=True)
    concepts_path = directory / "concepts.json"
    areas_path = directory / "areas.json"
    concepts_path.write_text(json.dumps(concepts_payload, indent=2), encoding="utf-8")
    areas_path.write_text(json.dumps(areas_payload, indent=2), encoding="utf-8")
    return concepts_path, areas_path
