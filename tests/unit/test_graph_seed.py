import copy
import json
from pathlib import Path

import pytest

from papermind.graph.seed import (
    CatalogError,
    check_files,
    find_cycles,
    load_catalog,
    parse_areas,
    parse_concepts,
    validate,
)
from papermind.graph.testing import sample_catalog, sample_payloads, write_sample_files


def problems_for(concepts_payload, areas_payload=None):
    areas_payload = areas_payload or sample_payloads()[1]
    problems = []
    areas = parse_areas(areas_payload, problems)
    concepts = parse_concepts(concepts_payload, problems)
    return problems + validate(areas, concepts)


def codes(problems, severity="error"):
    return sorted(p.code for p in problems if p.severity == severity)


def concept(cid, requires=(), *, area="math", tier="core", name=None, aliases=()):
    return {
        "id": cid,
        "name": name or cid.replace("-", " ").title(),
        "aliases": list(aliases),
        "area": area,
        "tier": tier,
        "description": f"About {cid}.",
        "requires": list(requires),
    }


def test_sample_catalog_is_valid_and_has_expected_stats():
    catalog = sample_catalog()
    assert catalog.is_valid and catalog.warnings == []
    stats = catalog.stats()
    assert (stats.concepts, stats.areas, stats.edges) == (12, 3, 11)
    assert stats.max_depth == 3  # transformer -> attention -> softmax -> exponential-function
    assert stats.per_area == {"dl": 4, "math": 5, "ml": 3}
    assert stats.roots == 4 and stats.leaves == 4


def test_cycle_is_rejected_with_its_full_path():
    payload = {
        "concepts": [concept("a", ["b"]), concept("b", ["c"]), concept("c", ["a"]), concept("d", ["a"])]
    }
    problems = problems_for(payload)
    cycles = [p.message for p in problems if p.code == "cycle"]
    assert cycles == ["cycle: a -> b -> c -> a"]


def test_every_cyclic_region_is_reported():
    edges = {"a": ["b"], "b": ["a"], "x": ["y"], "y": ["z"], "z": ["x"], "q": ["a"]}
    assert find_cycles(edges) == [["a", "b", "a"], ["x", "y", "z", "x"]]


def test_long_chain_does_not_hit_recursion_limit():
    n = 5000
    edges = {f"c{i}": [f"c{i + 1}"] for i in range(n)}
    edges[f"c{n}"] = ["c0"]
    (cycle,) = find_cycles(edges)
    assert len(cycle) == n + 2 and cycle[0] == cycle[-1]


def test_self_loop_dangling_unknown_area_duplicate_id_and_bad_id():
    payload = {
        "concepts": [
            concept("a", ["a"]),
            concept("b", ["ghost"]),
            concept("c", area="nowhere"),
            concept("d"),
            concept("d", name="Other D"),
            concept("Bad_Id", name="Bad"),
        ]
    }
    assert codes(problems_for(payload)) == [
        "bad-id",
        "dangling-requires",
        "duplicate-id",
        "self-loop",
        "unknown-area",
    ]


def test_normalised_name_collision_across_concepts():
    payload = {
        "concepts": [
            concept("self-attention", name="Self-Attention"),
            concept("intra-attention", name="Intra Attention", aliases=["self attention"]),
        ]
    }
    problems = problems_for(payload)
    assert codes(problems) == ["name-collision"]
    assert "'self attention'" in problems[0].message


def test_plural_alias_of_own_name_is_fine():
    payload = {
        "concepts": [concept("transformer", name="Transformer", aliases=["Transformers", "transformer"])]
    }
    assert codes(problems_for(payload)) == []


def test_schema_errors_are_reported_per_row_without_hiding_others():
    payload = {
        "concepts": [
            {**concept("a"), "tier": "expert"},
            {**concept("b"), "prerequisites": ["a"]},
            concept("c", ["a"]),
        ]
    }
    problems = problems_for(payload)
    schema = [p.message for p in problems if p.code == "schema"]
    assert len(schema) == 2
    assert "concepts[0] (a)" in schema[0] and "tier" in schema[0]
    assert "concepts[1] (b)" in schema[1] and "prerequisites" in schema[1]
    # The row that did parse is still validated: it requires 'a', which failed to parse.
    assert "dangling-requires" in codes(problems)


def test_warnings_do_not_fail_validation():
    areas = {
        "areas": [*sample_payloads()[1]["areas"], {"area_id": "empty", "name": "E", "description": "None."}]
    }
    payload = {
        "concepts": [
            concept("adv", tier="advanced"),
            concept("found", ["adv", "adv"], tier="foundation"),
        ]
    }
    problems = problems_for(payload, areas)
    assert codes(problems) == []
    assert codes(problems, "warning") == [
        "duplicate-requires",
        "empty-area",
        "empty-area",
        "empty-area",
        "tier-inversion",
    ]


def test_duplicate_area_and_top_level_shape():
    problems = []
    parse_areas({"areas": [{"area_id": "x", "name": "X", "description": "x"}] * 2}, problems)
    assert problems == []
    assert codes(
        validate(parse_areas({"areas": [{"area_id": "x", "name": "X", "description": "x"}] * 2}, []), [])
    ) == ["duplicate-area"]
    parse_concepts({"items": []}, problems)
    parse_concepts({"version": 2, "concepts": []}, problems)
    assert codes(problems) == ["schema", "schema"]


def test_load_catalog_from_files(tmp_path: Path):
    concepts_path, areas_path = write_sample_files(tmp_path)
    catalog = load_catalog(concepts_path, areas_path)
    assert catalog.prerequisites_of("attention") == ["softmax", "dot-product"]
    assert catalog.info("softmax").requires == ["exponential-function"]
    assert catalog.index.resolve("batchnorm").ref.concept_id == "batch-normalization"


def test_load_catalog_raises_with_every_error(tmp_path: Path):
    concepts_path, areas_path = write_sample_files(tmp_path)
    payload = json.loads(concepts_path.read_text())
    payload["concepts"][0]["requires"] = ["transformer"]  # linear-algebra -> ... -> linear-algebra
    payload["concepts"][1]["area"] = "nowhere"
    concepts_path.write_text(json.dumps(payload))
    with pytest.raises(CatalogError) as err:
        load_catalog(concepts_path, areas_path)
    assert codes(err.value.problems) == ["cycle", "unknown-area"]
    # Reported from the smallest id on the loop, so the message is stable.
    assert "cycle: attention -> dot-product -> linear-algebra -> transformer -> attention" in str(err.value)


def test_check_files_reports_missing_and_malformed_files(tmp_path: Path):
    catalog, problems = check_files(tmp_path / "concepts.json")
    assert catalog is None and codes(problems) == ["missing-file", "missing-file"]

    concepts_path, _ = write_sample_files(tmp_path)
    concepts_path.write_text("{not json")
    catalog, problems = check_files(concepts_path)
    assert catalog is None and codes(problems) == ["bad-json"]


def test_check_files_keeps_a_best_effort_catalog_for_stats(tmp_path: Path):
    concepts_payload, areas_payload = copy.deepcopy(sample_payloads())
    concepts_payload["concepts"][0]["requires"] = ["missing-one"]
    (tmp_path / "concepts.json").write_text(json.dumps(concepts_payload))
    (tmp_path / "areas.json").write_text(json.dumps(areas_payload))
    catalog, problems = check_files(tmp_path / "concepts.json")
    assert codes(problems) == ["dangling-requires"]
    assert catalog is not None and not catalog.is_valid
    assert catalog.stats().edges == 11  # the dangling edge is not counted


def test_names_differing_only_in_spacing_warn():
    payload = {
        "concepts": [concept("max-pooling", name="Max Pooling"), concept("maxpool", name="Maxpooling")]
    }
    problems = problems_for(payload)
    assert codes(problems) == []
    assert "compact-collision" in codes(problems, "warning")
